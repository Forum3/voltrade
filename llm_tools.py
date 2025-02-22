import os
import logging
import openai
from typing import Dict, Optional, Union
from datetime import datetime

from agent_types import MarketState, LiveMarketState, TradeDecision
from volatility_tools import format_market_data

class LLMTool:
    def __init__(self):
        self.api_key = os.getenv('OPENAI_API_KEY')
        if not self.api_key:
            raise ValueError("OpenAI API key not found")
            
        openai.api_key = self.api_key
        
    def run(self, prompt: str) -> Dict:
        """Run LLM inference."""
        try:
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": """
                    You are a sophisticated sports trading assistant.
                    Your role is to analyze market data and volatility metrics
                    to make trading decisions.
                    
                    Respond with a JSON object containing:
                    {
                        "analysis": "Your detailed analysis",
                        "confidence": 0.0-1.0,
                        "recommendation": "BUY_VOL or SELL_VOL or NO_ACTION",
                        "size": 0-100 (% of capital to risk)
                    }
                    """},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7
            )
            
            # Extract decision from response
            content = response.choices[0].message.content
            
            # Parse JSON-like string (basic implementation)
            lines = content.strip().split('\n')
            result = {}
            
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip().strip('"{}')
                    value = value.strip().strip('",')
                    if key == 'confidence' or key == 'size':
                        try:
                            value = float(value)
                        except:
                            value = 0.0
                    result[key] = value
            
            return result
            
        except Exception as e:
            logging.error(f"LLM error: {str(e)}")
            return {
                "analysis": "Error in LLM processing",
                "confidence": 0.0,
                "recommendation": "NO_ACTION",
                "size": 0
            }
    
    def get_decision(self, state: Union[MarketState, LiveMarketState]) -> TradeDecision:
        """Get trading decision for market state."""
        # Format data
        prompt = format_market_data(state)
        
        # Get LLM analysis
        result = self.run(prompt)
        
        return TradeDecision(
            event_id=state.event_id,
            league=state.league,
            side_index=state.side_index,
            analysis=result,
            timestamp=datetime.utcnow().isoformat()
        )

def get_llm_response(prompt: str) -> Dict:
    """Simple wrapper for one-off LLM calls."""
    tool = LLMTool()
    return tool.run(prompt) 