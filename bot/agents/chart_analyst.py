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
smc     = SMCEngine(swing_length=50, ob_length=5)   # swing struct=50, OB internal=5 (LuxAlgo default)
smc_m15 = SMCEngine(swing_length=50, ob_length=5)

def _get_mt5_price() -> float | None:
    """ดึงราคา mid (bid+ask)/2 จาก MT5 — ไม่ต้องเช็ค is_available() เพราะ _connect() จัดการเอง"""
    try:
        import MetaTrader5 as mt5
        from agents import mt5_executor
        from config.settings import MT5_SYMBOL
        ok, _ = mt5_executor._connect()
        if not ok:
            return None
        tick = mt5.symbol_info_tick(MT5_SYMBOL)
        mt5_executor.disconnect()
        if tick:
            return round((tick.bid + tick.ask) / 2, 2)
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
        df15 = _get_mt5_ohlcv(mt5.TIMEFRAME_M15, 700)   # ~7 วัน (M15)
        df5  = _get_mt5_ohlcv(mt5.TIMEFRAME_M5,  2016)  # 7 วัน (M5: 288 bar/day × 7)
        if df15 is not None and df5 is not None:
            price_source = "MT5"
            try:
                from agents.bar_cache import save_bars, merge_with_cache, resample_m15, get_extended_history
                # เติม gap ที่ copy_rates_from_pos รอบนี้อาจดึงมาไม่ครบ ด้วยข้อมูล
                # ที่ cache สะสมไว้จาก scan สดรอบก่อนๆ (สะอาดกว่า ไม่มี gap)
                df5 = merge_with_cache(df5)
                # แล้วบันทึกแท่งสดของรอบนี้ (รวมของที่เพิ่ง merge มา) ลง cache ต่อ
                save_bars(df5)
                # ขยาย lookback ย้อนหลังไกลกว่า 7 วัน (สูงสุด 60 วัน — เท่ากับ
                # retention ของ cache เอง) เพื่อให้เห็น weekly/monthly BSL/SSL
                # pool เก่าที่ยังไม่ sweep ได้ครบเท่าที่ cache มีจริง
                df5 = get_extended_history(df5, max_days=60)
                # ใช้ M15 resample จาก M5 (extended + gap-fixed) แทน native M15 fetch
                # (native df15 จาก MT5 ยังมี gap 06:50-08:00 เหมือนกัน ไม่เคย merge)
                m15_resampled = resample_m15(df5)
                if m15_resampled is not None and not m15_resampled.empty:
                    df15 = m15_resampled
            except Exception:
                pass
    except Exception:
        pass

    # ── Fallback: yfinance ─────────────────────────────────────────
    if df5 is None:
        ticker = yf.Ticker("GC=F")
        df_yf15 = ticker.history(period="7d", interval="15m")
        if not df_yf15.empty:
            df_yf15.columns = [c.lower() for c in df_yf15.columns]
            df15 = df_yf15[['open', 'high', 'low', 'close', 'volume']].dropna()
        df_yf5 = ticker.history(period="7d", interval="5m")
        if df_yf5.empty:
            return None, None
        df_yf5.columns = [c.lower() for c in df_yf5.columns]
        df5 = df_yf5[['open', 'high', 'low', 'close', 'volume']].dropna()

    # ── M15 summary (swing_length=50 ตรงกับ LuxAlgo swingsLengthInput=50) ────────
    m15_summary = None
    res15 = None
    if df15 is not None and not df15.empty:
        res15 = smc_m15.analyze(df15)
        m15_summary = summarize(res15, round(df15['close'].iloc[-1], 2))
        m15_summary["timeframe"] = "M15"

    # ── M5 summary ─────────────────────────────────────────────────
    mt5_price = _get_mt5_price() if price_source == "MT5" else None
    current_price = mt5_price or round(df5['close'].iloc[-1], 2)

    res5    = smc.analyze(df5)
    summary = summarize(res5, current_price, df5)
    summary["pair"]         = pair
    summary["timeframe"]    = "M5"
    summary["analyzed_at"]  = now_str
    summary["price_source"] = price_source
    summary["m15"]          = m15_summary

    # ── Merge M5 + M15 weekly pools ────────────────────────────────
    # รวม SSL/BSL จากทั้งสองไทม์เฟรมเข้าด้วยกัน dedup โดย proximity 1.5 USD
    if res15 is not None:
        from agents.smc_engine import classify_liquidity as _cl
        m15_liq = _cl(res15, current_price, timeframe="M15", df=df15)
        m5_liq  = summary.get("liquidity", {})
        m5_bsl  = m5_liq.get("bsl_pools", [])
        m5_ssl  = m5_liq.get("ssl_pools", [])
        m15_bsl = m15_liq.get("bsl_pools", [])
        m15_ssl = m15_liq.get("ssl_pools", [])

        def _dedup_merge(a, b, proximity=1.5):
            """รวม pool list, ถ้าระดับใกล้กัน < proximity USD ให้เก็บแค่อันที่ดีกว่า (M15 > M5, major > minor)"""
            merged = list(a)
            for pb in b:
                close = [p for p in merged if abs(p["level"] - pb["level"]) < proximity]
                if not close:
                    merged.append(pb)
                else:
                    # ถ้า M15 level ยังไม่อยู่ใน list → replace minor ด้วย M15
                    for existing in close:
                        if pb["timeframe"] == "M15" and existing["timeframe"] == "M5":
                            existing["timeframe"] = "M15"
                            existing["size"] = "major" if existing["size"] == "major" or pb["size"] == "major" else "minor"
                            existing["type"] = pb["type"] if pb["type"] in ("EQH","EQL") else existing["type"]
                            existing["time"] = pb.get("time")
                            existing["age_bars"] = pb.get("age_bars")
            merged.sort(key=lambda x: x["dist_pts"])
            return merged

        weekly_bsl = _dedup_merge(m5_bsl, m15_bsl)
        weekly_ssl = _dedup_merge(m5_ssl, m15_ssl)

        if "liquidity" not in summary:
            summary["liquidity"] = {}
        summary["liquidity"]["weekly_bsl_pools"] = weekly_bsl
        summary["liquidity"]["weekly_ssl_pools"] = weekly_ssl

    return df5, summary

def _haiku_precheck(smc_summary: dict) -> dict:
    """
    ถาม Haiku (ถูกกว่า 10x) ว่ามี SMC setup จริงๆมั้ย ก่อนส่ง Sonnet
    คืน {"pass": bool, "reason": str}
    """
    price    = smc_summary.get("current_price", 0)
    bias     = smc_summary.get("bias", "neutral")
    bull_ob  = smc_summary.get("active_bull_ob") or {}
    bear_ob  = smc_summary.get("active_bear_ob") or {}
    sweep    = smc_summary.get("last_sweep")
    liq      = smc_summary.get("liquidity") or {}
    post_c   = smc_summary.get("post_sweep_continuation")
    bear_rej = smc_summary.get("recent_bear_ob_rejection")
    bull_rej = smc_summary.get("recent_bull_ob_rejection")
    adv      = smc_summary.get("advanced") or {}

    in_bull   = bull_ob.get("in_ob", False)
    in_bear   = bear_ob.get("in_ob", False)
    bull_dist = round(abs(price - (bull_ob.get("top") or price)) * 10) if bull_ob else 9999
    bear_dist = round(abs(price - (bear_ob.get("bottom") or price)) * 10) if bear_ob else 9999
    bsl_swept = (liq.get("nearest_bsl") or {}).get("swept", False)
    ssl_swept = (liq.get("nearest_ssl") or {}).get("swept", False)

    mini = (
        f"XAUUSD M5 | price={price} | bias={bias}\n"
        f"Bull OB: {bull_ob.get('bottom','?')}–{bull_ob.get('top','?')} {'IN_OB✅' if in_bull else f'dist={bull_dist}p'}\n"
        f"Bear OB: {bear_ob.get('bottom','?')}–{bear_ob.get('top','?')} {'IN_OB✅' if in_bear else f'dist={bear_dist}p'}\n"
        f"Last sweep: {(str(sweep.get('kind')) + ' at ' + str(sweep.get('level'))) if sweep else 'none'}\n"
        f"BSL swept: {bsl_swept} | SSL swept: {ssl_swept}\n"
        f"Post-sweep pullback: {(str(post_c.get('direction')) + ' ' + str(post_c.get('pullback_pct')) + '%') if post_c else 'none'}\n"
        f"Recent OB rejection: bear={bool(bear_rej)} bull={bool(bull_rej)}\n"
        f"BOS/CHoCH: {bool(smc_summary.get('last_bos'))} / {bool(smc_summary.get('last_choch'))}\n"
        f"Momentum: bull={adv.get('momentum_bull',False)} bear={adv.get('momentum_bear',False)}"
    )

    try:
        from agents.sdk_utils import sdk_query
        txt = sdk_query(
            f"XAUUSD SMC snapshot:\n{mini}\n\n"
            "Is there a valid entry setup RIGHT NOW?\n"
            "YES if: price in/near OB (≤80p) AND (sweep happened OR rejection OR pullback detected)\n"
            "NO if: price far from OB (>200p) with nothing actionable\n"
            "Reply with only: YES or NO, then 3-5 words why",
            label="HaikuPrecheck"
        ).strip()
        passed = txt.upper().startswith("YES")
        reason = txt[:60]
        return {"pass": passed, "reason": reason}
    except Exception as e:
        return {"pass": True, "reason": f"haiku error→pass: {e}"}  # fail open


