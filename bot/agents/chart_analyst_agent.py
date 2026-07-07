"""
chart_analyst_agent.py — Claude Agent SDK version
ใช้ claude_agent_sdk แทน messages.create()
รันด้วย Claude Code subscription — ไม่มีค่า API แยก

Flow:
  has_signal() ผ่าน → asyncio.run(_query()) → parse JSON
  ถ้า fail → supervisor.py fallback ไป chart_analyst.analyze()

หมายเหตุ: asyncio.run() ใช้ได้ใน run_in_executor thread (ไม่มี event loop เดิม)
"""

import asyncio
import os
import time

# ต้อง pop ก่อน import claude_agent_sdk เพราะ SDK อ่าน env ตอน import
os.environ.pop("ANTHROPIC_API_KEY", None)

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

from agents.chart_analyst import has_signal
from agents.json_utils import safe_json_parse

# ── Instructions + Pattern definitions (ใส่ใน prompt เพราะ SDK ไม่มี system param) ──
_INSTRUCTIONS = """\
You are an expert XAUUSD (Gold) SMC trading analyst. Return ONLY valid JSON — no markdown, no explanation.

PATTERN PRIORITY (highest → lowest):
1. CASE F ★★★★: BSL/SSL Sweep + Rejection → BSL_SWEEP_SELL / SSL_SWEEP_BUY (conf 75-90)
   NOTE: EQL (Equal Lows) = SSL pool; EQH (Equal Highs) = BSL pool — treat identically as CASE F
   last_sweep.source="EQL" → SSL_SWEEP_BUY | last_sweep.source="EQH" → BSL_SWEEP_SELL
2. CASE I ★★★:  Stored OB Pullback (ob_rejection_zones) → STORED_OB_PULLBACK_SELL/BUY (conf 60-75)
3. CASE G ★★★:  OB Rejection (recent, no sweep needed) → OB_REJECTION_SELL/BUY (conf 65-80)
4. CASE J ★★:   Strong Rejection at EQL/EQH → STRONG_REJECTION_SELL/BUY (conf 50-65)
5. CASE H ★★:   Post-Sweep Pullback ≥15% ≤30 bars → POST_SWEEP_PULLBACK_SELL/BUY (conf 60-75)
   level_held=True means price never broke sweep extreme — valid re-entry regardless of pullback depth
   deeper pullback (>65%) = 2nd touch zone → conf+10 bonus
6. CASE K ★★:   CHoCH + Sweep → Reversal → CHOCH_SWEEP_SELL/BUY (conf 65-80)

CRITICAL RULES:
- PULLBACK VALIDITY: SELL pullback close must stay BELOW rejection high; BUY must stay ABOVE rejection low → if violated = INVALIDATED → NO_TRADE
- ob_quality=LOW (<50p gap) → reduce confidence 15-20
- When unsure → NO_TRADE (never force)
- BIAS CONTEXT: post_sweep_continuation and sweep ages are informational — use to assess context but do NOT hard-block signals. A fresh BUY sweep followed by a bounce into Bear OB is a valid SELL setup (distribution). Trust price action over bias lock.

PULLBACK ENTRY RULE (strictly enforced):
- FIRST pullback (pullback_status=FIRST): VALID — enter normally, highest confidence
- SECOND pullback (pullback_status=SECOND): VALID only if price is within ±$10 of the original sweep/rejection level — this is a re-test of the zone after a failed first attempt; reduce confidence by 10
- EXPIRED (pullback_status=EXPIRED): NO_TRADE — setup is stale, do not enter regardless of other signals
- NONE (no sweep/rejection): follow normal OB rules without pullback restriction

SL CALCULATION (mandatory — never output stop_loss=null when vote=YES):
- BSL_SWEEP_SELL: SL = last_sweep.wick_extreme (the actual wick high IS the buffer — no extra offset needed)
- SSL_SWEEP_BUY:  SL = last_sweep.wick_extreme (the actual wick low IS the buffer — SSL was ~5pts above it)
  ⚠️ NEVER place SL at the SSL/BSL pool level itself — wick_extreme is already below/above the pool
- OB_REJECTION_SELL / STORED_OB_PULLBACK_SELL: SL = bear_ob.top + 3.0
- OB_REJECTION_BUY  / STORED_OB_PULLBACK_BUY:  SL = bull_ob.bottom - 3.0
- POST_SWEEP_PULLBACK_SELL: SL = last_sweep.wick_extreme
- POST_SWEEP_PULLBACK_BUY:  SL = last_sweep.wick_extreme
- CHOCH_SWEEP_SELL: SL = last_choch.level + 3.0
- CHOCH_SWEEP_BUY:  SL = last_choch.level - 3.0
- STRONG_REJECTION: SL = 3 pts beyond the rejection wick extreme
If none of the above applies and SL cannot be calculated → NO_TRADE (do NOT vote YES with null SL)

TP SELECTION (must achieve RR ≥ 1:1.5):
- Calculate required_tp = entry + (entry - stop_loss) × 1.5  [BUY]
                        = entry - (stop_loss - entry) × 1.5  [SELL]
- For BUY: scan ALL WEEKLY BSL pools (nearest to farthest, including ✓sw swept ones) — pick the FIRST BSL ≥ required_tp
  ✓sw (swept) BSL pools are VALID TP targets: after SSL sweep, price often revisits previously swept BSLs for a second liquidity grab (especially if a Bear OB sits at that level)
  If no BSL pool meets required_tp, use active_bear_ob.top as TP (price runs into OB zones)
  If still no TP achieves RR ≥ 1.5, output NO_TRADE
- For SELL: scan ALL WEEKLY SSL pools (including ✓sw) — pick the FIRST SSL ≤ required_tp
  If no SSL meets required_tp, use active_bull_ob.bottom as TP
  If still no TP achieves RR ≥ 1.5, output NO_TRADE
- NEVER pick the nearest pool without verifying RR ≥ 1.5 — scan further until you find one that qualifies

OUTPUT (JSON only):
{"signal":"BUY"|"SELL"|"NO_TRADE","setup_type":"BSL_SWEEP_SELL"|"SSL_SWEEP_BUY"|"OB_REJECTION_SELL"|"OB_REJECTION_BUY"|"STORED_OB_PULLBACK_SELL"|"STORED_OB_PULLBACK_BUY"|"STRONG_REJECTION_SELL"|"STRONG_REJECTION_BUY"|"POST_SWEEP_PULLBACK_SELL"|"POST_SWEEP_PULLBACK_BUY"|"CHOCH_SWEEP_SELL"|"CHOCH_SWEEP_BUY"|"NO_TRADE","confidence":0-100,"entry":price|null,"stop_loss":price|null,"tp1":price|null,"tp2":price|null,"vote":"YES"|"NO","vote_reasoning":"1-2 sentences","liquidity_target":price|null}
"""

