"""
Bias Analyst — วิเคราะห์ Higher Timeframe direction
ดู H1, H4, Daily แล้วบอกว่าควรเทรด BUY หรือ SELL
"""

import yfinance as yf
import pandas as pd
import anthropic
import json
from datetime import datetime
from config.settings import ANTHROPIC_API_KEY, MODEL_SMART
from agents.smc_engine import SMCEngine, summarize

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
smc = SMCEngine(swing_length=5)


def get_htf_data() -> dict:
    """ดึงข้อมูล H1, H4, Daily แล้วรัน SMC Engine แต่ละ timeframe"""
    ticker = yf.Ticker("GC=F")
    result = {}

    timeframes = {
        "H1":    ("5d",  "1h"),
        "H4":    ("30d", "4h"),
        "Daily": ("90d", "1d"),
    }

    for tf_name, (period, interval) in timeframes.items():
        try:
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                continue

            df.columns = [c.lower() for c in df.columns]
            df = df[['open', 'high', 'low', 'close', 'volume']].dropna()

            smc_result = smc.analyze(df)
            current_price = round(df['close'].iloc[-1], 2)
            summary = summarize(smc_result, current_price)

            result[tf_name] = {
                "bias": summary["bias"],
                "last_bos": summary["last_bos"],
                "last_choch": summary["last_choch"],
                "last_sweep": summary["last_sweep"],
                "active_ob": summary["active_ob"],
                "equal_highs": summary["equal_highs"],
                "equal_lows": summary["equal_lows"],
                "current_price": current_price
            }
        except Exception as e:
            result[tf_name] = {"error": str(e)}

    return result


def analyze() -> dict:
    """วิเคราะห์ HTF bias แล้วให้ Claude สรุป"""
    htf_data = get_htf_data()

    prompt = f"""คุณคือ Bias Analyst ผู้เชี่ยวชาญ Higher Timeframe Analysis

ข้อมูล SMC แต่ละ Timeframe ของ XAUUSD:
{json.dumps(htf_data, indent=2, ensure_ascii=False)}

วิเคราะห์:
1. Swing Bias รวม (Daily + H4 + H1 ชี้ทางเดียวกันมั้ย?)
2. ถ้าขัดแย้งกัน — ให้น้ำหนัก Daily > H4 > H1
3. มี Key Level (OB หรือ EQH/EQL) ที่สำคัญบน HTF มั้ย?
4. ควรเทรดเฉพาะ BUY, เฉพาะ SELL หรือ ทั้งสองทาง?

ตอบเป็น JSON เท่านั้น:
{{
  "overall_bias": "bullish" หรือ "bearish" หรือ "neutral",
  "bias_strength": "strong" หรือ "moderate" หรือ "weak",
  "daily_bias": "bullish/bearish/neutral",
  "h4_bias": "bullish/bearish/neutral",
  "h1_bias": "bullish/bearish/neutral",
  "aligned": true/false,
  "trade_direction": "BUY_ONLY" หรือ "SELL_ONLY" หรือ "BOTH" หรือ "NO_TRADE",
  "key_levels": [{{
    "level": ราคา,
    "type": "resistance/support/ob",
    "timeframe": "H4/Daily"
  }}],
  "reasoning": "อธิบายสั้นๆ ภาษาไทย"
}}"""

    response = client.messages.create(
        model=MODEL_SMART,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    result = json.loads(text)
    result["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["raw_htf"] = htf_data

    return result


def format_bias_message(bias: dict) -> str:
    """แปลง bias result เป็นข้อความ Telegram"""

    if "error" in bias:
        return f"❌ Bias Analyst Error: {bias['error']}"

    direction_map = {
        "bullish": "🟢 BULL",
        "bearish": "🔴 BEAR",
        "neutral": "⚪ NEUTRAL"
    }

    trade_map = {
        "BUY_ONLY":  "✅ BUY Only",
        "SELL_ONLY": "✅ SELL Only",
        "BOTH":      "⚡ Both directions",
        "NO_TRADE":  "🚫 No Trade"
    }

    strength_icon = {"strong": "💪", "moderate": "👍", "weak": "⚠️"}.get(bias.get("bias_strength"), "")
    aligned_icon = "✅" if bias.get("aligned") else "⚠️ ขัดแย้ง"

    levels = bias.get("key_levels", [])
    levels_str = "\n".join([f"  `{l['level']}` ({l['type']} {l['timeframe']})" for l in levels[:3]]) or "  ไม่มี"

    return (
        f"🌍 *Bias Analyst Report*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{strength_icon} Overall: *{direction_map.get(bias.get('overall_bias'), '?')}*\n"
        f"📐 Aligned: {aligned_icon}\n"
        f"\n"
        f"Daily: `{bias.get('daily_bias', '?')}`\n"
        f"H4:    `{bias.get('h4_bias', '?')}`\n"
        f"H1:    `{bias.get('h1_bias', '?')}`\n"
        f"\n"
        f"🎯 Trade: *{trade_map.get(bias.get('trade_direction'), '?')}*\n"
        f"\n"
        f"📌 Key Levels:\n{levels_str}\n"
        f"\n"
        f"📝 {bias.get('reasoning', '')}\n"
        f"⏰ {bias.get('analyzed_at', '')}"
    )
