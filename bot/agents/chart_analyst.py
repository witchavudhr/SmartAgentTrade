import yfinance as yf
import anthropic
import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from config.settings import ANTHROPIC_API_KEY, MODEL_SMART, MODEL_FAST, TRADING_PAIR
from agents.smc_engine import SMCEngine, summarize
from agents.json_utils import safe_json_parse

_CACHE_PATH = Path(__file__).parent.parent / "data" / "ai_cache.json"

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
smc     = SMCEngine(swing_length=2)   # M5 OB engine — swing สั้น จับ OB entry zone ใกล้ราคา
smc_m15 = SMCEngine(swing_length=5)  # M15 OB engine — swing ใหญ่ จับ structural OB

def _get_mt5_price() -> float | None:
    """ดึงราคา ask/bid ล่าสุดจาก MT5 ถ้าเชื่อมอยู่"""
    try:
        from agents import mt5_executor
        from config.settings import MT5_SYMBOL
        if not mt5_executor.is_available():
            return None
        try:
            import MetaTrader5 as mt5
            ok, _ = mt5_executor._connect()
            if not ok:
                return None
            tick = mt5.symbol_info_tick(MT5_SYMBOL)
            mt5_executor.disconnect()
            if tick:
                return round((tick.bid + tick.ask) / 2, 2)
        except Exception:
            pass
    except Exception:
        pass
    return None


def _get_mt5_ohlcv(timeframe_mt5, count: int) -> pd.DataFrame | None:
    """ดึง OHLCV จาก MT5 โดยตรง"""
    try:
        from agents import mt5_executor
        from config.settings import MT5_SYMBOL
        import MetaTrader5 as mt5
        ok, _ = mt5_executor._connect()
        if not ok:
            return None
        rates = mt5.copy_rates_from_pos(MT5_SYMBOL, timeframe_mt5, 0, count)
        mt5_executor.disconnect()
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df.set_index('time')
        df = df.rename(columns={'tick_volume': 'volume'})[['open', 'high', 'low', 'close', 'volume']]
        return df.dropna()
    except Exception:
        return None