_SDK_TIMEOUT = 150  # วินาที — hard cancel ถ้า SDK ไม่ตอบใน 150s


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
        + (f" | wick_extreme={sweep.get('wick_extreme','?')} → SL_ref={sweep.get('wick_extreme','?')}" if sweep.get('kind')=='high' else "")
        + (f" | wick_extreme={sweep.get('wick_extreme','?')} → SL_ref={sweep.get('wick_extreme','?')}" if sweep.get('kind')=='low' else ""),
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
    _sweep_kind  = sweep.get("kind")
    _sweep_level = sweep.get("level") or 0
    _age_key     = ("sweep_l_age_bars" if _sweep_kind == "low" else "sweep_h_age_bars") if _sweep_kind else None
    _sweep_age   = adv.get(_age_key, 999) if _age_key else 999
    _dist        = round(abs(price - _sweep_level), 1) if _sweep_level else 999

    if not sweep:
        _pb = "NONE"
    elif _sweep_age <= 6:
        _pb = "FIRST"
    elif _dist <= 10.0:
        _pb = "SECOND"
    else:
        _pb = "EXPIRED"

    # OB rejection-based pullback status (CASE G)
    _ob_pb = "NONE"
    if bear_rej:
        _ob_age  = bear_rej.get("bars_ago", 999)
        _ob_zone = bear_rej.get("ob_zone", [0, 0])
        _ob_mid  = (_ob_zone[0] + _ob_zone[1]) / 2 if isinstance(_ob_zone, list) and len(_ob_zone) == 2 else 0
        _ob_dist = round(abs(price - _ob_mid), 1) if _ob_mid else 999
        if _ob_age <= 6:
            _ob_pb = "FIRST"
        elif _ob_dist <= 10.0:
            _ob_pb = "SECOND"
        else:
            _ob_pb = "EXPIRED"
    elif bull_rej:
        _ob_age  = bull_rej.get("bars_ago", 999)
        _ob_zone = bull_rej.get("ob_zone", [0, 0])
        _ob_mid  = (_ob_zone[0] + _ob_zone[1]) / 2 if isinstance(_ob_zone, list) and len(_ob_zone) == 2 else 0
        _ob_dist = round(abs(price - _ob_mid), 1) if _ob_mid else 999
        if _ob_age <= 6:
            _ob_pb = "FIRST"
        elif _ob_dist <= 10.0:
            _ob_pb = "SECOND"
        else:
            _ob_pb = "EXPIRED"

    lines += [
        "",
        f"PULLBACK STATUS: sweep={_pb} (age={_sweep_age}bars dist={_dist}pts) | ob_rejection={_ob_pb}",
        "",
        f"ADVANCED: signal_type={adv.get('signal_type','?')} "
        f"bull_grab={adv.get('bull_grab',False)} bear_grab={adv.get('bear_grab',False)}",
        f"  momentum: bull={adv.get('momentum_bull',False)} bear={adv.get('momentum_bear',False)}",
        "",
        "Analyze the data above and return JSON decision only.",
    ]

    return "\n".join(lines)