def has_signal(smc_summary: dict, force_session: bool = False) -> bool:
    """
    เช็คเบื้องต้นว่ามี setup ที่น่าสนใจมั้ย (ไม่ใช้ Claude API)
    ถ้าไม่มี → ไม่เรียก Claude เลย ประหยัด cost

    Off-hours: ยังรันเงื่อนไขทั้งหมดเพื่อ log/แจ้งว่า signal กำลังก่อตัวมั้ย
    (ให้เห็นข้อมูลต่อเนื่อง) แต่ไม่เรียก AI เด็ดขาด — คืน False เสมอนอกเวลาเทรด
    """
    import pytz
    from datetime import datetime as _dt
    _now_th = _dt.now(pytz.timezone("Asia/Bangkok")).strftime("%H:%M:%S")

    if not smc_summary:
        return False

    off_hours = not force_session and not smc_summary.get("tradeable_session", True)

    would_signal = _evaluate_signal_conditions(smc_summary)

    if off_hours:
        _sess = smc_summary.get("session", {}).get("session", "?")
        if would_signal:
            print(f"[has_signal] 🌙 {_now_th} OFF-HOURS (session={_sess}) — signal กำลังก่อตัวอยู่ (ดู log ✅ ด้านบน) แต่นอกเวลาเทรด ไม่เรียก AI")
            # เก็บ note ไว้ให้ supervisor.py/notifier.py ส่ง Telegram แจ้งได้ —
            # user อยากรู้ตอนบอทเริ่มรันใหม่ว่ามี signal ก่อตัวช่วง off-hours มั้ย
            smc_summary["off_hours_signal_note"] = _build_off_hours_note(smc_summary, _sess)
        else:
            print(f"[has_signal] ❌ {_now_th} OFF-HOURS (session={_sess}) — ไม่มี signal")
        return False  # Off-hours — ไม่เรียก AI เด็ดขาด (bypass ด้วย force_session=True)

    return would_signal


def _build_off_hours_note(smc_summary: dict, sess: str) -> str:
    """สร้างข้อความบอกว่ามี signal อะไรก่อตัวอยู่ตอน off-hours พร้อมรายละเอียด
    (level ที่ sweep, SSL/BSL ถัดไปที่รอ, จุด rejection ถ้ามี) — ใช้ label
    เดียวกับ smc_setup ใน supervisor.py (priority: eql/eqh > swing > sweep)"""
    price = smc_summary.get("current_price", "?")
    bias  = smc_summary.get("bias", "neutral")
    eql   = smc_summary.get("eql_eqh_sweep") or {}
    sw    = smc_summary.get("last_sweep") or {}
    rev   = smc_summary.get("reversal") or {}
    liq   = smc_summary.get("liquidity") or {}
    bear_rej = smc_summary.get("recent_bear_ob_rejection")
    bull_rej = smc_summary.get("recent_bull_ob_rejection")

    detail_lines = []

    if eql.get("signal"):
        label = eql.get("signal")
        detail_lines.append(f"level: {eql.get('level', '?')}")
    elif rev.get("swing_signal"):
        label = f"SWING_{rev['swing_signal']}"
        detail_lines.append(
            f"entry={rev.get('entry_zone')} SL={rev.get('stop_loss')} TP={rev.get('take_profit')}"
        )
        _reasons = ", ".join(rev.get("swing_reasons") or [])
        if _reasons:
            detail_lines.append(f"เหตุผล: {_reasons}")
    elif sw.get("kind"):
        label = f"SWEEP_{sw['kind'].upper()}"
        detail_lines.append(
            f"level: {sw.get('level')} (wick ถึง {sw.get('wick_extreme')}, {sw.get('age_bars')} bars ago)"
        )
        # sweep low = SSL ที่โดน sweep แล้ว → บอก SSL ถัดไปที่ยังไม่ swept ให้รอ
        # sweep high = BSL ที่โดน sweep แล้ว → บอก BSL ถัดไปที่ยังไม่ swept
        _next_pool = liq.get("nearest_ssl") if sw["kind"] == "low" else liq.get("nearest_bsl")
        _next_lvl  = (_next_pool.get("level") if isinstance(_next_pool, dict) else _next_pool) if _next_pool else None
        if _next_lvl:
            _pool_label = "SSL" if sw["kind"] == "low" else "BSL"
            detail_lines.append(f"รอ {_pool_label} ถัดไปที่ {_next_lvl}")
    else:
        label = "SIGNAL"

    _rej = bear_rej or bull_rej
    if _rej:
        detail_lines.append(
            f"rejection ที่ OB zone {_rej.get('ob_zone')} ({_rej.get('bars_ago')} bars ago)"
        )

    detail_str = " | " + " | ".join(detail_lines) if detail_lines else ""
    return f"🌙 Off-hours ({sess}) — {label} กำลังก่อตัว | ราคา {price} | bias={bias}{detail_str} (ไม่เรียก AI นอกเวลาเทรด)"


