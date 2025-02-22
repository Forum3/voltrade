import math
from typing import Optional, Tuple, Dict, Union
from scipy.stats import norm
from agent_types import MarketState, LiveMarketState, LEAGUE_PARAMS
import logging
import re
from datetime import datetime

def parse_game_clock(clock_str: str, league: str) -> Optional[float]:
    """
    Parse game clock string to get time elapsed fraction.
    Examples: "12:00 1H", "7:23 4Q", "8:45 2nd"
    Returns fraction of game completed (0.0 to 1.0)
    """
    if not clock_str:
        return None
        
    try:
        # Get total game minutes from league params
        total_minutes = LEAGUE_PARAMS[league]["total_minutes"]
        
        # Extract minutes and seconds
        match = re.search(r'(\d+):(\d+)', clock_str)
        if not match:
            return None
            
        minutes = int(match[1])
        seconds = int(match[2])
        
        # Determine period (quarter, half, etc)
        period = 1
        if '4Q' in clock_str or '4th' in clock_str:
            period = 4
        elif '3Q' in clock_str or '3rd' in clock_str:
            period = 3
        elif '2Q' in clock_str or '2H' in clock_str or '2nd' in clock_str:
            period = 2
            
        # Calculate time elapsed
        if league in ['NBA', 'CBB']:
            # 4 quarters or 2 halves
            period_length = total_minutes / (4 if period <= 4 else 2)
            time_elapsed = (period - 1) * period_length + (period_length - (minutes + seconds/60.0))
        else:
            # Default to simple calculation
            time_elapsed = total_minutes - (minutes + seconds/60.0)
            
        return min(1.0, time_elapsed / total_minutes)
    except:
        return None

def get_probability_from_source(source_price: float, source_format: int) -> Optional[float]:
    """
    Convert source price to probability based on Unabated source format:
    1 = American
    2 = Decimal
    3 = Percent
    4 = Probability
    5 = Sporttrade (0 to 100)
    
    Note: 
    - Unabated consensus line probabilities are already vig-free
    - SportTrade format (5) is required for live betting
    """
    try:
        if source_format == 5:  # SportTrade (required for live)
            return source_price / 100.0 if 0 < source_price < 100 else None
        elif source_format == 4:  # Native probability
            return source_price if 0 < source_price < 1 else None
        elif source_format == 3:  # Percent
            return source_price / 100.0 if 0 < source_price < 100 else None
        elif source_format == 2:  # Decimal
            return 1.0 / source_price if source_price > 1 else None
        elif source_format == 1:  # American
            if source_price > 0:
                return 100.0 / (source_price + 100.0)
            else:
                return abs(source_price) / (abs(source_price) + 100.0)
        return None
    except:
        return None

def compute_pregame_implied_vol(spread: float, moneyline_prob: float) -> Optional[float]:
    """
    Compute pregame implied volatility using Polson-Stern formula:
    σᵢᵥ = |μ/Φ⁻¹(p)|
    where:
    - μ is the point spread (from Unabated consensus line)
    - p is the probability from moneyline (from Unabated consensus line, already vig-free)
    
    Note: This should be called with the Unabated consensus line data
    for both spread and probability. No vig adjustment needed.
    """
    if moneyline_prob <= 0 or moneyline_prob >= 1:
        return None
    try:
        z = norm.ppf(moneyline_prob)
        if abs(z) < 1e-6:
            return None
        return abs(spread / z)  # Take absolute value
    except:
        return None

def compute_live_implied_vol(
    lead: float,
    pregame_spread: float,
    time_elapsed: float,
    current_prob: float,
    side_index: int,
    source_format: Optional[int] = None
) -> Optional[float]:
    """
    Compute time-varying implied volatility using Polson-Stern formula:
    σᵢᵥ,ₜ = |(l + μ(1-t)) / [Φ⁻¹(pₜ,ₗ) * sqrt(1-t)]|
    where:
    - l is current lead (positive for leading, negative for trailing)
    - μ is pregame spread (from initial Unabated consensus line)
    - t is fraction of game completed
    - pₜ,ₗ is current probability (must be from SportTrade for live betting)
    
    Note: For live betting, we require SportTrade probabilities (format 5)
    as they provide direct trading capabilities.
    """
    # Validate source format for live betting
    if source_format != 5:
        logging.error("Live betting requires SportTrade (format 5) for tradeable positions")
        return None
    
    if time_elapsed >= 1.0 or current_prob <= 0 or current_prob >= 1:
        return None
        
    remain = 1.0 - time_elapsed
    if remain <= 0:
        return None
        
    try:
        # Adjust spread sign based on side_index
        if side_index == 1:  # Home/Under side
            pregame_spread = -pregame_spread
            
        z = norm.ppf(current_prob)
        if abs(z) < 1e-6:
            return None
            
        numerator = lead + pregame_spread * remain
        denom = z * math.sqrt(remain)
        if abs(denom) < 1e-9:
            return None
            
        return abs(numerator / denom)  # Take absolute value
    except:
        return None

