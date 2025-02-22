import sqlite3
import logging
import time
from datetime import datetime
from contextlib import contextmanager
import math
import numpy as np
from scipy.stats import norm
from llm_tools import get_llm_response
from agent_types import TradeDecision, LiveMarketState
from agent_tools import format_market_data

DB_FILE = "unabated_odds.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    try:
        yield conn
    finally:
        conn.close()

def american_to_prob(american_price:int)->float:
    """No-vig implied prob from American line."""
    if american_price>0:
        return 100.0/(100.0+american_price)
    else:
        return abs(american_price)/(abs(american_price)+100.0)

class LiveVolAgent:
    """
    An agent that computes time-varying implied volatility (σᵢᵥ,ₜ)
    from Polson & Stern and uses LLM to make trading decisions.
    
    The volatility formula is:
    σᵢᵥ,ₜ = (l + μ(1-t)) / [ Φ⁻¹(pₜ,ₗ) * sqrt(1-t) ]
    where:
    - l = current lead
    - μ = original spread
    - t = fraction of game completed
    - pₜ,ₗ = current moneyline probability
    """
    def __init__(self, max_hold=15.0):
        self.max_hold = max_hold
    
    def get_live_events(self):
        """Get all live events with their current state."""
        with get_db_connection() as conn:
            cur = conn.cursor()
            events = cur.execute("""
                WITH recent_odds AS (
                    SELECT 
                        event_id,
                        league,
                        bet_type_id,
                        side_index,
                        american_price,
                        points,
                        game_clock,
                        away_team_score,
                        home_team_score,
                        ROW_NUMBER() OVER (
                            PARTITION BY event_id, bet_type_id, side_index 
                            ORDER BY timestamp_utc DESC
                        ) as rn
                    FROM game_odds
                    WHERE status_id = 2  -- live only
                )
                SELECT DISTINCT 
                    ro.event_id,
                    ro.league,
                    ro.game_clock,
                    ro.away_team_score,
                    ro.home_team_score,
                    p.implied_vol as pregame_vol
                FROM recent_odds ro
                LEFT JOIN pregame_implied_vol p ON 
                    ro.event_id = p.event_id 
                    AND ro.side_index = p.side_index
                WHERE ro.rn = 1
            """).fetchall()
            return events
    
    def get_market_state(self, event_id:int, side_idx:int)->dict:
        """Get current market state including all odds."""
        with get_db_connection() as conn:
            cur = conn.cursor()
            state = cur.execute("""
                SELECT 
                    g.game_clock,
                    g.away_team_score,
                    g.home_team_score,
                    g.american_price,
                    g.points,
                    g.bet_type_id,
                    p.spread_points as pregame_spread,
                    p.implied_vol as pregame_vol
                FROM game_odds g
                LEFT JOIN pregame_implied_vol p ON 
                    g.event_id = p.event_id 
                    AND g.side_index = p.side_index
                WHERE g.event_id = ?
                AND g.side_index = ?
                AND g.status_id = 2
                ORDER BY g.timestamp_utc DESC
                LIMIT 1
            """, (event_id, side_idx)).fetchone()
            return state
    
    def compute_time_elapsed(self, game_clock:str, league:str)->float:
        """
        Compute fraction of game completed based on clock and league.
        Returns a value between 0 and 1.
        """
        try:
            mm, ss = game_clock.split(":")
            mm, ss = int(mm), int(ss)
            secs_left = mm*60 + ss
            
            # Total game length by league
            total_mins = {
                "NFL": 60,
                "NBA": 48,
                "CBB": 40
            }.get(league, 60)
            
            total_secs = total_mins * 60
            used = float(total_secs - secs_left)
            t_frac = used / total_secs
            return max(0.0, min(t_frac, 1.0))
        except:
            return 0.0

    def compute_live_vol(self, lead:float, pregame_spread:float, 
                        t_elapsed:float, prob_now:float)->float:
        """
        Compute time-varying implied volatility using Polson-Stern formula.
        """
        if t_elapsed >= 1.0 or prob_now <= 0 or prob_now >= 1.0:
            return None
            
        remain = 1.0 - t_elapsed
        if remain <= 0:
            return None
            
        try:
            z = norm.ppf(prob_now)
            if abs(z) < 1e-6:
                return None
                
            numerator = lead + pregame_spread*remain
            denom = z * math.sqrt(remain)
            if abs(denom) < 1e-9:
                return None
                
            return numerator/denom
        except:
            return None

    def analyze_live_state(self, event_id:int, league:str, 
                          game_clock:str, away_score:int, 
                          home_score:int, pregame_vol:float):
        """Use LLM to analyze live market state and make trading decision."""
        # Get current state for both sides
        states = []
        for side_idx in [0,1]:  # away=0, home=1
            state = self.get_market_state(event_id, side_idx)
            if not state:
                continue
                
            # Compute live volatility
            lead = (away_score - home_score) if side_idx==0 else (home_score - away_score)
            t_elapsed = self.compute_time_elapsed(game_clock, league)
            prob_now = american_to_prob(state[3])  # current ML prob
            live_vol = self.compute_live_vol(
                lead=lead,
                pregame_spread=state[6],  # pregame spread
                t_elapsed=t_elapsed,
                prob_now=prob_now
            )
            
            if live_vol is None:
                continue
            
            market_state = LiveMarketState(
                event_id=event_id,
                league=league,
                side_index=side_idx,
                game_clock=game_clock,
                score_diff=lead,
                time_elapsed=t_elapsed,
                current_prob=prob_now,
                pregame_vol=pregame_vol,
                live_vol=live_vol
            )
            
            # Format data for LLM
            prompt = f"""
            Analyze this live market state and recommend a trading decision:
            
            {format_market_data(market_state)}
            
            Consider:
            1. How has implied volatility changed from pregame ({pregame_vol:.2f}) to now ({live_vol:.2f})?
            2. Is the current score differential ({lead}) justified by time remaining ({game_clock})?
            3. Does the current probability ({prob_now:.3f}) seem efficient?
            4. What's the risk/reward profile given game state?
            
            Provide a trade recommendation with size (0-100% of capital) and direction (buy/sell vol).
            """
            
            response = get_llm_response(prompt)
            decision = TradeDecision(
                event_id=event_id,
                league=league,
                side_index=side_idx,
                analysis=response,
                timestamp=datetime.utcnow().isoformat()
            )
            states.append(decision)
        
        return states

    def run_cycle(self):
        """Process all live events and get LLM analysis."""
        events = self.get_live_events()
        all_decisions = []
        
        for event in events:
            event_id, league, game_clock, away_sc, home_sc, pregame_vol = event
            decisions = self.analyze_live_state(
                event_id, league, game_clock, 
                away_sc, home_sc, pregame_vol
            )
            all_decisions.extend(decisions)
            
            for d in decisions:
                logging.info(
                    f"LiveVolEvent event={d.event_id}, league={d.league}, "
                    f"side={d.side_index}"
                )
                logging.info(f"LLM Analysis: {d.analysis}")
        
        return all_decisions

def main():
    agent = LiveVolAgent()
    while True:
        try:
            decisions = agent.run_cycle()
            logging.info(f"Analyzed {len(decisions)} live trading opportunities")
            time.sleep(10)  # check every 10s
        except KeyboardInterrupt:
            logging.info("Exiting live agent.")
            break
        except Exception as e:
            logging.error(f"Live agent error: {e}")
            time.sleep(5)

if __name__=="__main__":
    main() 