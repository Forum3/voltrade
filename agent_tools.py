import logging
from typing import Dict, Any, Optional
from datetime import datetime
import sqlite3
import requests
import json
import os
from contextlib import contextmanager
from dotenv import load_dotenv
import math
from scipy.stats import norm
from agent_types import MarketState, LiveMarketState, LEAGUE_PARAMS

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database configuration
DB_FILE = "unabated_odds.db"

@contextmanager
def get_db_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_FILE)
    try:
        yield conn
    finally:
        conn.close()

class DataFetchTool:
    """Tool to fetch data from Unabated API and store in local DB."""
    
    def __init__(self):
        self.api_key = os.getenv('UNABATED_API_KEY')
        if not self.api_key:
            raise ValueError("UNABATED_API_KEY not found in .env file")
        
        self.base_url = "https://api.unabated.com/v2"
        self.last_timestamp = None
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make request to Unabated API with proper error handling."""
        if params is None:
            params = {}
        
        # Add API key as query parameter
        params['key'] = self.api_key
        
        try:
            response = requests.get(
                f"{self.base_url}/{endpoint}",
                params=params,
                verify=True
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {str(e)}")
            return None
    
    def run(self, mode: str = "snapshot") -> Dict[str, Any]:
        """
        Fetch data from Unabated API.
        mode: "snapshot" or "changes"
        """
        logger.info(f"[DataFetchTool] Running with mode={mode}")
        
        if mode == "snapshot":
            # Fetch full snapshot
            response = self._make_request("odds/snapshot")
            if not response:
                return {"success": False, "error": "Failed to fetch snapshot"}
            
            # Update last_timestamp for future changes requests
            self.last_timestamp = response.get('lastTimestamp')
            
            return {
                "success": True,
                "data": response,
                "timestamp": self.last_timestamp
            }
            
        elif mode == "changes":
            if not self.last_timestamp:
                logger.warning("No last_timestamp available. Fetching snapshot instead.")
                return self.run(mode="snapshot")
            
            # Fetch changes since last timestamp
            response = self._make_request("odds/changes", {
                "lastTimestamp": self.last_timestamp
            })
            
            if not response:
                return {"success": False, "error": "Failed to fetch changes"}
            
            # Update last_timestamp
            self.last_timestamp = response.get('lastTimestamp')
            
            return {
                "success": True,
                "data": response,
                "timestamp": self.last_timestamp
            }
        
        else:
            return {"success": False, "error": f"Invalid mode: {mode}"}

class VolatilityTool:
    """Tool to compute or retrieve volatility estimates."""
    
    def __init__(self):
        pass
    
    def run(self, event_id: int, side_index: int) -> Dict[str, Any]:
        """
        Compute or retrieve volatility for a specific event and side.
        Returns both historical volatility and current volatility metrics.
        """
        logger.info(f"[VolatilityTool] Computing volatility for event_id={event_id}, side_index={side_index}")
        
        # First, get current game state
        with get_db_connection() as conn:
            cursor = conn.cursor()
            current_state = cursor.execute("""
                SELECT 
                    league,
                    game_clock,
                    away_team_score - home_team_score as score_diff
                FROM game_odds 
                WHERE event_id = ? 
                ORDER BY timestamp_utc DESC 
                LIMIT 1
            """, (event_id,)).fetchone()
            
            if not current_state:
                return {
                    "success": False,
                    "error": f"No data found for event_id {event_id}"
                }
            
            league, game_clock, score_diff = current_state
            
            # Get recent price changes to compute current volatility
            recent_changes = cursor.execute("""
                SELECT american_price
                FROM game_odds
                WHERE event_id = ?
                AND side_index = ?
                AND bet_type_id = 1
                ORDER BY timestamp_utc DESC
                LIMIT 10
            """, (event_id, side_index)).fetchall()
            
            if len(recent_changes) > 1:
                # Convert American odds to probabilities
                def american_to_prob(american_price: int) -> float:
                    if american_price > 0:
                        return 100.0 / (100.0 + american_price)
                    else:
                        return abs(american_price) / (abs(american_price) + 100.0)
                
                implied_probs = [american_to_prob(odds[0]) for odds in recent_changes]
                changes = [b - a for a, b in zip(implied_probs[1:], implied_probs[:-1])]
                import numpy as np
                current_vol = float(np.std(changes)) if changes else None
            else:
                current_vol = None
        
        return {
            "success": True,
            "event_id": event_id,
            "side_index": side_index,
            "league": league,
            "game_clock": game_clock,
            "score_diff": score_diff,
            "current_volatility": current_vol
        }

class TradingTool:
    """
    Tool to simulate or execute trades based on volatility signals.
    This is a mock implementation - you would integrate with SportTrade's API.
    """
    
    def __init__(self):
        self.trade_log_file = "trades.log"
    
    def run(self, action: str, event_id: int, side: str, size: float) -> Dict[str, Any]:
        """
        Simulate or execute a trade.
        action: "BUY_VOL" or "SELL_VOL"
        """
        logger.info(f"[TradingTool] {action} for event_id={event_id}, side={side}, size={size}")
        
        # Log trade to file
        timestamp = datetime.utcnow().isoformat()
        trade_record = {
            "timestamp": timestamp,
            "action": action,
            "event_id": event_id,
            "side": side,
            "size": size
        }
        
        with open(self.trade_log_file, "a") as f:
            f.write(json.dumps(trade_record) + "\n")
        
        return {
            "success": True,
            "trade_id": hash(f"{timestamp}{event_id}{action}"),
            **trade_record
        } 