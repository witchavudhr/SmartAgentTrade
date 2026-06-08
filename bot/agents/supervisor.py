"""
Supervisor — LLM as a Judge
รวบรวม input จากทุก agent แล้วตัดสินใจสุดท้าย
Voting: Chart (1) + Bias (1) + News (1) → ต้องผ่าน 2/3
Risk Manager มี VETO power แยกต่างหาก
"""

import anthropic
import json
from datetime import datetime
from config.settings import ANTHROPIC_API_KEY, MODEL_FAST
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

    # ── Stage 2: News Scout ────────────────────────────────────
    news = news_scout.analyze()
    blocked = news.get("is_blocked", False)
    result["stages"]["news"] = "BLOCKED" if blocked else "CLEAR"

    if blocked:
        result["reject_reason"] = news.get("block_reason", "ใกล้ข่าว High Impact")
        return result

    # ── Stage 3: Chart Analyst (Sonnet) ───────────────────────
    analysis = chart_analyst.analyze(smc_summary)
    signal = analysis.get("signal", "NO_TRADE")
    confidence = analysis.get("confidence", 0)

    result["stages"]["chart"] = {
        "signal": signal,
        "confidence": confidence,
        "reasoning": analysis.get("reasoning")
    }

    if signal == "NO_TRADE":
        result["reject_reason"] = "Chart Analyst: NO_TRADE"
        return result

    # ── Stage 4: Bias Analyst (Haiku, cached) ────────────────
    bias = bias_analyst.analyze()
    trade_dir = bias.get("trade_direction", "BOTH")
    bias_pass = (
        trade_dir == "BOTH" or
        (signal == "BUY" and trade_dir == "BUY_ONLY") or
        (signal == "SELL" and trade_dir == "SELL_ONLY")
    )

    result["stages"]["bias"] = {
        "overall": bias.get("overall_bias"),
        "trade_direction": trade_dir,
        "aligned": bias.get("aligned"),
        "pass": bias_pass,
        "from_cache": bias.get("from_cache", False)
    }

    # ── Stage 5: Voting ────────────────────────────────────────
    votes = {
        "chart": signal in ["BUY", "SELL"] and confidence >= 60,
        "bias": bias_pass,
        "news": not blocked
    }

    vote_score = sum(votes.values())
    result["vote_score"] = vote_score
    result["votes"] = votes

    if vote_score < 2:
        result["reject_reason"] = f"Vote ไม่ผ่าน {vote_score}/3"
        return result

    # ── Stage 6: Risk Manager (VETO) ──────────────────────────
    risk = risk_manager.evaluate(analysis, bias, balance)
    result["stages"]["risk"] = risk

    if risk.get("veto"):
        result["reject_reason"] = risk.get("veto_reason")
        return result

    # ── Stage 7: Supervisor Final Decision ────────────────────
    # ถ้า vote 2/3 หรือ 3/3 → ให้ Claude สรุปเป็น final verdict
    verdict = _supervisor_judge(analysis, bias, news, risk, vote_score)
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


def _supervisor_judge(analysis, bias, news, risk, vote_score) -> dict:
    """Supervisor ตัดสินใจสุดท้าย — LLM as a Judge"""

    prompt = f"""คุณคือ Supervisor Trading Agent ที่ตัดสินใจสุดท้าย

ผลการประเมินจากทุก Agent:

Chart Analyst:
- Signal: {analysis.get('signal')} (confidence {analysis.get('confidence')}%)
- Entry: {analysis.get('entry_zone')}
- SL: {analysis.get('stop_loss')} | TP: {analysis.get('take_profit')}
- RR: 1:{analysis.get('rr_ratio')}
- Reasoning: {analysis.get('reasoning')}

Bias Analyst:
- Overall: {bias.get('overall_bias')} | Aligned: {bias.get('aligned')}
- H4: {bias.get('h4_bias')} | H1: {bias.get('h1_bias')}
- Trade Direction: {bias.get('trade_direction')}

News Scout:
- Risk: {news.get('risk_level')} | Safe: {news.get('safe_to_trade')}
- Key Event: {news.get('key_event')}

Risk Manager:
- Lot: {risk.get('lot')} | Risk: {risk.get('risk_pct')}%
- Caution Mode: {risk.get('caution_mode')}
- Notes: {risk.get('notes')}

Vote Score: {vote_score}/3

ตัดสินใจว่าควร APPROVE หรือ REJECT trade นี้
พิจารณาว่า risk/reward คุ้มมั้ยในสภาวะตลาดนี้

ตอบ JSON:
{{
  "approve": true/false,
  "confidence": 0-100,
  "reasoning": "เหตุผลสั้นๆ ภาษาไทย ไม่เกิน 2 ประโยค"
}}"""

    response = client.messages.create(
        model=MODEL_FAST,
        max_tokens=200,
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

    vote = result.get("vote_score", 0)
    vote_bar = "●" * vote + "○" * (3 - vote)

    entry = result.get("entry_zone")
    entry_str = f"`{entry[0]} - {entry[1]}`" if entry else "N/A"

    return (
        f"🔔 *SETUP APPROVED — {signal}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{caution}"
        f"{emoji} Signal: *{signal}*\n"
        f"🗳 Vote: `{vote_bar}` {vote}/3\n"
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
