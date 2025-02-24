import requests
import time
import os
from datetime import datetime
import logging
import json
import sqlite3
from typing import Dict, List
from team_mapping import NBA_TEAM_IDS, get_team_abbr, get_team_name

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
LEAGUES = {
    1: "NFL",
    2: "CFB", 
    3: "NBA",
    4: "CBB",
    5: "MLB",
    6: "NHL"
}

BET_TYPES = {
    1: "Moneyline",
    2: "Spread", 
    3: "Total"
}

SIDE_INDEX = {
    0: "away/over",
    1: "home/under"
}

STATUS_ID = {
    1: "Pregame",
    2: "Live",
    3: "Final",
    4: "Delayed",
    5: "Postponed",
    6: "Cancelled"
}

SPORTSBOOKS = {
    1: "DraftKings",
    2: "FanDuel",
    4: "BetMGM",
    6: "Circa",
    7: "Pinnacle",
    8: "Bookmaker",
    # ... add other sportsbooks
}

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
    
    game_odds_events = data.get('results', [{}])[0].get('gameOdds', {}).get('gameOddsEvents', {})
    
    for league_period_key, events_list in game_odds_events.items():
        for event in events_list:
            event_id = event.get('eventId')
            event_start = event.get('eventStart')
            status_id = event.get('statusId')
            game_clock = event.get('gameClock')
            
            if not event_id:
                continue
                
            market_lines = event.get('gameOddsMarketSourcesLines', {})
            for market_key, bet_types in market_lines.items():
                for bet_type, line in bet_types.items():
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
                        'side_index': int(market_key.replace('si','').split(':')[0]),
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

