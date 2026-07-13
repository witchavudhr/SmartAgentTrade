"""
Supervisor — Multi-Agent Voting System
แต่ละ agent โหวต YES/NO พร้อม reasoning แล้ว Supervisor Sonnet ตัดสินใจสุดท้าย

Pipeline:
  1. SMC scan (rule-based, free)
  2. Chart Analyst (Sonnet) → VOTE YES/NO + signal + reasoning
  3. Bias Analyst  (Sonnet) → VOTE YES/NO รู้ signal direction แล้ว
  4. News Scout    (Sonnet) → VOTE YES/NO รู้ signal direction แล้ว
  5. Risk Manager  (rule)   → VETO power
  6. Supervisor   (Sonnet) → อ่าน reasoning ทั้ง 3 agent → APPROVE/REJECT
"""

import anthropic
import json
import time


def _md(text: str) -> str:
    """Escape Telegram Markdown v1 special chars ใน AI-generated text"""
    for ch in ('_', '*', '`', '['):
        text = text.replace(ch, f'\\{ch}')
    return text
from datetime import datetime

# ── Bias cache (15 นาที) — H1/H4 ไม่เปลี่ยนทุก 5 นาที ──────────────────
_BIAS_CACHE_TTL = 15 * 60  # 900 วินาที
_bias_cache: dict | None = None
_bias_cache_ts: float = 0.0


def _get_bias(signal_direction: str) -> dict:
    global _bias_cache, _bias_cache_ts
    age = time.time() - _bias_cache_ts
    if _bias_cache is not None and age < _BIAS_CACHE_TTL:
        print(f"[Supervisor] ♻️ bias cache hit ({int(age)}s old, TTL={_BIAS_CACHE_TTL}s)")
        return _bias_cache
    result = bias_analyst.analyze(signal_direction=signal_direction)
    _bias_cache = result
    _bias_cache_ts = time.time()
    print(f"[Supervisor] 🔄 bias refreshed (cache miss, age was {int(age)}s)")
    return result
