import requests
import json
import logging
import re
from datetime import datetime
from typing import Dict, Optional, List, Tuple, Any
from team_mapping import get_team_abbr, generate_polymarket_slug, find_team_by_partial_name

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Mapping from condition IDs to Polymarket slugs
# This would be populated with your actual mappings
CONDITION_ID_TO_SLUG = {
    # Example mapping based on your sample data
    "0xc78f33a01f718dd593187e9e93f35acd2519f3bf2c205d9658c52f66fc33a743": "nba-bkn-was-2025-02-24",
    # Add more mappings as needed
}

def fetch_event_data(slug: str) -> List[Dict]:
    """Fetch event data from Polymarket API by slug."""
    try:
        event_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        response = requests.get(event_url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.error(f"Error fetching event data: {e}")
        return []

def fetch_clob_data(token_id: str) -> Dict:
    """Fetch order book data from Polymarket CLOB API."""
    try:
        url = f"https://clob.polymarket.com/book?token_id={token_id}"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.error(f"Error fetching CLOB data: {e}")
        return {}

def extract_score_differential(description: str) -> Optional[int]:
    """
    Extract score differential from market description.
    Returns positive value if home team is leading, negative if away team is leading.
    """
    # Look for patterns like "Score: Home 85 - Away 82" or similar
    score_pattern = r'Score:?\s*(?P<home_team>[A-Za-z\s]+)\s*(?P<home_score>\d+)\s*-\s*(?P<away_team>[A-Za-z\s]+)\s*(?P<away_score>\d+)'
    match = re.search(score_pattern, description)
    
    if match:
        home_score = int(match.group('home_score'))
        away_score = int(match.group('away_score'))
        return home_score - away_score
    
    # Try alternative pattern: "Current score: 85-82"
    alt_pattern = r'Current score:?\s*(?P<home_score>\d+)\s*-\s*(?P<away_score>\d+)'
    match = re.search(alt_pattern, description)
    
    if match:
        home_score = int(match.group('home_score'))
        away_score = int(match.group('away_score'))
        return home_score - away_score
    
    return None

def extract_game_time(description: str) -> Optional[float]:
    """
    Extract normalized game time (0.0 to 1.0) from description.
    Returns None if time cannot be determined.
    """
    # Look for patterns like "Time: 3rd quarter, 5:30 remaining"
    quarter_pattern = r'(?:Time|Quarter):\s*(?P<quarter>\d)(?:st|nd|rd|th)(?:\s*quarter)?,?\s*(?P<minutes>\d+):(?P<seconds>\d+)'
    match = re.search(quarter_pattern, description)
    
    if match:
        quarter = int(match.group('quarter'))
        minutes = int(match.group('minutes'))
        seconds = int(match.group('seconds'))
        
        # Calculate elapsed time (assuming 48 minute NBA game)
        total_game_minutes = 48.0
        minutes_per_quarter = total_game_minutes / 4
        
        elapsed_minutes = (quarter - 1) * minutes_per_quarter + (minutes_per_quarter - minutes - seconds/60)
        return min(1.0, max(0.0, elapsed_minutes / total_game_minutes))
    
    # Look for halftime
    if re.search(r'half[ -]time', description, re.IGNORECASE):
        return 0.5
    
    # Look for end of regulation
    if re.search(r'end of (regulation|4th quarter)', description, re.IGNORECASE):
        return 1.0
    
    return None

def get_polymarket_data(slug: str) -> Optional[Dict]:
    """Get combined event and CLOB data for a specific market."""
    event_data = fetch_event_data(slug)
    if not event_data:
        return None
        
    event = event_data[0]  # Assuming first event is what we want
    
    # Extract market data
    if not event.get('markets'):
        return None
        
    market = event['markets'][0]
    
    # Get token IDs for order book
    try:
        clob_token_ids = json.loads(market.get('clobTokenIds', '[]'))
    except json.JSONDecodeError:
        logging.error(f"Error parsing clobTokenIds for {slug}")
        clob_token_ids = []
    
    clob_results = []
    for token_id in clob_token_ids:
        clob_data = fetch_clob_data(token_id)
        if clob_data:
            clob_results.append(clob_data)
    
    # Extract team names from title
    title = event.get('title', '')
    teams = title.split(' vs. ')
    home_team = teams[1] if len(teams) > 1 else ""
    away_team = teams[0] if len(teams) > 0 else ""
    
    # Extract current prices from outcomePrices or from best bid/ask
    try:
        outcome_prices = json.loads(market.get('outcomePrices', '[0.5, 0.5]'))
        away_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.5
        home_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.5
    except (json.JSONDecodeError, ValueError):
        away_price = 0.5
        home_price = 0.5
    
    # Get best bid/ask from order book
    best_bid = float(market.get('bestBid', 0.5))
    best_ask = float(market.get('bestAsk', 0.5))
    
    # Extract score differential from description
    description = market.get('description', '')
    score_diff = extract_score_differential(description)
    
    # Extract game time information
    game_time = extract_game_time(description)
    if game_time is None:
        # Default to 0.5 if we can't determine the time
        game_time = 0.5
    
    # Get current timestamp
    current_time = datetime.now().isoformat()
    
    return {
        "event_id": market.get('conditionId'),
        "home_team": home_team,
        "away_team": away_team,
        "home_price": home_price,
        "away_price": away_price,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "score_diff": score_diff,
        "game_time": game_time,
        "description": description,
        "current_time": current_time,
        "clob_data": clob_results
    }

def get_polymarket_slug_for_condition(condition_id, team_name=None):
    """
    Get Polymarket slug for a condition ID
    
    Args:
        condition_id: Condition ID from database
        team_name: Optional team name to help with matching
        
    Returns:
        Polymarket slug or None if not found
    """
    # Check if we have a direct mapping
    if condition_id in CONDITION_ID_TO_SLUG:
        return CONDITION_ID_TO_SLUG[condition_id]
    
    # If we have a team name, try to find a matching game today
    if team_name:
        team_abbr = get_team_abbr(team_name)
        if team_abbr:
            # Get today's date
            today = datetime.now().strftime("%Y-%m-%d")
            
            # Try to find games with this team
            try:
                # Search for games with this team
                search_url = f"https://gamma-api.polymarket.com/events?tag=nba&search={team_abbr}"
                response = requests.get(search_url)
                response.raise_for_status()
                events = response.json()
                
                # Find a game today with this team
                for event in events:
                    if event.get("eventDate") == today:
                        # Check if this team is playing
                        markets = event.get("markets", [])
                        if markets and len(markets) > 0:
                            outcomes = markets[0].get("outcomes", "[]")
                            if team_abbr in outcomes or team_name in outcomes:
                                # Found a match, save the mapping
                                slug = event.get("slug")
                                if slug:
                                    CONDITION_ID_TO_SLUG[condition_id] = slug
                                    logging.info(f"Found slug {slug} for condition {condition_id}")
                                    return slug
            except Exception as e:
                logging.error(f"Error searching for games: {e}")
    
    return None

def get_live_market_data_from_polymarket(condition_id, team_name):
    """
    Get live market data from Polymarket API
    
    Args:
        condition_id: Condition ID from database
        team_name: Team name to bet on
        
    Returns:
        Dictionary with market data or None if not found
    """
    # Get slug for this condition
    slug = get_polymarket_slug_for_condition(condition_id, team_name)
    
    if not slug:
        logging.warning(f"No slug found for condition {condition_id}, team {team_name}")
        
        # Try to find a game with this team
        team_abbr = get_team_abbr(team_name)
        if team_abbr:
            today = datetime.now().strftime("%Y-%m-%d")
            search_url = f"https://gamma-api.polymarket.com/events?tag=nba&search={team_abbr}"
            
            try:
                logging.info(f"Searching for games with team {team_name} ({team_abbr})")
                response = requests.get(search_url)
                response.raise_for_status()
                events = response.json()
                
                if events:
                    # Use the first event found
                    slug = events[0].get("slug")
                    logging.info(f"Found game with slug: {slug}")
                else:
                    # Fallback to a test slug
                    logging.warning(f"No games found for {team_name}, using test slug")
                    slug = f"nba-bkn-was-{today}"
            except Exception as e:
                logging.error(f"Error searching for games: {e}")
                slug = f"nba-bkn-was-{today}"
        else:
            # Fallback to a test slug
            slug = f"nba-bkn-was-{today}"
    
    # Get market data
    market_data = get_polymarket_data(slug)
    if not market_data:
        return None
    
    # Determine if team is home or away
    team_abbr = get_team_abbr(team_name)
    home_abbr = get_team_abbr(market_data["home_team"])
    away_abbr = get_team_abbr(market_data["away_team"])
    
    is_home = team_abbr == home_abbr if team_abbr else team_name.lower() in market_data["home_team"].lower()
    is_away = team_abbr == away_abbr if team_abbr else team_name.lower() in market_data["away_team"].lower()
    
    if not (is_home or is_away):
        logging.warning(f"Team {team_name} not found in market data for {slug}")
        
        # If we're using a test slug and the team isn't found, simulate data
        if "bkn-was" in slug:
            logging.info(f"Using simulated data for {team_name}")
            # Simulate as if team is home team
            is_home = True
            market_data["home_team"] = get_team_name(team_abbr) or team_name
    
    # Get current price and score differential
    current_price = market_data["home_price"] if is_home else market_data["away_price"]
    score_diff = market_data["score_diff"] or 0
    if is_away:
        score_diff = -score_diff  # Flip sign for away team
    
    # Get game time
    t = market_data["game_time"]
    
    logging.info(f"Using {market_data['home_team']} vs {market_data['away_team']} for {team_name}")
    logging.info(f"Current price: {current_price:.3f}, Score diff: {score_diff}, Game time: {t:.2f}")
    
    return {
        "event_id": market_data["event_id"],
        "home_team": market_data["home_team"],
        "away_team": market_data["away_team"],
        "current_price": current_price,
        "score_diff": score_diff,
        "game_time": t,
        "live_vol": None,  # Will be calculated by caller
        "expected_vol": None  # Will be calculated by caller
    }

def calculate_live_iv(score_diff: int, pregame_spread: float, 
                      time_elapsed: float, live_prob: float) -> float:
    """
    Calculate live implied volatility using the formula:
    σ_IV,t = |l + μ(1-t)| / (|Φ⁻¹(live_prob)| * sqrt(1-t))
    """
    import math
    from scipy.stats import norm
    
    # Pregame spread (μ)
    mu = pregame_spread
    
    # Time remaining (1-t)
    t_remain = 1.0 - time_elapsed
    
    # Calculate inverse normal CDF
    try:
        z = abs(norm.ppf(live_prob))
    except:
        return 0
    
    # Calculate live IV
    if z < 1e-6 or t_remain <= 0:
        return 0
        
    return abs(score_diff + mu * t_remain) / (z * math.sqrt(t_remain))

def calculate_expected_iv(pregame_iv: float, time_elapsed: float) -> float:
    """
    Calculate expected IV based on pregame IV and time elapsed.
    Expected IV decreases as sqrt(1-t).
    """
    import math
    t_remain = 1.0 - time_elapsed
    return pregame_iv * math.sqrt(t_remain)

def update_condition_id_mapping(condition_id: str, slug: str) -> None:
    """Update the mapping of condition IDs to slugs."""
    CONDITION_ID_TO_SLUG[condition_id] = slug
    logging.info(f"Updated mapping: {condition_id} -> {slug}") 