"""
chart_analyst_agent.py — เรียก Claude ตรงผ่าน Anthropic API (Sonnet)

Flow:
  has_signal() ผ่าน → api_query() → parse JSON
"""

import time

from config.settings import MODEL_SMART
from agents.sdk_utils import api_query
from agents.chart_analyst import has_signal
from agents.json_utils import safe_json_parse

# ── Instructions + Pattern definitions (ใส่ใน prompt เพราะ SDK ไม่มี system param) ──
_INSTRUCTIONS = """\
You are an expert XAUUSD (Gold) SMC trading analyst. Return ONLY valid JSON — no markdown, no explanation.

PATTERN PRIORITY (highest → lowest):
1. CASE F ★★★★: BSL/SSL Sweep + Rejection → BSL_SWEEP_SELL / SSL_SWEEP_BUY (conf 75-90)
   NOTE: EQL (Equal Lows) = SSL pool; EQH (Equal Highs) = BSL pool — treat identically as CASE F
   last_sweep.source="EQL" → SSL_SWEEP_BUY | last_sweep.source="EQH" → BSL_SWEEP_SELL
2. CASE L ★★★: Post-BOS CHoCH Retest → POST_BOS_CHOCH_RETEST_SELL/BUY (conf 65-80)
   BOS bearish → minor bullish CHoCH (bounce) → price returns near CHoCH swing high → SELL
   BOS bullish → minor bearish CHoCH (dip) → price returns near CHoCH swing low → BUY
   Entry: at/near CHoCH swing high (SELL) or swing low (BUY) — the retracement extreme
   SL: post_bos_choch_retest.sl_ref (3 pts beyond swing_extreme)
   Conf bonus: +10 if rejection_confirmed=True | reduce -10 if retrace_pct > 60 (deep = risky)
   ⚡ This is the "pullback to CHoCH level after BOS" — trend continuation, HIGH probability
3. CASE I ★★★:  Stored OB Pullback (ob_rejection_zones) → STORED_OB_PULLBACK_SELL/BUY (conf 60-75)
4. CASE G ★★★:  OB Rejection (recent, no sweep needed) → OB_REJECTION_SELL/BUY (conf 65-80)
5. CASE J ★★:   Strong Rejection at EQL/EQH → STRONG_REJECTION_SELL/BUY (conf 50-65)
6. CASE H ★★:   Post-Sweep Pullback ≥15% ≤30 bars → POST_SWEEP_PULLBACK_SELL/BUY (conf 60-75)
   level_held=True means price never broke sweep extreme — valid re-entry regardless of pullback depth
   deeper pullback (>65%) = 2nd touch zone → conf+10 bonus
7. CASE K ★★:   CHoCH + Sweep → Reversal → CHOCH_SWEEP_SELL/BUY (conf 65-80)

CRITICAL RULES:
- PULLBACK VALIDITY: SELL pullback close must stay BELOW rejection high; BUY must stay ABOVE rejection low → if violated = INVALIDATED → NO_TRADE
- ob_quality=LOW (<50p gap) → reduce confidence 15-20
- When unsure → NO_TRADE (never force)
- BIAS CONTEXT: post_sweep_continuation and sweep ages are informational — use to assess context but do NOT hard-block signals. A fresh BUY sweep followed by a bounce into Bear OB is a valid SELL setup (distribution). Trust price action over bias lock.
- OB MIN DISTANCE (strictly enforced for OB_REJECTION and STORED_OB_PULLBACK setups):
  BUY OB: current_price must be >= ob_top + 15  (price must be ≥15 pts ABOVE OB top before pulling back in)
           If current_price < ob_top + 15 → OB too close, no displacement → NO_TRADE
  SELL OB: current_price must be <= ob_bottom - 15  (price must be ≥15 pts BELOW OB bottom)
            If current_price > ob_bottom - 15 → OB too close → NO_TRADE
  Rationale: reversal needs distance (displacement) to build momentum before hitting the OB.
             An OB within 15 pts will be consumed without a meaningful reaction — no stops collected.
             Only flag OBs where price must travel ≥15 pts to reach the zone.

PULLBACK ENTRY RULE (strictly enforced):
- FIRST pullback (pullback_status=FIRST): VALID — price just bounced from sweep, first retracement candle near sweep level = entry. OB is the TP target, NOT the entry zone.
- SECOND pullback (pullback_status=SECOND): VALID — price moved away then returned within ±$10 of sweep level; reduce confidence by 10
- EXPIRED (pullback_status=EXPIRED): NO_TRADE — setup stale, price too far from sweep zone
- NONE: follow normal OB rules
⚠️ For sweep setups: set entry = current price (near sweep level), NOT at active OB. The OB zone is TP1/TP2.

SWEEP DEPTH BONUS (depth = how far wick went beyond SSL/BSL level):
- depth ≥ 5 pts:  conf +5  (meaningful sweep)
- depth ≥ 10 pts: conf +10 (significant liquidity grab)
- depth ≥ 20 pts: conf +20 (major sweep — highest priority, stops heavily collected)
The deeper the sweep, the more stops were collected → stronger reversal → higher conviction

SL CALCULATION (mandatory — never output stop_loss=null when vote=YES):
- BSL_SWEEP_SELL: SL = last_sweep.level + 10.0 (base off the actual BSL pool level, NOT the wick —
  wick depth varies per sweep and can leave too little room; pyramid legs 2/3 need the extra buffer
  to avoid getting stopped out before they can even trigger)
- SSL_SWEEP_BUY:  SL = last_sweep.level - 10.0 (base off the actual SSL pool level, NOT the wick, same reason)
  ⚠️ Use last_sweep.level (the SSL/BSL pool price), not last_sweep.wick_extreme, as the SL anchor
- OB_REJECTION_SELL / STORED_OB_PULLBACK_SELL: SL = bear_ob.top + 3.0
- OB_REJECTION_BUY  / STORED_OB_PULLBACK_BUY:  SL = bull_ob.bottom - 3.0
- POST_SWEEP_PULLBACK_SELL: SL = last_sweep.level + 10.0
- POST_SWEEP_PULLBACK_BUY:  SL = last_sweep.level - 10.0
- CHOCH_SWEEP_SELL: SL = last_choch.level + 3.0
- CHOCH_SWEEP_BUY:  SL = last_choch.level - 3.0
- STRONG_REJECTION: SL = 3 pts beyond the rejection wick extreme
If none of the above applies and SL cannot be calculated → NO_TRADE (do NOT vote YES with null SL)

TP SELECTION (must achieve RR ≥ 1:1.5 — verified mathematically before outputting):
Step 1: risk_distance = abs(entry - stop_loss)
Step 2: required_tp [BUY]  = entry + risk_distance × 1.5   (must be ABOVE entry)
        required_tp [SELL] = entry - risk_distance × 1.5   (must be BELOW entry)
Step 3a [BUY]:  scan WEEKLY BSL pools ordered nearest→farthest (include ✓sw swept ones)
                pick the FIRST pool where pool_level >= required_tp
                if none → try active_bear_ob.top (must also be >= required_tp)
                if still none → output signal=NO_TRADE (do NOT guess a TP)
Step 3b [SELL]: scan WEEKLY SSL pools ordered nearest→farthest (include ✓sw swept ones)
                pick the FIRST pool where pool_level <= required_tp
                if none → try active_bull_ob.bottom (must also be <= required_tp)
                if still none → output signal=NO_TRADE (do NOT guess a TP)
Step 4: VERIFY before output — compute actual_rr = abs(take_profit - entry) / risk_distance
        if actual_rr < 1.5 → go back to Step 3 and scan for a farther pool
        NEVER output take_profit that results in actual_rr < 1.5
⚠️ NEVER pick the nearest pool without verifying actual_rr ≥ 1.5 — scan farther until one qualifies
⚠️ A pool that is closer than required_tp does NOT qualify even if it is the only one available → NO_TRADE
✓sw (swept) BSL/SSL pools are VALID TP targets — price often revisits them for a second liquidity grab

OUTPUT (JSON only):
{"signal":"BUY"|"SELL"|"NO_TRADE","setup_type":"BSL_SWEEP_SELL"|"SSL_SWEEP_BUY"|"OB_REJECTION_SELL"|"OB_REJECTION_BUY"|"STORED_OB_PULLBACK_SELL"|"STORED_OB_PULLBACK_BUY"|"STRONG_REJECTION_SELL"|"STRONG_REJECTION_BUY"|"POST_SWEEP_PULLBACK_SELL"|"POST_SWEEP_PULLBACK_BUY"|"CHOCH_SWEEP_SELL"|"CHOCH_SWEEP_BUY"|"POST_BOS_CHOCH_RETEST_SELL"|"POST_BOS_CHOCH_RETEST_BUY"|"NO_TRADE","confidence":0-100,"entry":price|null,"stop_loss":price|null,"take_profit":price|null,"rr_ratio":number|null,"vote":"YES"|"NO","vote_reasoning":"1-2 sentences","liquidity_target":price|null}
NOTE: "take_profit" = primary TP (replaces tp1). "rr_ratio" = abs(take_profit-entry)/abs(entry-stop_loss) — compute and include it. If rr_ratio < 1.5, set signal=NO_TRADE instead.
"""

