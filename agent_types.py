from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

@dataclass
class MarketState:
    """Pregame market state for volatility analysis."""
    event_id: int
    league: str
    side_index: int  # 0=away, 1=home
    moneyline_prob: float
    spread_points: float
    total_points: float
    implied_vol: float  # σᵢᵥ from Polson-Stern
    odds_data: str  # Raw odds data for reference

@dataclass
class LiveMarketState:
    """Live market state incorporating Polson-Stern variables."""
    event_id: int
    league: str
    side_index: int  # 0=away, 1=home
    game_clock: str
    score_diff: float  # Current lead/deficit (l)
    time_elapsed: float  # Fraction of game completed (t)
    current_prob: float  # Current ML probability (pₜ,ₗ)
    pregame_vol: float  # Pregame σᵢᵥ
    live_vol: float  # Time-varying σᵢᵥ,ₜ
    expected_vol: Optional[float] = None  # Expected σₑ,ₜ if available

@dataclass
class VolSignal:
    """Trading signal based on volatility analysis."""
    event_id: int
    league: str
    side_index: int
    timestamp: str
    action: str  # BUY_VOL, SELL_VOL, NO_ACTION
    size: float  # 0-100% of capital
    reason: str  # Explanation of signal
    
    # Volatility metrics that generated signal
    pregame_vol: float
    live_vol: Optional[float] = None
    expected_vol: Optional[float] = None
    vol_diff: Optional[float] = None  # |σᵢᵥ,ₜ - σₑ,ₜ|

@dataclass
class TradePosition:
    """Active trade position."""
    event_id: int
    league: str
    side_index: int
    entry_time: str
    position_type: str  # LONG_VOL or SHORT_VOL
    size: float  # % of capital
    entry_score: int
    entry_prob: float
    entry_vol: float
    max_hold_time: float  # minutes
    
    # Updated during position lifecycle
    current_score: Optional[int] = None
    current_prob: Optional[float] = None
    current_vol: Optional[float] = None
    pnl: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: Optional[str] = None

@dataclass
class TradeDecision:
    """LLM-enhanced trading decision."""
    event_id: int
    league: str
    side_index: int
    timestamp: str
    analysis: str  # LLM analysis text
    
    # Optional fields populated by LLM
    action: Optional[str] = None  # BUY_VOL, SELL_VOL, NO_ACTION
    size: Optional[float] = None  # 0-100% of capital
    hold_time: Optional[float] = None  # max minutes to hold
    confidence: Optional[float] = None  # 0-1 confidence score

@dataclass
class ActionPlan:
    """Trading action plan generated by the agent."""
    event_id: int
    side_index: int
    action: str  # BUY_VOL, SELL_VOL
    confidence: float
    size: float
    rationale: str
    volatility_data: Dict[str, Any]
    timestamp: str = datetime.utcnow().isoformat()

@dataclass
class ExecutionResult:
    """Result of executing a trading action."""
    success: bool
    trade_id: Optional[str] = None
    error_message: Optional[str] = None

class AgentMemory:
    """Memory store for agent's past actions and context."""
    def __init__(self):
        self.actions: List[ActionPlan] = []
        self.executions: List[ExecutionResult] = []
        self.max_memory = 100  # Keep last 100 actions
        
    def add_action(self, action: ActionPlan):
        """Add an action to memory."""
        self.actions.append(action)
        if len(self.actions) > self.max_memory:
            self.actions.pop(0)
            
    def add_execution(self, result: ExecutionResult):
        """Add an execution result to memory."""
        self.executions.append(result)
        if len(self.executions) > self.max_memory:
            self.executions.pop(0)
            
    def get_recent_context(self, n: int = 5) -> List[Dict]:
        """Get recent actions and their results for context."""
        return [
            {
                "action": action,
                "result": result
            }
            for action, result in zip(
                self.actions[-n:],
                self.executions[-n:]
            )
        ]

class AgentState:
    """Current state of the agent."""
    def __init__(self):
        self.active_positions: Dict[Tuple[int, int], Dict] = {}  # (event_id, side_index) -> position
        self.position_size: float = 0.1  # Default 10% of capital per trade
        self.min_confidence: float = 0.7  # Minimum confidence threshold
        self.error_count: int = 0
        self.success_count: int = 0
        self.last_update_time: Optional[str] = None
        
    def can_take_new_position(self, event_id: int, side_index: int) -> bool:
        """Check if we can take a new position."""
        return (event_id, side_index) not in self.active_positions
        
    def add_position(self, event_id: int, side_index: int, position: Dict):
        """Add a new position."""
        self.active_positions[(event_id, side_index)] = position
        
    def remove_position(self, event_id: int, side_index: int):
        """Remove a position."""
        self.active_positions.pop((event_id, side_index), None)
        
    def record_error(self):
        """Record an error occurrence."""
        self.error_count += 1
        
    def record_success(self):
        """Record a successful operation."""
        self.success_count += 1

# League-specific parameters
LEAGUE_PARAMS = {
    "NFL": {
        "total_minutes": 60,
        "vol_threshold": 2.0,
        "size_multiplier": 1.0,
        "max_hold_time": 15.0
    },
    "NBA": {
        "total_minutes": 48,
        "vol_threshold": 1.5,
        "size_multiplier": 0.8,
        "max_hold_time": 12.0
    },
    "CBB": {
        "total_minutes": 40,
        "vol_threshold": 1.8,
        "size_multiplier": 0.6,
        "max_hold_time": 10.0
    }
} 