def _evaluate_signal_conditions(smc_summary: dict) -> bool:
    """
    เช็คเงื่อนไข signal ทั้งหมด (sweep + OB + structure + advanced patterns)
    แยกจาก has_signal() เพื่อให้เรียกได้ทั้งตอน trading hours ปกติ และตอน
    off-hours (แค่ preview/log ไม่เรียก AI จริง)

    เช็ค 2 ชั้น:
    1. SMC Engine: sweep + OB + structure
    2. Advanced: signal_type จาก indicator logic (A/B/C)
    """
    import pytz
    from datetime import datetime as _dt
    _now_th = _dt.now(pytz.timezone("Asia/Bangkok")).strftime("%H:%M:%S")

    # ── ชั้น 2: ราคาอยู่ใน OB → ผ่านทันที (OB-first logic) ──────
    # ใช้ M15 OB เป็น primary (significant zones) + M5 เป็น fallback
    m15_data = smc_summary.get("m15") or {}
    bull_ob = m15_data.get("active_bull_ob") or smc_summary.get("active_bull_ob") or {}
    bear_ob = m15_data.get("active_bear_ob") or smc_summary.get("active_bear_ob") or {}
    if bull_ob.get("in_ob") or bear_ob.get("in_ob"):
        print(f"[has_signal] ✅ IN_OB (M15) — bull_in={bull_ob.get('in_ob')} bear_in={bear_ob.get('in_ob')}")
        return True

    # ── ชั้น 2.5: EQL/EQH Liquidity Sweep — CASE F priority ──────
    # EQL = SSL, EQH = BSL — sweep เกิดแล้ว + recovered = สัญญาณแรง
    eql_sweep = smc_summary.get("eql_sweep_signal")
    eqh_sweep = smc_summary.get("eqh_sweep_signal")
    if eql_sweep or eqh_sweep:
        print(f"[has_signal] ✅ {_now_th} EQL/EQH SWEEP (CASE F equiv) — eql={eql_sweep} eqh={eqh_sweep}")
        return True

    # ── Counter-Trend Block ────────────────────────────────────────
    # ใช้ post_sweep_continuation (age ≤30 bars) + last_sweep เฉพาะตอน sweep ยัง fresh (≤50 bars)
    _post_dir = (smc_summary.get("post_sweep_continuation") or {}).get("direction")
    _last_sw  = smc_summary.get("last_sweep") or {}
    _adv      = smc_summary.get("advanced") or {}
    _sw_l_age = int(_adv.get("sweep_l_age_bars") or 999)
    _sw_h_age = int(_adv.get("sweep_h_age_bars") or 999)
    _SWEEP_BLOCK_BARS = 50  # บล็อกแค่ 50 bars หลัง sweep (~4 ชั่วโมง M5)
    _buy_bias_active  = _post_dir == "BUY"  or (_last_sw.get("kind") == "low"  and _sw_l_age <= _SWEEP_BLOCK_BARS)
    _sell_bias_active = _post_dir == "SELL" or (_last_sw.get("kind") == "high" and _sw_h_age <= _SWEEP_BLOCK_BARS)

    # ── ชั้น 3: TREND setup (priority รอง) ───────────────────────
    # NOTE: ถ้าอยากปิด cost filter นี้ → เปลี่ยน OB_NEARBY_THRESHOLD เป็น 9999
    OB_NEARBY_THRESHOLD  = 150  # จุด — ราคาต้องอยู่ภายในนี้จาก OB
    LIQ_NEARBY_THRESHOLD = 120  # จุด — ราคาต้องอยู่ภายในนี้จาก SSL/BSL pool
    price         = smc_summary.get("current_price") or 0
    bull_dist     = abs(price - (bull_ob.get("top", price) or price)) if bull_ob else 9999
    bear_dist     = abs(price - (bear_ob.get("bottom", price) or price)) if bear_ob else 9999
    # sweep นับเป็น "signal" ก็ต่อเมื่อยังไม่เกิน 48 bars (เกณฑ์ EXPIRED เดียวกับที่
    # chart_analyst_agent ใช้ตัดสิน PULLBACK ENTRY RULE) — ไม่งั้น sweep เก่า
    # (เช่น 86-88 bars) จะ trigger เรียก Sonnet ทุก 5 นาทีทั้งที่รู้อยู่แล้วว่า
    # AI จะตอบ NO_TRADE (EXPIRED) แน่ๆ เสียเงินฟรีๆ ซ้ำๆ
    _last_sweep_obj = smc_summary.get("last_sweep")
    _sweep_age_bars = (_last_sweep_obj or {}).get("age_bars")
    has_sweep     = _last_sweep_obj is not None and (_sweep_age_bars is None or _sweep_age_bars <= 48)
    # ob_nearby ต้องมี "displacement" อย่างน้อย OB_MIN_DISPLACEMENT (ตรงกับ
    # OB MIN DISTANCE rule ที่ chart_analyst_agent ใช้จริง — ราคาต้องห่างจาก OB
    # อย่างน้อย 15 จุดถึงจะมี room ให้เป็น setup ที่ valid) ไม่ใช่แค่ "ใกล้ๆ" เฉยๆ
    # เดิมเช็คแค่ abs(distance) < 150 ทำให้ราคาที่ใกล้ OB เกินไป (เช่น 8-13pts —
    # ใกล้เกินจะมี room แต่ absolute distance ยัง <150) ก็ผ่านเป็น "signal" ได้
    # ทั้งที่รู้อยู่แล้วว่าจะโดน reject เพราะ displacement ไม่พอ
    OB_MIN_DISPLACEMENT = 15  # จุด — เกณฑ์เดียวกับ OB MIN DISTANCE rule
    _bull_disp = (price - bull_ob.get("top", price)) if bull_ob else -9999
    _bear_disp = (bear_ob.get("bottom", price) - price) if bear_ob else -9999
    has_ob_nearby = (
        (bool(bull_ob) and OB_MIN_DISPLACEMENT <= _bull_disp < OB_NEARBY_THRESHOLD) or
        (bool(bear_ob) and OB_MIN_DISPLACEMENT <= _bear_disp < OB_NEARBY_THRESHOLD)
    )
    has_structure = (smc_summary.get("last_bos") is not None or
                     smc_summary.get("last_choch") is not None)
    bias = smc_summary.get("bias", "neutral")

    # liquidity proximity — SSL (ล่าง) = BUY target, BSL (บน) = SELL target
    # nearest_ssl/bsl อาจเป็น dict {"level":..., "swept":...} หรือตัวเลขตรงๆ
    _liq = smc_summary.get("liquidity") or {}
    _ssl_raw = _liq.get("nearest_ssl")
    _bsl_raw = _liq.get("nearest_bsl")
    _ssl_lvl = (_ssl_raw.get("level") if isinstance(_ssl_raw, dict) else _ssl_raw) if _ssl_raw else None
    _bsl_lvl = (_bsl_raw.get("level") if isinstance(_bsl_raw, dict) else _bsl_raw) if _bsl_raw else None
    ssl_dist = abs(price - float(_ssl_lvl)) if _ssl_lvl else 9999
    bsl_dist = abs(price - float(_bsl_lvl)) if _bsl_lvl else 9999
    has_liq_nearby = ssl_dist < LIQ_NEARBY_THRESHOLD or bsl_dist < LIQ_NEARBY_THRESHOLD
    _liq_dist_str  = f"ssl={ssl_dist:.0f}p bsl={bsl_dist:.0f}p" if (_ssl_lvl or _bsl_lvl) else "no_liq"

    score = sum([has_sweep, has_ob_nearby, has_structure, has_liq_nearby])

    # score >= 3 (ไม่ใช่ 2) — has_structure/has_liq_nearby เป็น context ทั่วไปที่
    # true อยู่บ่อยๆ (BOS/CHoCH เกิดง่าย, threshold 120pts กว้าง) แค่ 2 ตัวนี้
    # ไม่พอบอกว่ามี setup จริง ต้องมีอย่างน้อย sweep สดหรือ OB displacement พอ
    # ร่วมด้วย ไม่งั้น structure+liquidity อย่างเดียวจะดัน score=2 ผ่านได้ตลอด
    # แม้ sweep จะหมดอายุและ OB จะใกล้เกินไปแล้วก็ตาม (เรียก AI ฟรีๆ ซ้ำๆ)
    if score >= 3 and bias != "neutral":
        print(f"[has_signal] ✅ {_now_th} TREND — sweep={has_sweep} ob_nearby={has_ob_nearby}({min(bull_dist,bear_dist):.0f}p) struct={has_structure} liq_nearby={has_liq_nearby}({_liq_dist_str}) bias={bias} score={score}/4")
        return True  # Trend setup viable — ให้ Claude วิเคราะห์ตำแหน่ง OB ต่อ

    # ── ชั้น 3: Swing Entry signal (fallback เมื่อ trend ไม่ครบ) ──
    rev = smc_summary.get("reversal", {})
    if rev.get("swing_signal") and rev.get("swing_score", 0) >= 3:
        print(f"[has_signal] ✅ {_now_th} SWING — signal={rev.get('swing_signal')} score={rev.get('swing_score')}")
        return True

    # ── ชั้น 4: Type C indicator signal ──────────────────────────
    signal_type = smc_summary.get("signal_type")
    if signal_type and "C_" in str(signal_type):
        print(f"[has_signal] ✅ {_now_th} TYPE_C — signal_type={signal_type}")
        return True

    # ── ชั้น 6: AMD Pattern (Range→Sweep→CHoCH→BOS) ──────────
    amd = smc_summary.get("amd", {})
    if amd.get("amd_signal") and amd.get("amd_score", 0) >= 4:
        print(f"[has_signal] ✅ {_now_th} AMD — {amd.get('amd_signal')} {amd.get('amd_stars','')} score={amd.get('amd_score')}")
        return True

    # ── ชั้น 7: Recent OB Rejection (CASE G) — rejection เพิ่งเกิดใน 5 แท่ง ──
    _bear_rej = smc_summary.get("recent_bear_ob_rejection")
    _bull_rej = smc_summary.get("recent_bull_ob_rejection")
    if _bear_rej and _bear_rej.get("bars_ago", 99) <= 3:
        if _buy_bias_active:
            print(f"[has_signal] ⛔ {_now_th} COUNTER-TREND BLOCK — Bear OB rejection แต่ BUY sweep active, ข้าม")
        else:
            print(f"[has_signal] ✅ {_now_th} OB_REJECTION (BEAR) — zone={_bear_rej.get('ob_zone')} bars_ago={_bear_rej.get('bars_ago')}")
            return True
    if _bull_rej and _bull_rej.get("bars_ago", 99) <= 3:
        if _sell_bias_active:
            print(f"[has_signal] ⛔ {_now_th} COUNTER-TREND BLOCK — Bull OB rejection แต่ SELL sweep active, ข้าม")
        else:
            print(f"[has_signal] ✅ {_now_th} OB_REJECTION (BULL) — zone={_bull_rej.get('ob_zone')} bars_ago={_bull_rej.get('bars_ago')}")
            return True

    # ── ชั้น 8: Post-Sweep Continuation Pullback (CASE H) ────────────
    _post = smc_summary.get("post_sweep_continuation")
    if _post:
        print(f"[has_signal] ✅ {_now_th} POST_SWEEP_CONT — dir={_post.get('direction')} pb={_post.get('pullback_pct')}% drop={_post.get('initial_drop_pts') or _post.get('initial_rise_pts')}p")
        return True

    # ── ชั้น 9: Pattern 3 — Stored OB Rejection (ข้ามสแกน, 60 นาที) ──
    _stored = smc_summary.get("stored_ob_rejections") or []
    for _z in _stored:
        _lo, _hi = float(_z["zone"][0]), float(_z["zone"][1])
        _mid = (_lo + _hi) / 2
        _dist = abs(price - _mid) * 10
        if _dist <= 200:  # ราคาอยู่ใกล้ OB ที่เคย reject (<200p)
            print(f"[has_signal] ✅ {_now_th} STORED_OB_REJ — {_z.get('direction')} zone={_z['zone']} dist={_dist:.0f}p")
            return True

    # ── ชั้น 10: Pattern 1 — Sweep+Rejection Watch active ────────────
    _srw = smc_summary.get("sweep_rejection_watch")
    if _srw:
        print(f"[has_signal] ✅ {_now_th} SWEEP_WATCH active — {_srw.get('direction')} watching since {_srw.get('watched_since')}")
        return True

    # ── ชั้น 11: CASE K — CHoCH + Sweep → Rejection ─────────────────
    _ck = smc_summary.get("choch_sweep_setup")
    if _ck and _ck.get("confidence") in ("HIGH", "MEDIUM"):
        print(f"[has_signal] ✅ {_now_th} CHOCH_SWEEP — dir={_ck['direction']} choch={_ck['choch_level']} sweep={_ck['sweep_level']} conf={_ck['confidence']}")
        return True

    print(f"[has_signal] ❌ {_now_th} NO_SIGNAL — sweep={has_sweep} ob_nearby={has_ob_nearby}({min(bull_dist,bear_dist):.0f}p) struct={has_structure} liq_nearby={has_liq_nearby}({_liq_dist_str}) bias={bias} score={score}/4")
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
        from agents.sdk_utils import sdk_query
        result = safe_json_parse(sdk_query(prompt, label="ConfirmSignal"), fallback={"confidence": 0, "reasoning": "parse error"})
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

    # ── Haiku Pre-check: ก่อนส่ง Sonnet ถาม Haiku ก่อนว่ามี setup จริงๆมั้ย ──
    # Haiku ถูกกว่า 10x — ถ้า Haiku บอก NO → skip Sonnet ประหยัดเงิน
    _pre = _haiku_precheck(smc_summary)
    if not _pre["pass"]:
        print(f"[Haiku pre-check] ❌ SKIP Sonnet — {_pre['reason']}")
        return {
            "signal": "NO_TRADE",
            "confidence": 0,
            "current_price": smc_summary.get("current_price"),
            "analyzed_at": smc_summary.get("analyzed_at"),
            "smc_bias": smc_summary.get("bias"),
            "had_sweep": smc_summary.get("last_sweep") is not None,
            "reasoning": f"[Haiku pre-check] {_pre['reason']}",
            "claude_called": False,
        }
    print(f"[Haiku pre-check] ✅ PASS → calling Sonnet ({_pre['reason']})")

    adv  = smc_summary.get("advanced", {})
    sess = smc_summary.get("session", {})
    rev  = smc_summary.get("reversal", {})
    m15  = smc_summary.get("m15") or {}

    momentum_bull = adv.get("momentum_bull", False)
    momentum_bear = adv.get("momentum_bear", False)
    liq           = smc_summary.get("liquidity", {})

    # คำนวณระยะห่าง OB จริงๆ ก่อนสร้าง prompt
    price_now = float(smc_summary.get("price") or smc_summary.get("current_price") or 0)

    # Swing levels คำนวณโดย code (ไม่ใช่ LLM) — ใช้เป็น TP hint
    nearest_sh      = smc_summary.get("nearest_swing_high")
    nearest_sl_code = smc_summary.get("nearest_swing_low")
    sh_above        = smc_summary.get("swing_highs_above", [])
    sl_below        = smc_summary.get("swing_lows_below", [])
    _sh_pts  = round((nearest_sh - price_now) * 10, 0) if nearest_sh else None
    _sl_pts  = round((price_now - nearest_sl_code) * 10, 0) if nearest_sl_code else None
    pdh          = smc_summary.get("pdh")
    pdl          = smc_summary.get("pdl")
    round_levels = smc_summary.get("round_levels", [])
    _pdh_pts = round((pdh - price_now) * 10, 0) if pdh and pdh > price_now else None
    _pdl_pts = round((price_now - pdl) * 10, 0) if pdl and pdl < price_now else None

    _ote      = smc_summary.get("ote") or {}
    _ote_b    = _ote.get("ote_buy_zone")
    _ote_s    = _ote.get("ote_sell_zone")
    _in_ote_b = _ote.get("in_ote_buy",  False)
    _in_ote_s = _ote.get("in_ote_sell", False)
    _ote_hint = ""
    if _ote_b and _ote_s:
        _ote_hint = (
            f"\n\n📐 OTE Zone (Fibonacci 61.8%–78.6% retracement):\n"
            f"  BUY  OTE: {_ote_b[0]} – {_ote_b[1]}"
            + (" ← ราคาอยู่ใน OTE ✅ (confluence สูง)" if _in_ote_b else "") + "\n"
            f"  SELL OTE: {_ote_s[0]} – {_ote_s[1]}"
            + (" ← ราคาอยู่ใน OTE ✅ (confluence สูง)" if _in_ote_s else "") + "\n"
            f"  Range: {int(_ote.get('range_pts', 0))} pts (Swing High {_ote.get('swing_high')} → Swing Low {_ote.get('swing_low')})\n"
            f"  กฎ OTE: ถ้าราคาอยู่ใน OTE zone + OB อยู่ในโซนเดียวกัน = confluence สูงมาก → เพิ่ม confidence"
        )

    swing_hint = (
        f"🎯 S/R Levels (คำนวณโดย code):\n"
        f"  PDH (Prev Day High): {pdh or 'N/A'}"
        + (f" ({int(_pdh_pts):+,} จุด)" if _pdh_pts else "") + "\n"
        f"  PDL (Prev Day Low):  {pdl or 'N/A'}"
        + (f" ({int(-_pdl_pts):+,} จุด)" if _pdl_pts else "") + "\n"
        f"  Nearest Swing High (above): {nearest_sh or 'N/A'}"
        + (f" ({int(_sh_pts):,} จุด)" if _sh_pts else "") + "\n"
        f"  Nearest Swing Low  (below): {nearest_sl_code or 'N/A'}"
        + (f" ({int(_sl_pts):,} จุด)" if _sl_pts else "") + "\n"
        f"  Swing Highs above (top 3): {sh_above or 'N/A'}\n"
        f"  Swing Lows  below (top 3): {sl_below or 'N/A'}\n"
        f"  Round Numbers (±300p):     {round_levels or 'N/A'}"
        + _ote_hint
    )

    def _fmt_liq_pool(p: dict) -> str:
        if not p:
            return "–"
        swept_tag = " [SWEPT]" if p.get("swept") else ""
        return (f"{p.get('level','?')} ({p.get('type','?')}/{p.get('size','?')}"
                f" dist={p.get('dist_pts','?')}p){swept_tag}")

    def _fmt_liq_list(pools: list) -> str:
        if not pools:
            return "  (ไม่มี)"
        return "\n".join(f"  • {_fmt_liq_pool(p)}" for p in pools)

    _bsl_pools  = liq.get("bsl_pools",  [])
    _ssl_pools  = liq.get("ssl_pools",  [])
    _near_bsl   = liq.get("nearest_bsl")
    _near_ssl   = liq.get("nearest_ssl")
    _bsl_idm    = liq.get("bsl_inducement")
    _ssl_idm    = liq.get("ssl_inducement")

    # คำนวณระยะ SSL/BSL เป็นจุด สำหรับ Liquidity Gate check
    _dist_ssl_pts = _near_ssl.get("dist_pts") if _near_ssl else None
    _dist_bsl_pts = _near_bsl.get("dist_pts") if _near_bsl else None
    _ssl_swept    = _near_ssl.get("swept", False) if _near_ssl else True
    _bsl_swept    = _near_bsl.get("swept", False) if _near_bsl else True

    _liq_gate_warn = ""
    if _near_ssl and not _ssl_swept and _dist_ssl_pts and _dist_ssl_pts < 500:
        _liq_gate_warn += (
            f"\n  ⛔ LIQUIDITY GATE ACTIVE: SSL ที่ {_near_ssl.get('level')} ยังไม่ถูก sweep"
            f" (dist={_dist_ssl_pts}p) — ห้าม BUY ถ้า macro BEAR"
        )
    if _near_bsl and not _bsl_swept and _dist_bsl_pts and _dist_bsl_pts < 500:
        _liq_gate_warn += (
            f"\n  ⛔ LIQUIDITY GATE ACTIVE: BSL ที่ {_near_bsl.get('level')} ยังไม่ถูก sweep"
            f" (dist={_dist_bsl_pts}p) — ห้าม SELL ถ้า macro BULL"
        )

    liq_map_block = (
        f"🌊 LIQUIDITY MAP (BSL/SSL — คำนวณโดย code):\n"
        f"  Nearest intact BSL (Buy-Side / above price): {_fmt_liq_pool(_near_bsl)}\n"
        f"  Nearest intact SSL (Sell-Side / below price): {_fmt_liq_pool(_near_ssl)}\n"
        f"  BSL Inducement (minor pool ระหว่างทางก่อน BSL): {_fmt_liq_pool(_bsl_idm)}\n"
        f"  SSL Inducement (minor pool ระหว่างทางก่อน SSL): {_fmt_liq_pool(_ssl_idm)}\n"
        f"  BSL Pools (all above price, top 5):\n{_fmt_liq_list(_bsl_pools)}\n"
        f"  SSL Pools (all below price, top 5):\n{_fmt_liq_list(_ssl_pools)}"
        f"{_liq_gate_warn}\n"
        f"\n"
        f"  กฎการอ่าน Liquidity Map:\n"
        f"  - ราคาวิ่งหา liquidity ที่ใกล้ที่สุดก่อนเสมอ (nearest intact pool)\n"
        f"  - BSL = stop loss ของ Short sellers (อยู่เหนือราคา) → ราคาวิ่งขึ้นดูด → แล้ว SELL\n"
        f"  - SSL = stop loss ของ Long buyers (อยู่ใต้ราคา) → ราคาวิ่งลงดูด → แล้ว BUY\n"
        f"  - major pool (EQH/EQL) = target ใหญ่, minor pool (swing) = inducement ก่อนถึง major\n"
        f"  - ถ้า BSL/SSL ถูก swept แล้ว (SWEPT) = ราคาจะ reverse ได้เลย"
    )
    # recent OB rejection fields
    _recent_bear_rej = smc_summary.get("recent_bear_ob_rejection")
    _recent_bull_rej = smc_summary.get("recent_bull_ob_rejection")
    _ob_quality      = smc_summary.get("ob_quality") or {}
    _post_cont       = smc_summary.get("post_sweep_continuation")
    _stored_ob_rej   = smc_summary.get("stored_ob_rejections") or []
    _sweep_watch     = smc_summary.get("sweep_rejection_watch")

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

    src = smc_summary.get("price_source", "yfinance")
    def _ob_str(ob):
        if not ob: return "ไม่มี"
        in_tag = " ← IN OB" if ob.get("in_ob") else ""
        return f"{ob.get('bottom')} – {ob.get('top')} [{ob.get('tf','')}]{in_tag}"
    print(f"[OB] 📡 source={src} | ราคา={price_now}")
    print(f"[OB] 🟢 Bull OB: {_ob_str(primary_bull_ob)}  ห่าง {dist_to_bull_ob:.0f}p")
    print(f"[OB] 🔴 Bear OB: {_ob_str(primary_bear_ob)}  ห่าง {dist_to_bear_ob:.0f}p")

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

