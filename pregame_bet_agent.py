# File: pregame_bet_agent.py

import os
import psycopg2
import logging
from datetime import datetime
from scipy.stats import norm
from dotenv import load_dotenv
import sys
from unabated_api import get_live_market_data, get_live_market_data_by_team

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Use DATABASE_URL or POSTGRES_URL from your .env file
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
if not DATABASE_URL:
    raise ValueError("Postgres connection string not found in environment variables. Please set DATABASE_URL or POSTGRES_URL in your .env file.")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def compute_pregame_iv(pregame_spread, sportstensor_prob):
    """
    Compute pregame implied volatility using the formula:
      σ_IV = |mu| / |Φ⁻¹(p)|
    where p is the pregame (model) win probability.
    """
    if sportstensor_prob <= 0 or sportstensor_prob >= 1:
        return None
    z = abs(norm.ppf(float(sportstensor_prob)))
    if z < 1e-6:
        return None
    return abs(float(pregame_spread)) / z

def prompt_for_game_info():
    """Prompt user for all necessary game information."""
    info = {}
    
    # Get teams
    info['home_team'] = input("Enter home team name: ").strip()
    info['away_team'] = input("Enter away team name: ").strip()
    info['bet_team'] = input("Enter team you bet on: ").strip()
    
    # Get spread
    while True:
        try:
            spread = float(input("Enter the spread (positive for underdog, negative for favorite): "))
            info['spread'] = spread
            break
        except ValueError:
            print("Please enter a valid number.")
    
    # Get moneyline
    while True:
        try:
            ml = input("Enter the moneyline (e.g. +150 or -110): ")
            if ml.startswith('+') or ml.startswith('-'):
                info['moneyline'] = int(ml)
                break
            print("Moneyline must start with + or -")
        except ValueError:
            print("Please enter a valid moneyline.")
    
    # Get total
    while True:
        try:
            total = float(input("Enter the game total (over/under): "))
            if total > 0:
                info['total'] = total
                break
            print("Total must be positive.")
        except ValueError:
            print("Please enter a valid number.")
    
    # Get side of total (if applicable)
    info['total_side'] = input("Did you bet Over or Under? (O/U): ").upper()
    if info['total_side'] not in ['O', 'U']:
        info['total_side'] = None
    
    return info

def prompt_for_prob():
    """Prompt user for probability value."""
    while True:
        try:
            prob = float(input("Enter the probability (between 0 and 1): "))
            if 0 < prob < 1:
                return prob
            print("Probability must be between 0 and 1.")
        except ValueError:
            print("Please enter a valid number.")

def get_game_info_from_api(condition_id, outcome):
    """Try to get game information from Unabated API first."""
    try:
        logging.info(f"Attempting to fetch game info from API for condition {condition_id}")
        
        # Try different event ID formats
        event_ids = []
        
        # Original hex string
        if isinstance(condition_id, str):
            event_ids.append(condition_id)
            
            # Remove 0x prefix if present
            if condition_id.startswith('0x'):
                clean_hex = condition_id[2:]
                event_ids.append(clean_hex)
                
                # Try converting to decimal
                try:
                    decimal_id = str(int(clean_hex, 16))
                    event_ids.append(decimal_id)
                except ValueError:
                    pass
                    
                # Try last 16 chars of hex (in case it's too long)
                event_ids.append(clean_hex[-16:])
                
        logging.info(f"Will try event IDs: {event_ids}")
        
        # Try each event ID format
        for event_id in event_ids:
            logging.info(f"Trying event_id: {event_id}")
            market_data = get_live_market_data(event_id)
            if market_data:
                logging.info(f"Found market data with event_id: {event_id}")
                return market_data
                
        # If no match found by ID, try finding by team name
        if outcome and 'team' in outcome:
            logging.info(f"Trying to find event by team name: {outcome['team']}")
            market_data = get_live_market_data_by_team(outcome['team'])
            if market_data:
                logging.info(f"Found market data by team name")
                return market_data
            
        logging.warning(f"No market data found for any event ID format or team name")
        return None
            
    except Exception as e:
        logging.error(f"Error getting game info from API: {e}")
        return None