def get_price_data(pair: str = TRADING_PAIR, period: str = "5d", interval: str = "5m") -> tuple[pd.DataFrame, dict]:
    """
    ดึงข้อมูลราคา Gold — M15 (OB zone) + M5 (entry timing)
    ถ้า MT5 เชื่อมอยู่ → ดึงจาก MT5 (real-time)
    fallback → yfinance (delay ~15 นาที)
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── ลองดึงจาก MT5 ก่อน ────────────────────────────────────────
    df15, df5 = None, None
    price_source = "yfinance"
    try:
        import MetaTrader5 as mt5
        df15 = _get_mt5_ohlcv(mt5.TIMEFRAME_M15, 400)
        df5  = _get_mt5_ohlcv(mt5.TIMEFRAME_M5,  500)
        if df15 is not None and df5 is not None:
            price_source = "MT5"
    except Exception:
        pass

    # ── Fallback: yfinance ─────────────────────────────────────────
    if df5 is None:
        ticker = yf.Ticker("GC=F")
        df_yf15 = ticker.history(period="10d", interval="15m")
        if not df_yf15.empty:
            df_yf15.columns = [c.lower() for c in df_yf15.columns]
            df15 = df_yf15[['open', 'high', 'low', 'close', 'volume']].dropna()
        df_yf5 = ticker.history(period=period, interval=interval)
        if df_yf5.empty:
            return None, None
        df_yf5.columns = [c.lower() for c in df_yf5.columns]
        df5 = df_yf5[['open', 'high', 'low', 'close', 'volume']].dropna()

    # ── M15 summary (swing_length=2 ตรงกับ EA OB_SWING_STR=2) ────────
    m15_summary = None
    if df15 is not None and not df15.empty:
        res15 = smc_m15.analyze(df15)
        m15_summary = summarize(res15, round(df15['close'].iloc[-1], 2))
        m15_summary["timeframe"] = "M15"

    # ── M5 summary ─────────────────────────────────────────────────
    # ใช้ราคาจาก MT5 tick ถ้าได้ (แม่นที่สุด)
    mt5_price = _get_mt5_price() if price_source == "MT5" else None
    current_price = mt5_price or round(df5['close'].iloc[-1], 2)

    res5    = smc.analyze(df5)
    summary = summarize(res5, current_price, df5)
    summary["pair"]         = pair
    summary["timeframe"]    = "M5"
    summary["analyzed_at"]  = now_str
    summary["price_source"] = price_source
    summary["m15"]          = m15_summary

    return df5, summary

def has_signal(smc_summary: dict, force_session: bool = False) -> bool:
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
    if not force_session and not smc_summary.get("tradeable_session", True):
        print(f"[has_signal] ❌ OFF-HOURS — session={smc_summary.get('session',{}).get('session','?')}")
        return False  # Off-hours — ไม่เทรด (bypass ด้วย force_session=True)

    # ── ชั้น 2: ราคาอยู่ใน OB → ผ่านทันที (OB-first logic) ──────
    # ใช้ M15 OB เป็น primary (significant zones) + M5 เป็น fallback
    m15_data = smc_summary.get("m15") or {}
    bull_ob = m15_data.get("active_bull_ob") or smc_summary.get("active_bull_ob") or {}
    bear_ob = m15_data.get("active_bear_ob") or smc_summary.get("active_bear_ob") or {}
    if bull_ob.get("in_ob") or bear_ob.get("in_ob"):
        print(f"[has_signal] ✅ IN_OB (M15) — bull_in={bull_ob.get('in_ob')} bear_in={bear_ob.get('in_ob')}")
        return True

    # ── ชั้น 3: TREND setup (priority รอง) ───────────────────────
    has_sweep     = smc_summary.get("last_sweep") is not None
    has_ob        = bool(bull_ob) or bool(bear_ob) or smc_summary.get("active_ob") is not None
    has_structure = (smc_summary.get("last_bos") is not None or
                     smc_summary.get("last_choch") is not None)
    bias = smc_summary.get("bias", "neutral")
    score = sum([has_sweep, has_ob, has_structure])

    if score >= 2 and bias != "neutral":
        print(f"[has_signal] ✅ TREND — sweep={has_sweep} ob={has_ob} struct={has_structure} bias={bias} score={score}/3")
        return True  # Trend setup viable — ให้ Claude วิเคราะห์ตำแหน่ง OB ต่อ

    # ── ชั้น 3: Swing Entry signal (fallback เมื่อ trend ไม่ครบ) ──
    rev = smc_summary.get("reversal", {})
    if rev.get("swing_signal") and rev.get("swing_score", 0) >= 3:
        print(f"[has_signal] ✅ SWING — signal={rev.get('swing_signal')} score={rev.get('swing_score')}")
        return True

    # ── ชั้น 4: Type C indicator signal ──────────────────────────
    signal_type = smc_summary.get("signal_type")
    if signal_type and "C_" in str(signal_type):
        print(f"[has_signal] ✅ TYPE_C — signal_type={signal_type}")
        return True

    # ── ชั้น 5: EQL/EQH Liquidity Sweep signal ───────────────
    eql_sweep = smc_summary.get("eql_sweep_signal")
    eqh_sweep = smc_summary.get("eqh_sweep_signal")
    if eql_sweep or eqh_sweep:
        print(f"[has_signal] ✅ EQL/EQH SWEEP — eql={eql_sweep} eqh={eqh_sweep}")
        return True

    # ── ชั้น 6: AMD Pattern (Range→Sweep→CHoCH→BOS) ──────────
    amd = smc_summary.get("amd", {})
    if amd.get("amd_signal") and amd.get("amd_score", 0) >= 4:
        print(f"[has_signal] ✅ AMD — {amd.get('amd_signal')} {amd.get('amd_stars','')} score={amd.get('amd_score')}")
        return True

    print(f"[has_signal] ❌ NO_SIGNAL — sweep={has_sweep} ob={has_ob} struct={has_structure} bias={bias} score={score}/3 bull_ob={bool(bull_ob)} bear_ob={bool(bear_ob)}")
    return False


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
        result = safe_json_parse(resp.content[0].text, fallback={"confidence": 0, "reasoning": "parse error"})
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

    momentum_bull = adv.get("momentum_bull", False)
    momentum_bear = adv.get("momentum_bear", False)

    # คำนวณระยะห่าง OB จริงๆ ก่อนสร้าง prompt
    price_now = float(smc_summary.get("price") or smc_summary.get("current_price") or 0)

    # Swing levels คำนวณโดย code (ไม่ใช่ LLM) — ใช้เป็น TP hint
    nearest_sh      = smc_summary.get("nearest_swing_high")
    nearest_sl_code = smc_summary.get("nearest_swing_low")
    sh_above        = smc_summary.get("swing_highs_above", [])
    sl_below        = smc_summary.get("swing_lows_below", [])
    _sh_pts  = round((nearest_sh - price_now) * 10, 0) if nearest_sh else None
    _sl_pts  = round((price_now - nearest_sl_code) * 10, 0) if nearest_sl_code else None
    swing_hint = (
        f"🎯 Swing Levels (คำนวณโดย code — ใช้เป็น TP hint):\n"
        f"  Nearest Swing High (above): {nearest_sh or 'N/A'}"
        + (f" ({int(_sh_pts):,} จุด)" if _sh_pts else "") + "\n"
        f"  Nearest Swing Low  (below): {nearest_sl_code or 'N/A'}"
        + (f" ({int(_sl_pts):,} จุด)" if _sl_pts else "") + "\n"
        f"  Swing Highs above (top 3): {sh_above or 'N/A'}\n"
        f"  Swing Lows  below (top 3): {sl_below or 'N/A'}"
    )
    _bear_ob   = smc_summary.get("active_bear_ob") or {}
    _bull_ob   = smc_summary.get("active_bull_ob") or {}
    dist_to_bear_ob = round(abs(price_now - _bear_ob.get("bottom", price_now + 9999)) * 10, 1) if _bear_ob else 9999
    dist_to_bull_ob = round(abs(price_now - _bull_ob.get("top",    price_now + 9999)) * 10, 1) if _bull_ob else 9999
    # ถ้าราคาอยู่ใน OB แล้ว (in_ob=True) → near = True เสมอ ไม่ว่า dist จะวัดได้เท่าไหร่
    near_bear_ob = bool(_bear_ob and (dist_to_bear_ob <= 30 or _bear_ob.get("in_ob")))
    near_bull_ob = bool(_bull_ob and (dist_to_bull_ob <= 30 or _bull_ob.get("in_ob")))

    momentum_warn = ""
    _momentum_filter_msg = "✅ ไม่มี strong momentum — วิเคราะห์ OB ตามปกติ"
    if momentum_bear:
        if near_bull_ob:
            momentum_warn = f"⚡ MOMENTUM BEAR แรง — ราคาถึง Bull OB แล้ว → BUY ที่นี่ได้"
            _momentum_filter_msg = f"MOMENTUM BEAR แรง — ราคาถึง/อยู่ใน Bull OB แล้ว\n✅ BUY ที่ demand zone นี้ได้ — momentum พาราคามาถึงปลายทางแล้ว\n🚫 ห้าม SELL สวน"
        else:
            momentum_warn = f"🚫 MOMENTUM BEAR แรง — ยังไม่ถึง Bull OB ({dist_to_bull_ob:.0f}p) ห้าม BUY กลางอากาศ"
            _momentum_filter_msg = f"🚫 MOMENTUM BEAR แรง — ยังไม่ถึง Bull OB (ห่าง {dist_to_bull_ob:.0f}p)\nห้าม BUY กลางอากาศ รอให้ราคาถึง Bull OB ก่อน\nห้าม SELL สวน momentum เช่นกัน"
    if momentum_bull:
        if near_bull_ob:
            # Trend-aligned: bull momentum + ราคาที่ Bull OB = TREND_OB setup ที่ดีที่สุด
            momentum_warn = f"⚡ MOMENTUM BULL แรง + อยู่ที่ Bull OB → TREND_OB BUY setup!"
            _momentum_filter_msg = f"MOMENTUM BULL แรง — ราคาอยู่ที่ Bull OB (demand zone)\n✅✅ BUY ได้เลย — bull momentum + bull OB = TREND_OB สัญญาณแข็งที่สุด\n🚫 ห้าม SELL โดยเด็ดขาด"
        elif near_bear_ob:
            momentum_warn = f"⚡ MOMENTUM BULL แรง — ราคาถึง Bear OB แล้ว → SELL ที่นี่ได้"
            _momentum_filter_msg = f"MOMENTUM BULL แรง — ราคาถึง/อยู่ใน Bear OB แล้ว\n✅ SELL ที่ supply zone นี้ได้ — momentum พาราคามาถึงปลายทางแล้ว\n🚫 ห้าม BUY เพิ่ม"
        else:
            momentum_warn = f"🚫 MOMENTUM BULL แรง — ยังไม่ถึง Bear OB ({dist_to_bear_ob:.0f}p) ห้าม SELL กลางอากาศ"
            _momentum_filter_msg = f"🚫 MOMENTUM BULL แรง — ยังไม่ถึง Bear OB (ห่าง {dist_to_bear_ob:.0f}p) ยังไม่ถึง Bull OB ใหม่\nห้าม SELL กลางอากาศ ห้าม BUY กลางอากาศ รอ OB"

    rev_signal = rev.get("swing_signal") or rev.get("reversal_signal")
    rev_block  = ""
    if rev_signal:
        rev_block = f"""
