"""
chart_analyst_sdk.py — Anthropic Managed Agents version (ทดลอง)
ใช้แทน chart_analyst.py เมื่อ USE_AGENT_SDK=true ใน .env

ข้อดี (ทฤษฎี):
  - System prompt เก็บ server-side (prompt cache ดีกว่า)
  - Agent persistent ข้ามสแกน

ข้อจำกัด:
  - ต้องสร้าง Environment + Agent ครั้งแรก (เก็บ ID ใน bot_state)
  - Session ต้องสร้างใหม่ทุกสแกน
  - ใช้ polling (send → list) แทน streaming เพื่อความเรียบง่าย

fallback: ถ้า SDK fail → กลับไปใช้ chart_analyst.analyze() ทันที
"""

import json
import time
import anthropic

from config.settings import ANTHROPIC_API_KEY, MODEL_SMART
from agents.chart_analyst import has_signal, _haiku_precheck
from agents.json_utils import safe_json_parse
from agents import state_manager as _sm

_BETA = "managed-agents-2026-04-01"
_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── System Prompt (stored server-side in Agent) ──────────────────────────────
_SYSTEM_PROMPT = """You are an expert XAUUSD (Gold) Smart Money Concepts (SMC) trading analyst.

ROLE: Analyze market data → output JSON trade decision ONLY.

CORE SMC CONCEPTS:
- Order Block (OB): Supply (Bear OB) / Demand (Bull OB) zones
- BOS: Break of Structure (trend continuation)
- CHoCH: Change of Character (trend reversal)
- Liquidity Sweep: BSL (buy-side) / SSL (sell-side) swept
- Rejection: Price enters zone but closes back out (wick rejection)

════════════════════════════════════════════
📌 PATTERN PRIORITY (highest → lowest confidence):

1. CASE F ★★★★: BSL/SSL Sweep + Rejection
   - BSL swept → bearish rejection → SELL (BSL_SWEEP_SELL)
   - SSL swept → bullish rejection → BUY (SSL_SWEEP_BUY)
   - Confidence: 75-90

2. CASE I ★★★: Stored OB Pullback (from bot state)
   - Price returns to OB that had rejection → re-entry
   - STORED_OB_PULLBACK_SELL / STORED_OB_PULLBACK_BUY
   - Confidence: 60-75

3. CASE G ★★★: OB Rejection (recent, without sweep)
   - Price touches Bear/Bull OB → rejection candle
   - OB_REJECTION_SELL / OB_REJECTION_BUY
   - Confidence: 65-80

4. CASE J ★★: Strong Rejection at Key Level (EQL/EQH)
   - EQH/Swing High swept → strong bearish candle → SELL
   - EQL/Swing Low swept → strong bullish candle → BUY
   - STRONG_REJECTION_SELL / STRONG_REJECTION_BUY
   - Confidence: 50-65

5. CASE H ★★: Post-Sweep Continuation Pullback
   - After sweep+rejection, price runs → pullback 15-65% → entry
   - POST_SWEEP_PULLBACK_SELL / POST_SWEEP_PULLBACK_BUY
   - Confidence: 60-75

6. CASE K ★★: CHoCH + Sweep → Reversal
   - CHoCH confirms reversal → sweep near CHoCH level → rejection
   - CHOCH_SWEEP_SELL / CHOCH_SWEEP_BUY
   - Confidence: 65-80

════════════════════════════════════════════
📌 CRITICAL RULES:

-3. ⛔ PULLBACK VALIDITY (check first for ALL pullback setups):
    - SELL pullback: price close must stay BELOW rejection high
    - BUY pullback: price close must stay ABOVE rejection low
    - If price closes past rejection level → INVALIDATED → NO_TRADE

-2. 📐 OB Quality Check:
    - ob_quality=LOW (<50p gap from OB to sweep) → reduce confidence 15-20
    - OB too close to sweep = no accumulation space = weak rejection

-1. 🚧 Liquidity Gate:
    - If liq not swept → require strong confluence (OB + structure + momentum)
    - BYPASS if: "ob rejection" / "bsl swept" / "ssl swept" keywords in reasoning

0. NO_TRADE default: when unsure → NO_TRADE (never force a trade)

════════════════════════════════════════════
📌 OUTPUT FORMAT (JSON only, no markdown):

{
  "signal": "BUY" | "SELL" | "NO_TRADE",
  "setup_type": "BSL_SWEEP_SELL" | "SSL_SWEEP_BUY" | "OB_REJECTION_SELL" | "OB_REJECTION_BUY" | "STORED_OB_PULLBACK_SELL" | "STORED_OB_PULLBACK_BUY" | "STRONG_REJECTION_SELL" | "STRONG_REJECTION_BUY" | "POST_SWEEP_PULLBACK_SELL" | "POST_SWEEP_PULLBACK_BUY" | "CHOCH_SWEEP_SELL" | "CHOCH_SWEEP_BUY" | "NO_TRADE",
  "confidence": 0-100,
  "entry": <price or null>,
  "stop_loss": <price or null>,
  "tp1": <price or null>,
  "tp2": <price or null>,
  "vote": "YES" | "NO",
  "vote_reasoning": "<Thai or English, 1-2 sentences>",
  "liquidity_target": <price or null>
}

IMPORTANT: Return ONLY valid JSON. No explanation outside JSON."""


