import logging
from datetime import datetime
from typing import Dict, List
from agent_tools import get_db_connection
from volatility_tools import compute_pregame_implied_vol
from alerts import AlertManager

def get_upcoming_games() -> Dict[str, List[Dict]]:
    """Get upcoming games from the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT
                e.event_id,
                e.league,
                e.home_team,
                e.away_team,
                e.home_price,
                e.away_price,
                e.current_prob,
                p.spread_points
            FROM game_odds e
            JOIN pregame_implied_vol p ON e.event_id = p.event_id
            WHERE e.game_clock IS NULL  -- Pregame only
            AND e.league IN ('NBA', 'NFL')
            AND DATE(e.timestamp_utc) = DATE('now')
            ORDER BY e.league, e.event_id
        """)
        rows = cursor.fetchall()
        
    # Group by league
    games_by_league = {"NBA": [], "NFL": []}
    for row in rows:
        game = {
            "event_id": row[0],
            "league": row[1],
            "home_team": row[2],
            "away_team": row[3],
            "home_price": row[4],
            "away_price": row[5],
            "current_prob": row[6],
            "spread": row[7]
        }
        games_by_league[game["league"]].append(game)
    
    return games_by_league

def format_pregame_summary(games_by_league: Dict[str, List[Dict]]) -> str:
    """Format summary of pregame volatility calculations."""
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
                spread=-game["spread"],  # Flip sign for away
                moneyline_prob=1 - game["current_prob"]
            )
            
            summary += f"""
{game['away_team']} @ {game['home_team']}
Spread: {game['spread']:+.1f}
Home Vol: {home_vol:.2f} (${game['home_price']:.2f})
Away Vol: {away_vol:.2f} (${game['away_price']:.2f})
"""
    
    return summary

def main():
    logging.basicConfig(level=logging.INFO)
    alert_manager = AlertManager()
    
    # Get upcoming games
    logging.info("Fetching upcoming games...")
    games = get_upcoming_games()
    
    # Count games by league
    nba_count = len(games["NBA"])
    nfl_count = len(games["NFL"])
    logging.info(f"Found {nba_count} NBA games and {nfl_count} NFL games")
    
    if nba_count + nfl_count == 0:
        logging.warning("No upcoming games found")
        return
    
    # Format and send summary
    summary = format_pregame_summary(games)
    success = alert_manager.send_alert(summary)
    
    if success:
        logging.info("Pregame summary alert sent successfully")
    else:
        logging.error("Failed to send pregame summary alert")

if __name__ == "__main__":
    main() 