import yfinance as yf
import anthropic
import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from config.settings import ANTHROPIC_API_KEY, MODEL_SMART, MODEL_FAST, TRADING_PAIR
from agents.smc_engine import SMCEngine, summarize

_CACHE_PATH = Path(__file__).parent.parent / "data" / "ai_cache.json"

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

    # ── ชั้น 2: Reversal signal (priority สูงสุด) ──────────────
    rev = smc_summary.get("reversal", {})
    if rev.get("reversal_signal") and rev.get("reversal_score", 0) >= 3:
        return True  # จุดกลับตัวชัดเจน

    # ── ชั้น 3: advanced signal type (จาก indicator) ──────────
    signal_type = smc_summary.get("signal_type")
    if signal_type and "C_" in str(signal_type):
        return True  # Type C = CHoCH+Sweep = reversal quality

    # ── ชั้น 4: classic SMC (ถ้าไม่มี reversal) ──────────────
    has_sweep     = smc_summary.get("last_sweep") is not None
    has_ob        = smc_summary.get("active_ob") is not None
    has_structure = (smc_summary.get("last_bos") is not None or
                     smc_summary.get("last_choch") is not None)
    bias = smc_summary.get("bias", "neutral")

    score = sum([has_sweep, has_ob, has_structure])
    return score >= 2 and bias != "neutral"