─── 🔀 M5 SWING ENTRY DETECTED ───
Direction: {rev_signal} | Score: {rev.get('swing_score') or rev.get('reversal_score')}/10 {rev.get('swing_stars') or rev.get('reversal_stars','')}
Entry Zone: {rev.get('entry_zone')} | SL: {rev.get('stop_loss')} | TP: {rev.get('take_profit')} | RR: 1:{rev.get('rr')}
TP = next swing high/low เท่านั้น (ไม่คาด trend กลับ)
"""

    # EQL/EQH sweep signals (computed by smc_engine)
    eql_sweep = smc_summary.get("eql_sweep_signal")
    eqh_sweep = smc_summary.get("eqh_sweep_signal")

    # AMD pattern (Range → Sweep → CHoCH → BOS)
    amd = smc_summary.get("amd") or {}

    sweep_l_age = adv.get('sweep_l_age_bars') or 999
    sweep_h_age = adv.get('sweep_h_age_bars') or 999
    choch_age   = adv.get('choch_age_bars')   or 999
    h1_bull     = adv.get('h1_bull', False)
    h4_bull     = adv.get('h4_bull', False)
    macro_bias  = "BULL" if (h1_bull and h4_bull) else "BEAR" if (not h1_bull and not h4_bull) else "MIXED"

    # ── M5 + M15 OB — เลือก Primary OB ที่ใกล้ราคาที่สุด ────────────
    def _ob_overlap(ob_a: dict | None, ob_b: dict | None) -> dict | None:
        if not ob_a or not ob_b:
            return None
        lo = max(ob_a['bottom'], ob_b['bottom'])
        hi = min(ob_a['top'],    ob_b['top'])
        if hi > lo:
            return {"bottom": round(lo, 2), "top": round(hi, 2)}
        return None

    def _ob_dist(ob: dict | None, price: float) -> float:
        """ระยะห่างจากราคา → OB (0 ถ้าอยู่ใน OB แล้ว)"""
        if not ob:
            return 999999
        if ob.get('in_ob'):
            return 0
        top = ob.get('top', price)
        bot = ob.get('bottom', price)
        return min(abs(price - top), abs(price - bot))

    m5_bull_ob  = smc_summary.get('active_bull_ob')
    m5_bear_ob  = smc_summary.get('active_bear_ob')
    m15_bull_ob = m15.get('active_bull_ob')
    m15_bear_ob = m15.get('active_bear_ob')

    bull_confluence = _ob_overlap(m5_bull_ob, m15_bull_ob)
    bear_confluence = _ob_overlap(m5_bear_ob, m15_bear_ob)

    # Primary OB = ใกล้ราคาที่สุดระหว่าง M5 กับ M15 (merged zone ถ้า overlap)
    # Bull OB ต้องอยู่ใต้ราคา (top ≤ price) — Bear OB ต้องอยู่เหนือราคา (bottom ≥ price)
    def _primary_ob(m5_ob, m15_ob, price, ob_type: str = 'bull'):
        def _valid(ob):
            if not ob: return False
            if ob.get('in_ob'): return True
            if ob_type == 'bull':
                return ob.get('top', price + 1) <= price   # demand zone ต้องอยู่ใต้ราคา
            else:
                return ob.get('bottom', price - 1) >= price  # supply zone ต้องอยู่เหนือราคา

        v5  = m5_ob  if _valid(m5_ob)  else None
        v15 = m15_ob if _valid(m15_ob) else None

        overlap = _ob_overlap(v5, v15)
        if overlap:
            overlap['tf'] = 'M5+M15'
            overlap['in_ob'] = (v5 or {}).get('in_ob') or (v15 or {}).get('in_ob')
            return overlap
        d5  = _ob_dist(v5,  price)
        d15 = _ob_dist(v15, price)
        if d15 <= d5 and v15:
            ob = dict(v15); ob['tf'] = 'M15'; return ob
        if v5:
            ob = dict(v5); ob['tf'] = 'M5'; return ob
        return None

    primary_bull_ob = _primary_ob(m5_bull_ob, m15_bull_ob, price_now, 'bull')
    primary_bear_ob = _primary_ob(m5_bear_ob, m15_bear_ob, price_now, 'bear')

    # อัพเดต dist_to_* ด้วย primary OB
    dist_to_bull_ob = round(_ob_dist(primary_bull_ob, price_now) * 10, 1)
    dist_to_bear_ob = round(_ob_dist(primary_bear_ob, price_now) * 10, 1)
    near_bull_ob = bool(primary_bull_ob and (dist_to_bull_ob <= 30 or (primary_bull_ob or {}).get('in_ob')))
    near_bear_ob = bool(primary_bear_ob and (dist_to_bear_ob <= 30 or (primary_bear_ob or {}).get('in_ob')))

    def _fmt_ob(ob: dict | None, in_ob_key: bool = False) -> str:
        if not ob:
            return "ไม่มี"
        tag = " ← IN OB ✅" if ob.get('in_ob') else ""
        tf  = f" [{ob.get('tf','?')}]" if ob.get('tf') else ""
        return f"{ob['bottom']}–{ob['top']}{tf}{tag}"

    def _fmt_conf(zone: dict | None) -> str:
        if not zone:
            return "ไม่มี (M5 กับ M15 ไม่ overlap)"
        return f"🔥 {zone['bottom']}–{zone['top']} (M5+M15 ซ้อนกัน — confluence สูง)"

    prompt = f"""คุณคือ Chart Analyst Agent — วิเคราะห์ XAUUSD หาจุดเข้า trade
