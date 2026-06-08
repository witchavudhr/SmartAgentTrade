import yfinance as yf
import anthropic
import json
import pandas as pd
from datetime import datetime
from config.settings import ANTHROPIC_API_KEY, MODEL_SMART, TRADING_PAIR
from agents.smc_engine import SMCEngine, summarize

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
smc = SMCEngine(swing_length=5)

def get_price_data(pair: str = TRADING_PAIR, period: str = "5d", interval: str = "5m") -> tuple[pd.DataFrame, dict]:
    """ดึงข้อมูลราคา Gold จาก yfinance และรัน SMC Engine"""
    ticker = yf.Ticker("GC=F")
    df = ticker.history(period=period, interval=interval)

    if df.empty:
        return None, None

    df.columns = [c.lower() for c in df.columns]
    df = df[['open', 'high', 'low', 'close', 'volume']].dropna()

    current_price = round(df['close'].iloc[-1], 2)

    # รัน SMC Engine
    result = smc.analyze(df)
    summary = summarize(result, current_price)
    summary["pair"] = pair
    summary["timeframe"] = "M5"
    summary["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return df, summary

def analyze(smc_summary: dict = None) -> dict:
    """ส่ง SMC summary ให้ Claude วิเคราะห์ context และตัดสินใจ"""

    if smc_summary is None:
        _, smc_summary = get_price_data()

    if smc_summary is None:
        return {"error": "ดึงข้อมูลราคาไม่ได้"}

    prompt = f"""คุณคือ Chart Analyst ผู้เชี่ยวชาญ Smart Money Concepts (SMC)

ข้อมูล SMC Analysis ของ {smc_summary['pair']} Timeframe {smc_summary['timeframe']}:
{json.dumps(smc_summary, indent=2, ensure_ascii=False)}

จากข้อมูล SMC ที่คำนวณมาแล้ว ให้วิเคราะห์:

1. Setup ที่เห็นคือ BUY, SELL หรือ NO TRADE?
   - ถ้ามี Liquidity Sweep + Active OB + BOS/CHoCH ครบ = high probability
   - ถ้า bias ขัดแย้งกัน = NO TRADE

2. Entry Zone ที่เหมาะสมอยู่ที่ไหน?
   - ควรเข้าที่ OB zone หรือ FVG

3. SL อยู่ที่ไหน? (ใต้ sweep low หรือ เหนือ sweep high)

4. TP อยู่ที่ไหน? (next liquidity, EQH/EQL)

ตอบเป็น JSON เท่านั้น:
{{
  "signal": "BUY" หรือ "SELL" หรือ "NO_TRADE",
  "confidence": 0-100,
  "entry_zone": [low, high] หรือ null,
  "stop_loss": ราคา หรือ null,
  "take_profit": ราคา หรือ null,
  "rr_ratio": number หรือ null,
  "key_factors": ["factor1", "factor2"],
  "reasoning": "อธิบายเหตุผลสั้นๆ ภาษาไทย"
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
    result["analyzed_at"] = smc_summary.get("analyzed_at")
    result["current_price"] = smc_summary.get("current_price")
    result["smc_bias"] = smc_summary.get("bias")
    result["had_sweep"] = smc_summary.get("last_sweep") is not None

    return result

def format_signal_message(analysis: dict) -> str:
    """แปลง analysis เป็นข้อความสวยงามสำหรับ Telegram"""

    if "error" in analysis:
        return f"❌ Chart Analyst Error: {analysis['error']}"

    signal = analysis.get("signal", "NO_TRADE")
    confidence = analysis.get("confidence", 0)

    if signal == "NO_TRADE":
        return (
            f"📊 *Chart Analyst Report*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🔍 Signal: ไม่มี Setup ที่ชัดเจน\n"
            f"💰 ราคาปัจจุบัน: `{analysis.get('current_price')}`\n"
            f"📝 {analysis.get('reasoning', '')}\n"
            f"⏰ {analysis.get('analyzed_at', '')}"
        )

    emoji = "🟢" if signal == "BUY" else "🔴"
    entry = analysis.get("entry_zone")
    entry_str = f"`{entry[0]} - {entry[1]}`" if entry else "N/A"

    return (
        f"🔔 *SETUP FOUND — {signal}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{emoji} Signal: *{signal}*\n"
        f"📊 Confidence: `{confidence}%`\n"
        f"💰 ราคาปัจจุบัน: `{analysis.get('current_price')}`\n"
        f"📍 Entry Zone: {entry_str}\n"
        f"🛑 SL: `{analysis.get('stop_loss', 'N/A')}`\n"
        f"🎯 TP: `{analysis.get('take_profit', 'N/A')}`\n"
        f"⚖️ RR: `1:{analysis.get('rr_ratio', 'N/A')}`\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📐 BOS: {'✅' if analysis.get('bos_detected') else '❌'} | "
        f"Sweep: {'✅' if analysis.get('liquidity_sweep') else '❌'}\n"
        f"📝 {analysis.get('reasoning', '')}\n"
        f"⏰ {analysis.get('analyzed_at', '')}"
    )