def _fmt(val, suffix=""):
    return f"{val}{suffix}" if val is not None else "N/A"


def _build_market_prompt(smc: dict) -> str:
    """สร้าง compact market data prompt สำหรับส่งให้ Agent"""
    price   = smc.get("current_price", 0)
    bias    = smc.get("bias", "neutral")
    sweep   = smc.get("last_sweep") or {}
    choch   = smc.get("last_choch") or {}
    bos     = smc.get("last_bos") or {}
    bull_ob = smc.get("active_bull_ob") or {}
    bear_ob = smc.get("active_bear_ob") or {}
    adv     = smc.get("advanced") or {}
    liq     = smc.get("liquidity") or {}
    sess    = smc.get("session") or {}
    ob_q    = smc.get("ob_quality") or {}
    post_sw = smc.get("post_sweep_continuation") or {}
    bear_rej = smc.get("recent_bear_ob_rejection")
    bull_rej = smc.get("recent_bull_ob_rejection")
    choch_k  = smc.get("choch_sweep_setup")
    stored   = smc.get("stored_ob_rejections") or []
    srw      = smc.get("sweep_rejection_watch")

    lines = [
        f"=== MARKET DATA — XAUUSD M5 ===",
        f"Price: {price} | Bias: {bias} | Session: {sess.get('session','?')}",
        f"",
        f"STRUCTURE:",
        f"  CHoCH: {choch.get('direction','none')} @ {choch.get('level','?')}",
        f"  BOS:   {bos.get('direction','none')} @ {bos.get('level','?')}",
        f"",
        f"LAST SWEEP: {sweep.get('kind','none')} @ {sweep.get('level','?')} recovered={sweep.get('recovered','?')}",
        f"",
        f"ORDER BLOCKS:",
        f"  Bear OB: {bear_ob.get('bottom','?')} – {bear_ob.get('top','?')} (in_ob={bear_ob.get('in_ob',False)})",
        f"  Bull OB: {bull_ob.get('bottom','?')} – {bull_ob.get('top','?')} (in_ob={bull_ob.get('in_ob',False)})",
        f"",
        f"OB QUALITY: {ob_q}",
    ]

    # Liquidity
    ssl_raw = liq.get("nearest_ssl")
    bsl_raw = liq.get("nearest_bsl")
    ssl_lvl = ssl_raw.get("level") if isinstance(ssl_raw, dict) else ssl_raw
    bsl_lvl = bsl_raw.get("level") if isinstance(bsl_raw, dict) else bsl_raw
    lines += [
        f"",
        f"LIQUIDITY: SSL={_fmt(ssl_lvl)} BSL={_fmt(bsl_lvl)}",
        f"  Sweep ages: sweep_h={adv.get('sweep_h_age_bars','?')}bars sweep_l={adv.get('sweep_l_age_bars','?')}bars",
    ]

    # CHoCH+Sweep (CASE K)
    if choch_k:
        lines += [
            f"",
            f"CASE K — CHoCH+SWEEP: dir={choch_k['direction']} conf={choch_k['confidence']}",
            f"  CHoCH @ {choch_k['choch_level']} ({choch_k['choch_age_bars']}bars) | Sweep @ {choch_k['sweep_level']} ({choch_k['sweep_age_bars']}bars)",
            f"  rejection={choch_k['rejection_confirmed']}",
        ]

    # Post-sweep continuation
    if post_sw:
        lines += [
            f"",
            f"POST-SWEEP PULLBACK (CASE H): dir={post_sw.get('direction')} pb={post_sw.get('pullback_pct')}%",
        ]

    # Recent OB rejection
    if bear_rej:
        lines += [f"RECENT BEAR OB REJECTION: zone={bear_rej['ob_zone']} {bear_rej['bars_ago']}bars ago"]
    if bull_rej:
        lines += [f"RECENT BULL OB REJECTION: zone={bull_rej['ob_zone']} {bull_rej['bars_ago']}bars ago"]

    # Stored rejections (Pattern 3)
    if stored:
        lines += [f"STORED OB ZONES (Pattern 3):"]
        for z in stored[:3]:
            lines += [f"  {z['direction']} zone={z['zone']}"]

    # Sweep watch (Pattern 1)
    if srw:
        lines += [f"SWEEP WATCH ACTIVE: dir={srw['direction']} since={srw['watched_since']}"]

    # Advanced signals
    lines += [
        f"",
        f"ADVANCED: signal_type={adv.get('signal_type','?')} bull_grab={adv.get('bull_grab',False)} bear_grab={adv.get('bear_grab',False)}",
        f"  choch_grab: bull={adv.get('bull_choch_grab',False)} bear={adv.get('bear_choch_grab',False)}",
        f"  momentum: bull={adv.get('momentum_bull',False)} bear={adv.get('momentum_bear',False)}",
        f"",
        f"=== ANALYZE AND RETURN JSON ===",
    ]

    return "\n".join(lines)


