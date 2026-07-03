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
import time

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

OUTPUT (JSON only):
{"signal":"BUY"|"SELL"|"NO_TRADE","setup_type":"BSL_SWEEP_SELL"|"SSL_SWEEP_BUY"|"OB_REJECTION_SELL"|"OB_REJECTION_BUY"|"STORED_OB_PULLBACK_SELL"|"STORED_OB_PULLBACK_BUY"|"STRONG_REJECTION_SELL"|"STRONG_REJECTION_BUY"|"POST_SWEEP_PULLBACK_SELL"|"POST_SWEEP_PULLBACK_BUY"|"CHOCH_SWEEP_SELL"|"CHOCH_SWEEP_BUY"|"NO_TRADE","confidence":0-100,"entry":price|null,"stop_loss":price|null,"tp1":price|null,"tp2":price|null,"vote":"YES"|"NO","vote_reasoning":"1-2 sentences","liquidity_target":price|null}
"""

_SDK_TIMEOUT = 45   # วินาที — ถ้า SDK ช้ากว่านี้ให้ supervisor fallback


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
        f"LAST SWEEP: {sweep.get('kind','none')} @ {sweep.get('level','?')} recovered={sweep.get('recovered','?')}",
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

    lines += [
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
    raw = asyncio.run(_query_async(prompt))

    elapsed = round(time.time() - t0, 1)
    print(f"[AgentSDK] response in {elapsed}s: {raw[:120]}")

    if elapsed > _SDK_TIMEOUT:
        raise TimeoutError(f"Agent SDK took {elapsed}s > {_SDK_TIMEOUT}s limit")

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
