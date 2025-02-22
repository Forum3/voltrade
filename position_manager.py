from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import logging
from agent_types import LEAGUE_PARAMS

@dataclass
class Position:
    """Active trading position."""
    event_id: int
    league: str
    side_index: int
    entry_time: datetime
    position_type: str  # BUY_VOL or SELL_VOL
    size: float
    initial_deviation: float  # |σᵢᵥ,ₜ - σₑ,ₜ| at entry
    initial_live_vol: float   # σᵢᵥ,ₜ at entry
    initial_expected_vol: float  # σₑ,ₜ at entry
    entry_score_diff: float
    entry_prob: float
    max_hold_time: float  # minutes

class PositionManager:
    """Manages active positions and exit conditions."""
    
    def __init__(self):
        self.active_positions: Dict[Tuple[int, int], Position] = {}  # (event_id, side_index) -> Position
        
    def open_position(
        self,
        event_id: int,
        league: str,
        side_index: int,
        position_type: str,
        size: float,
        live_vol: float,
        expected_vol: float,
        score_diff: float,
        current_prob: float
    ) -> Position:
        """Open a new position and track it."""
        # Get league-specific max hold time
        max_hold_time = LEAGUE_PARAMS[league]["max_hold_time"]
        
        # Create position
        position = Position(
            event_id=event_id,
            league=league,
            side_index=side_index,
            entry_time=datetime.utcnow(),
            position_type=position_type,
            size=size,
            initial_deviation=abs(live_vol - expected_vol),
            initial_live_vol=live_vol,
            initial_expected_vol=expected_vol,
            entry_score_diff=score_diff,
            entry_prob=current_prob,
            max_hold_time=max_hold_time
        )
        
        # Track position
        self.active_positions[(event_id, side_index)] = position
        
        logging.info(f"Opened {position_type} position for event {event_id} side {side_index}")
        return position
    
    def check_exit_conditions(
        self,
        event_id: int,
        side_index: int,
        current_live_vol: float,
        current_expected_vol: float,
        time_elapsed: float,
        current_prob: float,
        score_diff: float
    ) -> Tuple[bool, str]:
        """
        Check if position should be exited based on:
        1. Mean reversion (vol difference shrinks)
        2. Stop loss (vol difference expands)
        3. Time-based exit
        4. Game state exit (large score differential)
        
        Returns (should_exit, reason)
        """
        position = self.active_positions.get((event_id, side_index))
        if not position:
            return False, ""
            
        # Current deviation from expected vol
        current_deviation = abs(current_live_vol - current_expected_vol)
        
        # 1. Mean reversion exit (vol difference shrinks below 30% of initial)
        reversion_threshold = 0.3
        if current_deviation < reversion_threshold * position.initial_deviation:
            return True, "MEAN_REVERSION"
            
        # 2. Stop loss exit (vol difference expands beyond 150% of initial)
        stop_loss_threshold = 1.5
        if current_deviation > stop_loss_threshold * position.initial_deviation:
            return True, "STOP_LOSS"
            
        # 3. Time-based exit
        minutes_held = (datetime.utcnow() - position.entry_time).total_seconds() / 60.0
        if minutes_held >= position.max_hold_time:
            return True, "TIME_BASED"
            
        # 4. Game state exit (if score differential becomes too large)
        score_change = abs(score_diff - position.entry_score_diff)
        if score_change > 14:  # Example threshold for large score change
            return True, "GAME_STATE"
            
        return False, ""
    
    def close_position(
        self,
        event_id: int,
        side_index: int,
        exit_reason: str
    ) -> Optional[Position]:
        """Close a position and remove from tracking."""
        position = self.active_positions.pop((event_id, side_index), None)
        if position:
            logging.info(f"Closed position for event {event_id} side {side_index}: {exit_reason}")
        return position
    
    def get_position(
        self,
        event_id: int,
        side_index: int
    ) -> Optional[Position]:
        """Get active position if it exists."""
        return self.active_positions.get((event_id, side_index))
    
    def has_position(
        self,
        event_id: int,
        side_index: int
    ) -> bool:
        """Check if position exists."""
        return (event_id, side_index) in self.active_positions 