🔁 RECENT OB REJECTION (ตรวจจาก 5 แท่งล่าสุด — ใช้สำหรับ CASE G):
  Bear OB rejection: {f"⚠️ SELL rejection ที่ {_recent_bear_rej['ob_zone']} — {_recent_bear_rej['bars_ago']} แท่งที่แล้ว (OB_REJECTION_SELL)" if _recent_bear_rej else "ไม่มี"}
  Bull OB rejection: {f"⚠️ BUY rejection ที่ {_recent_bull_rej['ob_zone']} — {_recent_bull_rej['bars_ago']} แท่งที่แล้ว (OB_REJECTION_BUY)" if _recent_bull_rej else "ไม่มี"}
  กฎ: ถ้ามี recent rejection (bars_ago ≤ 3) → CASE G ใช้ได้ แม้ราคาออกจาก OB ไปแล้ว
      ระบุ "ob rejection" ใน vote_reasoning เพื่อ bypass liq_gate ได้

📐 OB QUALITY (ระยะห่าง OB จาก sweep level — OB ใกล้ sweep เกิน = ไม่มีพื้นที่ accumulate):
  Bear OB sweep gap: {f"{_ob_quality.get('bear_ob_sweep_gap_pts','?')}p → quality={_ob_quality.get('bear_ob_quality','?')}" if _ob_quality.get('bear_ob_quality') else "ไม่มีข้อมูล (ไม่มี sweep หรือ OB)"}
  Bull OB sweep gap: {f"{_ob_quality.get('bull_ob_sweep_gap_pts','?')}p → quality={_ob_quality.get('bull_ob_quality','?')}" if _ob_quality.get('bull_ob_quality') else "ไม่มีข้อมูล"}
  กฎ quality: HIGH(≥100p) = OB ดีมาก | MEDIUM(50-99p) = ใช้ได้ | LOW(<50p) = OB ใกล้ sweep เกิน → confidence ต่ำ ลด 10-20
  เหตุผล: smart money ต้องการพื้นที่สะสม position ก่อน sweep ถ้า OB อยู่ชิด sweep = ไม่มี accumulation = rejection อ่อน