def fetch_snapshot():
    url = f"{BASE_URL}/markets/gameOdds"
    headers = {"x-api-key": API_KEY}
    
    try:
        logging.info("Fetching snapshot...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        logging.info("Snapshot fetched successfully.")
        return data
    except Exception as e:
        logging.error(f"Error fetching snapshot: {e}")
        return None

# ----------------------
# NEW FUNCTION: get_live_market_data
# ----------------------
def get_live_market_data(event_id: str, team_name: str = None) -> dict:
    """
    Get live market data for a specific event from Unabated API.
    
    Args:
        event_id: The event ID to fetch data for
        team_name: Optional team name to help with matching
        
    Returns:
        A dictionary containing market data
    """
    try:
        # Get API key from environment
        api_key = os.getenv("UNABATED_API_KEY")
        if not api_key:
            logging.warning("UNABATED_API_KEY not found in environment variables")
            return None
            
        # Get snapshot of all markets
        url = f"https://partner-api.unabated.com/api/markets/gameOdds?x-api-key={api_key}"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        # Only look at NBA games (league ID 3)
        nba_pregame_key = "lg3:pt1:pregame"
        if nba_pregame_key not in data.get("gameOddsEvents", {}):
            logging.warning(f"No NBA pregame data found in API response")
            return None
            
        nba_events = data["gameOddsEvents"][nba_pregame_key]
        logging.info(f"Found {len(nba_events)} NBA events")
        
        # Try to find the event by ID
        event = None
        for evt in nba_events:
            if str(evt.get("eventId")) == event_id:
                event = evt
                break
                
        # If not found by ID, try alternative formats
        if not event and event_id:
            # Try hex format
            for evt in nba_events:
                if evt.get("eventId") == int(event_id, 16):
                    event = evt
                    break
        
        # If still not found and we have a team name, try to match by team
        if not event and team_name:
            team_abbr = get_team_abbr(team_name)
            
            for evt in nba_events:
                home_team_id = evt.get("homeTeam", {}).get("id")
                away_team_id = evt.get("awayTeam", {}).get("id")
                
                home_abbr = NBA_TEAM_IDS.get(home_team_id)
                away_abbr = NBA_TEAM_IDS.get(away_team_id)
                
                if team_abbr in [home_abbr, away_abbr]:
                    event = evt
                    logging.info(f"Found event by team abbreviation: {team_abbr}")
                    break
                    
                # Try by full name
                home_name = evt.get("homeTeam", {}).get("name", "").lower()
                away_name = evt.get("awayTeam", {}).get("name", "").lower()
                
                if team_name.lower() in [home_name, away_name]:
                    event = evt
                    logging.info(f"Found event by team name: {team_name}")
                    break
                    
        # If still not found, give up
        if not event:
            logging.warning(f"No matching event found for event_id {event_id} or team {team_name}")
            return None
            
        # Extract relevant data
        home_team = event.get("homeTeam", {}).get("name")
        away_team = event.get("awayTeam", {}).get("name")
        
        # Get market lines
        market_lines = event.get("marketLines", [])
        
        # Extract best lines
        best_lines = {
            "moneyline_home": None,
            "moneyline_away": None,
            "spread": None,
            "total": None
        }
        
        for line in market_lines:
            bet_type = line.get("betType")
            side = line.get("side")
            
            if bet_type == 1:  # Moneyline
                if side == 0:  # Away
                    best_lines["moneyline_away"] = line.get("price")
                elif side == 1:  # Home
                    best_lines["moneyline_home"] = line.get("price")
            elif bet_type == 2:  # Spread
                if side == 0:  # Away
                    best_lines["spread"] = line.get("number")
            elif bet_type == 3:  # Total
                best_lines["total"] = line.get("number")
                
        logging.info(f"Found markets: {best_lines}")
        
        return {
            "event_id": event.get("eventId"),
            "game_clock": event.get("gameClock"),
            "home_team": home_team,
            "away_team": away_team,
            **best_lines,
            "current_price": None,  # For sell signal generator
            "live_vol": None,       # For sell signal generator
            "expected_vol": None    # For sell signal generator
        }
        
    except Exception as e:
        logging.error(f"Error fetching live market data: {str(e)}")
        return None

def fetch_changes(last_timestamp):
    """Fetch changes since last timestamp (if needed)."""
    try:
        url = f"{BASE_URL}/markets/changes"
        if last_timestamp:
            url += f"/{last_timestamp}"
        headers = {"x-api-key": API_KEY}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data.get('resultCode') == 'Failed':
            print("Changes request failed - need to fetch full snapshot")
            return None
        events = parse_game_odds_events(data)
        store_game_odds(events)
        return data.get('lastTimestamp')
    except Exception as e:
        print(f"Error fetching changes: {e}")
        return None

def run():
    """Main run loop to continuously fetch odds."""
    reset_database()
    last_timestamp = fetch_snapshot()
    while True:
        new_timestamp = fetch_changes(last_timestamp)
        if new_timestamp is None:
            last_timestamp = fetch_snapshot()
        else:
            last_timestamp = new_timestamp
        time.sleep(1)

def find_event_by_teams(data: dict, team_name: str) -> dict:
    """Try to find event by matching team names."""
    teams_data = data.get('teams', {})
    game_odds_events = data.get('gameOddsEvents', {})
    
    # First find team ID
    team_id = None
    for id, team_info in teams_data.items():
        if team_name.lower() in team_info.get('name', '').lower():
            team_id = id
            break
    
    if not team_id:
        return None
        
    # Then find event with this team
    for league_key, events in game_odds_events.items():
        for event in events:
            event_teams = event.get('eventTeams', {})
            for side in ['0', '1']:
                if str(event_teams.get(side, {}).get('id')) == team_id:
                    return event
                    
    return None

def get_live_market_data_by_team(team_name: str) -> dict:
    """
    Fetch live market data by searching for a team name.
    """
    logging.info(f"Fetching live market data for team: {team_name}")
    data = fetch_snapshot()
    if not data:
        logging.error("Failed to fetch snapshot from API")
        return None
        
    # Find event by team name
    event = find_event_by_teams(data, team_name)
    if not event:
        logging.warning(f"No event found for team {team_name}")
        return None
        
    # Get team data
    teams_data = data.get('teams', {})
    event_teams = event.get('eventTeams', {})
    
    # Get team names from the teams dictionary
    home_team = teams_data.get(str(event_teams.get('1', {}).get('id')), {}).get('name')
    away_team = teams_data.get(str(event_teams.get('0', {}).get('id')), {}).get('name')
    
    logging.info(f"Found event: {away_team} @ {home_team}")
    
    # Get market lines
    market_lines = event.get('gameOddsMarketSourcesLines', {})
    
    # Initialize containers for all markets
    best_lines = {
        'spread': None,
        'total': None,
        'moneyline_home': None,
        'moneyline_away': None
    }
    
    for key_line, line_data in market_lines.items():
        parts = key_line.split(':')
        if len(parts) < 2:
            continue
            
        side = int(parts[0].replace('si', ''))  # 0=away/over, 1=home/under
        sportsbook = int(parts[1].replace('ms', ''))
        
        for bet_type, details in line_data.items():
            bet_type_id = int(bet_type.replace('bt', ''))
            
            if bet_type_id == 1:  # Moneyline
                if side == 1:  # Home
                    best_lines['moneyline_home'] = details.get('americanPrice')
                else:  # Away
                    best_lines['moneyline_away'] = details.get('americanPrice')
            elif bet_type_id == 2:  # Spread
                points = details.get('points')
                if points is not None:
                    # For home team, we want to show the actual spread (negative)
                    points = points if side == 0 else -points
                    best_lines['spread'] = points
            elif bet_type_id == 3:  # Total
                best_lines['total'] = details.get('points')
    
    logging.info(f"Found markets: {best_lines}")
    
    return {
        "event_id": event.get('eventId'),
        "game_clock": event.get('gameClock'),
        "home_team": home_team,
        "away_team": away_team,
        **best_lines,
        "current_price": None,  # For sell signal generator
        "live_vol": None,       # For sell signal generator
        "expected_vol": None    # For sell signal generator
    }

if __name__ == '__main__':
    run()
