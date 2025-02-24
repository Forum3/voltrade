#!/usr/bin/env python3
"""
multi_agent_voltrade.py

This file integrates the new agents into the OpenAI multi-agent framework.
It defines two agents:
  1. The Pregame Bet Agent – reads initial bet data from the PostgreSQL table billysbetdata
     and computes the pregame implied volatility.
  2. The Sell Signal Agent – uses that baseline plus live market data from the Unabated API
     to generate sell signals (only if the bet is in profit).

Before running, ensure that your environment variables (including DATABASE_URL/POSTGRES_URL) are set.
Also, ensure that the table billysbetdata exists and that the new table billysSellData is created.
To run, execute:
    python multi_agent_voltrade.py
"""

from swarm import Swarm, Agent
from pregame_bet_agent import run_pregame_bet_agent, get_db_connection
from sell_signal_generator import generate_sell_signals
import logging
from polymarket_api import get_polymarket_data, get_live_market_data_from_polymarket

def run_pregame_bet_analysis():
    try:
        logging.info("Starting pregame bet analysis...")
        bets = run_pregame_bet_agent()
        
        if not bets:
            logging.warning("No active positions found. Checking database status...")
            conn = get_db_connection()
            with conn.cursor() as cur:
                # Check if table exists
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 
                        FROM information_schema.tables 
                        WHERE table_name = 'billysbetdata'
                    )
                """)
                table_exists = cur.fetchone()[0]
                
                if not table_exists:
                    return "Error: Table 'billysbetdata' does not exist. Please create the table first."
                
                # Check if table has any data
                cur.execute("SELECT COUNT(*) FROM billysbetdata")
                count = cur.fetchone()[0]
                
                if count == 0:
                    return "Error: No bets found in billysbetdata table. Please add some bets first."
                
                # If table exists and has data, show executed/redemption status
                cur.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN executed THEN 1 ELSE 0 END) as executed,
                        SUM(CASE WHEN redemption_status THEN 1 ELSE 0 END) as redeemed,
                        SUM(CASE WHEN executed AND NOT redemption_status THEN 1 ELSE 0 END) as active
                    FROM billysbetdata
                """)
                stats = cur.fetchone()
                return f"""
Database Status:
- Total bets: {stats[0]}
- Executed (positions taken): {stats[1]}
- Redeemed (positions closed): {stats[2]}
- Active positions: {stats[3]}

No active positions found to analyze. To analyze a position, it must be:
1. Executed (position taken)
2. Not yet redeemed (position still open)
"""
            
        summary = "Active Positions:\n"
        for bet in bets:
            other_team = bet['away_team'] if bet['bet_team'] == bet['home_team'] else bet['home_team']
            summary += (
                f"Bet ID {bet['id']}, {bet['bet_team']} vs {other_team}\n"
                f"Spread: {bet['spread']:+.1f}, ML: {bet['moneyline']:+d}, Total: {bet['total']} {bet['total_side']}\n"
                f"Model Prob: {bet['pregame_moneyline_prob']:.3f}, Pregame IV: {bet['pregame_iv']:.4f}\n"
            )
        return summary
    except Exception as e:
        logging.error(f"Error in pregame bet analysis: {str(e)}")
        return f"Error running pregame analysis: {str(e)}"

def run_sell_signal_agent():
    """Run the sell signal agent to generate sell signals."""
    logging.info("Running Sell Signal Agent...")
    
    # Generate sell signals using Polymarket data
    signals = generate_sell_signals(use_polymarket=True)
    
    if not signals:
        logging.info("No sell signals generated.")
        return
    
    # Display sell signals
    logging.info("\nSell Signals:")
    for signal in signals:
        logging.info(f"Bet ID {signal['id']}, {signal['outcome']}")
        logging.info(f"Entry: {signal['entry_price']:.3f}, Current: {signal['current_price']:.3f}")
        logging.info(f"PnL: ${signal['pnl']:.2f} ({signal['pnl_percentage']:+.1f}%)")
        logging.info(f"Live Vol: {signal['live_vol']:.2f}, Expected Vol: {signal['expected_vol']:.2f}")
        logging.info(f"Recommendation: Sell {signal['suggested_sell_shares']:.0f} shares at {signal['suggested_sell_price']:.3f}\n")

def main():
    client = Swarm()

    pregame_agent = Agent(
        name="Pregame Bet Agent",
        instructions=("Load pregame bet data from billysbetdata (PostgreSQL), compute baseline implied volatility "
                      "using the Polson–Stern model, and return the bet details."),
        functions=[run_pregame_bet_analysis]
    )

    sell_signal_agent = Agent(
        name="Sell Signal Agent",
        instructions=("Using the pregame bet data and live market data from the Unabated API, generate sell signals "
                      "for bets that are in profit and where the live implied volatility is favorable."),
        functions=[run_sell_signal_agent]
    )

    print("=== Running Pregame Bet Analysis ===")
    response1 = client.run(agent=pregame_agent, messages=[{"role": "user", "content": "Run pregame bet analysis."}])
    print(response1.messages[-1]["content"])

    print("\n=== Running Sell Signal Generation ===")
    response2 = client.run(agent=sell_signal_agent, messages=[{"role": "user", "content": "Generate sell signals."}])
    print(response2.messages[-1]["content"])

if __name__ == "__main__":
    main()