🔄 POST-SWEEP CONTINUATION (pullback หลัง sweep+rejection — ใช้สำหรับ CASE H):
{f"  ⚡ {_post_cont['direction']} continuation: sweep ที่ {_post_cont['sweep_level']} ({_post_cont['sweep_age_bars']} bars ago)" + chr(10) + f"  วิ่งไปแล้ว {_post_cont.get('initial_drop_pts') or _post_cont.get('initial_rise_pts')}p | pullback กลับมา {_post_cont['pullback_pts']}p ({_post_cont['pullback_pct']}%) → entry opportunity" if _post_cont else "  ไม่มี (ไม่มี sweep ล่าสุด หรือ pullback ยังไม่เกิด)"}

⏳ PATTERN 1 WATCH — Sweep+Rejection กำลัง monitor pullback:
{f"  🔍 {_sweep_watch['direction']} watch: sweep ที่ {_sweep_watch.get('sweep_level')} | watching since {_sweep_watch.get('watched_since')} | expire {_sweep_watch.get('expire_at')}" + chr(10) + f"  ถ้าตอนนี้มี pullback กลับมา → นี่คือ entry opportunity (POST_SWEEP_PULLBACK)" if _sweep_watch else "  ไม่มี (ไม่มี sweep+rejection ล่าสุด)"}

💾 PATTERN 3 — Stored OB Rejections (จำ OB ที่โดน rejection ไว้ข้ามสแกน):
{chr(10).join(f"  • {z['direction']} OB zone={z['zone']} | rejected {z['rejected_at']} | expire {z['expire_at']}" for z in _stored_ob_rej) if _stored_ob_rej else "  ไม่มี OB rejection ที่จำไว้"}
  กฎ: ถ้าราคา pullback กลับมาใกล้ stored zone (≤150p) → setup_type=STORED_OB_PULLBACK_{'{DIR}'}
  เหตุผล: OB ที่โดน rejection แล้วยังคง valid เป็น supply/demand zone ถ้า pullback กลับมา

{liq_map_block}

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
⚠️ LIQUIDITY GATE — เช็คก่อนทุก setup (บังคับ)
════════════════════════════════════════════
ก่อนเข้า trade ใดๆ ต้องผ่าน Liquidity Gate ก่อนเสมอ:

