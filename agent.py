import os
import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import sqlite3
import requests

from agent_tools import DataFetchTool, VolatilityTool, TradingTool
from llm_tools import LLMTool
from agent_types import ActionPlan, ExecutionResult, AgentMemory, AgentState
from rich.console import Console
from rich.table import Table

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

console = Console()

class VolatilityAgent:
    """Agent that monitors live games and executes trades based on volatility signals."""
    
    def __init__(self):
        # Initialize tools
        self.data_tool = DataFetchTool()
        self.volatility_tool = VolatilityTool()
        self.trading_tool = TradingTool()
        self.llm_tool = LLMTool()
        
        # Initialize agent state and memory
        self.memory = AgentMemory()
        self.state = AgentState()
        self.position_manager = self.state  # Use AgentState as position manager
        
        # Initialize database connection with explicit read/write permissions
        self.db_conn = sqlite3.connect('unabated_odds.db', check_same_thread=False, uri=True)
        self.db_conn.execute('PRAGMA journal_mode=WAL')  # Enable Write-Ahead Logging for better concurrency
        self._init_db()
        
        # Setup Telegram
        self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if not self.telegram_bot_token or not self.telegram_chat_id:
            logger.warning("Telegram credentials not found in environment")
    
    def _init_db(self):
        """Initialize database tables for trade alerts and pregame volatilities."""
        cursor = self.db_conn.cursor()
        
        # Trade alerts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_alerts (
                timestamp TEXT,
                event_id INTEGER,
                side_index INTEGER,
                action TEXT,
                confidence REAL,
                rationale TEXT,
                historical_vol REAL,
                current_vol REAL,
                game_clock TEXT,
                score_differential INTEGER
            )
        """)
        
        # Pregame volatilities table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pregame_volatilities (
                timestamp TEXT,
                event_id INTEGER,
                league TEXT,
                home_team TEXT,
                away_team TEXT,
                spread REAL,
                price INTEGER,
                source_format INTEGER,
                home_vol REAL,
                away_vol REAL,
                home_prob REAL,
                away_prob REAL
            )
        """)
        
        self.db_conn.commit()
    
    def send_telegram_alert(self, message: str) -> bool:
        """Send alert message to Telegram."""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            logger.warning("Cannot send Telegram alert: missing credentials")
            return False
            
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            data = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, json=data)
            response.raise_for_status()
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {str(e)}")
            return False
    
    def format_pregame_summary(self, events: List[Dict]) -> str:
        """Format pregame volatility summary for alerts."""
        if not events:
            return "No pregame events found"
            
        # Group by league
        games_by_league = {}
        for event in events:
            league = event['league']
            if league not in games_by_league:
                games_by_league[league] = []
            games_by_league[league].append(event)
        
        summary = f"""
📊 <b>Pregame Volatility Summary</b>
{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

"""
        for league in sorted(games_by_league.keys()):
            games = games_by_league[league]
            summary += f"\n<b>{league} Games ({len(games)})</b>\n"
            
            for game in games:
                prob = self._get_odds_probability(game['price'], game['source_format'])
                summary += f"""
{game['away_team']} @ {game['home_team']}
Spread: {game['spread']:+.1f}
Price: {game['price']} ({self._format_source(game['source_format'])})
Probability: {prob:.1%}
Home Vol: {game.get('home_vol', 'N/A'):.2f}
Away Vol: {game.get('away_vol', 'N/A'):.2f}
"""
        
        return summary
    
    def _format_source(self, source_format: int) -> str:
        """Format source format code as string."""
        formats = {
            1: "American",
            2: "Decimal",
            3: "Percent",
            4: "Probability",
            5: "SportTrade"
        }
        return formats.get(source_format, f"Unknown ({source_format})")
    
    def store_pregame_volatilities(self, events: List[Dict]):
        """Store pregame volatilities in database."""
        cursor = self.db_conn.cursor()
        now = datetime.utcnow().isoformat()
        
        for event in events:
            prob = self._get_odds_probability(event['price'], event['source_format'])
            cursor.execute("""
                INSERT INTO pregame_volatilities (
                    timestamp, event_id, league, home_team, away_team,
                    spread, price, source_format, home_vol, away_vol,
                    home_prob, away_prob
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now,
                event['event_id'],
                event['league'],
                event['home_team'],
                event['away_team'],
                event['spread'],
                event['price'],
                event['source_format'],
                event.get('home_vol'),
                event.get('away_vol'),
                prob if event['side_index'] == 1 else 1 - prob,
                1 - prob if event['side_index'] == 1 else prob
            ))
        
        self.db_conn.commit()
    
    def _american_to_prob(self, price: int) -> float:
        """
        Convert American odds to probability.
        Following Unabated API format (sourceFormat = 1 for American odds)
        """
        if price > 0:
            return 100.0 / (100.0 + price)
        else:
            return abs(price) / (abs(price) + 100.0)
    
    def _get_odds_probability(self, price: int, source_format: int) -> float:
        """
        Convert odds to probability based on source format.
        Following Unabated API source formats:
        1 = American
        2 = Decimal
        3 = Percent
        4 = Probability
        5 = Sporttrade (0 to 100)
        """
        if source_format == 1:  # American
            return self._american_to_prob(price)
        elif source_format == 2:  # Decimal
            return 1.0 / price
        elif source_format == 3:  # Percent
            return price / 100.0
        elif source_format == 4:  # Probability
            return price
        elif source_format == 5:  # Sporttrade
            return price / 100.0
        else:
            logger.warning(f"Unknown source format: {source_format}, defaulting to American odds")
            return self._american_to_prob(price)
    
    def fetch_and_analyze_event(self, event: Dict[str, Any]) -> Optional[ActionPlan]:
        """
        Analyze a single event and generate an action plan.
        Returns None if no action should be taken.
        """
        try:
            event_id = event['event_id']
            
            # Skip if we can't take new positions
            for side_index in [0, 1]:  # Both sides of the market
                if not self.state.can_take_new_position(event_id, side_index):
                    continue
                
                # Get volatility metrics
                vol_data = self.volatility_tool.run(event_id, side_index)
                if not vol_data:
                    logger.warning(f"No volatility data for event {event_id}, side {side_index}")
                    continue
                
                # Prepare context for LLM
                context = {
                    'league': event.get('league'),
                    'game_clock': event.get('game_clock'),
                    'score_differential': abs(
                        event.get('away_team_score', 0) - event.get('home_team_score', 0)
                    ),
                    'historical_vol': vol_data.get('historical_vol'),
                    'current_vol': vol_data.get('current_vol'),
                    'recent_changes': vol_data.get('recent_changes')
                }
                
                # Get recent history for context
                recent_history = self.memory.get_recent_context()
                
                # Query LLM for decision
                action, confidence, rationale = self.llm_tool.run(context, recent_history)
                
                # Create action plan if confidence meets threshold
                if confidence >= self.state.min_confidence and action != "NO_ACTION":
                    plan = ActionPlan(
                        event_id=event_id,
                        side_index=side_index,
                        action=action,
                        confidence=confidence,
                        size=self.state.position_size,
                        rationale=rationale,
                        volatility_data=vol_data
                    )
                    return plan
            
            return None
            
        except Exception as e:
            logger.error(f"Error analyzing event {event.get('event_id')}: {str(e)}")
            self.state.record_error()
            return None
    
    def execute_action_plan(self, plan: ActionPlan) -> ExecutionResult:
        """Execute a trading action plan."""
        try:
            # Log the trade alert
            self._log_trade_alert(plan)
            
            # Execute the trade
            trade_result = self.trading_tool.run(
                event_id=plan.event_id,
                side_index=plan.side_index,
                action=plan.action,
                size=plan.size
            )
            
            # Update state and memory
            if trade_result.get('success'):
                self.state.add_position(
                    plan.event_id,
                    plan.side_index,
                    {'action': plan.action, 'size': plan.size}
                )
                self.memory.add_action(plan)
                self.state.record_success()
                
                result = ExecutionResult(
                    success=True,
                    trade_id=trade_result.get('trade_id')
                )
            else:
                result = ExecutionResult(
                    success=False,
                    error_message=trade_result.get('error')
                )
            
            self.memory.add_execution(result)
            return result
            
        except Exception as e:
            logger.error(f"Error executing trade: {str(e)}")
            self.state.record_error()
            return ExecutionResult(success=False, error_message=str(e))
    
    def _log_trade_alert(self, plan: ActionPlan):
        """Log trade alert to database and console."""
        # Log to database
        cursor = self.db_conn.cursor()
        cursor.execute("""
            INSERT INTO trade_alerts (
                timestamp, event_id, side_index, action, confidence,
                rationale, historical_vol, current_vol, game_clock,
                score_differential
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            plan.timestamp,
            plan.event_id,
            plan.side_index,
            plan.action,
            plan.confidence,
            plan.rationale,
            plan.volatility_data.get('historical_vol'),
            plan.volatility_data.get('current_vol'),
            plan.volatility_data.get('game_clock'),
            plan.volatility_data.get('score_differential')
        ))
        self.db_conn.commit()
        
        # Log to console with rich formatting
        table = Table(title=f"Trade Alert - Event {plan.event_id}")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("Action", plan.action)
        table.add_row("Confidence", f"{plan.confidence:.2f}")
        table.add_row("Rationale", plan.rationale or "N/A")
        table.add_row("Historical Vol", f"{plan.volatility_data.get('historical_vol', 'N/A')}")
        table.add_row("Current Vol", f"{plan.volatility_data.get('current_vol', 'N/A')}")
        
        console.print(table)
    
    def get_active_events(self):
        """Get active events from the database."""
        cursor = self.db_conn.cursor()
        cursor.execute("""
            WITH latest_odds AS (
                SELECT 
                    event_id,
                    bet_type,
                    side_index,
                    source_format,
                    points,
                    price,
                    game_clock,
                    score_difference,
                    ROW_NUMBER() OVER (
                        PARTITION BY event_id, bet_type, side_index 
                        ORDER BY timestamp_utc DESC
                    ) as rn
                FROM game_odds
                WHERE game_clock IS NOT NULL  -- Live games only
                AND bet_type = '1'  -- Spread bets only
                AND DATE(timestamp_utc) = DATE('now')
            )
            SELECT 
                e.event_id,
                e.bet_type,
                e.side_index,
                e.source_format,
                e.points as spread,
                e.price,
                e.game_clock,
                e.score_difference
            FROM latest_odds e
            WHERE e.rn = 1
            ORDER BY e.event_id, e.side_index
        """)
        
        events = {}
        for row in cursor.fetchall():
            event_id = row[0]
            if event_id not in events:
                events[event_id] = {
                    'event_id': event_id,
                    'home_prob': None,
                    'away_prob': None,
                    'spread': None,
                    'game_clock': row[6],
                    'score_difference': row[7]
                }
            
            # Convert odds to probability based on source format
            prob = self._get_odds_probability(row[5], row[3])
            
            if row[2] == 0:  # Away team
                events[event_id]['away_prob'] = prob
                events[event_id]['spread'] = row[4]  # Points
            else:  # Home team
                events[event_id]['home_prob'] = prob
            
        return list(events.values())

    def get_pregame_events(self):
        """Get all pregame events with their spread odds."""
        cursor = self.db_conn.cursor()
        
        try:
            # Get latest spread odds for each event where game_clock is NULL (pregame)
            cursor.execute("""
                WITH latest_odds AS (
                    SELECT 
                        event_id,
                        timestamp_utc,
                        bet_type,
                        side_index,
                        points as spread,
                        price,
                        source_format,
                        ROW_NUMBER() OVER (
                            PARTITION BY event_id, side_index 
                            ORDER BY timestamp_utc DESC
                        ) as rn
                    FROM game_odds
                    WHERE game_clock IS NULL  -- Pregame only
                    AND bet_type = '2'  -- Spread only
                )
                SELECT 
                    event_id,
                    timestamp_utc,
                    side_index,
                    spread,
                    price,
                    source_format
                FROM latest_odds 
                WHERE rn = 1
                ORDER BY timestamp_utc DESC
            """)
            
            events = cursor.fetchall()
            return [{
                'event_id': event[0],
                'timestamp_utc': event[1],
                'side_index': event[2],
                'spread': event[3],
                'price': event[4],
                'source_format': event[5]
            } for event in events]
            
        except Exception as e:
            logger.error(f"Error getting pregame events: {e}")
            return []

    def run(self, interval: int = 60):
        """Main agent loop."""
        logger.info("Starting VolatilityAgent...")
        
        while True:
            try:
                # 1. First check pregame events
                pregame_events = self.get_pregame_events()
                if pregame_events:
                    logger.info(f"Found {len(pregame_events)} pregame events")
                    
                    # Process each event
                    for event in pregame_events:
                        # Convert odds to probability
                        prob = self._get_odds_probability(event['price'], event['source_format'])
                        
                        # Compute implied volatility
                        if event['spread'] is not None:
                            home_vol = self.compute_implied_vol(abs(event['spread']), prob)
                            away_vol = self.compute_implied_vol(abs(event['spread']), 1 - prob)
                            
                            event['home_vol'] = home_vol
                            event['away_vol'] = away_vol
                    
                    # Store volatilities and send alert
                    self.store_pregame_volatilities(pregame_events)
                    summary = self.format_pregame_summary(pregame_events)
                    self.send_telegram_alert(summary)
                
                # 2. Then check live events
                live_events = self.get_active_events()
                for event in live_events:
                    action = self.fetch_and_analyze_event(event)
                    if action:
                        self.execute_action(action)
                
                time.sleep(interval)
                
            except Exception as e:
                logger.error(f"Error in agent loop: {e}")
                time.sleep(interval)
            
    def compute_implied_vol(self, spread: float, prob: float) -> Optional[float]:
        """Compute implied volatility from spread and probability."""
        try:
            if not 0 < prob < 1 or spread <= 0:
                return None
                
            # Use inverse normal CDF to get z-score
            from scipy.stats import norm
            z_score = norm.ppf(prob)
            
            # Implied vol = |spread| / z_score
            implied_vol = abs(spread) / abs(z_score)
            
            return implied_vol
            
        except Exception as e:
            logger.error(f"Error computing implied vol: {e}")
            return None

def main():
    agent = VolatilityAgent()
    agent.run()

if __name__ == "__main__":
    main() 