from config.settings import ANTHROPIC_API_KEY, MODEL_SMART
from agents import chart_analyst, bias_analyst, news_scout, risk_manager
from agents.trade_log import get_performance_summary, get_loss_lesson_digest
from agents.json_utils import safe_json_parse

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def run(balance: float = 10000.0, force_session: bool = False, context: dict = None) -> dict:
    """
    รัน pipeline ทั้งหมด:
    1. SMC scan (ฟรี)
    2. ถ้ามี signal → เช็ค news block
    3. เช็ค bias
    4. Risk Manager
    5. Supervisor vote
    6. คืน final decision
    """

    result = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "approved": False,
        "final_signal": "NO_TRADE",
        "vote_score": 0,
        "stages": {}
    }

    # ── Stage 1: SMC Engine (ฟรี) ─────────────────────────────
    _, smc_summary = chart_analyst.get_price_data()
    if not smc_summary:
        result["reject_reason"] = "ดึงข้อมูลราคาไม่ได้"
        return result

    result["current_price"]      = smc_summary.get("current_price")
    result["choch_sweep_setup"]  = smc_summary.get("choch_sweep_setup")

    # เพิ่ม liquidity snapshot ทุก scan (แม้ NO_SIGNAL) เพื่อให้ notifier ตรวจ sweep ได้
    _liq = smc_summary.get("liquidity", {})
    result["liq_snapshot"] = {
        "nearest_ssl": _liq.get("nearest_ssl"),
        "nearest_bsl": _liq.get("nearest_bsl"),
        "ssl_pools":   _liq.get("ssl_pools", []),
        "bsl_pools":   _liq.get("bsl_pools", []),
    }

    # ── Inject context จาก bot_state (Pattern 1 & 3) ──────────────
    if context:
        _orz = [z for z in (context.get("ob_rejection_zones") or [])
                if not z.get("used") and z.get("expire_at", "") > datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        if _orz:
            smc_summary["stored_ob_rejections"] = _orz
        _srw = context.get("sweep_rejection_watch")
        if _srw and _srw.get("expire_at", "") > datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
            smc_summary["sweep_rejection_watch"] = _srw

    # Pre-filter: ถ้าไม่มี signal ไม่เรียก Claude เลย
    if not chart_analyst.has_signal(smc_summary, force_session=force_session):
        result["reject_reason"] = "SMC Engine: ไม่มี setup ครบเงื่อนไข"
        result["stages"]["smc"] = "NO_SIGNAL"
        return result

    result["stages"]["smc"] = "SIGNAL_FOUND"
    # เก็บ smc setup ไว้เพื่อ notifier แสดงใน lightweight alert แม้ chart AI บอก NO_TRADE
    _eql = smc_summary.get("eql_eqh_sweep") or {}
    _sw  = smc_summary.get("last_sweep") or {}
    _pc  = smc_summary.get("post_sweep_continuation") or {}
    result["smc_setup"] = (
        _eql.get("signal")
        or (_sw.get("kind") and f"SWEEP_{_sw['kind'].upper()}")
        or (_pc.get("direction") and f"POST_SWEEP_{_pc['direction']}")
        or "SIGNAL"
    )
    result["current_price"] = smc_summary.get("current_price")

    # ── Stage 2: Chart Analyst — SDK (subscription) ──
    try:
        from agents import chart_analyst_agent
        analysis = chart_analyst_agent.analyze(smc_summary)
    except Exception as _sdk_err:
        print(f"[Supervisor] ⚠️ Agent SDK failed ({_sdk_err}) — skip scan (no API fallback)")
        result["reject_reason"] = f"SDK timeout/error — scan skipped"
        return result
    signal     = analysis.get("signal", "NO_TRADE")
    confidence = analysis.get("confidence", 0)
    chart_vote = analysis.get("vote", "NO")

    result["stages"]["chart"] = {
        "signal":        signal,
        "confidence":    confidence,
        "vote":          chart_vote,
        "vote_reasoning": analysis.get("vote_reasoning", ""),
        "reasoning":     analysis.get("reasoning"),
        "bull_ob_zone":  analysis.get("bull_ob_zone"),
        "bear_ob_zone":  analysis.get("bear_ob_zone"),
    }

    result["analysis"] = analysis  # เก็บไว้เสมอ เพื่อให้ format_alert แสดง signal/conf/setup จริง

    # ── Zone cooldown gate — กันเทรดซ้ำโซนราคาใกล้เคียงกันในเวลาไม่ห่างกัน ──
    # (เช่น sweep-based signal ที่ swing point ใหม่เกิดใกล้ๆ level ที่เพิ่ง confirmed
    # ไปหมาดๆ — บอทไม่มี memory ข้าม scan จึงมองเป็นสัญญาณสดได้ทั้งที่เป็นโซนเดิม)
    if signal in ("BUY", "SELL") and chart_vote == "YES":
        _entry_chk = analysis.get("entry")
        if _entry_chk is not None:
            from agents.trade_log import check_zone_cooldown
            _cd = check_zone_cooldown(signal, _entry_chk)
            if _cd:
                result["reject_reason"] = (
                    f"Zone Cooldown — เพิ่งเทรด {signal} ใกล้ระดับนี้ไปแล้วเมื่อ {_cd['timestamp']} "
                    f"(entry เดิม {_cd['entry_low']}, ใหม่ {_entry_chk}) ภายใน 4 ชม. — ข้ามรอบนี้กันเทรดซ้ำโซน"
                )
                result["stages"]["zone_cooldown"] = _cd
                return result

    if signal == "NO_TRADE" or chart_vote == "NO":
        result["reject_reason"] = f"Chart Analyst voted NO — {analysis.get('vote_reasoning', 'NO_TRADE')}"
        # ตรวจว่า Claude ปฏิเสธเพราะ Liquidity Gate (รอ SSL/BSL sweep) หรือเปล่า
        _vote_reason = (analysis.get("vote_reasoning") or "").lower()
        _trade_plan  = (analysis.get("trade_plan") or "").lower()
        _reasoning   = (analysis.get("reasoning") or "").lower()
        _all_text = _vote_reason + _trade_plan + _reasoning

        # ── Bypass keywords: ถ้า Claude บอกว่า sweep เพิ่งเกิดแล้ว + rejection → ไม่ gate ──
        _bypass_keywords = (
            "bsl ถูก sweep", "ssl ถูก sweep", "sweep แล้ว", "swept already",
            "rejection ที่ ob", "ob rejection", "wick rejection", "bearish rejection",
            "bullish rejection", "rejection candle", "หลัง sweep", "after sweep",
            "sweep เกิดแล้ว", "bsl swept", "ssl swept",
        )
        _is_post_sweep_rejection = any(kw in _all_text for kw in _bypass_keywords)

        _liq_keywords = ("รอ ssl", "รอ bsl", "liquidity gate", "ssl ยัง", "bsl ยัง",
                         "ยังไม่ถูก sweep", "wait for ssl", "wait for bsl", "ssl intact", "bsl intact")
        if any(kw in _all_text for kw in _liq_keywords) and not _is_post_sweep_rejection:
            result["liq_gate_blocked"]  = True
            result["liq_gate_level"]    = analysis.get("liquidity_target")
            result["liq_gate_map_read"] = analysis.get("liquidity_map_read", "")
            result["liq_gate_signal"]   = analysis.get("signal")
        return result

    # ── Stage 3: Bias Analyst (cached 15 นาที) — รู้ signal แล้ว ──
    bias = _get_bias(signal_direction=signal)
    bias_vote = bias.get("vote", "NO")

    result["stages"]["bias"] = {
        "overall":         bias.get("overall_bias"),
        "weekly_bias":     bias.get("weekly_bias"),
        "trade_direction": bias.get("trade_direction"),
        "vote":            bias_vote,
        "vote_reasoning":  bias.get("vote_reasoning", ""),
        "case":            bias.get("case", "?"),
        "at_htf_level":    bias.get("at_htf_level", False),
        "htf_level_detail":bias.get("htf_level_detail"),
        "from_cache":      bias.get("from_cache", False),
    }

    # ── Stage 4: News Scout (Sonnet, cached) — รู้ signal แล้ว ──
    news = news_scout.analyze(signal_direction=signal)
    news_vote = news.get("vote", "NO")
    blocked   = news.get("is_blocked", False)

    result["stages"]["news"] = {
        "risk_level":    news.get("risk_level"),
        "vote":          news_vote,
        "vote_reasoning": news.get("vote_reasoning", ""),
        "key_event":     news.get("key_event"),
        "next_event":    news.get("next_event"),
    }

    # ── Stage 5: Agent Voting ──────────────────────────────────
    votes = {
        "chart": chart_vote == "YES",
        "bias":  bias_vote  == "YES",
        "news":  news_vote  == "YES",
    }

    vote_score = sum(votes.values())
    result["vote_score"] = vote_score
    result["votes"]      = votes
    result["vote_details"] = {
        "chart": analysis.get("vote_reasoning", ""),
        "bias":  bias.get("vote_reasoning", ""),
        "news":  news.get("vote_reasoning", ""),
    }

    if vote_score < 1:
        result["reject_reason"] = "Vote 0/3 — ทุก agent reject"
        return result

    # ── Stage 6: Risk Manager (VETO) ──────────────────────────
    risk = risk_manager.evaluate(analysis, bias, balance)
    result["stages"]["risk"] = risk

    if risk.get("veto"):
        result["reject_reason"] = risk.get("veto_reason")
        return result

    setup_type = analysis.get("setup_type", "")
    # rr_ratio: Claude ส่งมาโดยตรง (ถ้าคำนวณเอง) หรือคำนวณจาก entry/sl/tp
    rr         = float(analysis.get("rr_ratio") or 0)
    if rr == 0:
        _tp_sv  = analysis.get("take_profit") or analysis.get("tp1")
        _sl_sv  = analysis.get("stop_loss")
        _ez_sv  = analysis.get("entry_zone") or analysis.get("entry")
        _em_sv  = ((_ez_sv[0] + _ez_sv[1]) / 2 if isinstance(_ez_sv, list) and len(_ez_sv) == 2
                   else float(_ez_sv) if _ez_sv else None)
        if _em_sv and _sl_sv and _tp_sv:
            try:
                rr = round(abs(float(_tp_sv) - _em_sv) / abs(_em_sv - float(_sl_sv)), 2)
            except (TypeError, ValueError, ZeroDivisionError):
                rr = 0

    # ── OB PROXIMITY GUARD — ก่อน fast-approve ──────────────────
    # OB_REJECTION / STORED_OB_PULLBACK: OB ต้องห่างจากราคาปัจจุบัน ≥10 pts
    # ถ้า OB ใกล้เกินไป → ราคาจะผ่านทะลุทันที ไม่มี significance
    _ob_prox_setups = {
        "OB_REJECTION_BUY", "OB_REJECTION_SELL",
        "STORED_OB_PULLBACK_BUY", "STORED_OB_PULLBACK_SELL",
    }
    _cur_px = result.get("current_price") or 0
    if setup_type in _ob_prox_setups and _cur_px:
        _ez_prox  = analysis.get("entry_zone") or analysis.get("entry")
        _ob_t     = (_ez_prox[1] if isinstance(_ez_prox, list) and len(_ez_prox) == 2
                     else float(_ez_prox) if _ez_prox else None)
        _ob_b     = (_ez_prox[0] if isinstance(_ez_prox, list) and len(_ez_prox) == 2
                     else float(_ez_prox) if _ez_prox else None)
        if _ob_t and _ob_b:
            _too_close = False
            _prox_pts  = 0.0  # distance in pts (1 pt = $1 for XAUUSD)
            if signal == "BUY":
                # _prox_pts > 0  = ราคายังอยู่เหนือ ob_top (กำลังเข้าใกล้ แต่ยังไม่ถึง)
                # _prox_pts <= 0 = ราคาอยู่ใน/ต่ำกว่า OB แล้ว (valid rejection) → ไม่ filter
                _prox_pts = round(_cur_px - _ob_t, 1)
                if 0 < _prox_pts < 15:
                    _too_close = True
            else:
                # _prox_pts > 0  = ราคายังอยู่ต่ำกว่า ob_bottom (กำลังเข้าใกล้ แต่ยังไม่ถึง)
                # _prox_pts <= 0 = ราคาอยู่ใน/เหนือ OB แล้ว (valid rejection) → ไม่ filter
                _prox_pts = round(_ob_b - _cur_px, 1)
                if 0 < _prox_pts < 15:
                    _too_close = True
            if _too_close:
                result["reject_reason"] = (
                    f"OB ใกล้เกินไป — ราคา {_cur_px} ห่าง OB {_ob_b}–{_ob_t} แค่ {_prox_pts} pts "
                    f"(ต้องการ ≥15 pts เพื่อให้มี displacement พอก่อน rejection)\n"
                    f"รอ OB ที่ไกลกว่าหรือรอ setup ใหม่"
                )
                return result

    # ── Fast APPROVE: OB setups — rule-based ไม่ต้องให้ Claude ตัดสิน ──
    # เพิ่มเงื่อนไข: TREND_OB ต้องมี entry zone ใกล้ OB จริงๆ
    # ป้องกัน Claude return TREND_OB แต่ entry zone ไม่ใช่ OB (กลางอากาศ)
    ob_setups = {"BULL_OB_ENTRY", "BULL_OB_SWEEP_REJECT", "TREND_OB", "TREND_BOS_BREAK"}

    # ตรวจ entry zone ตรงกับ OB จริงมั้ย (เฉพาะ TREND_OB)
    _entry_zone = analysis.get("entry_zone") or []
    _entry_mid  = ((_entry_zone[0] + _entry_zone[1]) / 2) if len(_entry_zone) == 2 else None
    _bear_ob    = analysis.get("bear_ob_zone") or {}
    _bull_ob    = analysis.get("bull_ob_zone") or {}
    _ob_zone_ok = True
    if setup_type == "TREND_OB" and _entry_mid:
        if signal == "SELL" and _bear_ob:
            # entry ต้องอยู่ใกล้ Bear OB ≤300p
            bear_bottom = _bear_ob.get("bottom", _entry_mid)
            dist_entry_ob = abs(_entry_mid - bear_bottom) * 10
            if dist_entry_ob > 300:
                _ob_zone_ok = False
                result["reject_reason"] = (
                    f"TREND_OB SELL rejected: entry zone {_entry_zone} ห่าง Bear OB "
                    f"{_bear_ob.get('bottom')}–{_bear_ob.get('top')} อยู่ {dist_entry_ob:.0f}p — กลางอากาศ"
                )
        elif signal == "BUY" and _bull_ob:
            # entry ต้องอยู่ใกล้ Bull OB ≤300p
            bull_top = _bull_ob.get("top", _entry_mid)
            dist_entry_ob = abs(_entry_mid - bull_top) * 10
            if dist_entry_ob > 300:
                _ob_zone_ok = False
                result["reject_reason"] = (
                    f"TREND_OB BUY rejected: entry zone {_entry_zone} ห่าง Bull OB "
                    f"{_bull_ob.get('bottom')}–{_bull_ob.get('top')} อยู่ {dist_entry_ob:.0f}p — กลางอากาศ"
                )

    # ── TREND_OB bias conflict guard ──────────────────────────────
    # TREND_OB หมายถึง "trend-aligned" — ถ้า bias ขัดแย้งกับ signal ให้ reject ทันที
    # (ป้องกัน SELL ขณะ BULL bias หรือ BUY ขณะ BEAR bias)
    if setup_type == "TREND_OB" and _ob_zone_ok:
        _bias_dir = (bias.get("trade_direction") or "").upper()
        if signal == "SELL" and _bias_dir == "BUY":
            result["reject_reason"] = (
                f"TREND_OB SELL rejected: Bias = BUY (BULL) — ไม่ใช่ trend-aligned, ห้าม SELL สวนทาง"
            )
            return result
        if signal == "BUY" and _bias_dir == "SELL":
            result["reject_reason"] = (
                f"TREND_OB BUY rejected: Bias = SELL (BEAR) — ไม่ใช่ trend-aligned, ห้าม BUY สวนทาง"
            )
            return result

    if setup_type in ob_setups and chart_vote == "YES" and rr >= 1.5 and not blocked and _ob_zone_ok:
        auto_reason = {
            "BULL_OB_SWEEP_REJECT": "Sweep + rejection ที่ Bull OB — สัญญาณแข็งที่สุด auto-approve",
            "BULL_OB_ENTRY":        f"ราคาอยู่ที่ Bull OB (pyramid ไม้ 1) — RR {rr} auto-approve",
            "TREND_OB":             f"Trend-aligned OB entry — RR {rr} auto-approve",
            "TREND_BOS_BREAK":      f"BOS break pyramid — RR {rr} auto-approve",
        }.get(setup_type, f"{setup_type} auto-approve")

        liq_target_a = analysis.get("liquidity_target")
        idm_level_a  = analysis.get("inducement_level")
        liq_read_a   = analysis.get("liquidity_map_read", "")
        auto_liq = (
            f"BSL/SSL target: {liq_target_a}" if liq_target_a else ""
        ) + (f" | Inducement: {idm_level_a}" if idm_level_a else "")

        result["approved"]         = True
        result["final_signal"]     = signal
        result["lot"]              = risk.get("lot")
        result["risk_pct"]         = risk.get("risk_pct")
        result["caution_mode"]     = risk.get("caution_mode", False)
        result["entry_zone"]       = analysis.get("entry_zone")
        result["stop_loss"]        = analysis.get("stop_loss")
        result["take_profit"]      = analysis.get("take_profit") or analysis.get("tp1")
        result["rr_ratio"]         = rr
        result["reasoning"]        = auto_reason
        result["entry_condition"]  = analysis.get("vote_reasoning", auto_reason)
        result["liquidity_summary"]= liq_read_a or auto_liq or "–"
        result["analysis"]         = analysis
        result["stages"]["supervisor"] = {
            "approve": True, "reasoning": auto_reason,
            "entry_condition": result["entry_condition"],
            "liquidity_summary": result["liquidity_summary"],
            "auto": True,
        }
        return result

    # ── Stage 7: Supervisor Final Decision — สำหรับ setup ที่ไม่ชัด ──
    verdict = _supervisor_judge(analysis, bias, news, risk, vote_score, result["vote_details"], smc_summary)
    result["stages"]["supervisor"] = verdict

    if verdict.get("approve"):
        result["approved"]          = True
        result["final_signal"]      = signal
        result["lot"]               = risk.get("lot")
        result["risk_pct"]          = risk.get("risk_pct")
        result["caution_mode"]      = risk.get("caution_mode", False)
        result["entry_zone"]        = analysis.get("entry_zone")
        result["stop_loss"]         = analysis.get("stop_loss")
        result["take_profit"]       = analysis.get("take_profit") or analysis.get("tp1")
        result["rr_ratio"]          = rr
        result["reasoning"]         = verdict.get("reasoning")
        result["entry_condition"]   = verdict.get("entry_condition", "")
        result["liquidity_summary"] = verdict.get("liquidity_summary", "")
        result["analysis"]          = analysis
    else:
        result["reject_reason"] = verdict.get("reasoning", "Supervisor rejected")

    return result


def _supervisor_judge(analysis, bias, news, risk, vote_score, vote_details: dict, smc_summary: dict = None) -> dict:
    """
    Supervisor ตัดสินใจสุดท้าย — Claude Sonnet อ่าน reasoning ทุก agent
    ไม่ได้แค่นับ vote score แต่เข้าใจ WHY แต่ละ agent โหวต
    """
    chart_r = vote_details.get("chart", analysis.get("reasoning", ""))
    bias_r  = vote_details.get("bias",  bias.get("reasoning", ""))
    news_r  = vote_details.get("news",  news.get("reasoning", ""))

    # สร้าง vote summary สำหรับ prompt
    chart_vote_str = analysis.get('vote', '?')
    bias_vote_str  = bias.get('vote', '?')
    news_vote_str  = news.get('vote', '?')
    bias_case      = bias.get('case', '?')
    at_htf         = bias.get('at_htf_level', False)
    htf_detail     = bias.get('htf_level_detail', '')

    perf = get_performance_summary(days=30)
    loss_ctx = get_loss_lesson_digest()

    # Late NY session context
    from agents.smc_engine import get_session
    sess_now = get_session()
    is_late_ny = sess_now.get("is_late_ny", False)
    late_ny_warning = (
        "\n⚠️ *LATE NY SESSION (00:00–04:00 Thai)* — liquidity ต่ำ spread กว้าง\n"
        "   → ต้องการ confidence ≥ 75% + setup ชัดมาก (SWEEP_REJECT เท่านั้น)\n"
        "   → ถ้า setup เป็น BULL_OB_ENTRY หรือ TREND_OB ให้ REJECT ก่อน รอ session ปกติ\n"
    ) if is_late_ny else ""

    # Liquidity map info จาก chart analyst
    liq_target   = analysis.get("liquidity_target")
    idm_level    = analysis.get("inducement_level")
    liq_map_read = analysis.get("liquidity_map_read", "")
    setup_type_s = analysis.get("setup_type", "")

    # ── Build M15 context ──────────────────────────────────────────
    m15_ctx = ""
    if smc_summary:
        m15 = smc_summary.get("m15") or {}
        liq  = smc_summary.get("liquidity") or {}
        m15_bias   = m15.get("bias", "–")
        m15_bos    = m15.get("last_bos") or {}
        m15_choch  = m15.get("last_choch") or {}
        m15_ob_b   = m15.get("active_bull_ob") or {}
        m15_ob_bear= m15.get("active_bear_ob") or {}
        w_bsl = liq.get("weekly_bsl_pools") or []
        w_ssl = liq.get("weekly_ssl_pools") or []

        def _fmt_pool(pools, n=5):
            intact = [p for p in pools if not p.get("swept")][:n]
            swept  = [p for p in pools if p.get("swept")][:3]
            parts  = [f"{p['level']}({'M15' if p.get('timeframe')=='M15' else 'M5'}{'★' if p.get('size')=='major' else ''})" for p in intact]
            parts += [f"{p['level']}(✓swept)" for p in swept]
            return " | ".join(parts) if parts else "–"

        m15_ctx = f"""
🕐 M15 Context (ใช้เป็นเหตุผลประกอบ ไม่ใช่ vote):
   Bias: {m15_bias}
   BOS: {m15_bos.get('direction','–')} @ {m15_bos.get('level','–')}
   CHoCH: {m15_choch.get('direction','–')} @ {m15_choch.get('level','–')}
   Bull OB: {m15_ob_b.get('bottom','–')}–{m15_ob_b.get('top','–')} (in_ob={m15_ob_b.get('in_ob',False)})
   Bear OB: {m15_ob_bear.get('bottom','–')}–{m15_ob_bear.get('top','–')} (in_ob={m15_ob_bear.get('in_ob',False)})
   Weekly BSL (7d intact→swept): {_fmt_pool(w_bsl)}
   Weekly SSL (7d intact→swept): {_fmt_pool(w_ssl)}
   ★=EQH/EQL major level, M15=stronger level, ✓swept=โดน sweep ไปแล้ว
   → ถ้า signal ตรงกับ M15 bias = confluence สูง
   → ถ้า M15 Bear OB อยู่ใกล้ + BUY signal = OB นั้นอาจเป็น target หรือ resistance — ระบุในเหตุผล
   → Weekly SSL ที่ยัง intact + ราคาใกล้ = แนวที่รอ sweep อยู่
"""

    prompt = f"""คุณคือ Supervisor Agent — ตัดสินใจสุดท้าย APPROVE หรือ REJECT trade นี้
Vote รวม {vote_score}/3 — อ่านเหตุผลของทุก agent แล้วชั่งน้ำหนักเอง (ไม่ต้องนับเสียงข้างมาก)

{perf}
⚠️ Performance ข้างบนเป็นแค่ context — ห้ามนำ WR% หรือ sample size มาตั้งเกณฑ์ confidence ขั้นต่ำ
   การ APPROVE/REJECT ดูจากเงื่อนไข OB/setup/RR เท่านั้น ไม่ใช่จาก WR ประวัติ
{late_ny_warning}
{loss_ctx}

═══ Agent Votes & Reasoning ═══
🔍 Chart Analyst [{chart_vote_str}]
   {chart_r}
   → Signal: {analysis.get('signal')} | Confidence: {analysis.get('confidence')}% | Setup: {setup_type_s} | RR: 1:{analysis.get('rr_ratio')}
   → Entry: {analysis.get('entry_zone')} | SL: {analysis.get('stop_loss')} | TP: {analysis.get('take_profit')}

🌊 Liquidity Map (จาก Chart Analyst):
   {liq_map_read or '–'}
   BSL/SSL Target: {liq_target or '–'} | Inducement: {idm_level or '–'}

🌍 Bias Analyst [{bias_vote_str}] (Case {bias_case}{' — ถึง HTF level แล้ว' if at_htf else ''})
   {bias_r}
   → Weekly={bias.get('weekly_bias')} Daily={bias.get('daily_bias')} H4={bias.get('h4_bias')} H1={bias.get('h1_bias')}
   → Direction: {bias.get('trade_direction')} | HTF Level: {htf_detail or '–'}

📰 News Scout [{news_vote_str}]
   {news_r}
   → Risk: {news.get('risk_level')} | Key Event: {news.get('key_event')} | Gold Impact: {news.get('gold_impact')}

⚖️ Risk Manager: Lot={risk.get('lot')} | Risk={risk.get('risk_pct')}% | Caution={risk.get('caution_mode')} | {risk.get('notes','')}
{m15_ctx}
═══ วิธีตัดสิน ═══

── กฎเหล็ก (ห้ามฝ่าฝืน) ──
✅ APPROVE ทันทีถ้า:
   • setup_type = BSL_SWEEP_SELL / SSL_SWEEP_BUY → liquidity sweep เกิดแล้ว + rejection = highest conviction
   • setup_type = BULL_OB_SWEEP_REJECT → sweep+reject ที่ OB เกิดแล้ว = confirmation ชัดที่สุด
   • setup_type = BULL_OB_ENTRY + Chart YES + ราคาอยู่ใน OB + RR ≥ 1.5
     → นี่คือ pyramid ไม้ 1 เล็กๆ ก่อน ไม่ต้องรอ confirmation เพิ่ม
     → "counter-trend" ไม่ใช่เหตุผล reject สำหรับ BULL_OB setup เพราะ swing มีในทุก trend
   • setup_type = TREND_OB + Chart YES → trend-aligned entry approve ได้เลย

❌ REJECT ได้แค่ถ้า:
   • มีข่าว High Impact ใน 30 นาที (ฟัง News Scout)
   • Risk Manager VETO (loss streak / daily limit)
   • Chart Analyst NO หรือ confidence < 30%
   • OB ถูก mitigated แล้ว (Chart ระบุ)
   • RR < 1.5

── ชั่งน้ำหนัก ──
1. Chart Analyst = agent หลัก น้ำหนักสูงสุด
2. Bias NO เพราะ "counter-trend" → น้ำหนักต่ำ ถ้า setup เป็น BULL_OB หรือ BSL/SSL sweep
3. Bias NO เพราะข่าว/HTF structure พัง → น้ำหนักสูง
4. News NO เพราะข่าว High Impact → น้ำหนักสูงที่สุด ต้องฟัง

ตอบ JSON เท่านั้น — ห้ามมีข้อความ/markdown อธิบายก่อนหรือหลัง JSON เด็ดขาด ตัวอักษรแรกของคำตอบต้องเป็น "{{":
{{
  "approve": true/false,
  "confidence": 0-100,
  "entry_condition": "ระบุให้ชัด: เข้าเงื่อนไข Case ไหน (A/B/C/D/E/F) และทำไม เช่น 'Case F1 — BSL ที่ 3350 swept แล้ว + bearish wick ยาว → BSL_SWEEP_SELL' หรือ 'Case B1 — EQL swept + rejection ที่ Bull OB 3180 → BULL_OB_SWEEP_REJECT'",
  "liquidity_summary": "สรุปสถานะ liquidity: BSL อยู่ที่ไหน / SSL อยู่ที่ไหน / ราคากำลังมุ่งหาอะไร / inducement มีมั้ย เช่น 'SSL (EQL) ที่ 3165 swept แล้ว, BSL ที่ 3330 คือ next target TP, ไม่มี inducement ระหว่างทาง'",
  "key_agent": "chart/bias/news — agent ที่มีน้ำหนักมากสุด",
  "reject_reason_detail": {{
    "chart": "เหตุผลจาก Chart vote",
    "bias": "เหตุผลจาก Bias vote",
    "news": "เหตุผลจาก News vote",
    "supervisor": "เหตุผลสุดท้ายที่ supervisor ตัดสิน"
  }},
  "what_to_watch": "⚠️ REQUIRED ถ้า approve=false — ระบุให้ชัดว่าต้องรออะไรและที่ราคาเท่าไร เช่น 'รอ pullback ถึง Bull OB 3180–3182 แล้วดู rejection candle' หรือ 'รอ sweep SSL ที่ 3165 ก่อน แล้วค่อย BUY' — ต้องมีราคาอ้างอิงเสมอ ห้าม null เมื่อ reject",
  "reasoning": "2-3 ประโยค ภาษาไทย — ระบุ: ① เข้าเงื่อนไขไหน ② liquidity อยู่ตรงไหน ③ ทำไมถึง approve/reject"
}}"""

    from agents.sdk_utils import api_query
    raw = api_query(prompt, model=MODEL_SMART, label="Supervisor", max_tokens=1500)
    result = safe_json_parse(
        raw,
        fallback={"approve": vote_score >= 2, "confidence": 0, "reasoning": "JSON parse error — auto by vote score"}
    )
    result["confidence"] = int(result.get("confidence", 0))
    return result


def analyze_reentry(open_trade: dict, current_price: float, smc_summary: dict) -> dict:
    """
    Sonnet วิเคราะห์ว่าควร re-enter หลังราคากลับมาที่ entry zone หรือไม่
    เรียกเมื่อราคาดิ่งกลับมาใกล้ entry หลังจากเคยกำไร ≥200p
    """
    direction = open_trade.get("direction", "?")
    entry     = open_trade.get("entry", 0)
    peak      = open_trade.get("peak_price", entry)
    original_sl = open_trade.get("original_sl", 0)
    tp        = open_trade.get("tp", 0)
    profit_had = abs(peak - entry)

    active_ob    = smc_summary.get("active_ob")
    last_choch   = smc_summary.get("last_choch")
    last_sweep   = smc_summary.get("last_sweep")
    equal_highs  = smc_summary.get("equal_highs")
    equal_lows   = smc_summary.get("equal_lows")
    nearest_fvg  = smc_summary.get("nearest_fvg")

    prompt = f"""คุณคือ Chart Analyst ผู้เชี่ยวชาญ SMC
trade นี้เปิดไปแล้ว แต่ราคากลับมาใกล้ entry zone — วิเคราะห์ว่าควร re-enter หรือยกเลิก

═══ สถานะ Trade เดิม ═══
Direction:  {direction}
Entry:      {entry}
Peak Price: {peak} (เคย profit {profit_had:.1f}p)
ราคาปัจจุบัน: {current_price} (กลับมาจาก peak แล้ว {abs(current_price - peak):.1f}p)
Original SL: {original_sl}  |  TP: {tp}

═══ SMC Context ตอนนี้ ═══
Active OB:   {active_ob}
CHoCH ล่าสุด: {last_choch}
Sweep ล่าสุด: {last_sweep}
EQH: {equal_highs}  |  EQL: {equal_lows}
FVG ใกล้สุด: {nearest_fvg}

วิเคราะห์:
1. ราคาอยู่ที่ OB หรือ Key Level จริงมั้ย?
2. Structure ยัง intact อยู่มั้ย (CHoCH ยังสด/ไม่ถูก invalidate)?
3. ถ้า re-enter SL ใหม่ควรอยู่ที่ไหน?
4. หรือ pullback นี้เป็นสัญญาณว่า thesis เสียแล้ว?

ตอบ JSON เท่านั้น — ห้ามมีข้อความ/markdown อธิบายก่อนหรือหลัง JSON เด็ดขาด ตัวอักษรแรกของคำตอบต้องเป็น "{{":
{{
  "reenter": true/false,
  "confidence": 0-100,
  "reasoning": "2-3 ประโยค ภาษาไทย — ระบุว่า OB/Level ที่กลับมาถึงคืออะไร",
  "new_sl": ราคา SL ใหม่ หรือ null,
  "caution": "ข้อควรระวัง 1 ประโยค" หรือ null
}}"""

    from agents.sdk_utils import api_query
    raw = api_query(prompt, model=MODEL_SMART, label="ReEntry", max_tokens=1000)
    return safe_json_parse(
        raw,
        fallback={"reenter": False, "confidence": 0, "reasoning": "Parse error", "new_sl": None, "caution": None}
    )


def _fmt_next_event(news_s: dict) -> str:
    """บรรทัดข่าวถัดไป: 'ข่าวต่อไป: NFP — พรุ่งนี้ 19:30 (อีก 21.5 ชม.)'"""
    ne = news_s.get("next_event") if isinstance(news_s, dict) else None
    if not ne:
        return ""
    name = _md(str(ne.get("event", ""))[:40])
    hrs  = ne.get("hours_until", 0)
    when = f"{ne.get('day_label','')} {ne.get('time_label','')}".strip()
    impact = ne.get("impact", "")
    icon = "🔴" if impact == "High" else "🟡" if impact == "Medium" else "⚪"
    if hrs < 1:
        left = f"อีก {ne.get('minutes_until', 0)} นาที"
    else:
        left = f"อีก {hrs:g} ชม."
    return f"\n   📅 ข่าวต่อไป: {icon} {name} — {when} ({left})"


def format_alert(result: dict) -> str:
    """แปลง supervisor result เป็น Telegram alert"""

    if not result.get("approved"):
        vote_score  = result.get("vote_score", 0)
        votes_map   = result.get("votes", {})
        vote_detail = result.get("vote_details", {})
        stages      = result.get("stages", {})
        chart_s     = stages.get("chart", {})
        bias_s      = stages.get("bias", {})
        news_s      = stages.get("news", {})
        risk_s      = stages.get("risk", {})
        sup_stage   = stages.get("supervisor", {}) or {}
        watch       = sup_stage.get("what_to_watch", "") if isinstance(sup_stage, dict) else ""
        sup_r       = result.get("reject_reason", "ไม่ผ่านเงื่อนไข")
        analysis    = result.get("analysis", {}) or {}

        def vi(v): return "✅" if v == "YES" or v is True else "❌"

        chart_vote = chart_s.get("vote", votes_map.get("chart") and "YES" or "NO")
        bias_vote  = bias_s.get("vote",  votes_map.get("bias")  and "YES" or "NO")
        news_vote  = news_s.get("vote",  votes_map.get("news")  and "YES" or "NO")

        chart_r = _md((vote_detail.get("chart") or chart_s.get("vote_reasoning") or "")[:120])
        bias_r  = _md((vote_detail.get("bias")  or bias_s.get("vote_reasoning")  or "")[:120])
        news_r  = _md((vote_detail.get("news")  or news_s.get("vote_reasoning")  or "")[:100])

        setup_type = analysis.get("setup_type", "–")
        conf       = analysis.get("confidence", "–")
        signal     = analysis.get("signal", "–")
        watch_line = f"\n👁 *รอดู:* _{_md(watch)}_" if watch else ""

        risk_line = ""
        if risk_s:
            veto = "⛔ VETO" if risk_s.get("veto") else "✅ OK"
            risk_line = f"*[6] Risk:* {veto} | Lot: `{risk_s.get('lot')}` ({risk_s.get('risk_pct')}%)\n"

        return (
            f"🔍 *Scan — 🔴 REJECTED* `({vote_score}/3)`\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💰 ราคา: `{result.get('current_price')}`\n\n"
            f"*[2] Chart:* {vi(chart_vote)} Signal: `{signal}` Conf: `{conf}%` Setup: `{setup_type}`\n"
            f"   _{chart_r}_\n\n"
            f"*[3] Bias:* {vi(bias_vote)} `{bias_s.get('trade_direction','–')}`\n"
            f"   _{bias_r}_\n\n"
            f"*[4] News:* {vi(news_vote)} Risk: `{news_s.get('risk_level','–')}`\n"
            f"   _{news_r}_"
            f"{_fmt_next_event(news_s)}\n\n"
            f"*[5] Vote:* `{vote_score}/3`\n"
            f"{risk_line}"
            f"*[7] Supervisor:* ❌\n"
            f"   _{_md(sup_r[:200])}_"
            f"{watch_line}\n"
            f"⏰ {result.get('timestamp')}"
        )

    signal = result.get("final_signal")
    emoji = "🟢" if signal == "BUY" else "🔴"
    caution = "🟡 *CAUTION MODE*\n" if result.get("caution_mode") else ""

    vote        = result.get("vote_score", 0)
    vote_bar    = "●" * vote + "○" * (3 - vote)
    vote_detail = result.get("vote_details", {})
    votes_map   = result.get("votes", {})

    vote_lines = ""
    bias_stage  = result.get("stages", {}).get("bias", {})
    news_stage  = result.get("stages", {}).get("news", {})
    for agent, passed in votes_map.items():
        icon    = "✅" if passed else "❌"
        reason  = vote_detail.get(agent, "")
        label   = {"chart": "Chart", "bias": "Bias ", "news": "News "}[agent]
        extra   = ""
        if agent == "bias" and bias_stage.get("at_htf_level"):
            extra = f" 📍_{_md(bias_stage.get('htf_level_detail','HTF level'))}_"
        vote_lines += f"\n  {icon} {label}: _{_md(reason[:60])}_{extra}"
        if agent == "news":
            vote_lines += _fmt_next_event(news_stage)

    entry = result.get("entry_zone")
    entry_str = f"`{entry[0]} - {entry[1]}`" if entry else "N/A"

    analysis   = result.get("analysis", {})
    setup_type = analysis.get("setup_type", "")
    rev_stars  = analysis.get("reversal_stars") or ""
    rev_score  = analysis.get("reversal_score", 0)

    if setup_type == "BULL_OB_SWEEP_REJECT":
        setup_line = f"🔥 Setup: *BULL OB SWEEP+REJECT* {rev_stars} — สัญญาณแข็งที่สุด\n"
    elif setup_type == "BULL_OB_ENTRY":
        setup_line = f"📍 Setup: *BULL OB ENTRY* (pyramid) {rev_stars}\n"
    elif "SWING_OB" in str(setup_type):
        setup_line = f"🔀 Setup: *SWING OB* {rev_stars} (score {rev_score}/10)\n"
    elif setup_type:
        setup_line = f"📐 Setup: `{setup_type}`\n"
    else:
        setup_line = ""

    # ── Entry zone with buffer ────────────────────────────────
    sl    = result.get("stop_loss")
    tp    = result.get("take_profit")
    rr    = result.get("rr_ratio")
    price = result.get("current_price")
    lot   = result.get("lot")

    # แสดง entry zone พร้อม buffer ±2 pips
    if entry and len(entry) == 2:
        buf = 2.0
        ez_lo = round(entry[0] - buf, 2)
        ez_hi = round(entry[1] + buf, 2)
        entry_str = f"`{ez_lo} – {ez_hi}`"
    else:
        entry_str = "N/A"

    # คำนวณ entry mid สำหรับหาระยะ SL/TP
    entry_mid = (entry[0] + entry[1]) / 2 if entry and len(entry) == 2 else (price or 0)

    def _pts(price_a, price_b):
        """ระยะห่างระหว่างสองราคา → จุด (×10)"""
        if price_a and price_b:
            return round(abs(float(price_a) - float(price_b)) * 10, 0)
        return None

    sl_pts  = _pts(entry_mid, sl)
    tp_pts  = _pts(entry_mid, tp)
    sl_line = f"  🛑 SL: `{sl}`" + (f"  _({int(sl_pts):,} จุด)_" if sl_pts else "") + "\n"

    # แสดง TP เป็น reference target (EA POS Guard จัดการ exit จริง)
    tp_ext = analysis.get("tp_extended")
    tp1_line = f"  🎯 TP: `{tp}`" + (f"  _({int(tp_pts):,} จุด)_" if tp_pts else "") + "  _(EA จัดการ)_\n"
    tp_lines = tp1_line
    if tp_ext:
        tp_ext_pts = _pts(entry_mid, tp_ext)
        tp_lines += f"  🎯 TP2: `{tp_ext}`" + (f"  _({int(tp_ext_pts):,} จุด)_" if tp_ext_pts else "") + "\n"

    pyramid_plan = analysis.get("pyramid_plan")
    pyramid_line = f"\n📐 *Pyramid:* _{_md(str(pyramid_plan))}_\n" if pyramid_plan else ""

    # Entry condition & liquidity summary
    entry_cond   = result.get("entry_condition", "")
    liq_summary  = result.get("liquidity_summary", "")
    liq_target_r = analysis.get("liquidity_target")
    idm_level_r  = analysis.get("inducement_level")

    cond_line = f"📌 *เงื่อนไข:* _{_md(entry_cond[:180])}_\n" if entry_cond else ""

    liq_line = ""
    if liq_summary and liq_summary != "–":
        liq_line = f"🌊 *Liquidity:* _{_md(liq_summary[:180])}_\n"
    if liq_target_r:
        liq_line += f"   🎯 Target: `{liq_target_r}`"
        if idm_level_r:
            liq_line += f" | IDM: `{idm_level_r}`"
        liq_line += "\n"

    return (
        f"🔔 *SETUP APPROVED — {signal}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{caution}"
        f"{emoji} *{signal}* | {setup_line.strip()}\n"
        f"🗳 Vote: `{vote_bar}` {vote}/3{vote_lines}\n\n"
        f"━━━ เหตุผล ━━━\n"
        f"{cond_line}"
        f"{liq_line}"
        f"━━━ จุดเข้า ━━━\n"
        f"{'🟢 BUY' if signal=='BUY' else '🔴 SELL'} zone: {entry_str}\n"
        f"{tp_lines}"
        f"{sl_line}"
        f"  ⚖️ RR: `1:{rr}` | Lot: `{lot}` ({result.get('risk_pct')}%)\n"
        f"{pyramid_line}"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 ราคาปัจจุบัน: `{price}`\n"
        f"📝 _{_md(result.get('reasoning', ''))}_\n"
        f"⏰ {result.get('timestamp')}"
    )