จุดออก/trailing stop ใช้ EA — หน้าที่คุณคือหาจุดเข้าและวางแผนเข้าเท่านั้น

════════════════════════════════════════════
MARKET DATA
════════════════════════════════════════════
📌 Macro Bias (H1/H4):
  H1: {'▲ BULL' if h1_bull else '▼ BEAR'}  |  H4: {'▲ BULL' if h4_bull else '▼ BEAR'}  |  รวม: {macro_bias}
  (MIXED = ทั้งสอง TF ขัดกัน → โอกาส swing สูงทั้งสองทาง ดู OB เป็นหลัก)
  ราคาปัจจุบัน: {smc_summary.get('current_price')}
  Session: {sess.get('emoji','')} {sess.get('session','')} ({sess.get('time_thai','')})

🎯 PRIMARY OB (ใกล้ราคาที่สุด — ใช้เป็น entry zone หลัก):
  Primary Bull OB: {_fmt_ob(primary_bull_ob)} (ห่าง {dist_to_bull_ob:.0f} จุด)
  Primary Bear OB: {_fmt_ob(primary_bear_ob)} (ห่าง {dist_to_bear_ob:.0f} จุด)
  ⚠️ ใช้ Primary OB นี้เป็น entry zone เสมอ — ไม่ใช่ M5 หรือ M15 แยกกัน