def confirm_signal(df_slice: pd.DataFrame, signal: dict, h4_bias: str,
                   bar_time: str, confidence_threshold: int = 60) -> dict:
    """
    Claude Haiku lightweight confirmation for backtest.
    Cache: bot/data/ai_cache.json — deterministic re-runs.
    Returns: {"confirmed": bool, "confidence": int, "reasoning": str}
    """
    cache_key = f"{bar_time}|{signal['signal']}|{signal.get('score', 0)}"

    # ── Load cache ──────────────────────────────────────────────────
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if _CACHE_PATH.exists():
        try:
            cache = json.loads(_CACHE_PATH.read_text())
        except Exception:
            cache = {}

    if cache_key in cache:
        cached = cache[cache_key]
        confirmed = cached["confidence"] >= confidence_threshold
        return {"confirmed": confirmed, **cached}

    # ── Build minimal context (last 5 bars) ─────────────────────────
    last5 = df_slice.tail(5)[["open", "high", "low", "close"]].round(2)
    bars_str = " | ".join(
        f"O={r.open} H={r.high} L={r.low} C={r.close}"
        for _, r in last5.iterrows()
    )

    reasons_str = ", ".join(signal.get("reasons", [])[:3]) or "-"
    prompt = f"""You are a Gold (XAUUSD) M5 trade filter. Confirm if this setup is valid.

Setup: {signal['signal']} | Score: {signal.get('score',0)}/10 | RR: {signal.get('rr',0)} | H4: {h4_bias}
Type: {signal.get('setup_type','?')} | Entry: {signal.get('entry')} | SL: {signal.get('sl')} ({signal.get('sl_pips','?')}p) | TP: {signal.get('tp')}
Reasons: {reasons_str}
Last 5 bars: {bars_str}

Reply JSON only: {{"confidence": 0-100, "reasoning": "1-2 sentences max"}}"""

    try:
        resp = client.messages.create(
            model=MODEL_FAST,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        result = json.loads(text)
        entry = {
            "confidence": int(result.get("confidence", 0)),
            "reasoning":  str(result.get("reasoning", ""))[:200],
        }
    except Exception as e:
        entry = {"confidence": 0, "reasoning": f"error: {e}"}

    # ── Save cache ──────────────────────────────────────────────────
    cache[cache_key] = entry
    _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))

    confirmed = entry["confidence"] >= confidence_threshold
    return {"confirmed": confirmed, **entry}


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

    adv  = smc_summary.get("advanced", {})
    sess = smc_summary.get("session", {})
    rev  = smc_summary.get("reversal", {})

    momentum_warn = ""
    if adv.get("momentum_bear"): momentum_warn = "⚠️ Momentum ลงแรง (>2.5×ATR) — ระวัง Long"
    if adv.get("momentum_bull"): momentum_warn = "⚠️ Momentum ขึ้นแรง (>2.5×ATR) — ระวัง Short"

    # Reversal block สำหรับ prompt
    rev_signal = rev.get("reversal_signal")
    rev_block  = ""
    if rev_signal:
        rev_block = f"""
─── 🔄 REVERSAL SETUP DETECTED ───
Direction:  {rev_signal}
Score:      {rev.get('reversal_score')}/10  {rev.get('reversal_stars','')}  [{rev.get('reversal_grade','')}]
Reasons:    {', '.join(rev.get('reversal_reasons', []))}
Entry Zone: {rev.get('entry_zone')}
SL Suggest: {rev.get('stop_loss')} ({rev.get('sl_pips')} pips)
TP Suggest: {rev.get('take_profit')} ({rev.get('tp_pips')} pips)
RR Suggest: 1:{rev.get('rr')}
Weak Low:   {rev.get('weak_low')} | Weak High: {rev.get('weak_high')}
EQL: {rev.get('eql_levels')} | EQH: {rev.get('eqh_levels')}
"""

    prompt = f"""คุณคือ Chart Analyst Agent ผู้เชี่ยวชาญ Smart Money Concepts (SMC)
หน้าที่: วิเคราะห์ chart แล้ว VOTE YES/NO ว่าควรเข้า trade นี้หรือไม่
เน้น: หาจุดกลับตัว (Reversal) ที่ราคา sweep liquidity แล้วกลับทิศ

═══ SMC Analysis: {smc_summary.get('pair')} {smc_summary.get('timeframe')} ═══
ราคาปัจจุบัน: {smc_summary.get('current_price')}
Session: {sess.get('emoji','')} {sess.get('session','')} ({sess.get('time_thai','')})
Bias (M5): {smc_summary.get('bias')}
{rev_block}
─── Structure ───
CHoCH ล่าสุด: {smc_summary.get('last_choch')}  อายุ {adv.get('choch_age_bars','?')} บาร์
BOS ล่าสุด:   {smc_summary.get('last_bos')}
Sweep ล่าสุด: {smc_summary.get('last_sweep')}

─── Liquidity Levels ───
EQH: {smc_summary.get('equal_highs')}
EQL: {smc_summary.get('equal_lows')}
Active OB: {smc_summary.get('active_ob')}
FVG: {smc_summary.get('nearest_fvg')}

─── Confirmation ───
H1: {'▲ Bull' if adv.get('h1_bull') else '▼ Bear'}  H4: {'▲ Bull' if adv.get('h4_bull') else '▼ Bear'}
Sweep Low: {adv.get('recent_sweep_low')} ({adv.get('sweep_l_age_bars')}b ago)
Sweep High: {adv.get('recent_sweep_high')} ({adv.get('sweep_h_age_bars')}b ago)
CHoCH Grab: Bull={adv.get('bull_choch_grab')} Bear={adv.get('bear_choch_grab')}
Candle: Bull={adv.get('bull_candle')} Bear={adv.get('bear_candle')}
{momentum_warn}

ตัดสินใจโดย:
1. ถ้า REVERSAL SETUP DETECTED → ให้น้ำหนักมากที่สุด score ≥7 = confidence ≥80
2. Reversal ที่ดี = CHoCH สด + Sweep + Confirm candle ครบ
3. SL ต้องอยู่ใต้ sweep zone เสมอ — ถ้า SL ไม่ชัด = NO_TRADE
4. TP = next liquidity (EQH/EQL) หรือ OB ฝั่งตรงข้าม
5. momentum แรงสวนทาง → ลด confidence 20 หรือ NO_TRADE

ตอบ JSON เท่านั้น:
{{
  "vote": "YES/NO",
  "vote_reasoning": "เหตุผลที่โหวต 1-2 ประโยค — ระบุจุดเด่น/ข้อกังวลหลัก",
  "signal": "BUY/SELL/NO_TRADE",
  "confidence": 0-100,
  "entry_zone": [low, high] หรือ null,
  "stop_loss": ราคา หรือ null,
  "take_profit": ราคา หรือ null,
  "rr_ratio": number หรือ null,
  "setup_type": "REVERSAL/CONTINUATION/NO_TRADE",
  "key_factors": ["factor1", "factor2"],
  "reasoning": "สั้นๆ ภาษาไทย — ระบุว่า reversal จาก sweep zone ไหน"
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
    result["analyzed_at"]   = smc_summary.get("analyzed_at")
    result["current_price"] = smc_summary.get("current_price")
    result["smc_bias"]      = smc_summary.get("bias")
    result["had_sweep"]     = smc_summary.get("last_sweep") is not None
    result["reversal_score"] = smc_summary.get("reversal_score", 0)
    result["reversal_stars"] = smc_summary.get("reversal_stars")
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
