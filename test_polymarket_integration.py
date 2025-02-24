#!/usr/bin/env python3
"""
Test script to verify Polymarket integration and pregame data retrieval.
"""

import os
import logging
import psycopg2
from dotenv import load_dotenv
from polymarket_api import get_polymarket_data, calculate_live_iv, calculate_expected_iv
import requests
from datetime import datetime
from team_mapping import get_team_abbr

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Load environment variables
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")

def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(DATABASE_URL)

def test_polymarket_data_retrieval():
    """Test retrieving data from Polymarket API."""
    # Test with a known slug
    slug = "nba-bkn-was-2025-02-24"
    logging.info(f"Testing Polymarket data retrieval for slug: {slug}")
    
    market_data = get_polymarket_data(slug)
    if not market_data:
        logging.error(f"Failed to retrieve data for {slug}")
        return
    
    logging.info("Successfully retrieved Polymarket data:")
    logging.info(f"Event ID: {market_data['event_id']}")
    logging.info(f"Teams: {market_data['away_team']} @ {market_data['home_team']}")
    logging.info(f"Prices: Away={market_data['away_price']:.3f}, Home={market_data['home_price']:.3f}")
    logging.info(f"Score Differential: {market_data['score_diff']}")
    logging.info(f"Game Time: {market_data['game_time']:.2f}")
    
    # Test volatility calculations
    pregame_spread = 11.5  # Example value
    pregame_prob = 0.1746  # Example value
    
    # Calculate pregame IV
    from scipy.stats import norm
    pregame_iv = abs(pregame_spread) / abs(norm.ppf(pregame_prob))
    logging.info(f"Pregame IV: {pregame_iv:.2f}")
    
    # Calculate live IV
    score_diff = market_data['score_diff'] or 0
    game_time = market_data['game_time']
    away_price = market_data['away_price']
    
    live_iv = calculate_live_iv(score_diff, pregame_spread, game_time, away_price)
    expected_iv = calculate_expected_iv(pregame_iv, game_time)
    
    logging.info(f"Live IV: {live_iv:.2f}")
    logging.info(f"Expected IV: {expected_iv:.2f}")
    
    return market_data

def test_with_active_positions():
    """Test with active positions from database."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Get active positions
            query = """
            SELECT id, condition_id, outcome, price, amount, num_shares, 
                   sportstensor_prob, start_time
            FROM billysbetdata
            WHERE executed = true
              AND redemption_status = false
            LIMIT 5
            """
            cur.execute(query)
            active_positions = cur.fetchall()
            
            if not active_positions:
                logging.warning("No active positions found in database")
                return
            
            logging.info(f"Found {len(active_positions)} active positions")
            
            for position in active_positions:
                (bet_id, condition_id, outcome, entry_price, amount, 
                 num_shares, model_prob, start_time) = position
                
                logging.info(f"\nTesting position {bet_id}:")
                logging.info(f"Condition ID: {condition_id}")
                logging.info(f"Team: {outcome}")
                logging.info(f"Entry price: {entry_price:.3f}")
                
                # For testing, use a hardcoded slug
                slug = "nba-bkn-was-2025-02-24"
                market_data = get_polymarket_data(slug)
                
                if not market_data:
                    logging.warning(f"No market data found for position {bet_id}")
                    continue
                
                # Determine if team is home or away
                is_home = outcome.lower() in market_data["home_team"].lower()
                is_away = outcome.lower() in market_data["away_team"].lower()
                
                if not (is_home or is_away):
                    logging.warning(f"Team {outcome} not found in market data")
                    continue
                
                # Get current price
                current_price = market_data["home_price"] if is_home else market_data["away_price"]
                logging.info(f"Current price: {current_price:.3f}")
                
                # Calculate PnL
                pnl = (current_price - entry_price) * num_shares
                pnl_percentage = (current_price - entry_price) / entry_price * 100
                logging.info(f"PnL: ${pnl:.2f} ({pnl_percentage:+.1f}%)")
                
                # Get pregame spread from database or use default
                # In a real implementation, you would fetch this from your database
                pregame_spread = 11.5  # Default value
                
                # Calculate pregame IV
                from scipy.stats import norm
                pregame_iv = abs(pregame_spread) / abs(norm.ppf(float(model_prob)))
                logging.info(f"Pregame IV: {pregame_iv:.2f}")
                
                # Calculate live IV
                score_diff = market_data['score_diff'] or 0
                if is_away:
                    score_diff = -score_diff
                
                game_time = market_data['game_time']
                
                live_iv = calculate_live_iv(score_diff, pregame_spread, game_time, current_price)
                expected_iv = calculate_expected_iv(pregame_iv, game_time)
                
                logging.info(f"Live IV: {live_iv:.2f}")
                logging.info(f"Expected IV: {expected_iv:.2f}")
                
                # Check if sell signal would be generated
                if pnl > 0 and live_iv < expected_iv:
                    sell_fraction = min(1.0, (pregame_iv - live_iv) / pregame_iv)
                    suggested_shares = num_shares * sell_fraction
                    max_shares_to_recover = amount / current_price
                    suggested_shares = min(suggested_shares, max_shares_to_recover)
                    
                    logging.info(f"SELL SIGNAL: Sell {suggested_shares:.0f} shares at {current_price:.3f}")
                else:
                    logging.info("No sell signal would be generated")
    
    except Exception as e:
        logging.error(f"Error testing with active positions: {e}")
    finally:
        conn.close()

def test_with_specific_team(team_name):
    """Test finding and retrieving data for a specific team."""
    logging.info(f"Testing search for team: {team_name}")
    
    # Get team abbreviation
    team_abbr = get_team_abbr(team_name)
    
    if not team_abbr:
        logging.warning(f"Could not find abbreviation for {team_name}")
        return None
    
    # Search for games with this team
    today = datetime.now().strftime("%Y-%m-%d")
    search_url = f"https://gamma-api.polymarket.com/events?tag=nba&search={team_abbr}"
    
    try:
        logging.info(f"Searching for games with team {team_name} ({team_abbr})")
        response = requests.get(search_url)
        response.raise_for_status()
        events = response.json()
        
        if not events:
            logging.warning(f"No games found for {team_name}")
            return None
        
        # Use the first event found
        event = events[0]
        slug = event.get("slug")
        logging.info(f"Found game with slug: {slug}")
        
        # Get market data
        market_data = get_polymarket_data(slug)
        if not market_data:
            logging.warning(f"Could not get market data for {slug}")
            return None
        
        logging.info(f"Successfully retrieved data for {team_name}:")
        logging.info(f"Event: {market_data['home_team']} vs {market_data['away_team']}")
        logging.info(f"Prices: Home={market_data['home_price']:.3f}, Away={market_data['away_price']:.3f}")
        
        return market_data
    except Exception as e:
        logging.error(f"Error searching for games: {e}")
        return None

if __name__ == "__main__":
    logging.info("Starting Polymarket integration test")
    
    # Test basic data retrieval
    market_data = test_polymarket_data_retrieval()
    
    # Test with active positions
    if market_data:
        test_with_active_positions()
    
    # Test with specific team
    team_name = "Jazz"
    team_data = test_with_specific_team(team_name)
    if team_data:
        logging.info(f"Team data for {team_name}:")
        logging.info(f"Event: {team_data['home_team']} vs {team_data['away_team']}")
        logging.info(f"Prices: Home={team_data['home_price']:.3f}, Away={team_data['away_price']:.3f}")
    
    logging.info("Test completed") 