import requests
import time
import os
from datetime import datetime
import logging
import json
import sqlite3
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# -------------- CONFIG --------------
API_KEY = os.getenv("UNABATED_API_KEY")  # From .env
BASE_URL = "https://partner-api.unabated.com/api"
DB_FILE = "unabated_odds.db"

def get_db_connection():
    """Get a database connection."""
    return sqlite3.connect(DB_FILE)

def reset_database():
    """Reset database by deleting and recreating it."""
    try:
        # Delete existing database
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)
            logging.info(f"Deleted existing database: {DB_FILE}")
            
        # Initialize fresh database
        init_database()
        logging.info("Database initialized with fresh schema")
        
    except Exception as e:
        logging.error(f"Error resetting database: {e}")
        raise

# Reference data
TRACKED_LEAGUES = {1: "NFL", 3: "NBA", 4: "CBB"}
TRACKED_BET_TYPES = {1: "Moneyline", 2: "Spread", 3: "Total"}

def init_database():
    """Initialize SQLite database with required tables."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Create game_odds table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_odds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL,
                bet_type TEXT NOT NULL,  -- 1=moneyline, 2=spread, 3=total
                side_index INTEGER NOT NULL,  -- 0=away/over, 1=home/under
                source_format INTEGER NOT NULL,  -- 1=American, 2=Decimal, 3=Percent, 4=Probability, 5=Sporttrade
                points REAL,  -- spread or total points
                price REAL NOT NULL,  -- Price in source format
                game_clock TEXT,  -- Format: "12:00 1H", "7:23 4Q" etc
                score_difference INTEGER,  -- Positive = home leading
                UNIQUE(event_id, timestamp_utc, bet_type, side_index)
            )
        """)
        
        # Create pregame_volatilities table for storing computed vols
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pregame_volatilities (
                event_id TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL,
                home_spread REAL NOT NULL,
                home_prob REAL NOT NULL,
                away_prob REAL NOT NULL,
                home_implied_vol REAL,
                away_implied_vol REAL,
                PRIMARY KEY (event_id, timestamp_utc)
            )
        """)
        
        conn.commit()
        logging.info("Database schema initialized")
        
    except Exception as e:
        logging.error(f"Error initializing database: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

MARKET_SOURCES = {
    1: "Pinnacle",
    2: "FanDuel",
    4: "DraftKings",
    6: "BetMGM",
    7: "Caesars",
    8: "WynnBet",
    17: "Bet365",
    20: "BetRivers",
    22: "Barstool",
    24: "PointsBet",
    25: "SuperBook",
    27: "TwinSpires",
    36: "Circa",
    49: "BetUS",
    60: "BetOnline",
    66: "Heritage",
    67: "Bovada",
    69: "BetAnySports",
    77: "Bookmaker",
    78: "BetCRIS",
    86: "SBK",
    89: "SI Sportsbook",
    92: "Unibet"
}

def parse_game_odds_events(data):
    """Parse game odds events from Unabated API response."""
    events = []
    
    # Get the gameOddsEvents section which contains all leagues/periods
    # Format: lg{league-id}:pt{period-type-id}:{pregame/live}
    game_odds_events = data.get('results', [{}])[0].get('gameOdds', {}).get('gameOddsEvents', {})
    
    for league_period_key, events_list in game_odds_events.items():
        for event in events_list:
            event_id = event.get('eventId')
            event_start = event.get('eventStart')
            status_id = event.get('statusId')
            game_clock = event.get('gameClock')
            
            # Only process if we have valid event data
            if not event_id:
                continue
                
            # Process each market source line
            # Format: si{side-index}:ms{market-source-id}:an{alternate-line-index}
            market_lines = event.get('gameOddsMarketSourcesLines', {})
            for market_key, bet_types in market_lines.items():
                # Parse market key components
                key_parts = market_key.split(':')
                if len(key_parts) != 3:
                    continue
                    
                side_index = key_parts[0].replace('si', '')  # 0=away/over, 1=home/under
                market_source = key_parts[1].replace('ms', '')
                
                # Process each bet type (bt1=moneyline, bt2=spread, bt3=total)
                for bet_type, line in bet_types.items():
                    # Only process spread (bt2) for now
                    if bet_type != 'bt2':
                        continue
                        
                    points = line.get('points')
                    price = line.get('sourcePrice')
                    source_format = line.get('sourceFormat')
                    modified_on = line.get('modifiedOn')
                    
                    if None in [points, price, source_format]:
                        continue
                        
                    events.append({
                        'event_id': str(event_id),
                        'timestamp_utc': modified_on or event_start,
                        'bet_type': bet_type.replace('bt', ''),
                        'side_index': int(side_index),
                        'source_format': source_format,
                        'points': float(points) if points is not None else None,
                        'price': float(price),
                        'game_clock': game_clock,
                        'score_difference': None
                    })
    
    return events

def store_game_odds(events):
    """Store game odds in the database."""
    if not events:
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Use INSERT OR REPLACE to handle updates
        cursor.executemany("""
            INSERT OR REPLACE INTO game_odds (
                event_id, timestamp_utc, bet_type, side_index, 
                source_format, points, price, game_clock, score_difference
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                e['event_id'], e['timestamp_utc'], e['bet_type'], 
                e['side_index'], e['source_format'], e['points'],
                e['price'], e['game_clock'], e['score_difference']
            ) for e in events
        ])
        
        conn.commit()
        print(f"Stored {len(events)} events in database")
        
    except Exception as e:
        print(f"Error storing events: {e}")
        conn.rollback()
    finally:
        conn.close()

