import os
from dotenv import load_dotenv

load_dotenv()

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

# Trading
TRADING_PAIR = os.getenv("TRADING_PAIR", "XAUUSD")
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", 15))
MAX_RISK_PERCENT = float(os.getenv("MAX_RISK_PERCENT", 2.0))

# Sessions (UTC+7)
LONDON_SESSION_START = os.getenv("LONDON_SESSION_START", "14:00")
LONDON_SESSION_END = os.getenv("LONDON_SESSION_END", "23:00")
NEW_YORK_SESSION_START = os.getenv("NEW_YORK_SESSION_START", "19:00")
NEW_YORK_SESSION_END = os.getenv("NEW_YORK_SESSION_END", "04:00")
NEWS_BLOCK_MINUTES = int(os.getenv("NEWS_BLOCK_MINUTES", 30))

# Models
MODEL_FAST = "claude-haiku-4-5"    # bias, news, filter (ถูก 12x)
MODEL_SMART = "claude-sonnet-4-5"  # final chart decision เท่านั้น

# Cache settings
BIAS_CACHE_MINUTES = 60    # HTF bias cache 1 ชั่วโมง
NEWS_CACHE_MINUTES = 30    # News cache 30 นาที
