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
    """
    ดึงข้อมูลราคา Gold — M15 (OB zone) + M5 (entry timing)
    คืน df_m5, summary ที่มี m15_summary ฝังอยู่ด้วย
    """
    ticker = yf.Ticker("GC=F")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── M15 (โครงสร้างใหญ่ — OB zone, CHoCH, Sweep) ──────────────
    df15 = ticker.history(period="10d", interval="15m")
    m15_summary = None
    if not df15.empty:
        df15.columns = [c.lower() for c in df15.columns]
        df15 = df15[['open', 'high', 'low', 'close', 'volume']].dropna()
        res15 = smc.analyze(df15)
        m15_summary = summarize(res15, round(df15['close'].iloc[-1], 2))
        m15_summary["timeframe"] = "M15"

    # ── M5 (entry timing — micro OB, confirm candle) ───────────────
    df5 = ticker.history(period=period, interval=interval)
    if df5.empty:
        return None, None

    df5.columns = [c.lower() for c in df5.columns]
    df5 = df5[['open', 'high', 'low', 'close', 'volume']].dropna()

    current_price = round(df5['close'].iloc[-1], 2)
    res5  = smc.analyze(df5)
    summary = summarize(res5, current_price, df5)
    summary["pair"]        = pair
    summary["timeframe"]   = "M5"
    summary["analyzed_at"] = now_str
    summary["m15"]         = m15_summary  # ฝัง M15 ไว้ใน summary

    return df5, summary

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
    m15  = smc_summary.get("m15") or {}

    momentum_warn = ""
    if adv.get("momentum_bear"): momentum_warn = "⚠️ Momentum ลงแรง (>2.5×ATR) — ระวัง Long"
    if adv.get("momentum_bull"): momentum_warn = "⚠️ Momentum ขึ้นแรง (>2.5×ATR) — ระวัง Short"

    rev_signal = rev.get("reversal_signal")
    rev_block  = ""
    if rev_signal:
        rev_block = f"""
─── 🔄 M5 REVERSAL DETECTED ───
Direction: {rev_signal} | Score: {rev.get('reversal_score')}/10 {rev.get('reversal_stars','')}
Entry Zone: {rev.get('entry_zone')} | SL: {rev.get('stop_loss')} | TP: {rev.get('take_profit')} | RR: 1:{rev.get('rr')}
"""

    sweep_l_age = adv.get('sweep_l_age_bars') or 999
    sweep_h_age = adv.get('sweep_h_age_bars') or 999
    choch_age   = adv.get('choch_age_bars')   or 999
    h1_bull     = adv.get('h1_bull', False)
    h4_bull     = adv.get('h4_bull', False)
    macro_bias  = "BULL" if (h1_bull and h4_bull) else "BEAR" if (not h1_bull and not h4_bull) else "MIXED"

    prompt = f"""คุณคือ Chart Analyst Agent — หาจุดเข้า trade XAUUSD
วิเคราะห์ M15 ก่อน (โครงสร้าง + OB zone) แล้วหา entry แม่นใน M5
จุดออก/trailing stop ใช้ EA — หน้าที่คุณคือหาจุดเข้าเท่านั้น

══════ ① Macro Bias (H1/H4) — กำหนด direction ══════
H1: {'▲ BULL' if h1_bull else '▼ BEAR'}  |  H4: {'▲ BULL' if h4_bull else '▼ BEAR'}  |  Macro: {macro_bias}
→ BEAR = SELL เท่านั้น | BULL = BUY เท่านั้น | MIXED = ลด confidence 20

══════ ② M15 — โครงสร้างใหญ่ + OB zone ══════
Bias:        {m15.get('bias','?')}
CHoCH:       {m15.get('last_choch','–')}
BOS:         {m15.get('last_bos','–')}
Last Sweep:  {m15.get('last_sweep','–')}
Active OB:   {m15.get('active_ob','–')}   ← OB zone หลักสำหรับ entry
FVG:         {m15.get('nearest_fvg','–')}
EQH/EQL:     {m15.get('equal_highs','–')} / {m15.get('equal_lows','–')}

→ M15 OB คือ zone ที่จะรอราคา pullback มาถึง
→ ถ้า M15 ไม่มี OB ที่ชัด หรือ bias ขัด macro = NO_TRADE

══════ ③ M5 — จุดเข้าแม่นภายใน M15 OB ══════
ราคาปัจจุบัน: {smc_summary.get('current_price')}
Sweep Low:   {adv.get('recent_sweep_low','–')} ({sweep_l_age} bars ago)
Sweep High:  {adv.get('recent_sweep_high','–')} ({sweep_h_age} bars ago)
CHoCH:       {smc_summary.get('last_choch','–')} ({choch_age} bars ago)
BOS:         {smc_summary.get('last_bos','–')}
Active OB:   {smc_summary.get('active_ob','–')}   ← micro OB ใน M5 สำหรับ entry จุดแม่น
FVG:         {smc_summary.get('nearest_fvg','–')}
Confirm:     Bull={adv.get('bull_candle')} Bear={adv.get('bear_candle')}
{rev_block}{momentum_warn}

→ ลำดับ Sweep→CHoCH บังคับ (sweep_age > choch_age = ถูกต้อง)
→ เข้าที่ M5 micro OB ภายใน M15 OB zone
→ SL ใต้ M15 sweep low (BUY) หรือ เหนือ M15 sweep high (SELL)
Session: {sess.get('emoji','')} {sess.get('session','')} ({sess.get('time_thai','')})

══════ เกณฑ์โหวต ══════
YES:
  ✓ direction ตรง macro bias (H1/H4)
  ✓ M15 มี OB zone + Sweep + CHoCH ชัดเจน
  ✓ M5 Sweep เกิดก่อน CHoCH
  ✓ ราคาอยู่ที่ M5 OB/FVG ภายใน M15 zone แล้ว
  ✓ มี confirm candle | RR ≥ 1.5

NO ทันที:
  ✗ signal สวน H1/H4 macro
  ✗ M15 ไม่มี OB zone ที่ชัด
  ✗ M5 ไม่มี Sweep ก่อน CHoCH
  ✗ ราคายังไม่ถึง OB zone
  ✗ ไม่มี confirm candle

ตอบ JSON เท่านั้น:
{{
  "vote": "YES/NO",
  "vote_reasoning": "1-2 ประโยค — M15 OB zone + M5 entry confirmation",
  "signal": "BUY/SELL/NO_TRADE",
  "confidence": 0-100,
  "setup_type": "TREND_OB/REVERSAL/NO_TRADE",
  "entry_zone": [low, high] จาก M5 micro OB หรือ null,
  "stop_loss": ราคา (ใต้/เหนือ M15 sweep zone) หรือ null,
  "take_profit": ราคา (next liquidity — R:R เท่านั้น EA จัดการ exit) หรือ null,
  "rr_ratio": number หรือ null,
  "m15_ob": "M15 OB zone ที่ใช้ เช่น 3285-3300" หรือ null,
  "key_factors": ["factor1", "factor2"],
  "reasoning": "ภาษาไทย — ระบุ: M15 structure, M5 entry zone, Sweep→CHoCH order, confirm"
}}"""

    response = client.messages.create(
        model=MODEL_SMART,
        max_tokens=1500,
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
    result["m15_bias"]      = m15.get("bias")
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