def compute_pregame_implied_vol() -> None:
    """Compute pregame implied volatility for each event."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get latest odds for each event
        cursor.execute("""
            WITH latest_odds AS (
                SELECT 
                    event_id,
                    points as spread,
                    price,
                    source_format,
                    ROW_NUMBER() OVER (
                        PARTITION BY event_id 
                        ORDER BY timestamp_utc DESC
                    ) as rn
                FROM game_odds
                WHERE game_clock IS NULL  -- pregame only
                AND bet_type = '2'  -- spread only
            )
            SELECT 
                event_id,
                spread,
                price,
                source_format
            FROM latest_odds
            WHERE rn = 1
        """)
        
        events = cursor.fetchall()
        for event in events:
            event_id, spread, price, source_format = event
            
            # Convert odds to probability
            prob = get_odds_probability(price, source_format)
            
            # Compute implied vol
            if spread is not None and prob is not None:
                home_vol = compute_implied_vol(abs(spread), prob)
                away_vol = compute_implied_vol(abs(spread), 1 - prob)
                
                if home_vol and away_vol:
                    try:
                        cursor.execute("""
                            INSERT INTO pregame_volatilities (
                                event_id,
                                timestamp_utc,
                                home_spread,
                                home_prob,
                                away_prob,
                                home_implied_vol,
                                away_implied_vol
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            event_id,
                            datetime.utcnow().isoformat(),
                            spread,
                            prob,
                            1 - prob,
                            home_vol,
                            away_vol
                        ))
                    except Exception as e:
                        logging.error(f"Error storing implied vol: {str(e)}")
                        continue
        
        conn.commit()
        
    except Exception as e:
        logging.error(f"Error computing implied vol: {e}")
        conn.rollback()
    finally:
        conn.close()

def get_odds_probability(price: float, source_format: int) -> float:
    """
    Convert odds to probability based on source format.
    Following Unabated API source formats:
    1 = American
    2 = Decimal
    3 = Percent
    4 = Probability
    5 = Sporttrade (0 to 100)
    """
    try:
        if source_format == 1:  # American
            if price > 0:
                return 100.0 / (100.0 + price)
            else:
                return abs(price) / (abs(price) + 100.0)
        elif source_format == 2:  # Decimal
            return 1.0 / price
        elif source_format == 3:  # Percent
            return price / 100.0
        elif source_format == 4:  # Probability
            return price
        elif source_format == 5:  # Sporttrade
            return price / 100.0
        else:
            logging.warning(f"Unknown source format: {source_format}")
            return None
    except Exception as e:
        logging.error(f"Error converting odds to probability: {e}")
        return None

