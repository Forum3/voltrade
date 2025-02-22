import os
import logging
import requests
from typing import Optional
from datetime import datetime

class AlertManager:
    """Manages trade alerts via Telegram."""
    
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.enabled = bool(self.bot_token and self.chat_id)
        
        if not self.enabled:
            logging.warning("Telegram alerts disabled: Missing BOT_TOKEN or CHAT_ID")
    
    def send_alert(self, msg: str) -> bool:
        """Send alert message to Telegram."""
        if not self.enabled:
            return False
            
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": msg,
                "parse_mode": "HTML"
            }
            response = requests.post(url, data=data, timeout=5)
            response.raise_for_status()
            return True
            
        except Exception as e:
            logging.error(f"Failed to send Telegram alert: {str(e)}")
            return False
    
    def format_entry_alert(
        self,
        event_id: int,
        league: str,
        side_index: int,
        direction: str,
        size: float,
        confidence: float,
        live_vol: float,
        expected_vol: float,
        score_diff: float,
        current_prob: float,
        game_clock: Optional[str] = None,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        home_price: Optional[float] = None,
        away_price: Optional[float] = None
    ) -> str:
        """Format entry alert message."""
        # Format team matchup
        if home_team and away_team:
            matchup = f"{away_team} @ {home_team}"
        else:
            matchup = "Teams N/A"
            
        # Get price for selected side
        if side_index == 1:  # Home
            team = home_team or "Home"
            price = home_price
        else:  # Away
            team = away_team or "Away"
            price = away_price
            
        price_str = f"${price:.2f}" if price is not None else "N/A"
        
        return f"""
🚨 <b>New Trade Alert</b>

Event: {event_id} ({league})
Matchup: {matchup}
Side: {team}
Price: {price_str}
Action: {direction}
Size: ${size:.2f}
Game Clock: {game_clock or "N/A"}
Score Diff: {score_diff:+.1f}

<b>Volatility Analysis</b>
Live Vol: {live_vol:.2f}
Expected Vol: {expected_vol:.2f}
Deviation: {((live_vol - expected_vol) / expected_vol * 100):.1f}%
Confidence: {confidence:.1%}

<b>Market State</b>
Current Prob: {current_prob:.3f}
Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}
"""
    
    def format_exit_alert(
        self,
        event_id: int,
        league: str,
        side_index: int,
        position_type: str,
        reason: str,
        pnl: float,
        total_pnl: float,
        live_vol: float,
        expected_vol: float,
        score_diff: float,
        current_prob: float,
        game_clock: Optional[str] = None,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        home_price: Optional[float] = None,
        away_price: Optional[float] = None
    ) -> str:
        """Format exit alert message."""
        # Format team matchup
        if home_team and away_team:
            matchup = f"{away_team} @ {home_team}"
        else:
            matchup = "Teams N/A"
            
        # Get price for selected side
        if side_index == 1:  # Home
            team = home_team or "Home"
            price = home_price
        else:  # Away
            team = away_team or "Away"
            price = away_price
            
        price_str = f"${price:.2f}" if price is not None else "N/A"
        
        # Format PnL with color indicators
        pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪️"
        
        return f"""
💰 <b>Position Closed</b>

Event: {event_id} ({league})
Matchup: {matchup}
Side: {team}
Exit Price: {price_str}
Type: {position_type}
Exit Reason: {reason}
PnL: {pnl_emoji} ${pnl:+,.2f}
Total PnL: ${total_pnl:+,.2f}
Game Clock: {game_clock or "N/A"}
Score Diff: {score_diff:+.1f}

<b>Final State</b>
Live Vol: {live_vol:.2f}
Expected Vol: {expected_vol:.2f}
Deviation: {((live_vol - expected_vol) / expected_vol * 100):.1f}%
Current Prob: {current_prob:.3f}
Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}
""" 