📊 M15 — OB zones อ้างอิง:
  M15 Bull OB:  {_fmt_ob(m15_bull_ob)}
  M15 Bear OB:  {_fmt_ob(m15_bear_ob)}
  BOS:   {m15.get('last_bos','–')}  |  CHoCH: {m15.get('last_choch','–')}
  Sweep: {m15.get('last_sweep','–')}
  FVG:   {m15.get('nearest_fvg','–')}
  EQH/EQL: {m15.get('equal_highs','–')} / {m15.get('equal_lows','–')}
  🔍 EQL Sweep Signal: {eql_sweep or 'ไม่มี'}
  🔍 EQH Sweep Signal: {eqh_sweep or 'ไม่มี'}
  🎯 AMD Pattern: {amd.get('amd_signal') or 'ไม่มี'} {amd.get('amd_stars','') or ''} (phase={amd.get('amd_phase','?')} type={amd.get('amd_type','?')} score={amd.get('amd_score',0)})
    Range: {amd.get('amd_range_bottom','?')}–{amd.get('amd_range_top','?')} | Sweep: {amd.get('amd_sweep_level','?')}
    Reasons: {', '.join(amd.get('amd_reasons',[]) or ['–'])}

📍 M5 — entry detail:
  M5 Bull OB:  {_fmt_ob(m5_bull_ob)}
  M5 Bear OB:  {_fmt_ob(m5_bear_ob)}
  M5 FVG:      {smc_summary.get('nearest_fvg','–')}
  CHoCH M5:    {smc_summary.get('last_choch','–')} ({choch_age} bars ago)
  Sweep Low:   {adv.get('recent_sweep_low','–')} ({sweep_l_age} bars ago)
  Sweep High:  {adv.get('recent_sweep_high','–')} ({sweep_h_age} bars ago)
  Confirm:     Bull={adv.get('bull_candle')} Bear={adv.get('bear_candle')}
  {momentum_warn}

⭐ OB Confluence (M5 ∩ M15):
  Bull zone: {_fmt_conf(bull_confluence)}
  Bear zone: {_fmt_conf(bear_confluence)}

{swing_hint}

{rev_block if rev_signal else ''}

════════════════════════════════════════════
⛔ MOMENTUM FILTER
════════════════════════════════════════════
{_momentum_filter_msg}

กฎ: momentum = แรงที่พาราคาไปถึง OB ฝั่งตรงข้าม
- ถ้าราคายังไม่ถึง OB → ห้ามสวน momentum (กลางอากาศ = เสี่ยงสูง)
- ถ้าราคาถึง OB ฝั่งตรงข้ามแล้ว → trade ที่ OB ได้เลย (นั่นคือ supply/demand จริงๆ)

════════════════════════════════════════════
📌 หลักการหลัก: ซื้อแนวรับ ขายแนวต้าน
════════════════════════════════════════════
กฎข้อ 1 (สำคัญที่สุด): ตัดสินใจจาก OB ที่ราคาอยู่ใกล้ ไม่ใช่จาก macro bias
  → ราคาอยู่ที่/ใกล้ Bull OB (demand/support) → ดู BUY setup
  → ราคาอยู่ที่/ใกล้ Bear OB (supply/resistance) → ดู SELL setup
  → ราคากลางอากาศ (ไม่ถึง OB ไหนเลย) → NO_TRADE รอ

กฎข้อ 2 — Macro bias ใช้ปรับ confidence เท่านั้น:
  → trade ตาม trend + ตาม OB = confidence สูงสุด (BUY ที่ Bull OB ใน uptrend)
  → trade สวน trend แต่มี OB รองรับ = confidence ต่ำกว่า (SELL ที่ Bear OB ใน uptrend)
  → ห้าม trade สวน trend โดยไม่มี OB รองรับ = NO_TRADE

กฎข้อ 3 — ห้าม SELL กลางอากาศ (ไม่มี Bear OB):
  → H4+H1 BULL + EQH sweep แต่ไม่มี Bear OB ใกล้ → ห้าม SELL (ไม่มี resistance รองรับ)
  → EQH sweep ใน uptrend โดยไม่มี supply zone = AMD Upthrust → ดู BUY ที่ Bull OB แทน

ตัวอย่าง:
  H4 BULL + H1 BULL + ราคาที่ Bear OB 4316 → SELL ได้ (resistance จริง) confidence ปานกลาง
  H4 BULL + H1 BULL + ราคาที่ Bull OB 4300 → BUY ได้ (support + trend ตรงกัน) confidence สูง
  H4 BULL + H1 BULL + ราคากลางอากาศ 4312 (ไม่มี OB ใกล้) → NO_TRADE รอ OB

════════════════════════════════════════════
STEP 1 — Primary OB (code เลือกให้แล้ว)
════════════════════════════════════════════
Code คำนวณ Primary OB ให้แล้วด้านบน — เลือก OB ที่ใกล้ราคาที่สุดระหว่าง M5 กับ M15
(ถ้า overlap → merge เป็น zone เดียว, ถ้าไม่ overlap → เอาอันที่ใกล้กว่า)

  Primary Bull OB: {_fmt_ob(primary_bull_ob)} ห่าง {dist_to_bull_ob:.0f} จุด
  Primary Bear OB: {_fmt_ob(primary_bear_ob)} ห่าง {dist_to_bear_ob:.0f} จุด

