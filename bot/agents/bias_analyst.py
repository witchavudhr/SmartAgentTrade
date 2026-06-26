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
from agents.json_utils import safe_json_parse

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
smc = SMCEngine(swing_length=5)

# Cache
_cache: dict = {"result": None, "timestamp": None}
CACHE_MINUTES = 60


def _get_mt5_htf(tf_mt5, count: int):
    """ดึง OHLCV จาก MT5 สำหรับ higher timeframe"""
    try:
        from agents import mt5_executor
        from config.settings import MT5_SYMBOL
        import MetaTrader5 as mt5
        ok, _ = mt5_executor._connect()
        if not ok:
            return None
        rates = mt5.copy_rates_from_pos(MT5_SYMBOL, tf_mt5, 0, count)
        mt5_executor.disconnect()
        if rates is None or len(rates) == 0:
            return None
        import pandas as pd
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df.set_index('time')
        df = df.rename(columns={'tick_volume': 'volume'})[['open', 'high', 'low', 'close', 'volume']]
        return df.dropna()
    except Exception:
        return None


def get_htf_data() -> dict:
    """ดึงข้อมูล H1, H4, Daily — MT5 ก่อน (real-time), fallback yfinance"""
    result = {}

    # MT5 timeframe map
    try:
        import MetaTrader5 as mt5
        mt5_map = {
            "H1":    (mt5.TIMEFRAME_H1,  200),
            "H4":    (mt5.TIMEFRAME_H4,  200),
            "Daily": (mt5.TIMEFRAME_D1,  200),
        }
    except Exception:
        mt5_map = {}

    yf_map = {
        "H1":     ("5d",   "1h"),
        "H4":     ("30d",  "4h"),
        "Daily":  ("90d",  "1d"),
        "Weekly": ("730d", "1wk"),
    }

    ticker = yf.Ticker("GC=F")

    for tf_name in ["H1", "H4", "Daily", "Weekly"]:
        df = None

        # ลอง MT5 ก่อน (ยกเว้น Weekly)
        if tf_name in mt5_map:
            tf_mt5, count = mt5_map[tf_name]
            df = _get_mt5_htf(tf_mt5, count)
            if df is not None:
                print(f"[bias] {tf_name} ← MT5 ({len(df)} bars)")

        # fallback yfinance
        if df is None and tf_name in yf_map:
            try:
                period, interval = yf_map[tf_name]
                df = ticker.history(period=period, interval=interval)
                if not df.empty:
                    df.columns = [c.lower() for c in df.columns]
                    df = df[['open', 'high', 'low', 'close', 'volume']].dropna()
                    print(f"[bias] {tf_name} ← yfinance ({len(df)} bars)")
                else:
                    df = None
            except Exception:
                df = None

        if df is None:
            result[tf_name] = {"error": "no data"}
            continue

        try:
            smc_result = smc.analyze(df)
            current_price = round(df['close'].iloc[-1], 2)
            summary = summarize(smc_result, current_price)
            result[tf_name] = {
                "bias":          summary["bias"],
                "last_bos":      summary["last_bos"],
                "last_choch":    summary["last_choch"],
                "last_sweep":    summary["last_sweep"],
                "active_ob":     summary["active_ob"],
                "equal_highs":   summary["equal_highs"],
                "equal_lows":    summary["equal_lows"],
                "current_price": current_price,
            }
        except Exception as e:
            result[tf_name] = {"error": str(e)}

    return result


def _vote_from_direction(trade_direction: str, signal_direction: str | None) -> str:
    """derive vote จาก trade_direction และ signal ที่ Chart แนะนำ"""
    if not signal_direction or signal_direction == "NO_TRADE":
        return "NO"
    if trade_direction == "BOTH":
        return "YES"
    if trade_direction == "NO_TRADE":
        return "NO"
    if trade_direction == "BUY_ONLY" and signal_direction == "BUY":
        return "YES"
    if trade_direction == "SELL_ONLY" and signal_direction == "SELL":
        return "YES"
    return "NO"  # counter-trend