🚫 ห้าม BUY ที่ Bull OB ถ้า:
   nearest_ssl ยังไม่ถูก sweep (SWEPT=False)
   AND dist_ssl < 500p (SSL อยู่ใกล้กว่า 500 จุด)
   AND macro BEAR (H4 BEAR หรือ H1+H4 BEAR)
   → เหตุผล: ราคามักวิ่งลงดูด SSL ก่อน → Bull OB ที่เข้าอยู่จะถูกทะลุ → SL โดน
   → แม้ราคาจะ AT Bull OB แต่ถ้า SSL intact + macro BEAR = BUY ก่อนกาล
   → สิ่งที่ทำแทน: รอ SSL ถูก sweep แล้วดู rejection → SSL_SWEEP_BUY
   → signal = NO_TRADE | trade_plan = "รอ SSL ที่ {_near_ssl.get('level') if _near_ssl else '?'} ถูก sweep ก่อน"

🚫 ห้าม SELL ที่ Bear OB ถ้า:
   nearest_bsl ยังไม่ถูก sweep (SWEPT=False)
   AND dist_bsl < 500p (BSL อยู่ใกล้กว่า 500 จุด)
   AND macro BULL (H4 BULL หรือ H1+H4 BULL)
   → ราคามักวิ่งขึ้นดูด BSL ก่อน → Bear OB ที่เข้าจะถูกทะลุ → SL โดน
   → รอ BSL ถูก sweep แล้วดู rejection → BSL_SWEEP_SELL

✅ ยกเว้น Liquidity Gate ถ้า:
   • SSL/BSL ถูก sweep แล้ว (SWEPT=True) → ผ่านได้ทันที
   • dist_ssl หรือ dist_bsl ≥ 500p (liquidity ไกลมาก ไม่ใช่ next target)
   • macro BULL + BUY หรือ macro BEAR + SELL (trend-aligned ไม่มี opposing liquidity ใกล้)
   • มี sweep+rejection ชัดที่ OB แล้ว (CASE B1/F) → liquidity ถือว่า cleared จาก OB

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

  ✅ B ผ่านถ้า: Bull OB unmitigated + RR ≥ 1.5 + ผ่าน Liquidity Gate (SSL swept หรือ ไกล ≥500p หรือ macro BULL)
  ❌ B ไม่ผ่านถ้า: OB mitigated แล้ว หรือ RR < 1.5 หรือ SSL intact < 500p + macro BEAR → รอ SSL sweep

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
    SL: ต่ำกว่า sweep_low 10-15 จุด (หลุดแนวพอ)
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
    SL: สูงกว่า sweep_high 10-15 จุด (หลุดแนวพอ)
    TP: nearest_swing_low ที่คำนวณโดย code (ด้านล่าง)
    → setup_type = EQH_SWEEP_SELL
    confidence สูงขึ้นถ้ามี bear confirm candle + macro BEAR หรือ MIXED เท่านั้น

── CASE E: S/R Entry — แนวรับ/แนวต้านโดยไม่มี OB ──────────────
ใช้เมื่อ: ราคาอยู่ใกล้ key S/R level แต่ไม่มี OB ใกล้ (OB ไกลเกิน 300 จุด)

  Key S/R levels ที่ใช้ได้ (เรียงตาม priority):
    1. PDH (Prev Day High) — แนวต้านสำคัญที่สุด
    2. PDL (Prev Day Low)  — แนวรับสำคัญที่สุด
    3. Swing High ใกล้ที่สุด (above price)
    4. Swing Low ใกล้ที่สุด (below price)
    5. Round Number ที่ราคาใกล้ที่สุด (4200, 4250, 4300 ฯลฯ)

  🔴 E1 — SR_SELL (ที่ S/R resistance):
    เงื่อนไข: ราคา ≤ 20 จุด ต่ำกว่า S/R resistance (PDH / Swing High / Round)
             + bearish rejection candle (wick ยาวบน / bear engulf)
             + ไม่มี OB ใกล้ (Bear OB ไกลเกิน 300p) หรือ S/R ≥ confluence 2 ระดับ
    Entry: ราคาปัจจุบัน หรือ limit ที่ S/R level
    SL: S/R level + 10-15 จุด (หลุดแนวพอ)
    TP: nearest_swing_low / PDL / Bull OB ที่อยู่ใต้ราคา
    setup_type = SR_SELL | sr_level = ราคา S/R นั้น

  🟢 E2 — SR_BUY (ที่ S/R support):
    เงื่อนไข: ราคา ≤ 20 จุด สูงกว่า S/R support (PDL / Swing Low / Round)
             + bullish rejection candle (wick ยาวล่าง / bull engulf)
             + ไม่มี OB ใกล้ (Bull OB ไกลเกิน 300p) หรือ S/R ≥ confluence 2 ระดับ
    Entry: ราคาปัจจุบัน หรือ limit ที่ S/R level
    SL: S/R level - 10-15 จุด (หลุดแนวพอ)
    TP: nearest_swing_high / PDH / Bear OB ที่อยู่เหนือราคา
    setup_type = SR_BUY | sr_level = ราคา S/R นั้น

  ⚠️ ห้าม E ถ้า: momentum แรงเกิน (2.5×ATR) หรือ RR < 1.5

── CASE F: BSL/SSL Liquidity Sweep — Highest Priority ───────────
ใช้ Liquidity Map ข้างบน: ราคาเพิ่ง sweep BSL หรือ SSL แล้ว → reverse entry

  🔴 F1 — BSL_SWEEP_SELL (ราคา swept BSL แล้ว กำลัง reverse ลง):
    เงื่อนไข:
      - nearest_bsl SWEPT = True (price วิ่งขึ้นดูด BSL pool เสร็จแล้ว)
      - ราคาปัจจุบันต่ำกว่า BSL level (กลับลงมาใต้ pool)
      - bearish rejection candle หรือ CHoCH ลง
    Entry: ราคาปัจจุบัน (หรือ limit ที่ BSL level)
    SL: BSL level + 10-15 จุด (หลุดเหนือ pool ที่ถูก sweep)
    TP: nearest_ssl level / PDL / Bull OB ที่อยู่ใต้ราคา
    setup_type = BSL_SWEEP_SELL
    liquidity_target = nearest_bsl level
    confidence สูงถ้า: major pool (EQH/EQL) + HTF BEAR หรือ MIXED

  🟢 F2 — SSL_SWEEP_BUY (ราคา swept SSL แล้ว กำลัง reverse ขึ้น):
    เงื่อนไข:
      - nearest_ssl SWEPT = True (price วิ่งลงดูด SSL pool เสร็จแล้ว)
      - ราคาปัจจุบันสูงกว่า SSL level (กลับขึ้นมาเหนือ pool)
      - bullish rejection candle หรือ CHoCH ขึ้น
    Entry: ราคาปัจจุบัน (หรือ limit ที่ SSL level)
    SL: SSL level - 10-15 จุด (หลุดใต้ pool ที่ถูก sweep)
    TP: nearest_bsl level / PDH / Bear OB ที่อยู่เหนือราคา
    setup_type = SSL_SWEEP_BUY
    liquidity_target = nearest_ssl level
    confidence สูงถ้า: major pool (EQH/EQL) + HTF BULL หรือ MIXED

  🎯 Inducement Logic (อย่าหลงกล):
    ถ้า bsl_inducement หรือ ssl_inducement มีค่า = ราคาอาจวิ่งดูด inducement ก่อน
    แล้วค่อย reverse หลังจาก major pool ถูก swept
    → รอ sweep inducement เสร็จก่อน แล้วค่อยดู CASE F

  ⚠️ ห้าม F ถ้า: BSL/SSL ยังไม่ถูก sweep (ราคายังไม่ถึง pool)
     → ใช้ liquidity_target เป็น entry_far แจ้งรอแทน

── CASE D: AMD Pattern — Range → Sweep → CHoCH → BOS ───────────
ท่าเจอบ่อยที่สุด: ออกข้าง (Accumulation) → ดูด liquidity (Manipulation) → พลิกทิศ (Distribution)

  🟢 D1 — AMD_BUY / Spring (ถ้า amd_signal=AMD_BUY):
    EQL swept → ดูด sell stops → CHoCH Bull → BOS up → คนที่ short โดน squeeze
    Entry: ราคาปัจจุบัน หรือ pullback มา EQL level (ถ้ายังใกล้)
    SL: ต่ำกว่า amd_sweep_level 10-15 จุด (หลุดแนวพอ)
    TP: nearest_swing_high / Bear OB ถ้าไม่ไกลเกิน
    setup_type = AMD_BUY
    confidence สูงขึ้นถ้า: CHoCH fresh (≤5 bars) + BOS confirmed + bull candle

  🔴 D2 — AMD_SELL / Upthrust (ถ้า amd_signal=AMD_SELL):
    EQH swept → ดูด buy stops → CHoCH Bear → BOS down → คนที่ long โดน flush
    Entry: ราคาปัจจุบัน หรือ pullback มา EQH level
    SL: สูงกว่า amd_sweep_level 10-15 จุด (หลุดแนวพอ)
    TP: nearest_swing_low / Bull OB ถ้าไม่ไกลเกิน
    setup_type = AMD_SELL
    confidence สูงขึ้นถ้า: CHoCH fresh + BOS confirmed + bear candle

  ⚠️ AMD ★★★ (score≥8) = high conviction — เข้าได้ lot ปกติ
  ⚠️ AMD ★★  (score 5-7) = moderate — เข้าได้ lot เล็ก หรือรอ confirmation เพิ่ม

