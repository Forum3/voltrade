import logging
import time
from datetime import datetime
from typing import Dict, List, Optional
from agent_tools import get_db_connection
from volatility_tools import compute_pregame_implied_vol
from agent_types import LEAGUE_PARAMS
from llm_tools import get_llm_response
from alerts import AlertManager

class PregameAgent:
    def __init__(self):
        self.alert_manager = AlertManager()
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
    
    def get_pregame_candidates(self) -> List[Dict]:
        """Get pregame events from database."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                WITH latest_odds AS (
                    SELECT 
                        event_id,
                        league,
                        home_team,
                        away_team,
                        bet_type,
                        points,
                        price,
                        side_index,
                        ROW_NUMBER() OVER (
                            PARTITION BY event_id, bet_type, side_index 
                            ORDER BY timestamp_utc DESC
                        ) as rn
                    FROM game_odds
                    WHERE game_clock IS NULL  -- Pregame only
                    AND league IN ('NBA', 'NFL')
                    AND DATE(timestamp_utc) = DATE('now')
                )
                SELECT 
                    s1.event_id,
                    s1.league,
                    s1.home_team,
                    s1.away_team,
                    s1.points as spread,
                    m1.price as home_prob,
                    m2.price as away_prob,
                    t1.points as total
                FROM latest_odds s1
                JOIN latest_odds s2 
                    ON s1.event_id = s2.event_id
                    AND s1.bet_type = 'Spread'
                    AND s2.bet_type = 'Spread'
                    AND s1.side_index = 1  -- Home spread
                    AND s2.side_index = 0  -- Away spread
                    AND s1.rn = 1 AND s2.rn = 1
                JOIN latest_odds m1
                    ON s1.event_id = m1.event_id
                    AND m1.bet_type = 'Moneyline'
                    AND m1.side_index = 1  -- Home ML
                    AND m1.rn = 1
                JOIN latest_odds m2
                    ON s1.event_id = m2.event_id
                    AND m2.bet_type = 'Moneyline'
                    AND m2.side_index = 0  -- Away ML
                    AND m2.rn = 1
                JOIN latest_odds t1
                    ON s1.event_id = t1.event_id
                    AND t1.bet_type = 'Total'
                    AND t1.side_index = 1  -- Over
                    AND t1.rn = 1
                ORDER BY s1.league, s1.event_id
            """)
            return [
                {
                    "event_id": row[0],
                    "league": row[1],
                    "home_team": row[2],
                    "away_team": row[3],
                    "spread": row[4],
                    "home_price": row[5],  # Home probability
                    "away_price": row[6],  # Away probability
                    "total_points": row[7],
                    "current_prob": row[5]  # Use home probability
                }
                for row in cursor.fetchall()
            ]
    
    def compute_and_store_volatilities(self, events: List[Dict]) -> None:
        """Compute pregame implied volatilities and store in database."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Clear existing pregame vols for these events
            event_ids = [e["event_id"] for e in events]
            if event_ids:
                cursor.execute(
                    "DELETE FROM pregame_implied_vol WHERE event_id IN (%s)" % 
                    ",".join("?" * len(event_ids)), 
                    event_ids
                )
            
            # Compute and store new vols
            for event in events:
                # Compute vols for both sides
                home_vol = compute_pregame_implied_vol(
                    spread=event["spread"],
                    moneyline_prob=event["current_prob"]
                )
                away_vol = compute_pregame_implied_vol(
                    spread=-event["spread"],
                    moneyline_prob=1 - event["current_prob"]
                )
                
                if home_vol and away_vol:
                    cursor.execute("""
                        INSERT INTO pregame_implied_vol 
                        (event_id, league, spread_points, implied_vol, timestamp_utc)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        event["event_id"],
                        event["league"],
                        event["spread"],
                        (home_vol + away_vol) / 2,  # Average of both sides
                        datetime.utcnow().isoformat()
                    ))
            
            conn.commit()
    
    def format_volatility_summary(self, events: List[Dict]) -> str:
        """Format summary of pregame volatility calculations."""
        # Group by league
        games_by_league = {"NBA": [], "NFL": []}
        for event in events:
            games_by_league[event["league"]].append(event)
        
        summary = f"""
📊 <b>Pregame Volatility Summary</b>
{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

"""
        for league in ["NBA", "NFL"]:
            games = games_by_league[league]
            if not games:
                continue
                
            summary += f"\n<b>{league} Games ({len(games)})</b>\n"
            for game in games:
                # Compute implied vol for both sides
                home_vol = compute_pregame_implied_vol(
                    spread=game["spread"],
                    moneyline_prob=game["current_prob"]
                )
                away_vol = compute_pregame_implied_vol(
                    spread=-game["spread"],
                    moneyline_prob=1 - game["current_prob"]
                )
                
                if home_vol and away_vol:
                    summary += f"""
{game['away_team']} @ {game['home_team']}
Spread: {game['spread']:+.1f}
Home Vol: {home_vol:.2f} (${game['home_price']:.2f})
Away Vol: {away_vol:.2f} (${game['away_price']:.2f})
"""
        
        return summary
    
    def analyze_market_state(self, event: Dict) -> Optional[Dict]:
        """Use LLM to analyze pregame market state."""
        # Compute vols for analysis
        home_vol = compute_pregame_implied_vol(
            spread=event["spread"],
            moneyline_prob=event["current_prob"]
        )
        away_vol = compute_pregame_implied_vol(
            spread=-event["spread"],
            moneyline_prob=1 - event["current_prob"]
        )
        
        if not home_vol or not away_vol:
            return None
            
        # Format market data for LLM
        prompt = f"""
Pregame Market Analysis:
Event: {event['away_team']} @ {event['home_team']} ({event['league']})
Spread: {event['spread']:+.1f}
Home Price: ${event['home_price']:.2f} (Vol: {home_vol:.2f})
Away Price: ${event['away_price']:.2f} (Vol: {away_vol:.2f})

Based on these metrics, should we flag this game for potential volatility trading?
Consider league dynamics and market pricing.
"""
        
        response = get_llm_response(prompt)
        return response if response.get('confidence', 0) > 0.7 else None
    
    def run(self, interval: int = 300) -> None:
        """Main agent loop."""
        logging.info("Starting PregameAgent...")
        
        while True:
            try:
                # Get pregame events
                events = self.get_pregame_candidates()
                if not events:
                    logging.info("No upcoming games found")
                    continue
                
                # Compute and store volatilities
                self.compute_and_store_volatilities(events)
                logging.info(f"Computed volatilities for {len(events)} events")
                
                # Send summary alert
                summary = self.format_volatility_summary(events)
                if self.alert_manager.send_alert(summary):
                    logging.info("Sent volatility summary alert")
                else:
                    logging.error("Failed to send summary alert")
                
                # Analyze each event
                for event in events:
                    analysis = self.analyze_market_state(event)
                    if analysis:
                        logging.info(f"Flagged {event['away_team']} @ {event['home_team']} for potential trading")
                
            except Exception as e:
                logging.error(f"Error in pregame agent: {str(e)}")
            
            time.sleep(interval)

if __name__ == "__main__":
    agent = PregameAgent()
    agent.run() 