def _fast_bias(htf_data: dict, signal_direction: str | None = None) -> dict | None:
    """
    Fast path: ถ้า bias ทุก TF ตรงกันชัดเจน → ไม่ต้องเรียก Claude
    คืน None ถ้า ambiguous (ต้องให้ Claude ช่วย)
    """
    biases = {tf: htf_data.get(tf, {}).get("bias", "neutral")
              for tf in ["Daily", "H4", "H1"]}

    bull_count = sum(1 for b in biases.values() if b == "bullish")
    bear_count = sum(1 for b in biases.values() if b == "bearish")

    if bull_count == 3:
        td = "BUY_ONLY"
        vote = _vote_from_direction(td, signal_direction)
        return {
            "overall_bias": "bullish", "bias_strength": "strong",
            "daily_bias": "bullish", "h4_bias": "bullish", "h1_bias": "bullish",
            "aligned": True, "trade_direction": td,
            "key_levels": [],
            "vote": vote,
            "vote_reasoning": f"ทุก TF bullish — {'สนับสนุน BUY' if vote=='YES' else 'ไม่สนับสนุน SELL counter-trend'}",
            "reasoning": "ทุก TF bullish — เทรด Long เท่านั้น",
            "claude_called": False,
        }
    if bear_count == 3:
        td = "SELL_ONLY"
        vote = _vote_from_direction(td, signal_direction)
        return {
            "overall_bias": "bearish", "bias_strength": "strong",
            "daily_bias": "bearish", "h4_bias": "bearish", "h1_bias": "bearish",
            "aligned": True, "trade_direction": td,
            "key_levels": [],
            "vote": vote,
            "vote_reasoning": f"ทุก TF bearish — {'สนับสนุน SELL' if vote=='YES' else 'ไม่สนับสนุน BUY counter-trend'}",
            "reasoning": "ทุก TF bearish — เทรด Short เท่านั้น",
            "claude_called": False,
        }
    if bull_count == 2 and biases.get("Daily") == "bullish":
        td = "BUY_ONLY"
        vote = _vote_from_direction(td, signal_direction)
        return {
            "overall_bias": "bullish", "bias_strength": "moderate",
            "daily_bias": biases["Daily"], "h4_bias": biases["H4"], "h1_bias": biases["H1"],
            "aligned": False, "trade_direction": td,
            "key_levels": [],
            "vote": vote,
            "vote_reasoning": f"Daily+H4 bullish แต่ H1 ขัด — {'ยัง OK สำหรับ BUY' if vote=='YES' else 'ไม่สนับสนุน SELL'}",
            "reasoning": "Daily+H4 bullish, H1 ขัด → ยังเทรด Long แต่ระวัง",
            "claude_called": False,
        }
    if bear_count == 2 and biases.get("Daily") == "bearish":
        h1_is_bull = biases.get("H1") == "bullish"
        h4_is_bear = biases.get("H4") == "bearish"
        if h1_is_bull and h4_is_bear:
            # H4 BEAR + H1 BULL → H1 พลิก momentum กำลังเปลี่ยน → ไม่แนะนำ SELL
            td   = "BOTH"
            vote = _vote_from_direction(td, signal_direction)
            return {
                "overall_bias": "bearish", "bias_strength": "weak",
                "daily_bias": biases["Daily"], "h4_bias": biases["H4"], "h1_bias": biases["H1"],
                "aligned": False, "trade_direction": td,
                "key_levels": [],
                "vote": vote,
                "vote_reasoning": "H4 BEAR แต่ H1 พลิก BULL — momentum เริ่มเปลี่ยน ระวัง SELL / follow H1 ปลอดภัยกว่า",
                "reasoning": "H4 BEAR + H1 BULL = mixed แบบอันตราย H1 ที่ fresh กว่าบอกว่าอาจกลับตัว → ไม่แนะนำ SELL aggressive",
                "h1_conflict_warning": True,
                "claude_called": False,
            }
        td = "SELL_ONLY"
        vote = _vote_from_direction(td, signal_direction)
        return {
            "overall_bias": "bearish", "bias_strength": "moderate",
            "daily_bias": biases["Daily"], "h4_bias": biases["H4"], "h1_bias": biases["H1"],
            "aligned": False, "trade_direction": td,
            "key_levels": [],
            "vote": vote,
            "vote_reasoning": f"Daily+H4 bearish แต่ H1 ขัด — {'ยัง OK สำหรับ SELL' if vote=='YES' else 'ไม่สนับสนุน BUY'}",
            "reasoning": "Daily+H4 bearish, H1 ขัด → ยังเทรด Short แต่ระวัง",
            "claude_called": False,
        }

    return None  # ambiguous → ต้องใช้ Claude