════════════════════════════════════════════
STEP 3 — ตัดสินใจและโหวต
════════════════════════════════════════════
หลักการ: ซื้อแนวรับ ขายแนวต้าน — ราคาต้องถึง OB ก่อนเสมอ ไม่ trade กลางอากาศ

ลำดับ priority (ไล่ตามความ confidence สูงสุด → ต่ำสุด):
-3. ⛔ PULLBACK VALIDITY RULE (สำคัญที่สุด — ตรวจก่อนทุก pattern):
    Pullback หลัง rejection valid ก็ต่อเมื่อ: ราคา **ไม่ close เกิน rejection level**
    🔴 SELL setup: pullback ขึ้นมา แต่ candle close ต้องอยู่ **ต่ำกว่า** rejection high
    🟢 BUY setup: pullback ลงมา แต่ candle close ต้องอยู่ **สูงกว่า** rejection low
    ❌ ถ้า price close ทะลุ rejection level = setup INVALIDATED → NO_TRADE
    เหตุผล: rejection level ที่ถูกทะลุ = supply/demand นั้นอ่อนแอ → smart money ไม่อยู่แล้ว
    ตัวอย่าง: BSL sweep ที่ 3300 → reject ลง → pullback ขึ้นมา 3298 (close ต่ำกว่า 3300) = VALID ENTRY
              BSL sweep ที่ 3300 → reject ลง → pullback ขึ้นมา 3302 (close เกิน 3300) = INVALIDATED

-2. 📐 OB Quality & Distance Check (ทำก่อนทุกอย่าง):
    (a) ob_quality: ดูจาก MARKET DATA → ถ้า quality=LOW (<50p จาก sweep) → ลด confidence 15-20 คะแนน
        เหตุผล: OB ใกล้ sweep เกิน = smart money ไม่มีพื้นที่ accumulate → rejection อ่อน
    (b) OB distance (Pattern 2): ถ้า OB ห่างจากราคาน้อยกว่า 50p = ใกล้เกิน → ไม่ใช่ setup ที่ดี
        OB ที่ดีต้องห่างพอสมควร (50p+) เพื่อให้ราคามี "journey" มาถึง = momentum สร้างได้
        ยกเว้น: ถ้าราคาอยู่ IN OB แล้ว (in_ob=True) = valid entry ทันที
-1. ⛔ ตรวจ Liquidity Gate ก่อน: BUY + SSL intact < 500p + macro BEAR → NO_TRADE (รอ SSL sweep)
                                  SELL + BSL intact < 500p + macro BULL → NO_TRADE (รอ BSL sweep)
    🟡 ยกเว้น Liq Gate: ถ้า CASE G ผ่าน (ob rejection ชัดเจน) → ข้าม gate ได้ ไม่ต้องรอ sweep
0. ถ้าไม่มีอะไรผ่านเลย → NO_TRADE รอ แต่ระบุว่า liquidity target อยู่ตรงไหน
1. ★★★ CASE F1 — nearest_bsl SWEPT + bearish reject → SELL, setup_type=BSL_SWEEP_SELL (liquidity sweep = highest conviction)
   ★★★ CASE F2 — nearest_ssl SWEPT + bullish reject → BUY,  setup_type=SSL_SWEEP_BUY  (liquidity sweep = highest conviction)
1.5. ★★ CASE G — OB Rejection (ไม่ต้องรอ sweep ถ้า rejection ชัด):
   🔴 G1 — OB_REJECTION_SELL:
      ทริกเกอร์ได้ 2 แบบ:
        (a) ราคาอยู่ใน Bear OB ตอนนี้ + bearish rejection candle
        (b) recent_bear_ob_rejection แสดงว่ามี rejection ใน 3 แท่งล่าสุด (ราคาออกมาแล้ว แต่ setup ยังใหม่)
      → SELL | setup_type=OB_REJECTION_SELL
      vote_reasoning ต้องระบุว่า "ob rejection" หรือ "bearish rejection ที่ Bear OB"
      SL: Bear OB top + 10-15 จุด | TP: nearest_ssl / PDL / Bull OB ใต้ราคา
   🟢 G2 — OB_REJECTION_BUY:
      ทริกเกอร์ได้ 2 แบบ:
        (a) ราคาอยู่ใน Bull OB ตอนนี้ + bullish rejection candle
        (b) recent_bull_ob_rejection แสดงว่ามี rejection ใน 3 แท่งล่าสุด (ราคาออกมาแล้ว แต่ setup ยังใหม่)
      → BUY | setup_type=OB_REJECTION_BUY
      vote_reasoning ต้องระบุว่า "ob rejection" หรือ "bullish rejection ที่ Bull OB"
      SL: Bull OB bottom - 10-15 จุด | TP: nearest_bsl / PDH / Bear OB เหนือราคา
   ⚠️ CASE G confidence ต่ำกว่า CASE F เพราะไม่มี sweep confirmation — ระบุ confidence 50-70
1.3. ★★★ CASE I — Stored OB Pullback (Pattern 3 — สำคัญมาก):
   ดู PATTERN 3 ข้างบน — ถ้ามี stored OB rejections และราคา pullback กลับมาใกล้ zone นั้น
   🔴 I1: ราคา pullback มาใกล้ Stored SELL OB zone (≤150p) → SELL
           setup_type = STORED_OB_PULLBACK_SELL | SL = zone top + 10-15p
   🟢 I2: ราคา pullback มาใกล้ Stored BUY OB zone (≤150p) → BUY
           setup_type = STORED_OB_PULLBACK_BUY | SL = zone bottom - 10-15p
   ⚠️ PULLBACK CHECK: pullback valid ก็ต่อเมื่อ price ไม่ close เกิน rejection level
      SELL: pullback close ต้องต่ำกว่า OB top (rejection high) — ถ้า close เกิน = INVALIDATED
      BUY:  pullback close ต้องสูงกว่า OB bottom (rejection low) — ถ้า close เกิน = INVALIDATED
   confidence: 60-75 (OB ยังไม่ถูก invalidate → valid re-entry)

1.6. ★★ CASE J — Strong Rejection at Key Level (Pattern 4 — ไม่ต้องมี OB):
   ดูจาก EQL/EQH sweep หรือ Swing High/Low ที่โดน sweep แบบรุนแรง
   🔴 J1 — STRONG_REJECTION_SELL: EQH/Swing High ถูก sweep → bearish rejection candle แรง (wick ยาวมาก) → SELL
            ไม่ต้องอยู่ใน Bear OB แต่ต้องมั่นใจว่า rejection level ยังคงอยู่
            SL: เหนือ rejection high + 10-15p | setup_type = STRONG_REJECTION_SELL
   🟢 J2 — STRONG_REJECTION_BUY: EQL/Swing Low ถูก sweep → bullish rejection candle แรง → BUY
            SL: ต่ำกว่า rejection low - 10-15p | setup_type = STRONG_REJECTION_BUY
   ⚠️ PULLBACK CHECK (สำคัญมาก): pullback ต้องไม่ทะลุ rejection level
      SELL: candle close ต้องต่ำกว่า EQH/Swing High ที่ sweep — ถ้า close เกินนั้น = INVALIDATED → NO_TRADE
      BUY:  candle close ต้องสูงกว่า EQL/Swing Low ที่ sweep — ถ้า close เกินนั้น = INVALIDATED
   confidence: 50-65 (ไม่มี OB รองรับ — ใช้ EQL/EQH sweep confirmation เป็นหลัก)