def compute_expected_vol(
    pregame_vol: float,
    time_elapsed: float,
    league: str
) -> Optional[float]:
    """
    Compute expected volatility at time t:
    σₑ,ₜ = σᵢᵥ * sqrt(1-t)
    """
    if time_elapsed >= 1.0:
        return None
    
    # Get league-specific parameters
    params = LEAGUE_PARAMS.get(league)
    if not params:
        return None
    
    # Simple sqrt decay model
    remain = 1.0 - time_elapsed
    return pregame_vol * math.sqrt(remain)

def compute_vol_deviation(
    current_vol: float,
    expected_vol: float
) -> float:
    """
    Compute volatility deviation as percentage:
    dev = (σᵢᵥ,ₜ - σₑ,ₜ) / σₑ,ₜ * 100
    """
    return ((current_vol - expected_vol) / expected_vol) * 100.0

def check_vol_signal(
    live_vol: float,
    expected_vol: float,
    league: str,
    source_format: Optional[int] = None
) -> Tuple[bool, str, Dict]:
    """
    Check if volatility deviation exceeds threshold.
    Returns (should_trade, direction, metadata).
    
    Note: For live trading signals, we require SportTrade data
    as we need the ability to trade in and out of positions.
    """
    # Validate source format for live betting
    if source_format != 5:
        logging.error("Live trading signals require SportTrade (format 5) for tradeable positions")
        return False, "NO_ACTION", {}
    
    params = LEAGUE_PARAMS.get(league)
    if not params:
        return False, "NO_ACTION", {}
    
    threshold = params["vol_threshold"]
    deviation = compute_vol_deviation(live_vol, expected_vol)
    
    metadata = {
        "deviation": deviation,
        "threshold": threshold,
        "live_vol": live_vol,
        "expected_vol": expected_vol,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if abs(deviation) < threshold:
        return False, "NO_ACTION", metadata
    
    # Align with Polson-Stern paper:
    # SHORT_VOL when market overestimates uncertainty (positive deviation)
    # LONG_VOL when market underestimates uncertainty (negative deviation)
    if deviation > threshold:
        return True, "SELL_VOL", metadata  # Market overestimating uncertainty
    else:
        return True, "BUY_VOL", metadata   # Market underestimating uncertainty

def format_market_data(state: Union[MarketState, LiveMarketState]) -> str:
    """Format market data for LLM consumption."""
    if isinstance(state, MarketState):
        return f"""
PREGAME MARKET STATE (Unabated Consensus Line):
Event: {state.event_id} ({state.league})
Side: {"Away" if state.side_index == 0 else "Home"}
Moneyline Probability: {state.moneyline_prob:.3f}
Spread: {state.spread_points:+.1f}
Total Points: {state.total_points:.1f}
Implied Volatility: {state.implied_vol:.2f}
Raw Odds: {state.odds_data}
"""
    else:  # LiveMarketState
        vol_deviation = ((state.live_vol - state.expected_vol) / state.expected_vol * 100.0) if state.expected_vol else None
        return f"""
LIVE MARKET STATE (SportTrade):
Event: {state.event_id} ({state.league})
Side: {"Away" if state.side_index == 0 else "Home"}
Game Clock: {state.game_clock}
Score Differential: {state.score_diff:+.1f}
Time Elapsed: {state.time_elapsed:.2%}
Current Probability: {state.current_prob:.3f}
Pregame Implied Vol: {state.pregame_vol:.2f}
Live Implied Vol: {state.live_vol:.2f}
Expected Vol: {state.expected_vol:.2f if state.expected_vol else "N/A"}
Vol Deviation: {vol_deviation:.1f}% if vol_deviation else "N/A"
"""

def get_position_size(
    league: str,
    confidence: float,
    vol_diff: float
) -> float:
    """
    Compute position size as % of capital based on:
    1. League-specific multiplier
    2. Signal confidence
    3. Volatility difference magnitude
    """
    params = LEAGUE_PARAMS.get(league, LEAGUE_PARAMS["NBA"])
    
    # Base size is 5% of capital
    base_size = 5.0
    
    # Apply multipliers
    league_mult = params["size_multiplier"]
    conf_mult = min(1.0, confidence)
    vol_mult = min(2.0, abs(vol_diff) / params["vol_threshold"])
    
    size = base_size * league_mult * conf_mult * vol_mult
    
    # Cap at 20% of capital
    return min(20.0, size) 