def analyze(force: bool = False, signal_direction: str | None = None) -> dict:
    """
    วิเคราะห์ HTF bias — cache 1 ชั่วโมง
    signal_direction: "BUY"/"SELL" จาก Chart Analyst → ใช้โหวต YES/NO ตรงๆ
    Fast path: bias ชัด → derive vote จาก direction (ไม่เรียก Claude)
    Slow path: bias ขัดแย้ง → Sonnet วิเคราะห์ + โหวต
    """
    global _cache

    if not force and _cache["result"] and _cache["timestamp"]:
        age = (datetime.now() - _cache["timestamp"]).total_seconds() / 60
        if age < CACHE_MINUTES:
            cached = dict(_cache["result"])
            # re-derive vote ถ้า signal_direction เปลี่ยน (cache bias แต่ vote fresh)
            if signal_direction and "trade_direction" in cached:
                cached["vote"] = _vote_from_direction(cached["trade_direction"], signal_direction)
            cached["from_cache"] = True
            cached["cache_age_min"] = round(age)
            return cached

    htf_data = get_htf_data()

    # ── Fast path ─────────────────────────────────────────────
    fast = _fast_bias(htf_data, signal_direction)
    if fast:
        fast["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fast["raw_htf"] = htf_data
        fast["from_cache"] = False
        _cache["result"] = fast
        _cache["timestamp"] = datetime.now()
        return fast

    # ── Slow path: Claude Sonnet ──────────────────────────────
    signal_ctx = f"Chart Analyst เสนอเข้า: {signal_direction}" if signal_direction else "ยังไม่มี signal direction"

    # สรุป key data แต่ละ TF ให้ compact
    def _tf_summary(tf: str) -> str:
        d = htf_data.get(tf, {})
        if "error" in d:
            return f"[{tf}] ERROR"
        return (
            f"[{tf}] bias={d.get('bias','?')} | price={d.get('current_price','?')} | "
            f"OB={d.get('active_ob','–')} | CHoCH={d.get('last_choch','–')} | "
            f"Sweep={d.get('last_sweep','–')} | EQH={d.get('equal_highs','–')} | EQL={d.get('equal_lows','–')}"
        )

    htf_lines = "\n".join(_tf_summary(tf) for tf in ["Weekly","Daily","H4","H1"])

    prompt = f"""คุณคือ Bias Analyst Agent — วิเคราะห์ภาพใหญ่ของ XAUUSD จาก Weekly/Daily/H4/H1
{signal_ctx}

══════ HTF Data ══════
{htf_lines}

══════ หน้าที่ของคุณ ══════

① อ่าน macro trend จากภาพใหญ่ (Weekly > Daily > H4 > H1)
   — ตอนนี้อยู่ใน trend ไหน? Bear run มานานแค่ไหน? ราคามาไกลแค่ไหนแล้ว?

② ⚠️ เช็ค H1 vs H4 conflict ก่อน (สำคัญมาก)
   H1 เป็น timeframe ที่ fresh กว่า H4 — ถ้า H1 พลิกทิศ แสดงว่า momentum กำลังเปลี่ยน

   กรณี H4 BEAR + H1 BULL (mixed):
   → H1 กำลังบอกว่า short-term momentum เริ่มกลับ
   → ⛔ อย่า follow H4 BEAR แบบ aggressive — เสี่ยงสูงที่จะ sell ตอน market กลับตัว
   → ✅ ให้ follow H1 BULL แทน (safer) หรืออย่างน้อยลด lot SELL ลงมาก
   → trade_direction = BOTH หรือ BUY_ONLY (ขึ้นกับ Daily/Weekly)
   → bias_strength = weak สำหรับ SELL direction

   กรณี H4 BULL + H1 BEAR (mixed):
   → H1 กำลัง pullback ใน uptrend — ยังไม่กลับทิศ
   → SELL ได้ถ้าอยู่ที่ Supply zone แต่ระวัง
   → trade_direction = BOTH (ระวัง)

   กรณี H4 + H1 ตรงกัน (strong trend):
   → follow trend ได้เต็มที่

③ ตรวจสอบ Demand/Supply Zone ของ HTF
   — มี OB, EQH/EQL, หรือ Key Level สำคัญของ H4/Daily/Weekly ที่ราคากำลังถึงหรือไม่?
   — ถ้าราคาเข้าสู่ Daily/Weekly Demand zone (ใน downtrend) → โอกาส rebound มีสูง
   — ถ้าราคาเข้าสู่ Daily/Weekly Supply zone (ใน uptrend) → โอกาส pullback มีสูง

④ ประเมิน Signal ที่เสนอ
   สถานการณ์ที่ vote YES ได้:
   A) signal ตรงกับ macro trend + H1 และ H4 ตรงกัน → YES เต็มที่
   B) H4 BEAR แต่ H1 BULL + signal เป็น BUY → YES (follow H1 ที่ fresh กว่า)
   C) signal สวนทาง แต่ราคาถึง HTF Demand/Supply zone สำคัญ → YES ได้

   สถานการณ์ที่ vote NO:
   D) H4 BEAR + H1 BULL แต่ signal เป็น SELL → NO (H1 บอกว่ากำลังกลับ อย่า SELL)
   E) signal สวนทาง และราคายังไม่ถึง HTF level ใดเลย → NO

══════ เกณฑ์ vote ══════
YES — ถ้า A, B หรือ C (ระบุว่าเป็น case ไหน + H1/H4 conflict หรือไม่)
NO  — ถ้า D หรือ E (ระบุชัดว่า H1/H4 ขัดยังไง)

ตอบ JSON เท่านั้น:
{{
  "vote": "YES/NO",
  "vote_reasoning": "1-2 ประโยค — Case A/B/C/D + level สำคัญที่เกี่ยวข้อง",
  "case": "A/B/C/D",
  "at_htf_level": true/false,
  "htf_level_detail": "ชื่อ level + TF + ราคา เช่น Daily Demand OB 4050-4080 หรือ null",
  "overall_bias": "bullish/bearish/neutral",
  "bias_strength": "strong/moderate/weak",
  "weekly_bias": "bullish/bearish/neutral",
  "daily_bias": "bullish/bearish/neutral",
  "h4_bias": "bullish/bearish/neutral",
  "h1_bias": "bullish/bearish/neutral",
  "trade_direction": "BUY_ONLY/SELL_ONLY/BOTH/NO_TRADE",
  "key_levels": [{{"level": ราคา, "type": "demand/supply/ob/eqh/eql", "timeframe": "Weekly/Daily/H4/H1", "note": "ใกล้/ถึงแล้ว/ห่างอยู่"}}],
  "reasoning": "ภาษาไทย — ระบุ macro trend, ระยะที่วิ่งมา, level ที่เกี่ยวข้อง และเหตุผลที่ vote"
}}"""

    # ── Prompt Caching: split static rules → system (cached), dynamic HTF data → user ──
    _SPLIT = "══════ หน้าที่ของคุณ ══════"
    if _SPLIT in prompt:
        _idx = prompt.index(_SPLIT)
        _usr, _sys = prompt[:_idx], prompt[_idx:]
    else:
        _usr, _sys = prompt, ""

    response = client.messages.create(
        model=MODEL_SMART,
        max_tokens=800,
        system=[{"type": "text", "text": _sys, "cache_control": {"type": "ephemeral"}}] if _sys else None,
        messages=[{"role": "user", "content": _usr}]
    )

    result = safe_json_parse(
        response.content[0].text,
        fallback={
            "overall_bias": "neutral", "bias_strength": "weak",
            "vote": "NO", "vote_reasoning": "JSON parse error",
            "aligned": False, "trade_direction": "BOTH",
            "case": "D", "at_htf_level": False, "htf_level_detail": None,
            "weekly_bias": "neutral", "key_levels": [],
            "reasoning": "Parse error — ระวังด้วย"
        }
    )

    result["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["raw_htf"] = htf_data
    result["from_cache"] = False
    result["claude_called"] = True

    _cache["result"] = result
    _cache["timestamp"] = datetime.now()
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