def run_pregame_bet_agent(interactive=True):
    """
    Reads active bet data from billysbetdata, computes baseline implied volatility,
    and returns a list of bet dictionaries.
    """
    bets = []
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # First check if the table exists and has any data
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.tables 
                    WHERE table_name = 'billysbetdata'
                )
            """)
            table_exists = cur.fetchone()[0]
            
            if not table_exists:
                logging.error("Table 'billysbetdata' does not exist!")
                return []

            # Check total number of bets
            cur.execute("SELECT COUNT(*) FROM billysbetdata")
            total_bets = cur.fetchone()[0]
            logging.info(f"Total bets in database: {total_bets}")
            
            # Modified query to show all bets, with their status
            query = """
            SELECT id, condition_id, outcome, price, amount, num_shares, 
                   sportstensor_prob, start_time, executed, redemption_status
            FROM billysbetdata
            """
            cur.execute(query)
            all_rows = cur.fetchall()
            logging.info(f"Found {len(all_rows)} total bets")
            
            # Now get just the executed but unredeemed bets (active positions)
            query = """
            SELECT id, condition_id, outcome, price, amount, num_shares, 
                   sportstensor_prob, start_time
            FROM billysbetdata
            WHERE executed = true
              AND redemption_status = false
            """
            cur.execute(query)
            rows = cur.fetchall()
            logging.info(f"Found {len(rows)} active positions")

            for row in rows:
                (bet_id, condition_id, outcome, price, amount, num_shares, sportstensor_prob, start_time) = row
                
                # Use price instead of model probability for actual position
                entry_prob = float(price)  # Assuming price is in probability format (0-1)
                logging.info(f"Using entry price {entry_prob:.3f} for bet {bet_id}")
                
                # Try to get game info from API first
                game_info = get_game_info_from_api(condition_id, outcome)
                
                # If API data not available and in interactive mode, prompt for info
                if game_info is None:
                    if interactive:
                        logging.info("API data not available, prompting for manual input")
                        print(f"\nProcessing bet {bet_id} for {outcome}")
                        game_info = prompt_for_game_info()
                    else:
                        # Non-interactive mode: use defaults
                        logging.warning("API data not available and non-interactive mode, using defaults")
                        game_info = {
                            'spread': 11.5,
                            'moneyline': None,
                            'total': None,
                            'home_team': 'HOME',
                            'away_team': 'AWAY',
                            'bet_team': outcome,
                            'total_side': None
                        }
                
                # Use absolute value of spread for IV calculation
                pregame_iv = compute_pregame_iv(abs(game_info['spread']), float(entry_prob))
                
                bet = {
                    "id": bet_id,
                    "condition_id": condition_id,
                    "outcome": outcome,
                    "price": float(price),
                    "amount": float(amount),
                    "num_shares": float(num_shares),
                    "sportstensor_prob": float(sportstensor_prob),
                    "start_time": start_time,
                    "entry_price": entry_prob,
                    "pregame_iv": pregame_iv,
                    "model_prob": float(sportstensor_prob),
                    # Add all game info
                    "home_team": game_info['home_team'],
                    "away_team": game_info['away_team'],
                    "bet_team": game_info['bet_team'],
                    "spread": game_info['spread'],
                    "moneyline": game_info['moneyline'],
                    "total": game_info['total'],
                    "total_side": game_info['total_side']
                }
                bets.append(bet)
                
                # Log the collected information
                logging.info(f"Collected bet info for {bet_id}:")
                logging.info(f"Teams: {game_info['away_team']} @ {game_info['home_team']}")
                logging.info(f"Bet on: {game_info['bet_team']}")
                logging.info(f"Spread: {game_info['spread']}")
                logging.info(f"Moneyline: {game_info['moneyline']}")
                logging.info(f"Total: {game_info['total']} ({game_info['total_side']})")
                
        conn.commit()
    except Exception as e:
        logging.error(f"Error in pregame bet agent: {e}")
        raise
    finally:
        conn.close()
    return bets

def init_database():
    """Initialize database with required tables."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Create game_odds table with proper structure
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS game_odds (
                id SERIAL PRIMARY KEY,
                event_id TEXT NOT NULL,
                timestamp_utc TIMESTAMP WITH TIME ZONE NOT NULL,
                bet_type INTEGER NOT NULL,  -- 1=moneyline, 2=spread, 3=total
                side_index INTEGER NOT NULL,  -- 0=away/over, 1=home/under
                sportsbook_id INTEGER NOT NULL,
                points DECIMAL(5,1),  -- spread or total points
                american_odds INTEGER NOT NULL,  -- American odds format
                game_clock TEXT,
                status_id INTEGER,  -- 1=pregame, 2=live, 3=final
                UNIQUE(event_id, timestamp_utc, bet_type, side_index, sportsbook_id)
            )
        """)
        
        # Create teams reference table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                league TEXT NOT NULL,
                UNIQUE(name, league)
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

if __name__ == "__main__":
    interactive = "--non-interactive" not in sys.argv
    bets = run_pregame_bet_agent(interactive=interactive)
    for bet in bets:
        print("\nBet Information:")
        print(f"ID: {bet['id']}")
        print(f"Teams: {bet['away_team']} @ {bet['home_team']}")
        print(f"Bet on: {bet['bet_team']}")
        print(f"Spread: {bet['spread']}")
        print(f"Moneyline: {bet['moneyline']}")
        print(f"Total: {bet['total']} ({bet['total_side']})")
        print(f"Pregame IV: {bet['pregame_iv']}")
        print(f"Model Probability: {bet['model_prob']}")

