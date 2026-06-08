import yfinance as yf
import anthropic
import json
from datetime import datetime
from config.settings import ANTHROPIC_API_KEY, MODEL_SMART, TRADING_PAIR

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def get_price_data(pair: str = TRADING_PAIR, period: str = "2d", interval: str = "5m") -> dict:
    """ดึงข้อมูลราคา Gold จาก yfinance"""
    ticker = yf.Ticker("GC=F")  # Gold Futures
    df = ticker.history(period=period, interval=interval)

    if df.empty:
        return None

    # เอา 50 แท่งล่าสุด
    df = df.tail(50)

    candles = []
    for idx, row in df.iterrows():
        candles.append({
            "time": str(idx),
            "open": round(row["Open"], 2),
            "high": round(row["High"], 2),
            "low": round(row["Low"], 2),
            "close": round(row["Close"], 2),
            "volume": int(row["Volume"])
        })

    current_price = candles[-1]["close"]
    recent_high = max(c["high"] for c in candles[-20:])
    recent_low = min(c["low"] for c in candles[-20:])

    return {
        "pair": pair,
        "timeframe": "M5",
        "current_price": current_price,
        "recent_high_20": recent_high,
        "recent_low_20": recent_low,
        "candles": candles[-20:]  # ส่งแค่ 20 แท่งล่าสุดให้ AI
    }

def analyze(price_data: dict = None) -> dict:
    """ให้ Chart Analyst วิเคราะห์ตาม SMC concept"""

    if price_data is None:
        price_data = get_price_data()

    if price_data is None:
        return {"error": "ดึงข้อมูลราคาไม่ได้"}

    prompt = f"""คุณคือ Chart Analyst ผู้เชี่ยวชาญ Smart Money Concepts (SMC)

ข้อมูลราคา {price_data['pair']} Timeframe M5:
- ราคาปัจจุบัน: {price_data['current_price']}
- High 20 แท่งล่าสุด: {price_data['recent_high_20']}
- Low 20 แท่งล่าสุด: {price_data['recent_low_20']}

ข้อมูล Candle 20 แท่งล่าสุด:
{json.dumps(price_data['candles'], indent=2)}

วิเคราะห์ตามหลัก SMC:
1. มี Order Block (OB) ที่ชัดเจนมั้ย? ถ้ามีอยู่ที่ระดับไหน?
2. มี Break of Structure (BOS) หรือ CHoCH มั้ย?
3. มี Liquidity Sweep มั้ย? (sweep high/low แล้วกลับ)
4. Setup ที่เห็นคือ BUY, SELL หรือ NO TRADE?

ตอบเป็น JSON format นี้เท่านั้น:
{{
  "signal": "BUY" หรือ "SELL" หรือ "NO_TRADE",
  "confidence": 0-100,
  "order_block_level": ราคาที่ OB อยู่ หรือ null,
  "entry_zone": [ราคาต่ำสุด, ราคาสูงสุด] หรือ null,
  "stop_loss": ราคา SL หรือ null,
  "take_profit": ราคา TP หรือ null,
  "rr_ratio": Risk:Reward ratio หรือ null,
  "bos_detected": true/false,
  "liquidity_sweep": true/false,
  "reasoning": "อธิบายเหตุผลสั้นๆ ภาษาไทย"
}}"""

    response = client.messages.create(
        model=MODEL_SMART,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()

    # parse JSON จาก response
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    result = json.loads(text)
    result["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["current_price"] = price_data["current_price"]

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