→ ใช้ Primary OB นี้เป็น entry_zone เสมอ ไม่ต้องคำนวณใหม่

════════════════════════════════════════════
STEP 2 — วิเคราะห์ Setup ที่ OB นั้น
════════════════════════════════════════════

── ★ TREND-ALIGNED OB (ดีที่สุด — เตรียมเข้าได้เลย) ──────
OB ที่ใกล้สุด ตรงกับ macro trend:
  Bear OB ใกล้ + macro BEAR → SELL setup เตรียมได้เลย
  Bull OB ใกล้ + macro BULL → BUY setup เตรียมได้เลย

เงื่อนไข: ราคาอยู่ใน OB หรือ ≤300 จุด จาก OB edge
  - มี BOS ตาม trend + ราคา pullback มาที่ OB → เข้าได้เลย lot เต็ม
  - ยังไม่ pullback ถึง OB แต่กำลังมา → เตรียม limit order รอที่ OB
  - Sweep ไม่บังคับ (bonus +confidence ถ้ามี)
  - RR ≥ 1.5 | setup_type = TREND_OB
  confidence สูงสุดเพราะ: OB + macro + structure ตรงกันหมด

── ★★ BREAKER BLOCK / BOS RETEST (สำคัญมาก — อย่าพลาด) ──────
เมื่อ BOS ขึ้น (bullish) ทะลุผ่าน Bear OB ไปแล้ว → Bear OB นั้นกลายเป็น support (Breaker Block)
ราคา pull back มาทดสอบ Breaker Block นี้ = BUY setup ไม่ใช่ SELL

สัญญาณ:
  - มี BOS ขึ้น (ราคาเคยอยู่เหนือ Bear OB ที่ปัจจุบันเห็น)
  - ราคาตอนนี้ pull back มาใกล้ Bear OB นั้น (ด้านล่าง ≤150 จุด)
  - macro BULL (H4+H1 bullish)
  → vote BUY | setup_type = TREND_OB | confidence สูง

⛔ อย่าสับสน: Bear OB ที่เห็นอยู่เหนือราคาตอนนี้ = code เลือกให้ว่าเป็น resistance
  แต่ถ้าราคาเพิ่ง break ขึ้นไปเหนือมันแล้ว pull back ลงมา = Breaker Block = support = BUY
  ดูจาก: BOS ขึ้นล่าสุด + ราคาเคยอยู่เหนือ Bear OB zone + ตอนนี้ pull back มา

── CASE A: ใกล้ Bear OB แต่ macro ไม่ตรง หรือ MIXED ──
ราคาขึ้นสู่ supply zone → โอกาส SELL แต่ระวังมากขึ้น

  A1 — TREND_OB (macro BEAR + Bear OB):
    BOS ลง + pullback ถึง Bear OB + rejection candle
    → เข้า SELL | RR ≥ 1.5

  A2 — TREND_BOS_BREAK (momentum ผ่าน OB ไปแล้ว):
    BOS ลงชัด + ราคาผ่าน OB เกิน 300 จุด + มี FVG
    → ไม้ 1 ที่ FVG | รอ pullback ถึง Bear OB เป็นไม้ 2
    → pyramid_mode=true

── CASE B: ใกล้ Bull OB (Demand Zone) ──────────────
ราคาลงมาสู่ demand → โอกาส swing ขึ้น

  ⚠️ concept: ทุก trend มี swing ขึ้น-ลงอยู่เสมอ เราเล่น swing นั้น
  TP = ใช้ "Nearest Swing High (above)" จาก Swing Levels ด้านบนเป็นหลัก
       ถ้า Bear OB อยู่ระหว่าง entry กับ swing high → ใช้ Bear OB bottom เป็น TP แทน (conservative)
       ถ้า swing high ไกลกว่า Bear OB มาก (>500 จุด) → ใช้ swing high เป็น tp_extended

  🔥 B1 — BULL_OB_SWEEP_REJECT (สัญญาณดีที่สุดใน counter-trend):
    มี Sweep ต่ำกว่า OB + rejection แรง (wick ยาว / engulfing / strong close)
    → buyer ตอบสนองทันทีที่ demand = high probability swing
    → เข้าได้เลย lot ปกติ (50-60%) | pyramid ไม้ 2 ถ้า double-dip
    → setup_type = BULL_OB_SWEEP_REJECT

  📍 B2 — BULL_OB_ENTRY (ยังไม่ sweep):
    ราคาอยู่ใน Bull OB หรือ ≤200 จุด จาก top
    → ไม้ 1 เล็ก (30-40%) รอดู | SL ใต้ OB
    → ไม้ 2 ถ้า sweep เกิด (trade_monitor แจ้ง)
    → setup_type = BULL_OB_ENTRY, pyramid_mode=true

  ✅ B ผ่านถ้า: Bull OB unmitigated + RR ≥ 1.5
  ❌ B ไม่ผ่านถ้า: OB mitigated แล้ว หรือ RR < 1.5

  📐 Bear OB Distance Bonus:
    Bear OB คือ TP สูงสุดของ swing นี้
    ยิ่ง Bear OB ไกล → TP เพิ่ม + quality สูงขึ้น เพราะ:
      - ระหว่างทางมี liquidity ถูก sweep ไปเยอะแล้ว
      - market structure เอื้อให้ราคาวิ่งได้ไกล
    dist_bear_ob > 1,000 จุด → TP ขยายได้
    dist_bear_ob > 2,000 จุด → high conviction swing

