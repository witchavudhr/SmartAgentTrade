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
from datetime import datetime
from config.settings import ANTHROPIC_API_KEY, MODEL_SMART
from agents import chart_analyst, bias_analyst, news_scout, risk_manager

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def run(balance: float = 10000.0) -> dict:
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

    result["current_price"] = smc_summary.get("current_price")

    # Pre-filter: ถ้าไม่มี signal ไม่เรียก Claude เลย
    if not chart_analyst.has_signal(smc_summary):
        result["reject_reason"] = "SMC Engine: ไม่มี setup ครบเงื่อนไข"
        result["stages"]["smc"] = "NO_SIGNAL"
        return result

    result["stages"]["smc"] = "SIGNAL_FOUND"

    # ── Stage 2: Chart Analyst (Sonnet) — votes first ─────────
    analysis = chart_analyst.analyze(smc_summary)
    signal     = analysis.get("signal", "NO_TRADE")
    confidence = analysis.get("confidence", 0)
    chart_vote = analysis.get("vote", "NO")

    result["stages"]["chart"] = {
        "signal":        signal,
        "confidence":    confidence,
        "vote":          chart_vote,
        "vote_reasoning": analysis.get("vote_reasoning", ""),
        "reasoning":     analysis.get("reasoning"),
    }

    if signal == "NO_TRADE" or chart_vote == "NO":
        result["reject_reason"] = f"Chart Analyst voted NO — {analysis.get('vote_reasoning', 'NO_TRADE')}"
        return result

    # ── Stage 3: Bias Analyst (Sonnet, cached) — รู้ signal แล้ว ─
    bias = bias_analyst.analyze(signal_direction=signal)
    bias_vote = bias.get("vote", "NO")

    result["stages"]["bias"] = {
        "overall":        bias.get("overall_bias"),
        "trade_direction": bias.get("trade_direction"),
        "vote":           bias_vote,
        "vote_reasoning": bias.get("vote_reasoning", ""),
        "from_cache":     bias.get("from_cache", False),
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

    if vote_score < 2:
        losing = [k for k, v in votes.items() if not v]
        reasons = " | ".join(
            f"{k}: {result['vote_details'][k]}" for k in losing
        )
        result["reject_reason"] = f"Vote {vote_score}/3 — {reasons}"
        return result

    # ── Stage 6: Risk Manager (VETO) ──────────────────────────
    risk = risk_manager.evaluate(analysis, bias, balance)
    result["stages"]["risk"] = risk

    if risk.get("veto"):
        result["reject_reason"] = risk.get("veto_reason")
        return result

    # ── Stage 7: Supervisor Final Decision (Sonnet) ───────────
    verdict = _supervisor_judge(analysis, bias, news, risk, vote_score, result["vote_details"])
    result["stages"]["supervisor"] = verdict

    if verdict.get("approve"):
        result["approved"] = True
        result["final_signal"] = signal
        result["lot"] = risk.get("lot")
        result["risk_pct"] = risk.get("risk_pct")
        result["caution_mode"] = risk.get("caution_mode", False)
        result["entry_zone"] = analysis.get("entry_zone")
        result["stop_loss"] = analysis.get("stop_loss")
        result["take_profit"] = analysis.get("take_profit")
        result["rr_ratio"] = analysis.get("rr_ratio")
        result["reasoning"] = verdict.get("reasoning")
        result["analysis"] = analysis
    else:
        result["reject_reason"] = verdict.get("reasoning", "Supervisor rejected")

    return result


def _supervisor_judge(analysis, bias, news, risk, vote_score, vote_details: dict) -> dict:
    """
    Supervisor ตัดสินใจสุดท้าย — Claude Sonnet อ่าน reasoning ทุก agent
    ไม่ได้แค่นับ vote score แต่เข้าใจ WHY แต่ละ agent โหวต
    """
    chart_r = vote_details.get("chart", analysis.get("reasoning", ""))
    bias_r  = vote_details.get("bias",  bias.get("reasoning", ""))
    news_r  = vote_details.get("news",  news.get("reasoning", ""))

    prompt = f"""คุณคือ Supervisor Agent — ตัดสินใจสุดท้ายว่าจะ APPROVE หรือ REJECT trade นี้

═══ Agent Votes ═══
🔍 Chart Analyst  [{analysis.get('vote','?')}]: {chart_r}
   Signal: {analysis.get('signal')} | Confidence: {analysis.get('confidence')}% | RR: 1:{analysis.get('rr_ratio')} | Entry: {analysis.get('entry_zone')} | SL: {analysis.get('stop_loss')} | TP: {analysis.get('take_profit')}

🌍 Bias Analyst   [{bias.get('vote','?')}]: {bias_r}
   HTF: Daily={bias.get('daily_bias')} H4={bias.get('h4_bias')} H1={bias.get('h1_bias')} | Direction: {bias.get('trade_direction')}

📰 News Scout     [{news.get('vote','?')}]: {news_r}
   Risk: {news.get('risk_level')} | Key Event: {news.get('key_event')} | Impact: {news.get('gold_impact')}

⚖️ Risk Manager: Lot={risk.get('lot')} Risk={risk.get('risk_pct')}% | Caution={risk.get('caution_mode')} | {risk.get('notes','')}

Vote Score: {vote_score}/3

═══ คำถาม ═══
พิจารณา reasoning ของแต่ละ agent อย่างละเอียด:
- Agent ที่โหวต NO มีเหตุผลน่าเป็นห่วงแค่ไหน?
- Risk/Reward คุ้มค่าในสภาวะตลาดนี้มั้ย?
- มีจุดที่ต้องระวังพิเศษมั้ย (counter-trend, ก่อนข่าว, SL กว้าง)?

ตอบ JSON เท่านั้น:
{{
  "approve": true/false,
  "confidence": 0-100,
  "reasoning": "เหตุผล 2-3 ประโยค ภาษาไทย — ระบุว่าโหวตใครหนักที่สุดและทำไม"
}}"""

    response = client.messages.create(
        model=MODEL_SMART,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(text)
    except Exception:
        return {"approve": vote_score >= 2, "reasoning": "Auto-approve by vote score"}


def format_alert(result: dict) -> str:
    """แปลง supervisor result เป็น Telegram alert"""

    if not result.get("approved"):
        return (
            f"🔍 *Scan Complete — No Trade*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"❌ {result.get('reject_reason', 'ไม่ผ่านเงื่อนไข')}\n"
            f"Vote: `{result.get('vote_score', 0)}/3`\n"
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
    for agent, passed in votes_map.items():
        icon    = "✅" if passed else "❌"
        reason  = vote_detail.get(agent, "")
        label   = {"chart": "Chart", "bias": "Bias ", "news": "News "}[agent]
        vote_lines += f"\n  {icon} {label}: _{reason[:60]}_"

    entry = result.get("entry_zone")
    entry_str = f"`{entry[0]} - {entry[1]}`" if entry else "N/A"

    analysis   = result.get("analysis", {})
    setup_type = analysis.get("setup_type", "")
    rev_stars  = analysis.get("reversal_stars") or ""
    rev_score  = analysis.get("reversal_score", 0)

    if "REVERSAL" in str(setup_type):
        setup_line = f"🔄 Setup: *REVERSAL* {rev_stars} (score {rev_score}/10)\n"
    elif setup_type:
        setup_line = f"📐 Setup: `{setup_type}`\n"
    else:
        setup_line = ""

    return (
        f"🔔 *SETUP APPROVED — {signal}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{caution}"
        f"{emoji} Signal: *{signal}*\n"
        f"🗳 Vote: `{vote_bar}` {vote}/3{vote_lines}\n"
        f"{setup_line}"
        f"💰 ราคา: `{result.get('current_price')}`\n"
        f"📍 Entry: {entry_str}\n"
        f"🛑 SL: `{result.get('stop_loss')}`\n"
        f"🎯 TP: `{result.get('take_profit')}`\n"
        f"⚖️ RR: `1:{result.get('rr_ratio')}`\n"
        f"📦 Lot: `{result.get('lot')}` ({result.get('risk_pct')}% risk)\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📝 {result.get('reasoning', '')}\n"
        f"⏰ {result.get('timestamp')}"
    )