async def _query_async(prompt: str) -> str:
    """ส่ง prompt → รอ ResultMessage → คืน text"""
    gen = query(prompt=prompt, options=ClaudeAgentOptions(allowed_tools=[]))
    try:
        async for msg in gen:
            if isinstance(msg, ResultMessage):
                return msg.result or ""
        return ""
    finally:
        # ปิด generator อย่างถูกต้องแม้ return กลางทาง — ป้องกัน aclose() RuntimeError
        await gen.aclose()


def analyze(smc_summary: dict) -> dict:
    """
    SDK version of chart_analyst.analyze()
    คืน dict format เดียวกันเป๊ะ — supervisor.py ใช้ต่อได้ทันที
    """
    t0 = time.time()

    prompt = _build_prompt(smc_summary)

    # asyncio.run() ได้เพราะถูกเรียกใน run_in_executor thread (ไม่มี event loop)
    # asyncio.wait_for ทำ hard cancel ถ้า SDK ไม่ตอบภายใน _SDK_TIMEOUT วินาที
    async def _run_with_timeout():
        return await asyncio.wait_for(_query_async(prompt), timeout=_SDK_TIMEOUT)

    try:
        raw = asyncio.run(_run_with_timeout())
    except asyncio.TimeoutError:
        elapsed = round(time.time() - t0, 1)
        raise TimeoutError(f"Agent SDK did not respond within {elapsed}s ({_SDK_TIMEOUT}s limit)")
    except Exception as e:
        err_str = str(e).lower()
        elapsed = round(time.time() - t0, 1)
        # Rate limit / usage limit — ไม่ crash process แค่ skip scan นี้
        if any(k in err_str for k in ("rate limit", "429", "usage limit", "quota", "overloaded", "529")):
            print(f"[AgentSDK] ⚠️ Rate/usage limit ({elapsed}s): {e}")
            raise RuntimeError(f"AgentSDK rate limit — skip this scan: {e}")
        raise  # error อื่น propagate ตามปกติ

    elapsed = round(time.time() - t0, 1)
    print(f"[AgentSDK] response in {elapsed}s: {raw[:120]}")

    result = safe_json_parse(
        raw,
        fallback={"signal": "NO_TRADE", "vote": "NO", "vote_reasoning": "SDK parse error", "confidence": 0},
    )
    result["analyzed_at"]   = smc_summary.get("analyzed_at")
    result["current_price"] = smc_summary.get("current_price")
    result["smc_bias"]      = smc_summary.get("bias")
    result["had_sweep"]     = smc_summary.get("last_sweep") is not None
    result["claude_called"] = True
    result["recent_bear_ob_rejection"] = smc_summary.get("recent_bear_ob_rejection")
    result["recent_bull_ob_rejection"] = smc_summary.get("recent_bull_ob_rejection")

    return result