── CASE C: EQL/EQH Liquidity Sweep Entry ────────────────
ไม่ต้องรอ OB — liquidity ถูก sweep ไปแล้ว ราคา bounce/reject ทันที

  🟢 C1 — EQL_SWEEP_BUY (ถ้า eql_sweep_signal ≠ null):
    EQL swept → ดูด sell-side liquidity → ราคากลับขึ้นมาเหนือ EQL
    เงื่อนไข: eql_level + sweep_low ชัดเจน + ราคาปัจจุบัน > eql_level
    Entry: ราคาปัจจุบัน (หรือ limit ที่ eql_level)
    SL: ต่ำกว่า sweep_low 5-10 จุด
    TP: nearest_swing_high ที่คำนวณโดย code (ด้านบน)
    → setup_type = EQL_SWEEP_BUY
    confidence สูงขึ้นถ้ามี bull confirm candle

  🔴 C2 — EQH_SWEEP_SELL (ถ้า eqh_sweep_signal ≠ null):
    EQH swept → ดูด buy-side liquidity → ราคา reject ลงมาต่ำกว่า EQH
    เงื่อนไข: eqh_level + sweep_high ชัดเจน + ราคาปัจจุบัน < eqh_level
    ⛔ ห้ามใช้ C2 ถ้า: H4 BULL และ H1 BULL และ **ไม่มี Bear OB** ใกล้ราคา
       → กลางอากาศ + uptrend = ไม่มี resistance รองรับ → NO_TRADE ดีกว่า
       → ถ้ามี Bear OB ใกล้ราคา → SELL ที่ Bear OB ได้ (resistance จริง) แม้ macro จะ BULL
    Entry: ราคาปัจจุบัน (หรือ limit ที่ eqh_level)
    SL: สูงกว่า sweep_high 5-10 จุด
    TP: nearest_swing_low ที่คำนวณโดย code (ด้านล่าง)
    → setup_type = EQH_SWEEP_SELL
    confidence สูงขึ้นถ้ามี bear confirm candle + macro BEAR หรือ MIXED เท่านั้น

── CASE D: AMD Pattern — Range → Sweep → CHoCH → BOS ───────────
ท่าเจอบ่อยที่สุด: ออกข้าง (Accumulation) → ดูด liquidity (Manipulation) → พลิกทิศ (Distribution)

  🟢 D1 — AMD_BUY / Spring (ถ้า amd_signal=AMD_BUY):
    EQL swept → ดูด sell stops → CHoCH Bull → BOS up → คนที่ short โดน squeeze
    Entry: ราคาปัจจุบัน หรือ pullback มา EQL level (ถ้ายังใกล้)
    SL: ต่ำกว่า amd_sweep_level 5-10 จุด (ใต้ wick sweep)
    TP: nearest_swing_high / Bear OB ถ้าไม่ไกลเกิน
    setup_type = AMD_BUY
    confidence สูงขึ้นถ้า: CHoCH fresh (≤5 bars) + BOS confirmed + bull candle

  🔴 D2 — AMD_SELL / Upthrust (ถ้า amd_signal=AMD_SELL):
    EQH swept → ดูด buy stops → CHoCH Bear → BOS down → คนที่ long โดน flush
    Entry: ราคาปัจจุบัน หรือ pullback มา EQH level
    SL: สูงกว่า amd_sweep_level 5-10 จุด (เหนือ wick sweep)
    TP: nearest_swing_low / Bull OB ถ้าไม่ไกลเกิน
    setup_type = AMD_SELL
    confidence สูงขึ้นถ้า: CHoCH fresh + BOS confirmed + bear candle

  ⚠️ AMD ★★★ (score≥8) = high conviction — เข้าได้ lot ปกติ
  ⚠️ AMD ★★  (score 5-7) = moderate — เข้าได้ lot เล็ก หรือรอ confirmation เพิ่ม

════════════════════════════════════════════
STEP 3 — ตัดสินใจและโหวต
════════════════════════════════════════════
หลักการ: ซื้อแนวรับ ขายแนวต้าน — ราคาต้องถึง OB ก่อนเสมอ ไม่ trade กลางอากาศ

ลำดับ priority:
0. ถ้าไม่มี OB ใกล้ราคา (ทั้ง Bull และ Bear ห่างเกิน 300 จุด) → NO_TRADE รอ
1. ★ ราคาที่ Bull OB + macro BULL → BUY, setup_type=TREND_OB (confidence สูงสุด — support+trend)
   ★ ราคาที่ Bear OB + macro BEAR → SELL, setup_type=TREND_OB (confidence สูงสุด — resistance+trend)
