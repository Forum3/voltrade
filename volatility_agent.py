import logging
import time
from datetime import datetime
from typing import Optional, List, Dict

from position_manager import PositionManager, Position
from volatility_tools import (
    compute_live_implied_vol,
    compute_expected_vol,
    compute_vol_deviation,
    check_vol_signal,
    parse_game_clock,
    get_probability_from_source,
    get_position_size
)
from agent_tools import get_db_connection
from agent_types import LEAGUE_PARAMS
from llm_tools import get_llm_response
from alerts import AlertManager

class VolatilityAgent:
    """
    Unified agent that handles both entry and exit decisions using 
    Polson-Stern volatility calculations and LLM-based analysis.
    """
    
    def __init__(self, initial_capital: float = 1000.0):
        self.position_manager = PositionManager()
        self.alert_manager = AlertManager()
        self.initial_capital = initial_capital
        self.total_pnl = 0.0
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        
        logging.info("VolatilityAgent initialized with alerts %s", 
                    "enabled" if self.alert_manager.enabled else "disabled")
    
    def get_active_events(self) -> List[Dict]:
        """Query DB for active events with required data."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT 
                    e.event_id,
                    e.league,
                    e.game_clock,
                    e.score_diff,
                    e.current_prob,
                    e.source_format,
                    e.home_team,
                    e.away_team,
                    e.home_price,
                    e.away_price,
                    p.spread_points,
                    p.implied_vol as pregame_vol
                FROM game_odds e
                JOIN pregame_implied_vol p ON e.event_id = p.event_id
                WHERE e.source_format = 5  -- SportTrade required for live
                AND e.game_clock IS NOT NULL
                ORDER BY e.event_id
            """)
            return cursor.fetchall()
    
    def analyze_position(
        self,
        event_id: int,
        league: str,
        side_index: int,
        game_clock: str,
        score_diff: float,
        current_prob: float,
        pregame_spread: float,
        pregame_vol: float
    ) -> Optional[Dict]:
        """
        Analyze event using Polson-Stern formulas and return trading decision.
        """
        # Parse game clock
        time_elapsed = parse_game_clock(game_clock, league)
        if not time_elapsed:
            return None
            
        # Compute live implied volatility
        live_vol = compute_live_implied_vol(
            lead=score_diff,
            pregame_spread=pregame_spread,
            time_elapsed=time_elapsed,
            current_prob=current_prob,
            side_index=side_index,
            source_format=5  # SportTrade
        )
        if not live_vol:
            return None
            
        # Compute expected volatility
        expected_vol = compute_expected_vol(
            pregame_vol=pregame_vol,
            time_elapsed=time_elapsed,
            league=league
        )
        if not expected_vol:
            return None
            
        # Check for trading signal
        should_trade, direction, metadata = check_vol_signal(
            live_vol=live_vol,
            expected_vol=expected_vol,
            league=league,
            source_format=5  # SportTrade
        )
        
        if not should_trade:
            return None
            
        # Get LLM analysis
        prompt = f"""
        Event {event_id} ({league}) Analysis:
        - Time Elapsed: {time_elapsed:.1%}
        - Score Differential: {score_diff:+.1f}
        - Current Probability: {current_prob:.3f}
        - Pregame Implied Vol: {pregame_vol:.2f}
        - Live Implied Vol: {live_vol:.2f}
        - Expected Vol: {expected_vol:.2f}
        - Vol Deviation: {metadata['deviation']:.1f}%
        
        Based on these metrics, should we {direction} with confidence?
        Consider game state and volatility trends.
        """
        
        llm_response = get_llm_response(prompt)
        confidence = float(llm_response.get('confidence', 0.0))
        
        if confidence < 0.7:  # Require high confidence
            return None
            
        return {
            'direction': direction,
            'confidence': confidence,
            'live_vol': live_vol,
            'expected_vol': expected_vol,
            'metadata': metadata
        }
    
    def execute_trade(
        self,
        event_id: int,
        league: str,
        side_index: int,
        direction: str,
        confidence: float,
        live_vol: float,
        expected_vol: float,
        score_diff: float,
        current_prob: float,
        game_clock: Optional[str] = None,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        home_price: Optional[float] = None,
        away_price: Optional[float] = None
    ) -> bool:
        """Execute trade and track position."""
        # Compute position size (% of initial capital)
        vol_diff = compute_vol_deviation(live_vol, expected_vol)
        size_pct = get_position_size(league, confidence, vol_diff)
        size = (size_pct / 100.0) * self.initial_capital
        
        # Open position
        self.position_manager.open_position(
            event_id=event_id,
            league=league,
            side_index=side_index,
            position_type=direction,
            size=size,
            live_vol=live_vol,
            expected_vol=expected_vol,
            score_diff=score_diff,
            current_prob=current_prob
        )
        
        # Send entry alert
        alert_msg = self.alert_manager.format_entry_alert(
            event_id=event_id,
            league=league,
            side_index=side_index,
            direction=direction,
            size=size,
            confidence=confidence,
            live_vol=live_vol,
            expected_vol=expected_vol,
            score_diff=score_diff,
            current_prob=current_prob,
            game_clock=game_clock,
            home_team=home_team,
            away_team=away_team,
            home_price=home_price,
            away_price=away_price
        )
        self.alert_manager.send_alert(alert_msg)
        
        logging.info(f"Opened {direction} position: event={event_id} side={side_index} size=${size:.2f}")
        return True
    
    def check_exits(self, events: List[Dict]) -> None:
        """Check exit conditions for all open positions."""
        for event in events:
            event_id = event['event_id']
            for side_index in [0, 1]:
                position = self.position_manager.get_position(event_id, side_index)
                if not position:
                    continue
                    
                # Get current market state
                time_elapsed = parse_game_clock(event['game_clock'], event['league'])
                if not time_elapsed:
                    continue
                    
                # Compute current volatilities
                live_vol = compute_live_implied_vol(
                    lead=event['score_diff'],
                    pregame_spread=event['spread_points'],
                    time_elapsed=time_elapsed,
                    current_prob=event['current_prob'],
                    side_index=side_index,
                    source_format=5
                )
                expected_vol = compute_expected_vol(
                    pregame_vol=event['pregame_vol'],
                    time_elapsed=time_elapsed,
                    league=event['league']
                )
                
                if not live_vol or not expected_vol:
                    continue
                
                # Check exit conditions
                should_exit, reason = self.position_manager.check_exit_conditions(
                    event_id=event_id,
                    side_index=side_index,
                    current_live_vol=live_vol,
                    current_expected_vol=expected_vol,
                    time_elapsed=time_elapsed,
                    current_prob=event['current_prob'],
                    score_diff=event['score_diff']
                )
                
                if should_exit:
                    # Compute simple PnL based on volatility difference
                    entry_vol_diff = abs(position.initial_live_vol - position.initial_expected_vol)
                    exit_vol_diff = abs(live_vol - expected_vol)
                    pnl = position.size * (entry_vol_diff - exit_vol_diff) / entry_vol_diff
                    
                    if position.position_type == "SELL_VOL":
                        pnl = -pnl  # Reverse for short vol positions
                        
                    self.total_pnl += pnl
                    
                    # Send exit alert
                    alert_msg = self.alert_manager.format_exit_alert(
                        event_id=event_id,
                        league=event['league'],
                        side_index=side_index,
                        position_type=position.position_type,
                        reason=reason,
                        pnl=pnl,
                        total_pnl=self.total_pnl,
                        live_vol=live_vol,
                        expected_vol=expected_vol,
                        score_diff=event['score_diff'],
                        current_prob=event['current_prob'],
                        game_clock=event['game_clock'],
                        home_team=event.get('home_team'),
                        away_team=event.get('away_team'),
                        home_price=event.get('home_price'),
                        away_price=event.get('away_price')
                    )
                    self.alert_manager.send_alert(alert_msg)
                    
                    # Close position
                    self.position_manager.close_position(event_id, side_index, reason)
                    
                    logging.info(
                        f"Closed position: event={event_id} side={side_index} "
                        f"reason={reason} pnl=${pnl:.2f} total_pnl=${self.total_pnl:.2f}"
                    )
    
    def run(self, interval: int = 60):
        """Main agent loop."""
        logging.info(f"Starting VolatilityAgent with ${self.initial_capital:.2f} per trade")
        
        while True:
            try:
                # Get active events
                events = self.get_active_events()
                if not events:
                    continue
                    
                # Check exits first
                self.check_exits(events)
                
                # Look for entries
                for event in events:
                    for side_index in [0, 1]:
                        # Skip if we already have a position
                        if self.position_manager.has_position(event['event_id'], side_index):
                            continue
                            
                        # Analyze for potential entry
                        analysis = self.analyze_position(
                            event_id=event['event_id'],
                            league=event['league'],
                            side_index=side_index,
                            game_clock=event['game_clock'],
                            score_diff=event['score_diff'],
                            current_prob=event['current_prob'],
                            pregame_spread=event['spread_points'],
                            pregame_vol=event['pregame_vol']
                        )
                        
                        if analysis:
                            self.execute_trade(
                                event_id=event['event_id'],
                                league=event['league'],
                                side_index=side_index,
                                direction=analysis['direction'],
                                confidence=analysis['confidence'],
                                live_vol=analysis['live_vol'],
                                expected_vol=analysis['expected_vol'],
                                score_diff=event['score_diff'],
                                current_prob=event['current_prob']
                            )
                
            except Exception as e:
                logging.error(f"Error in agent loop: {str(e)}")
                
            time.sleep(interval)

if __name__ == "__main__":
    agent = VolatilityAgent(initial_capital=1000.0)
    agent.run() 