_API_TIMEOUT = 60  # วินาที — ต่อ 1 request


def _fmt(val):
    return str(val) if val is not None else "N/A"


def _build_prompt(smc: dict) -> str:
    price    = smc.get("current_price", 0)
    bias     = smc.get("bias", "neutral")
    sweep    = smc.get("last_sweep") or {}
    choch    = smc.get("last_choch") or {}
    bos      = smc.get("last_bos") or {}
    bull_ob  = smc.get("active_bull_ob") or {}
    bear_ob  = smc.get("active_bear_ob") or {}
    adv      = smc.get("advanced") or {}
    liq      = smc.get("liquidity") or {}
    sess     = smc.get("session") or {}
    ob_q     = smc.get("ob_quality") or {}
    post_sw  = smc.get("post_sweep_continuation") or {}
    bear_rej = smc.get("recent_bear_ob_rejection")
    bull_rej = smc.get("recent_bull_ob_rejection")
    choch_k  = smc.get("choch_sweep_setup")
    stored   = smc.get("stored_ob_rejections") or []
    srw      = smc.get("sweep_rejection_watch")

    ssl_raw = liq.get("nearest_ssl")
    bsl_raw = liq.get("nearest_bsl")
    ssl_lvl = ssl_raw.get("level") if isinstance(ssl_raw, dict) else ssl_raw
    bsl_lvl = bsl_raw.get("level") if isinstance(bsl_raw, dict) else bsl_raw

    # compute sweep_depth ก่อน lines = [...] เพราะถูกใช้ใน LAST SWEEP line
    _sweep_level_early = sweep.get("level") or 0
    _wick_ext_early    = sweep.get("wick_extreme") or _sweep_level_early
    _sweep_depth       = round(abs(_sweep_level_early - _wick_ext_early), 2) if _sweep_level_early else 0

    lines = [
        _INSTRUCTIONS,
        "",
        "=== MARKET DATA — XAUUSD M5 ===",
        f"Price: {price} | Bias: {bias} | Session: {sess.get('session','?')}",
        "",
        "STRUCTURE:",
        f"  CHoCH: {choch.get('direction','none')} @ {choch.get('level','?')}",
        f"  BOS:   {bos.get('direction','none')} @ {bos.get('level','?')}",
        "",
        f"LAST SWEEP: {sweep.get('kind','none')} @ {sweep.get('level','?')} recovered={sweep.get('recovered','?')}"
        + (f" | wick_extreme={sweep.get('wick_extreme','?')} depth={_sweep_depth}pts → SL_ref=level+10 ({round((sweep.get('level') or 0)+10, 2)})" if sweep.get('kind')=='high' else "")
        + (f" | wick_extreme={sweep.get('wick_extreme','?')} depth={_sweep_depth}pts → SL_ref=level-10 ({round((sweep.get('level') or 0)-10, 2)})" if sweep.get('kind')=='low' else ""),
        "",
        "ORDER BLOCKS:",
        f"  Bear OB: {bear_ob.get('bottom','?')} – {bear_ob.get('top','?')} (in_ob={bear_ob.get('in_ob',False)})",
        f"  Bull OB: {bull_ob.get('bottom','?')} – {bull_ob.get('top','?')} (in_ob={bull_ob.get('in_ob',False)})",
        "",
        f"OB QUALITY: {ob_q}",
        "",
        f"LIQUIDITY: SSL={_fmt(ssl_lvl)} BSL={_fmt(bsl_lvl)}",
        f"  Sweep ages: sweep_h={adv.get('sweep_h_age_bars','?')}bars sweep_l={adv.get('sweep_l_age_bars','?')}bars",
    ]

    weekly_bsl = liq.get("weekly_bsl_pools") or []
    weekly_ssl = liq.get("weekly_ssl_pools") or []
    if weekly_bsl:
        _bsl_str = " | ".join(
            f"{p['level']}({'M15' if p['timeframe']=='M15' else 'M5'}{'★' if p['size']=='major' else ''}{'✓sw' if p['swept'] else ''})"
            for p in weekly_bsl[:8]
        )
        lines += [f"  WEEKLY BSL (7d): {_bsl_str}"]
    if weekly_ssl:
        _ssl_str = " | ".join(
            f"{p['level']}({'M15' if p['timeframe']=='M15' else 'M5'}{'★' if p['size']=='major' else ''}{'✓sw' if p['swept'] else ''})"
            for p in weekly_ssl[:8]
        )
        lines += [f"  WEEKLY SSL (7d): {_ssl_str}"]

    pb_bos_choch = smc.get("post_bos_choch_retest")
    if pb_bos_choch:
        lines += [
            "",
            f"CASE L — POST-BOS CHoCH RETEST: dir={pb_bos_choch['direction']} retrace={pb_bos_choch['retrace_pct']}%",
            f"  BOS @ {pb_bos_choch['bos_level']} ({pb_bos_choch['bos_age_bars']}bars ago)",
            f"  CHoCH @ {pb_bos_choch['choch_level']} ({pb_bos_choch['choch_age_bars']}bars ago) | swing_extreme={pb_bos_choch['swing_extreme']}",
            f"  dist_to_choch={pb_bos_choch['dist_pts']}pts | rejection={pb_bos_choch['rejection_confirmed']} | SL_ref={pb_bos_choch['sl_ref']}",
        ]

    if choch_k:
        lines += [
            "",
            f"CASE K — CHoCH+SWEEP: dir={choch_k['direction']} conf={choch_k['confidence']}",
            f"  CHoCH @ {choch_k['choch_level']} ({choch_k['choch_age_bars']}bars) | Sweep @ {choch_k['sweep_level']} ({choch_k['sweep_age_bars']}bars)",
            f"  rejection={choch_k['rejection_confirmed']}",
        ]

    if post_sw:
        lines += [
            "",
            f"POST-SWEEP PULLBACK (CASE H): dir={post_sw.get('direction')} pb={post_sw.get('pullback_pct')}% level_held={post_sw.get('level_held', False)}",
        ]

    if bear_rej:
        lines += [f"RECENT BEAR OB REJECTION: zone={bear_rej['ob_zone']} {bear_rej['bars_ago']}bars ago"]
    if bull_rej:
        lines += [f"RECENT BULL OB REJECTION: zone={bull_rej['ob_zone']} {bull_rej['bars_ago']}bars ago"]

    if stored:
        lines += ["STORED OB ZONES (CASE I):"]
        for z in stored[:3]:
            lines += [f"  {z['direction']} zone={z['zone']}"]

    if srw:
        lines += [f"SWEEP WATCH ACTIVE: dir={srw['direction']} since={srw['watched_since']}"]

    # ── Pullback status ──────────────────────────────────────────────
    # sweep-based pullback status
    # FIRST  = sweep ≤48 bars + price อยู่ใน OB zone (กำลัง pullback เข้า OB ครั้งแรก)
    # SECOND = sweep ≤48 bars + price ออกจาก OB ไปแล้ว แต่กลับมาใน ±$10 ของ sweep level
    # EXPIRED = sweep >48 bars + price ห่าง level, หรือ setup หมดอายุ
    # BUG FIX: เดิมดึง age จาก adv.sweep_l/h_age_bars (20-bar rolling mechanism,
    # คนละตัวกับ "sweep" ที่มาจาก last_sweep — full-history scan) ทำให้ age มัก
    # อ่านได้ 999 (sentinel "ไม่เจอ") แม้ sweep จะสดจริง → EXPIRED ผิดๆ บ่อยมาก
    # ตอนนี้ใช้ sweep["age_bars"] ที่คำนวณจาก object เดียวกันโดยตรงแทน
    _sweep_kind  = sweep.get("kind")
    _sweep_level = sweep.get("level") or 0
    _sweep_age   = sweep.get("age_bars")
    _sweep_age   = _sweep_age if _sweep_age is not None else 999
    _dist        = round(abs(price - _sweep_level), 1) if _sweep_level else 999
    _in_any_ob   = bull_ob.get("in_ob", False) or bear_ob.get("in_ob", False)

    # FIRST window กว้างขึ้นตาม sweep depth (deep sweep → bounce ไกลกว่าก่อน pullback)
    _first_window = max(15.0, _sweep_depth * 2.5)

    # entry หลัง sweep อยู่ใกล้ sweep level ไม่ใช่ที่ OB (OB คือ TP)
    if not sweep:
        _pb = "NONE"
    elif _sweep_age > 48:
        _pb = "EXPIRED"
    elif _sweep_age <= 12 and _dist <= _first_window:
        _pb = "FIRST"   # ยังใกล้ sweep zone — แท่งแดงแรกหลัง bounce = entry
    elif _dist <= 10.0:
        _pb = "SECOND"  # price ออกไปแล้วแต่กลับมา ±$10 = second chance entry
    else:
        _pb = "EXPIRED"

    # OB rejection-based pullback status (CASE G)
    _ob_pb = "NONE"
    _ob_rej = bear_rej or bull_rej
    if _ob_rej:
        _ob_age  = _ob_rej.get("bars_ago", 999)
        _ob_zone = _ob_rej.get("ob_zone", [0, 0])
        _ob_mid  = (_ob_zone[0] + _ob_zone[1]) / 2 if isinstance(_ob_zone, list) and len(_ob_zone) == 2 else 0
        _ob_dist = round(abs(price - _ob_mid), 1) if _ob_mid else 999
        if _ob_age > 48:
            _ob_pb = "EXPIRED"
        elif _ob_age <= 12 and _ob_dist <= 15.0:
            _ob_pb = "FIRST"
        elif _ob_dist <= 10.0:
            _ob_pb = "SECOND"
        else:
            _ob_pb = "EXPIRED"

    lines += [
        "",
        f"PULLBACK STATUS: sweep={_pb} (age={_sweep_age}bars dist={_dist}pts depth={_sweep_depth}pts) | ob_rejection={_ob_pb}",
        "",
        f"ADVANCED: signal_type={adv.get('signal_type','?')} "
        f"bull_grab={adv.get('bull_grab',False)} bear_grab={adv.get('bear_grab',False)}",
        f"  momentum: bull={adv.get('momentum_bull',False)} bear={adv.get('momentum_bear',False)}",
        "",
        "Analyze the data above and return JSON decision only.",
    ]

    return "\n".join(lines)


