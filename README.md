# VolTrade

A volatility-based trading bot for sports betting markets using the Unabated API.

## Overview

This project implements an automated trading system that:
- Monitors real-time sports betting markets via Unabated API
- Analyzes volatility patterns in both pregame and live markets
- Makes automated trading decisions using volatility signals
- Manages positions with risk controls
- Provides real-time Telegram alerts
- Enhances decision making with LLM analysis

## Setup

1. Clone the repository:
```bash
git clone https://github.com/Forum3/voltrade.git
cd voltrade
```

2. Set up the virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables:
```bash
# Create .env file with your API keys
UNABATED_API_KEY=your_api_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
OPENAI_API_KEY=your_openai_key
```

## Usage

Run the volatility trading agent:
```bash
python volatility_agent.py
```

For pregame analysis only:
```bash
python agent_pregame.py
```

For live market monitoring:
```bash
python agent_live.py
```

## Project Structure

- `agent.py` - Core agent functionality and base classes
- `agent_pregame.py` - Pregame market analysis system
- `agent_live.py` - Live market monitoring system
- `agent_tools.py` - Data fetching and trading utilities
- `alerts.py` - Telegram notification system
- `llm_tools.py` - LLM integration for enhanced analysis
- `unabated_api.py` - Unabated API interaction
- `volatility_tools.py` - Volatility calculation utilities
- `position_manager.py` - Trade position management
- `agent_types.py` - Data classes and type definitions

## Features

### Market Analysis
- Real-time odds monitoring
- Implied volatility calculations
- Volatility pattern detection
- Score and time-based adjustments

### Trading Logic
- Volatility-based entry signals
- Position sizing based on confidence
- Automated exit conditions
- PnL tracking

### Risk Management
- Maximum position sizes
- Stop-loss implementation
- Hold time limits
- League-specific parameters

### Monitoring
- Real-time Telegram alerts
- Trade logging
- Error handling and reporting
- Performance metrics

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License

[MIT](https://choosealicense.com/licenses/mit/) 