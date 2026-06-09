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

    # รัน SMC Engine (ส่ง df ไปด้วยให้ summarize เรียก advanced_signals อัตโนมัติ)
    result = smc.analyze(df)
    summary = summarize(result, current_price, df)
    summary["pair"] = pair
    summary["timeframe"] = "M5"
    summary["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return df, summary

def has_signal(smc_summary: dict) -> bool:
    """
    เช็คเบื้องต้นว่ามี setup ที่น่าสนใจมั้ย (ไม่ใช้ Claude API)
    ถ้าไม่มี → ไม่เรียก Claude เลย ประหยัด cost

    เช็ค 2 ชั้น:
    1. SMC Engine: sweep + OB + structure
    2. Advanced: signal_type จาก indicator logic (A/B/C)
    """
    if not smc_summary:
        return False

    # ── ชั้น 1: session filter ────────────────────────────────
    if not smc_summary.get("tradeable_session", True):
        return False  # Off-hours — ไม่เทรด

    # ── ชั้น 2: advanced signal type (จาก indicator) ──────────
    signal_type = smc_summary.get("signal_type")
    if signal_type:
        return True  # indicator พบ signal ชัดเจน

    # ── ชั้น 3: classic SMC check ─────────────────────────────
    has_sweep     = smc_summary.get("last_sweep") is not None
    has_ob        = smc_summary.get("active_ob") is not None
    has_structure = (smc_summary.get("last_bos") is not None or
                     smc_summary.get("last_choch") is not None)
    bias = smc_summary.get("bias", "neutral")

    score = sum([has_sweep, has_ob, has_structure])
    return score >= 2 and bias != "neutral"


def analyze(smc_summary: dict = None) -> dict:
    """ส่ง SMC summary ให้ Claude วิเคราะห์ context และตัดสินใจ"""

    if smc_summary is None:
        _, smc_summary = get_price_data()

    if smc_summary is None:
        return {"error": "ดึงข้อมูลราคาไม่ได้"}

    # เช็คก่อน — ถ้าไม่มี signal อย่าเสียเงินเรียก Claude
    if not has_signal(smc_summary):
        return {
            "signal": "NO_TRADE",
            "confidence": 0,
            "current_price": smc_summary.get("current_price"),
            "analyzed_at": smc_summary.get("analyzed_at"),
            "smc_bias": smc_summary.get("bias"),
            "had_sweep": False,
            "reasoning": "SMC Engine ไม่พบ setup ที่ครบเงื่อนไข",
            "claude_called": False
        }

    # ดึง advanced signals + session สำหรับใส่ใน prompt
    adv  = smc_summary.get("advanced", {})
    sess = smc_summary.get("session", {})
    signal_type  = smc_summary.get("signal_type", "ไม่มี")
    long_stars   = smc_summary.get("long_stars") or "-"
    short_stars  = smc_summary.get("short_stars") or "-"
    momentum_warn = ""
    if adv.get("momentum_bear"): momentum_warn = "⚠️ Momentum ลงแรง (>2.5×ATR) — ระวัง Long"
    if adv.get("momentum_bull"): momentum_warn = "⚠️ Momentum ขึ้นแรง (>2.5×ATR) — ระวัง Short"

    prompt = f"""คุณคือ Chart Analyst ผู้เชี่ยวชาญ Smart Money Concepts (SMC)

═══ SMC Analysis: {smc_summary.get('pair')} {smc_summary.get('timeframe')} ═══
ราคาปัจจุบัน: {smc_summary.get('current_price')}
Session: {sess.get('emoji','')} {sess.get('session','')} ({sess.get('time_thai','')})
Bias (M5): {smc_summary.get('bias')}

─── Structure ───
BOS ล่าสุด:   {smc_summary.get('last_bos')}
CHoCH ล่าสุด: {smc_summary.get('last_choch')} (อายุ {adv.get('choch_age_bars', '?')} บาร์)
Sweep ล่าสุด: {smc_summary.get('last_sweep')}

─── Order Block ───
Active OB: {smc_summary.get('active_ob')}
FVG ใกล้สุด: {smc_summary.get('nearest_fvg')}
EQH: {smc_summary.get('equal_highs')}
EQL: {smc_summary.get('equal_lows')}

─── Indicator Signals (SMC By Beam) ───
Signal Type: {signal_type}
Long Stars:  {long_stars} (score {adv.get('long_score',0)})
Short Stars: {short_stars} (score {adv.get('short_score',0)})
H1 Bias: {'▲ Bull' if adv.get('h1_bull') else '▼ Bear'} (mid {adv.get('h1_mid')})
H4 Bias: {'▲ Bull' if adv.get('h4_bull') else '▼ Bear'} (mid {adv.get('h4_mid')})
In OB:   Bull={adv.get('in_bull_ob')} | Bear={adv.get('in_bear_ob')}
Sweep:   Low={adv.get('recent_sweep_low')} ({adv.get('sweep_l_age_bars')} bars ago) | High={adv.get('recent_sweep_high')} ({adv.get('sweep_h_age_bars')} bars ago)
Candle:  Bull={adv.get('bull_candle')} | Bear={adv.get('bear_candle')}
CHoCH Grab: Bull={adv.get('bull_choch_grab')} | Bear={adv.get('bear_choch_grab')}
{momentum_warn}
ATR: {adv.get('atr')}

ให้วิเคราะห์และตัดสินใจ:
1. BUY / SELL / NO_TRADE — โดยใช้ signal_type เป็นหลัก ถ้า C/B2 = high confidence
2. Entry Zone — เข้าใน OB หรือ FVG
3. SL — ใต้ sweep low หรือ เหนือ sweep high
4. TP — next liquidity / EQH / EQL
5. ถ้า momentum แรงสวนทาง = ลด confidence หรือ NO_TRADE

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
    result["claude_called"] = True

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
