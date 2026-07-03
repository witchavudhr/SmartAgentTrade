import os
from dotenv import load_dotenv

load_dotenv()

# Claude API — เก็บค่าไว้ใช้ใน fallback แต่ลบออกจาก env
# เพื่อไม่ให้ claude_agent_sdk หยิบไปใช้แทน subscription
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
os.environ.pop("ANTHROPIC_API_KEY", None)

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

# Trading
TRADING_PAIR = os.getenv("TRADING_PAIR", "XAUUSD")
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 15))
MAX_RISK_PERCENT = float(os.getenv("MAX_RISK_PERCENT", 2.0))
BALANCE          = float(os.getenv("BALANCE", 10000.0))
FIXED_LOT        = float(os.getenv("FIXED_LOT", 0.0))    # 0 = คำนวณจาก risk%, >0 = fixed
MAX_LOT          = float(os.getenv("MAX_LOT", 0.1))      # lot สูงสุดต่อ 1 ไม้

# Sessions (UTC+7)
LONDON_SESSION_START = os.getenv("LONDON_SESSION_START", "14:00")
LONDON_SESSION_END = os.getenv("LONDON_SESSION_END", "23:00")
NEW_YORK_SESSION_START = os.getenv("NEW_YORK_SESSION_START", "19:00")
NEW_YORK_SESSION_END = os.getenv("NEW_YORK_SESSION_END", "04:00")
NEWS_ENABLED       = os.getenv("NEWS_ENABLED", "false").lower() == "true"
NEWS_BLOCK_MINUTES = int(os.getenv("NEWS_BLOCK_MINUTES", 30))

# MT5 Auto-Execute
MT5_ENABLED          = os.getenv("MT5_ENABLED", "false").lower() == "true"
MT5_PATH             = os.getenv("MT5_PATH", "")   # path to terminal64.exe (ถ้ามีหลาย MT5)
MT5_LOGIN            = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD         = os.getenv("MT5_PASSWORD", "")
MT5_SERVER           = os.getenv("MT5_SERVER", "")
MT5_SYMBOL           = os.getenv("MT5_SYMBOL", "XAUUSD")
MT5_MAGIC            = int(os.getenv("MT5_MAGIC", "20250101"))
MT5_MAX_SLIPPAGE_PIPS = int(os.getenv("MT5_MAX_SLIPPAGE_PIPS", "3"))

# POS Guard — Per-position trailing stop (mirrors SmartPartialClose.mq5 PosGuard)
POSGUARD_ENABLED        = os.getenv("POSGUARD_ENABLED", "false").lower() == "true"
POSGUARD_TRIGGER_USD    = float(os.getenv("POSGUARD_TRIGGER_USD", "20.0"))  # profit $ เมื่อ lock SL
POSGUARD_LOCK_TICKS     = float(os.getenv("POSGUARD_LOCK_TICKS", "10.0"))   # ticks เหนือ open (initial BE)
POSGUARD_STEP_TICKS     = float(os.getenv("POSGUARD_STEP_TICKS", "500.0"))  # trailing step ticks
POSGUARD_CHECK_INTERVAL = int(os.getenv("POSGUARD_CHECK_INTERVAL", "10"))   # วินาที ระหว่างเช็ค

# Models
MODEL_FAST = "claude-haiku-4-5"    # backtest confirm_signal (ถูก 12x)
MODEL_SMART = "claude-sonnet-4-6"  # all voting agents + supervisor

# Agent SDK — ใช้ Anthropic Managed Agents แทน messages.create()
# ตั้งค่า USE_AGENT_SDK=true ใน .env เพื่อทดลอง (code เดิมยังอยู่)
USE_AGENT_SDK = os.getenv("USE_AGENT_SDK", "false").lower() == "true"

# Cache settings
BIAS_CACHE_MINUTES = 60    # HTF bias cache 1 ชั่วโมง
NEWS_CACHE_MINUTES = 30    # News cache 30 นาที
