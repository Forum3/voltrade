# File: sell_signal_generator.py

import os
import math
import psycopg2
import logging
from datetime import datetime
from scipy.stats import norm
from dotenv import load_dotenv
from unabated_api import get_live_market_data  # Import live market data from unabated_api.py
from polymarket_api import get_live_market_data_from_polymarket

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
if not DATABASE_URL:
    raise ValueError("Postgres connection string not found in environment variables. Please set DATABASE_URL or POSTGRES_URL in your .env file.")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def create_sell_table_if_not_exists():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS billysSellData (
                id SERIAL PRIMARY KEY,
                bet_id INTEGER REFERENCES billysbetdata(id),
                sell_shares NUMERIC(12,6) NOT NULL,
                sell_price DECIMAL(10,8) NOT NULL,
                sell_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
            conn.commit()
    except Exception as e:
        logging.error(f"Error creating sell table: {e}")
        conn.rollback()
    finally:
        conn.close()

def compute_pregame_iv(pregame_spread, sportstensor_prob):
    """
    Compute pregame implied volatility: σ_IV = |mu| / |Φ⁻¹(p)|
    """
    if sportstensor_prob <= 0 or sportstensor_prob >= 1:
        return None
    z = abs(norm.ppf(float(sportstensor_prob)))
    if z < 1e-6:
        return None
    return abs(float(pregame_spread)) / z

def compute_live_iv(l, mu, t, live_prob):
    """
    Compute live (time-varying) implied volatility:
      σ_IV,t = | l + mu*(1-t) | / ( |Φ⁻¹(live_prob)| * sqrt(1-t) )
    """
    if t >= 1 or live_prob <= 0 or live_prob >= 1:
        return None
    remain = 1.0 - t
    z = abs(norm.ppf(float(live_prob)))
    if z < 1e-6 or remain <= 0:
        return None
    return abs(l + mu * remain) / (z * math.sqrt(remain))

def generate_sell_signals(use_polymarket=False):
    """Generate sell signals for active positions."""
    signals = []
    conn = get_db_connection()
    
    try:
        with conn.cursor() as cur:
            # Get active positions
            cur.execute("""
            SELECT id, condition_id, outcome, price, amount, num_shares, 
                   sportstensor_prob, pregame_spread
            FROM billysbetdata
            WHERE executed = true
              AND redemption_status = false
            """)
            active_positions = cur.fetchall()
            
            if not active_positions:
                logging.info("No active positions found")
                return []
                
            logging.info(f"Found {len(active_positions)} active positions")
            
            for position in active_positions:
                try:
                    (bet_id, condition_id, outcome, entry_price, amount, 
                     num_shares, sportstensor_prob, pregame_spread) = position
                    
                    logging.info(f"\nAnalyzing bet {bet_id} for {outcome}")
                    
                    # Calculate pregame IV
                    pregame_iv = compute_pregame_iv(pregame_spread, sportstensor_prob)
                    if not pregame_iv:
                        logging.warning(f"Could not compute pregame IV for bet {bet_id}")
                        continue
                        
                    logging.info(f"Pregame IV: {pregame_iv:.2f}")
                    
                    # Fetch market data
                    try:
                        logging.info(f"Fetching market data for condition {condition_id}...")
                        
                        if use_polymarket:
                            # Use Polymarket API with team name
                            market_data = get_live_market_data_from_polymarket(str(condition_id), outcome)
                        else:
                            # Use Unabated API with team name
                            market_data = get_live_market_data(str(condition_id), outcome)
                        
                        if not market_data:
                            logging.warning(f"No market data available for bet {bet_id}")
                            continue
                            
                        # If using Polymarket, calculate live IV and expected IV
                        if use_polymarket and market_data:
                            score_diff = market_data.get('score_diff', 0)
                            game_time = market_data.get('game_time', 0)
                            current_price = market_data.get('current_price')
                            
                            # Calculate live IV
                            live_vol = compute_live_iv(score_diff, float(pregame_spread), game_time, current_price)
                            
                            # Calculate expected IV
                            expected_vol = pregame_iv * math.sqrt(1 - game_time)
                            
                            # Update market data
                            market_data['live_vol'] = live_vol
                            market_data['expected_vol'] = expected_vol
                            
                        current_price = market_data.get('current_price')
                        logging.info(f"Current market price: {current_price:.3f}")
                        
                        # Calculate PnL
                        pnl = (current_price - entry_price) * num_shares
                        pnl_percentage = (current_price - entry_price) / entry_price * 100
                        logging.info(f"Current PnL: ${pnl:.2f} ({pnl_percentage:+.1f}%)")
                        
                        # Get volatility metrics
                        live_vol = market_data.get('live_vol')
                        expected_vol = market_data.get('expected_vol')
                        logging.info(f"Live vol: {live_vol:.2f}, Expected vol: {expected_vol:.2f}")
                        
                        # Check if position is in profit
                        if pnl <= 0:
                            logging.info("Position not in profit - skipping")
                            continue
                            
                        # Check volatility conditions
                        vol_diff = live_vol - expected_vol
                        vol_ratio = live_vol / expected_vol if expected_vol else float('inf')
                        logging.info(f"Vol difference: {vol_diff:.2f}, Vol ratio: {vol_ratio:.2f}")
                        
                        # Calculate sell fraction based on the formula
                        sell_fraction = min(1.0, (pregame_iv - live_vol) / pregame_iv)
                        
                        # Calculate shares to sell
                        suggested_shares = num_shares * sell_fraction
                        
                        # Cap shares to sell based on initial wager
                        max_shares_to_recover_initial = amount / current_price
                        suggested_shares = min(suggested_shares, max_shares_to_recover_initial)
                        
                        if vol_ratio > 1.3:  # Example threshold
                            logging.info("Volatility conditions met for sell signal")
                            signals.append({
                                "id": bet_id,
                                "condition_id": condition_id,
                                "outcome": outcome,
                                "entry_price": entry_price,
                                "current_price": current_price,
                                "pnl": pnl,
                                "pnl_percentage": pnl_percentage,
                                "live_vol": live_vol,
                                "expected_vol": expected_vol,
                                "suggested_sell_shares": suggested_shares,
                                "suggested_sell_price": current_price
                            })
                        else:
                            logging.info("Volatility conditions not met for sell signal")
                    except Exception as e:
                        logging.error(f"Error analyzing bet {bet_id}: {str(e)}")
                        continue
                except Exception as e:
                    logging.error(f"Error processing position {position}: {str(e)}")
                    continue
                    
            logging.info(f"\nGenerated {len(signals)} sell signals")
            return signals
    except Exception as e:
        logging.error(f"Error in sell signal generation: {str(e)}")
        return []
    finally:
        conn.close()

if __name__ == "__main__":
    create_sell_table_if_not_exists()
    signals = generate_sell_signals()
    for s in signals:
        print(s)