_MAX_RETRIES = 2   # retry กี่ครั้งก่อน give up
_RETRY_DELAY = 8   # วินาทีที่รอระหว่าง retry


def analyze(smc_summary: dict) -> dict:
    """
    เรียก Sonnet ผ่าน Anthropic API ตรงๆ
    คืน dict format เดียวกันเป๊ะ — supervisor.py ใช้ต่อได้ทันที
    """
    t0 = time.time()

    prompt = _build_prompt(smc_summary)

    raw = None
    last_err = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            raw = api_query(prompt, model=MODEL_SMART, label="ChartAnalyst",
                             max_tokens=600, timeout=_API_TIMEOUT)
            break  # สำเร็จ — ออกจาก retry loop
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            err_str = str(e).lower()
            if any(k in err_str for k in ("rate limit", "429", "usage limit", "quota", "overloaded", "529")):
                print(f"[ChartAnalyst] ⚠️ Rate limit ({elapsed}s): {e}")
                raise RuntimeError(f"ChartAnalyst rate limit — skip: {e}")
            last_err = e

        if attempt < _MAX_RETRIES:
            print(f"[ChartAnalyst] ⚠️ attempt {attempt+1} failed — retry in {_RETRY_DELAY}s ({last_err})")
            time.sleep(_RETRY_DELAY)

    if raw is None:
        raise last_err or RuntimeError("ChartAnalyst API failed after retries")

    elapsed = round(time.time() - t0, 1)
    print(f"[ChartAnalyst] response in {elapsed}s: {raw[:120]}")

    result = safe_json_parse(
        raw,
        fallback={"signal": "NO_TRADE", "vote": "NO", "vote_reasoning": "API parse error", "confidence": 0},
    )
    result["analyzed_at"]   = smc_summary.get("analyzed_at")
    result["current_price"] = smc_summary.get("current_price")
    result["smc_bias"]      = smc_summary.get("bias")
    result["had_sweep"]     = smc_summary.get("last_sweep") is not None
    result["claude_called"] = True
    result["recent_bear_ob_rejection"] = smc_summary.get("recent_bear_ob_rejection")
    result["recent_bull_ob_rejection"] = smc_summary.get("recent_bull_ob_rejection")

    return result