def _get_or_create_ids(bot_state: dict) -> tuple[str, str]:
    """สร้าง environment + agent ครั้งแรก เก็บ ID ใน bot_state"""
    agent_id = bot_state.get("sdk_agent_id")
    env_id   = bot_state.get("sdk_env_id")

    if not env_id:
        print("[SDK] Creating Managed Agent environment...")
        env = _client.beta.environments.create(
            name="SmartAgentTrade-env",
            config={"type": "cloud"},
            betas=[_BETA],
        )
        env_id = env.id
        bot_state["sdk_env_id"] = env_id
        print(f"[SDK] Environment created: {env_id}")

    if not agent_id:
        print("[SDK] Creating Managed Agent...")
        agent = _client.beta.agents.create(
            name="SmartAgentTrade-analyst",
            model=MODEL_SMART,
            system=_SYSTEM_PROMPT,
            betas=[_BETA],
        )
        agent_id = agent.id
        bot_state["sdk_agent_id"] = agent_id
        print(f"[SDK] Agent created: {agent_id}")

    return agent_id, env_id


def analyze(smc_summary: dict) -> dict:
    """
    SDK version of chart_analyst.analyze()
    สร้าง session → ส่ง market data → รอ agent response → parse JSON

    SDK IDs (agent_id, env_id) ถูกเก็บใน bot_state.json อัตโนมัติ
    """
    # โหลด/save state เองเพื่อให้ supervisor.py ไม่ต้องส่ง bot_state
    _bot_state = _sm.load()

    # Haiku pre-check (ยังใช้ filter cost ก่อน SDK session)
    _pre = _haiku_precheck(smc_summary)
    if not _pre["pass"]:
        print(f"[SDK Haiku] ❌ SKIP — {_pre['reason']}")
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
    print(f"[SDK Haiku] ✅ PASS → calling Agent SDK")

    try:
        agent_id, env_id = _get_or_create_ids(_bot_state)
        _sm.save(_bot_state)  # บันทึก IDs ทันที
    except Exception as _e:
        print(f"[SDK] Cannot create agent/env: {_e}")
        raise  # caller falls back to chart_analyst

    market_prompt = _build_market_prompt(smc_summary)

    # สร้าง session ใหม่ต่อทุกสแกน
    session = _client.beta.sessions.create(
        agent={"id": agent_id},
        environment_id=env_id,
        betas=[_BETA],
    )
    print(f"[SDK] Session created: {session.id}")

    try:
        # ส่ง user message
        _client.beta.sessions.events.send(
            session.id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": market_prompt}],
            }],
            betas=[_BETA],
        )

        # Poll สำหรับ agent.message (max 60s)
        raw_text = _poll_for_response(session.id, max_secs=60)

    finally:
        # Archive session หลังใช้งาน
        try:
            _client.beta.sessions.archive(session.id, betas=[_BETA])
        except Exception:
            pass

    print(f"[SDK] Response: {raw_text[:200]}")

    result = safe_json_parse(
        raw_text,
        fallback={"signal": "NO_TRADE", "vote": "NO", "vote_reasoning": "SDK JSON parse error", "confidence": 0},
    )
    result["analyzed_at"]   = smc_summary.get("analyzed_at")
    result["current_price"] = smc_summary.get("current_price")
    result["smc_bias"]      = smc_summary.get("bias")
    result["had_sweep"]     = smc_summary.get("last_sweep") is not None
    result["claude_called"] = True
    result["recent_bear_ob_rejection"] = smc_summary.get("recent_bear_ob_rejection")
    result["recent_bull_ob_rejection"] = smc_summary.get("recent_bull_ob_rejection")

    return result


def _poll_for_response(session_id: str, max_secs: int = 60) -> str:
    """Poll events.list() จนกว่าจะได้ agent.message หรือ timeout"""
    deadline = time.time() + max_secs
    seen_ids: set[str] = set()

    while time.time() < deadline:
        time.sleep(2)
        try:
            page = _client.beta.sessions.events.list(
                session_id,
                order="asc",
                betas=[_BETA],
            )
            for event in page.data:
                ev_id = getattr(event, "id", None)
                if ev_id in seen_ids:
                    continue
                seen_ids.add(ev_id)

                ev_type = getattr(event, "type", None)
                if ev_type == "agent.message":
                    content = getattr(event, "content", [])
                    return "".join(
                        getattr(b, "text", "") for b in content
                    )
                elif ev_type in ("session.status.terminated", "session.status.idle"):
                    print(f"[SDK] Session ended ({ev_type}) — no agent.message yet")
        except Exception as _e:
            print(f"[SDK] Poll error: {_e}")
            time.sleep(3)

    print(f"[SDK] Timeout after {max_secs}s")
    return ""
