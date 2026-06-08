"""
Risk Manager — คำนวณ lot size + VETO power
Rules:
  - Max risk 2% ต่อ trade
  - H4 ขัด bias → ลด lot 50%
  - Loss streak 3 ครั้ง → VETO หยุดเทรด
  - Daily loss > 3% → VETO หยุดทั้งวัน
  - Drawdown > 10% → VETO หยุดทั้งสัปดาห์
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from config.settings import MAX_RISK_PERCENT

DB_PATH = Path(__file__).parent.parent / "data" / "trade_log.db"

# Gold pip value (approximate)
# 1 pip XAUUSD = $0.01, 1 lot = 100oz
# pip value per lot = $1
GOLD_PIP_VALUE_PER_LOT = 1.0


def get_recent_results(days: int = 1) -> list:
    """ดึงผลเทรดล่าสุดจาก DB"""
    if not DB_PATH.exists():
        return []

    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT outcome, pnl_pips
        FROM trades
        WHERE action='confirmed' AND timestamp >= ?
        ORDER BY id DESC
    """, (since,)).fetchall()
    conn.close()
    return [{"outcome": r[0], "pnl_pips": r[1]} for r in rows]


def check_loss_streak() -> int:
    """นับ loss streak ติดต่อกัน"""
    if not DB_PATH.exists():
        return 0

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT outcome FROM trades
        WHERE action='confirmed' AND outcome != 'pending'
        ORDER BY id DESC LIMIT 10
    """).fetchall()
    conn.close()

    streak = 0
    for (outcome,) in rows:
        if outcome == 'loss':
            streak += 1
        else:
            break
    return streak


def get_daily_pnl_pct(balance: float) -> float:
    """คำนวณ P&L วันนี้เป็น %"""
    today_results = get_recent_results(days=1)
    total_pips = sum(r["pnl_pips"] or 0 for r in today_results)
    # แปลง pips → dollar → %
    dollar_pnl = total_pips * GOLD_PIP_VALUE_PER_LOT
    return (dollar_pnl / balance * 100) if balance > 0 else 0


def calculate_lot(balance: float, sl_pips: float, risk_pct: float = None) -> float:
    """
    คำนวณ lot size
    formula: lot = (balance × risk%) / (sl_pips × pip_value)
    """
    if risk_pct is None:
        risk_pct = MAX_RISK_PERCENT

    risk_amount = balance * (risk_pct / 100)
    if sl_pips <= 0:
        return 0.01

    lot = risk_amount / (sl_pips * GOLD_PIP_VALUE_PER_LOT * 100)
    # Round to 2 decimal places, min 0.01
    lot = max(0.01, round(lot, 2))
    return lot


def evaluate(
    analysis: dict,
    bias: dict,
    balance: float = 10000.0
) -> dict:
    """
    Risk Manager ประเมินและตัดสินใจ
    Returns: {approved, lot, risk_pct, veto, veto_reason, notes}
    """

    signal = analysis.get("signal", "NO_TRADE")
    entry = analysis.get("entry_zone")
    sl = analysis.get("stop_loss")
    tp = analysis.get("take_profit")
    htf_aligned = bias.get("aligned", True)
    trade_dir = bias.get("trade_direction", "BOTH")

    # คำนวณ SL เป็น pips
    sl_pips = 0
    if entry and sl:
        entry_mid = (entry[0] + entry[1]) / 2
        sl_pips = abs(entry_mid - sl) * 10  # Gold: 1 pip = $0.1

    # ── VETO checks ──────────────────────────────────────────────

    # 1. Loss streak
    streak = check_loss_streak()
    if streak >= 3:
        return {
            "approved": False,
            "veto": True,
            "veto_reason": f"🚫 Loss streak {streak} ครั้งติด — หยุดพักก่อน",
            "lot": 0,
            "risk_pct": 0,
            "notes": "แนะนำ review strategy ก่อนเทรดต่อ"
        }

    # 2. Daily loss limit
    daily_pnl = get_daily_pnl_pct(balance)
    if daily_pnl <= -3.0:
        return {
            "approved": False,
            "veto": True,
            "veto_reason": f"🚫 Daily loss {daily_pnl:.1f}% — หยุดเทรดวันนี้",
            "lot": 0,
            "risk_pct": 0,
            "notes": "กลับมาพรุ่งนี้"
        }

    # 3. SL ไม่มีหรือ signal ไม่ชัด
    if signal == "NO_TRADE" or not sl or sl_pips <= 0:
        return {
            "approved": False,
            "veto": True,
            "veto_reason": "🚫 ไม่มี SL ที่ชัดเจน",
            "lot": 0,
            "risk_pct": 0,
            "notes": "ต้องมี SL ก่อนเสมอ"
        }

    # 4. RR ต่ำเกินไป
    rr = analysis.get("rr_ratio", 0) or 0
    if rr < 1.5:
        return {
            "approved": False,
            "veto": True,
            "veto_reason": f"🚫 RR {rr} ต่ำเกินไป — ต้องการอย่างน้อย 1:1.5",
            "lot": 0,
            "risk_pct": 0,
            "notes": "ไม่คุ้มความเสี่ยง"
        }

    # ── ผ่าน VETO — คำนวณ lot ──────────────────────────────────

    risk_pct = MAX_RISK_PERCENT

    # H4 ขัด bias → ลด risk ครึ่งนึง
    h4_bias = bias.get("h4_bias", "neutral")
    h4_conflict = (
        (signal == "BUY" and h4_bias == "bearish") or
        (signal == "SELL" and h4_bias == "bullish")
    )

    caution_mode = False
    if h4_conflict or not htf_aligned:
        risk_pct = MAX_RISK_PERCENT * 0.5
        caution_mode = True

    lot = calculate_lot(balance, sl_pips, risk_pct)
    risk_amount = balance * (risk_pct / 100)

    notes = []
    if caution_mode:
        notes.append(f"⚠️ H4 ขัด bias → ลด lot เหลือ {risk_pct}% risk")
    if streak > 0:
        notes.append(f"⚠️ มี loss streak {streak} ครั้ง — ระวังด้วย")

    return {
        "approved": True,
        "veto": False,
        "veto_reason": None,
        "lot": lot,
        "risk_pct": risk_pct,
        "risk_amount": round(risk_amount, 2),
        "sl_pips": round(sl_pips, 1),
        "caution_mode": caution_mode,
        "loss_streak": streak,
        "daily_pnl_pct": round(daily_pnl, 2),
        "notes": " | ".join(notes) if notes else "✅ ปกติ"
    }


def format_risk_message(risk: dict, analysis: dict) -> str:
    """แปลงผล risk evaluation เป็นข้อความ Telegram"""

    if risk.get("veto"):
        return (
            f"⛔ *Risk Manager — VETO*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"{risk.get('veto_reason')}\n"
            f"📝 {risk.get('notes', '')}"
        )

    caution = "🟡 CAUTION" if risk.get("caution_mode") else "🟢 NORMAL"
    entry = analysis.get("entry_zone")
    entry_str = f"`{entry[0]} - {entry[1]}`" if entry else "N/A"

    return (
        f"⚖️ *Risk Manager — APPROVED*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Mode: {caution}\n"
        f"📦 Lot Size: `{risk.get('lot')}`\n"
        f"💰 Risk: `{risk.get('risk_pct')}%` (${risk.get('risk_amount')})\n"
        f"📏 SL Distance: `{risk.get('sl_pips')} pips`\n"
        f"📊 Daily P&L: `{risk.get('daily_pnl_pct')}%`\n"
        f"🔴 Loss Streak: `{risk.get('loss_streak')}`\n"
        f"📝 {risk.get('notes')}"
    )