2. ★★ BOS ขึ้น + pullback มา Bear OB เดิม + macro BULL → BUY, setup_type=BREAKER_BLOCK (Breaker Block = support)
   ★★ BOS ลง + pullback มา Bull OB เดิม + macro BEAR → SELL, setup_type=BREAKER_BLOCK
3. CASE A + A2 ผ่าน → YES, setup_type=TREND_BOS_BREAK, pyramid_mode=true
4. CASE B + B1 (sweep+reject) → YES, setup_type=BULL_OB_SWEEP_REJECT
5. CASE B + B2 → YES, setup_type=BULL_OB_ENTRY, pyramid_mode=true
6. CASE C1 (eql_sweep_signal ≠ null) → YES, setup_type=EQL_SWEEP_BUY
7. CASE C2 (eqh_sweep_signal ≠ null) → YES, setup_type=EQH_SWEEP_SELL
8. CASE D1 (amd_signal=AMD_BUY) → YES, setup_type=AMD_BUY
9. CASE D2 (amd_signal=AMD_SELL) → YES, setup_type=AMD_SELL
10. ไม่มี OB ใกล้หรือเงื่อนไขไม่ผ่าน → NO, ระบุใน trade_plan ว่ารอราคาไปไหน

ตอบ JSON เท่านั้น:
{{
  "vote": "YES/NO",
  "vote_reasoning": "1-2 ประโยค — ระบุ Case A/B/C + zone + เหตุผล",
  "signal": "BUY/SELL/NO_TRADE",
  "setup_type": "TREND_OB/BREAKER_BLOCK/TREND_BOS_BREAK/BULL_OB_SWEEP_REJECT/BULL_OB_ENTRY/EQL_SWEEP_BUY/EQH_SWEEP_SELL/AMD_BUY/AMD_SELL/WAIT_FOR_OB/NO_TRADE",
  "trend_aligned": true ถ้า OB ที่ใกล้ตรงกับ macro trend หรือ false,
  "proximity_case": "A หรือ B",
  "pyramid_mode": true หรือ false,
  "pyramid_plan": "ไม้ 1/2/3 plan ถ้า pyramid_mode=true เช่น 'ไม้ 1 ที่ OB 4059 | ไม้ 2 ถ้า sweep | ไม้ 3 หลัง CHoCH'" หรือ null,
  "sweep_rejection": true ถ้ามี sweep + rejection แล้ว หรือ false,
  "dist_to_bear_ob_pts": number — ระยะห่างจากราคาถึง Bear OB (จุด),
  "dist_to_bull_ob_pts": number — ระยะห่างจากราคาถึง Bull OB (จุด),
  "confidence": 0-100,
  "entry_zone": [low, high] หรือ null,
  "stop_loss": ราคา หรือ null,
  "take_profit": ราคา หรือ null,
  "tp_extended": ราคา Bear OB ถ้า dist > 1,000 จุด และ swing ไปถึงได้ หรือ null,
  "rr_ratio": number หรือ null,
  "price_vs_ob": "AT_OB/APPROACHING/FAR",
  "trade_plan": "แผน step-by-step รวม pyramid + TP target",
  "key_factors": ["factor1", "factor2"],
  "reasoning": "ภาษาไทย: ① H1/H4 macro ② OB ที่ใกล้ที่สุดคือไหน ③ มี sweep+rejection มั้ย ④ setup ที่เลือก ⑤ pyramid plan ⑥ Bear OB distance + TP logic"
}}"""

    response = client.messages.create(
        model=MODEL_SMART,
        max_tokens=3500,   # Thai reasoning + pyramid_plan ยาว — 2000 ไม่พอ
        messages=[{"role": "user", "content": prompt}]
    )

    raw_text = response.content[0].text
    # Log raw response เพื่อ debug — ดูว่า Claude ตอบอะไร
    print(f"[ChartAnalyst] stop_reason={response.stop_reason} tokens={response.usage.output_tokens}")
    print(f"[ChartAnalyst] raw={raw_text[:300]}")

    result = safe_json_parse(
        raw_text,
        fallback={"signal": "NO_TRADE", "vote": "NO", "vote_reasoning": "JSON parse error — truncated response", "confidence": 0}
    )
    result["analyzed_at"]   = smc_summary.get("analyzed_at")
    result["current_price"] = smc_summary.get("current_price")
    result["smc_bias"]      = smc_summary.get("bias")
    result["had_sweep"]     = smc_summary.get("last_sweep") is not None
    result["reversal_score"] = smc_summary.get("reversal_score", 0)
    result["reversal_stars"] = smc_summary.get("reversal_stars")
    result["m15_bias"]      = m15.get("bias")
    result["claude_called"] = True

    def _ob_zone(ob):
        if not ob: return None
        return {"top": ob.get("top"), "bottom": ob.get("bottom"), "tf": ob.get("tf", ""), "in_ob": ob.get("in_ob", False)}

    result["bull_ob_zone"] = _ob_zone(primary_bull_ob)
    result["bear_ob_zone"] = _ob_zone(primary_bear_ob)

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
