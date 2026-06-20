# SmartAgentTrade

## Run (Windows)
```
git pull
pip install -r bot/requirements.txt
cd bot && python main.py
```

## Project Overview
Multi-Agent AI Trading System สำหรับ XAUUSD (Gold Spot)
ใช้ Smart Money Concepts (SMC) strategy + Claude API เป็น AI brain

## Strategy
- Style: SMC — Order Block, BOS, CHoCH, Liquidity Sweep
- Asset หลัก: XAUUSD (Gold)
- Timeframe: M5 entry, H1/H4 bias
- Setup ที่ชอบ: Bullish/Bearish OB หลัง Liquidity Sweep
- Mode: Semi-Auto (AI แจ้งเตือน คุณกด confirm เอง)

## Tech Stack
- Language: Python 3.11+
- AI: Claude API (Haiku สำหรับ scan, Sonnet สำหรับวิเคราะห์)
- Notification: Telegram Bot
- Data: MetaAPI หรือ yfinance
- UI: Rich (terminal dashboard)

## Agent Roster
| Agent | หน้าที่ |
|-------|---------|
| Chart Analyst | วิเคราะห์ OB, BOS, CHoCH, Sweep บน M5 |
| Bias Analyst | เช็ค HTF direction H1/H4 |
| News Scout | Economic calendar, แจ้งเตือนก่อนข่าว |
| Risk Manager | คำนวณ lot size, max risk 1-2% |
| Supervisor | รวม input ทุก agent ตัดสินใจสุดท้าย |
| Notifier | ส่ง alert ไป Telegram พร้อม Confirm/Skip |

## Project Structure
```
Forex/
├── CLAUDE.md
├── indicator/
│   └── SMC_Complete_4.pine      # TradingView indicator
├── ea/
│   └── SmartPartialClose.mq5    # MT5 EA
└── bot/
    ├── main.py                  # Entry point
    ├── agents/
    │   ├── chart_analyst.py
    │   ├── bias_analyst.py
    │   ├── news_scout.py
    │   ├── risk_manager.py
    │   ├── supervisor.py
    │   └── notifier.py
    ├── config/
    │   └── settings.py          # API keys, pairs, timeframe
    └── tests/
        └── test_risk_manager.py
```

## Phase Plan
- Phase 1: Chart Analyst + Notifier (Telegram alert)
- Phase 2: Bias Analyst + News Scout
- Phase 3: Risk Manager + Supervisor + Dashboard UI

## Rules
- ห้าม execute trade เอง — แค่แจ้งเตือน คุณเป็นคนกด
- Risk ต่อ trade ไม่เกิน 2% ของ balance
- ไม่เทรดช่วง 30 นาทีก่อนข่าว High Impact
- เทรดเฉพาะ London และ NY session