def compute_implied_vol(spread: float, prob: float) -> float:
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
        logging.error(f"Error computing implied vol: {e}")
        return None

def fetch_snapshot():
    url = f"{BASE_URL}/markets/gameOdds"
    headers = {"x-api-key": API_KEY}
    
    try:
        logging.info("Fetching snapshot...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        logging.info("Snapshot fetched successfully.")
        logging.info(f"Response data structure: {json.dumps(data, indent=2)}")
        return data
    except Exception as e:
        logging.error(f"Error fetching snapshot: {e}")
        return None

def fetch_changes(last_sequence=None):
    url = f"{BASE_URL}/markets/changes"
    if last_sequence:
        url += f"?sequence={last_sequence}"
    
    headers = {"x-api-key": API_KEY}
    logging.info(f"Fetching changes from {url}")
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        logging.info(f"Changes data structure: {json.dumps(data, indent=2)}")
        return data
    except Exception as e:
        logging.error(f"Error fetching changes: {e}")
        return None

def merge_changes_into_snapshot(snapshot_data, changes_data):
    """Extract newly changed odds from the 'changes' JSON."""
    merged_rows = []
    for result in changes_data.get("results", []):
        # if it has "gameOdds" -> "gameOddsEvents"
        if "gameOdds" in result and "gameOddsEvents" in result["gameOdds"]:
            partial = parse_game_odds_events(result["gameOdds"]["gameOddsEvents"])
            merged_rows.extend(partial)
    return merged_rows

def fetch_and_store_odds():
    """Fetch odds from Unabated API and store in database."""
    try:
        # Get API key from environment
        api_key = os.getenv('UNABATED_API_KEY')
        if not api_key:
            raise ValueError("UNABATED_API_KEY environment variable not set")

        # Fetch data from Unabated API
        url = "https://partner-api.unabated.com/api/markets/gameOdds"
        headers = {"x-api-key": api_key}
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        
        # Parse and store events
        events = parse_game_odds_events(data)
        store_game_odds(events)
        
        # Return timestamp for changes endpoint
        return data.get('lastTimestamp')
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching odds: {e}")
    except Exception as e:
        print(f"Error processing odds: {e}")
    
    return None

def fetch_changes(last_timestamp):
    """Fetch changes since last timestamp."""
    try:
        api_key = os.getenv('UNABATED_API_KEY')
        if not api_key:
            raise ValueError("UNABATED_API_KEY environment variable not set")
            
        url = f"https://partner-api.unabated.com/api/markets/changes"
        if last_timestamp:
            url += f"/{last_timestamp}"
            
        headers = {"x-api-key": api_key}
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        
        # Check result code
        if data.get('resultCode') == 'Failed':
            print("Changes request failed - need to fetch full snapshot")
            return None
            
        # Parse and store events
        events = parse_game_odds_events(data)
        store_game_odds(events)
        
        return data.get('lastTimestamp')
        
    except Exception as e:
        print(f"Error fetching changes: {e}")
        return None

def run():
    """Main run loop to continuously fetch odds."""
    # Reset database on startup
    reset_database()
    
    # First get initial snapshot
    last_timestamp = fetch_and_store_odds()
    
    while True:
        try:
            # Get changes since last timestamp
            new_timestamp = fetch_changes(last_timestamp)
            
            # If changes request failed, get new snapshot
            if new_timestamp is None:
                last_timestamp = fetch_and_store_odds()
            else:
                last_timestamp = new_timestamp
                
            # Wait 1 second before next request
            time.sleep(1)
            
        except Exception as e:
            print(f"Error in run loop: {e}")
            time.sleep(5)  # Wait longer on error
            
if __name__ == '__main__':
    run() 