1.7. ★★ CASE H — Post-Sweep Continuation Pullback:
   🔴 H1 — POST_SWEEP_PULLBACK_SELL:
      BSL ถูก sweep แล้ว + ราคาวิ่งลงมาแล้ว + ตอนนี้มี pullback กลับขึ้น (15-65% retracement)
      → SELL ที่ pullback นี้ ตามทิศ sweep rejection
      Entry: ราคาปัจจุบัน หรือรอ pullback ชะลอ (ดู Bear OB ที่ใกล้ราคาสุดเป็น SL)
      SL: Bear OB top + 10-15p (ถ้ามี) หรือ swing high ล่าสุด + 10p
      TP: low ก่อนหน้า / nearest_ssl / Bull OB ใต้ราคา
      setup_type = POST_SWEEP_PULLBACK_SELL
      ⚠️ ลำดับความสำคัญ: ถ้าตอนนี้ pullback มาแตะ Bear OB พอดี → confidence สูงมาก (CASE F + H รวมกัน)
         ถ้า pullback ไม่มี OB รองรับ → confidence ปานกลาง (50-65) แต่ก็ valid
   🟢 H2 — POST_SWEEP_PULLBACK_BUY:
      SSL ถูก sweep แล้ว + ราคาวิ่งขึ้นไปแล้ว + ตอนนี้มี pullback ลงมา (15-65% retracement)
      → BUY ที่ pullback นี้ ตามทิศ sweep rejection
      SL: Bull OB bottom - 10-15p (ถ้ามี) หรือ swing low ล่าสุด - 10p
      setup_type = POST_SWEEP_PULLBACK_BUY
2. ★★ ราคาที่ Bull OB + macro BULL → BUY, setup_type=TREND_OB (support+trend ตรงกัน)
   ★★ ราคาที่ Bear OB + macro BEAR → SELL, setup_type=TREND_OB (resistance+trend ตรงกัน)
3. ★★ BOS ขึ้น + pullback มา Bear OB เดิม + macro BULL → BUY, setup_type=BREAKER_BLOCK
   ★★ BOS ลง + pullback มา Bull OB เดิม + macro BEAR → SELL, setup_type=BREAKER_BLOCK
4. CASE A + A2 ผ่าน → YES, setup_type=TREND_BOS_BREAK, pyramid_mode=true
5. CASE B + B1 (sweep+reject) → YES, setup_type=BULL_OB_SWEEP_REJECT
6. CASE B + B2 → YES, setup_type=BULL_OB_ENTRY, pyramid_mode=true
7. CASE C1 (eql_sweep_signal ≠ null) → YES, setup_type=EQL_SWEEP_BUY
8. CASE C2 (eqh_sweep_signal ≠ null) → YES, setup_type=EQH_SWEEP_SELL
9. CASE D1 (amd_signal=AMD_BUY) → YES, setup_type=AMD_BUY
10. CASE D2 (amd_signal=AMD_SELL) → YES, setup_type=AMD_SELL
11. CASE E1 (ราคาใกล้ PDH/SwingHigh/Round + bearish reject) → YES, setup_type=SR_SELL
    CASE E2 (ราคาใกล้ PDL/SwingLow/Round + bullish reject)  → YES, setup_type=SR_BUY
12. ไม่มีเงื่อนไขผ่านเลย → NO, ระบุใน trade_plan ว่าราคากำลังมุ่งหา liquidity pool ไหน (BSL/SSL)

⛔ กฎ SL (บังคับ — วาง SL หลุดแนว structure ไปซักหน่อย):
  SELL: stop_loss = ob_top + 10-15 จุด (พ้น Bear OB top เล็กน้อย — หลุดแนว ไม่ต้องไกลมาก)
        ตัวอย่าง: ob_top=4344 → SL ควรอยู่ที่ 4345.0–4345.5
  BUY:  stop_loss = ob_bottom - 10-15 จุด (พ้น Bull OB bottom เล็กน้อย — หลุดแนว)
        ตัวอย่าง: ob_bottom=4317 → SL ควรอยู่ที่ 4315.5–4316.0
  Sweep setups (EQL/EQH/AMD): SL = sweep_level + 10-15 จุด (หลุด wick sweep ไปนิดนึง)
  ไม่วาง SL แน่นจน price wick ปกติโดน

ตอบ JSON เท่านั้น:
{{
  "vote": "YES/NO",
  "vote_reasoning": "1-2 ประโยค — ระบุ Case A/B/C + zone + เหตุผล",
  "signal": "BUY/SELL/NO_TRADE",
  "setup_type": "BSL_SWEEP_SELL/SSL_SWEEP_BUY/OB_REJECTION_SELL/OB_REJECTION_BUY/POST_SWEEP_PULLBACK_SELL/POST_SWEEP_PULLBACK_BUY/STORED_OB_PULLBACK_SELL/STORED_OB_PULLBACK_BUY/STRONG_REJECTION_SELL/STRONG_REJECTION_BUY/TREND_OB/BREAKER_BLOCK/TREND_BOS_BREAK/BULL_OB_SWEEP_REJECT/BULL_OB_ENTRY/EQL_SWEEP_BUY/EQH_SWEEP_SELL/AMD_BUY/AMD_SELL/SR_SELL/SR_BUY/WAIT_FOR_OB/NO_TRADE",
  "sr_level": ราคา S/R ที่ใช้เป็น entry reference (เฉพาะ SR_SELL/SR_BUY) หรือ null,
  "liquidity_target": ราคา BSL หรือ SSL pool ที่ราคากำลังมุ่งหา (เฉพาะ F cases หรือ NO_TRADE ที่รอ sweep) หรือ null,
  "inducement_level": ราคา inducement pool ถ้ามี (minor pool ที่อยู่ระหว่างราคากับ target) หรือ null,
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
  "liquidity_map_read": "สรุป 1 ประโยคว่า nearest BSL/SSL อยู่ที่ไหน swept หรือยัง และราคากำลังมุ่งหาอะไร",
  "reasoning": "ภาษาไทย: ① H1/H4 macro ② Liquidity map — nearest BSL/SSL swept แล้วหรือยัง ③ OB ที่ใกล้ที่สุดคือไหน ④ มี sweep+rejection มั้ย ⑤ setup ที่เลือก (Case ไหน) ⑥ pyramid plan ⑦ TP logic (Bear OB / BSL / PDH / swing)"
}}"""

    from agents.sdk_utils import sdk_query
    raw_text = sdk_query(prompt, label="ChartAnalyst")
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
    # ส่ง recent_ob_rejection กลับไปให้ notifier เพื่อ save ลง bot_state (Pattern 3)
    result["recent_bear_ob_rejection"] = smc_summary.get("recent_bear_ob_rejection")
    result["recent_bull_ob_rejection"] = smc_summary.get("recent_bull_ob_rejection")

    def _ob_zone(ob):
        if not ob: return None
        return {"top": ob.get("top"), "bottom": ob.get("bottom"), "tf": ob.get("tf", ""), "in_ob": ob.get("in_ob", False)}

    result["bull_ob_zone"] = _ob_zone(primary_bull_ob)
    result["bear_ob_zone"] = _ob_zone(primary_bear_ob)

    # ── SL sanity check: ถูกทิศ + พ้น entry zone ──────────────────────────
    # entry zone มี buffer ±2pt จาก OB edge → SL ต้องอยู่นอก zone ด้วย ไม่แค่นอก OB
    sig = result.get("signal")
    sl  = result.get("stop_loss")
    ob_top_ref    = (primary_bear_ob or {}).get("top")
    ob_bottom_ref = (primary_bull_ob or {}).get("bottom")

    ENTRY_BUF = 2.0   # buffer ที่ notifier ใช้แสดง entry zone
    SL_PAD    = 0.5   # ระยะพ้น entry zone top/bottom เพิ่มอีกนิด

    if sig == "SELL":
        # SL ต้องอยู่เหนือ ob_top + ENTRY_BUF (พ้น entry zone top)
        min_sl_sell = round((ob_top_ref + ENTRY_BUF + SL_PAD) if ob_top_ref else (price_now + 2.5), 2)
        if not sl or sl <= price_now or sl < min_sl_sell:
            old_sl = sl
            result["stop_loss"] = min_sl_sell
            print(f"[ChartAnalyst] SELL SL {old_sl} ต่ำเกิน (ราคา={price_now} min={min_sl_sell}) → {min_sl_sell}")
    elif sig == "BUY":
        # SL ต้องอยู่ต่ำกว่า ob_bottom - ENTRY_BUF (พ้น entry zone bottom)
        max_sl_buy = round((ob_bottom_ref - ENTRY_BUF - SL_PAD) if ob_bottom_ref else (price_now - 2.5), 2)
        if not sl or sl >= price_now or sl > max_sl_buy:
            old_sl = sl
            result["stop_loss"] = max_sl_buy
            print(f"[ChartAnalyst] BUY SL {old_sl} สูงเกิน (ราคา={price_now} max={max_sl_buy}) → {max_sl_buy}")

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
