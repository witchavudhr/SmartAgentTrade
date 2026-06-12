"""
Paper Trader — บันทึก trade ลงกระดาษ (ไม่ได้เทรดจริง)
ใช้สำหรับทดสอบ strategy ก่อน live

Features:
  - บันทึก entry: direction, price, SL, TP, setup type, stars
  - ติดตาม open trades (เช็คราคาจริงว่าถึง TP/SL ยัง)
  - สรุป P&L, win rate, avg RR
  - รองรับ Scale-in (หลาย entry ต่อ 1 idea)

Commands:
  /paper buy 4290 sl 4250 tp 4360
  /paper sell 4350 sl 4380 tp 4290
  /paper close [price]
  /paper status
  /pnl
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from agents.json_utils import fmt_pts

DB_PATH = Path(__file__).parent.parent / "data" / "paper_trades.db"


# ─── Schema ───────────────────────────────────────────────────────

def _init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            direction   TEXT    NOT NULL,        -- BUY / SELL
            entry_price REAL    NOT NULL,
            sl_price    REAL    NOT NULL,
            tp_price    REAL    NOT NULL,
            lot         REAL    DEFAULT 0.01,
            setup_type  TEXT,                    -- A_LONG / B_SHORT / C_LONG ฯลฯ
            stars       TEXT,                    -- ★ / ★★ / ★★★
            session     TEXT,                    -- London / NY / ...
            notes       TEXT,
            status      TEXT    DEFAULT 'open',  -- open / win / loss / be / manual
            close_price REAL,
            pnl_pips    REAL,
            pnl_pct     REAL,
            rr_actual   REAL,
            opened_at   TEXT    NOT NULL,
            closed_at   TEXT
        )
    """)
    conn.commit()
    conn.close()


_init_db()


# ─── Open Trade ───────────────────────────────────────────────────

def open_trade(
    direction: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    lot: float = 0.01,
    setup_type: str = None,
    stars: str = None,
    session: str = None,
    notes: str = None,
) -> dict:
    """
    บันทึก paper trade ใหม่
    direction: 'BUY' หรือ 'SELL'
    """
    direction = direction.upper()
    if direction not in ("BUY", "SELL"):
        return {"error": "direction ต้องเป็น BUY หรือ SELL"}

    sl_pips = abs(entry_price - sl_price) * 10
    tp_pips = abs(entry_price - tp_price) * 10
    rr = round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0

    if rr < 1.0:
        return {"error": f"RR = {rr} ต่ำเกินไป (ต้องมากกว่า 1:1)"}

    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        INSERT INTO paper_trades
          (direction, entry_price, sl_price, tp_price, lot,
           setup_type, stars, session, notes, opened_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (direction, entry_price, sl_price, tp_price, lot,
          setup_type, stars, session, notes,
          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": trade_id,
        "direction": direction,
        "entry": entry_price,
        "sl": sl_price,
        "tp": tp_price,
        "sl_pips": round(sl_pips, 1),
        "tp_pips": round(tp_pips, 1),
        "rr": rr,
        "lot": lot,
        "setup_type": setup_type,
        "stars": stars,
    }


# ─── Close Trade ──────────────────────────────────────────────────

def close_trade(trade_id: int, close_price: float, balance: float = 10000.0) -> dict:
    """ปิด paper trade และคำนวณ P&L"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT * FROM paper_trades WHERE id=? AND status='open'", (trade_id,)
    ).fetchone()

    if not row:
        conn.close()
        return {"error": f"ไม่พบ trade #{trade_id} หรือปิดแล้ว"}

    cols = [d[0] for d in conn.execute("SELECT * FROM paper_trades LIMIT 0").description]
    trade = dict(zip(cols, row))

    direction   = trade["direction"]
    entry_price = trade["entry_price"]
    sl_price    = trade["sl_price"]
    tp_price    = trade["tp_price"]
    lot         = trade["lot"]

    # คำนวณ P&L
    if direction == "BUY":
        pnl_pips = (close_price - entry_price) * 10
    else:
        pnl_pips = (entry_price - close_price) * 10

    pnl_dollar = pnl_pips * lot * 100   # Gold: 1 pip × 1 lot = $100
    pnl_pct    = (pnl_dollar / balance) * 100

    sl_pips    = abs(entry_price - sl_price) * 10
    rr_actual  = round(pnl_pips / sl_pips, 2) if sl_pips > 0 else 0

    # ตัดสิน outcome
    if direction == "BUY":
        if close_price >= tp_price:
            status = "win"
        elif close_price <= sl_price:
            status = "loss"
        elif abs(close_price - entry_price) < 0.5:
            status = "be"   # breakeven
        else:
            status = "manual"
    else:
        if close_price <= tp_price:
            status = "win"
        elif close_price >= sl_price:
            status = "loss"
        elif abs(close_price - entry_price) < 0.5:
            status = "be"
        else:
            status = "manual"

    conn.execute("""
        UPDATE paper_trades
        SET status=?, close_price=?, pnl_pips=?, pnl_pct=?, rr_actual=?, closed_at=?
        WHERE id=?
    """, (status, close_price, round(pnl_pips, 1), round(pnl_pct, 2),
          rr_actual, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), trade_id))
    conn.commit()
    conn.close()

    return {
        "id": trade_id,
        "direction": direction,
        "entry": entry_price,
        "close": close_price,
        "pnl_pips": round(pnl_pips, 1),
        "pnl_dollar": round(pnl_dollar, 2),
        "pnl_pct": round(pnl_pct, 2),
        "rr_actual": rr_actual,
        "status": status,
    }


# ─── Check Open Trades ────────────────────────────────────────────

def check_open_trades(current_price: float) -> list[dict]:
    """เช็คว่า open trades โดนหรือยัง (TP/SL touched)"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT id, direction, entry_price, sl_price, tp_price FROM paper_trades WHERE status='open'"
    ).fetchall()
    conn.close()

    touched = []
    for trade_id, direction, entry, sl, tp in rows:
        if direction == "BUY":
            if current_price >= tp:
                touched.append({"id": trade_id, "event": "TP_HIT",  "price": current_price})
            elif current_price <= sl:
                touched.append({"id": trade_id, "event": "SL_HIT",  "price": current_price})
        else:
            if current_price <= tp:
                touched.append({"id": trade_id, "event": "TP_HIT",  "price": current_price})
            elif current_price >= sl:
                touched.append({"id": trade_id, "event": "SL_HIT",  "price": current_price})
    return touched


# ─── Get Open Trades ──────────────────────────────────────────────

def get_open_trades() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT id, direction, entry_price, sl_price, tp_price, lot,
               setup_type, stars, session, opened_at
        FROM paper_trades WHERE status='open'
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    return [
        {
            "id": r[0], "direction": r[1], "entry": r[2],
            "sl": r[3], "tp": r[4], "lot": r[5],
            "setup_type": r[6], "stars": r[7],
            "session": r[8], "opened_at": r[9],
            "sl_pips": round(abs(r[2]-r[3])*10, 1),
            "tp_pips": round(abs(r[2]-r[4])*10, 1),
            "rr":      round(abs(r[2]-r[4])/abs(r[2]-r[3]), 2) if abs(r[2]-r[3]) > 0 else 0,
        }
        for r in rows
    ]


# ─── P&L Summary ─────────────────────────────────────────────────

def get_pnl_summary() -> dict:
    """สรุป paper trade ทั้งหมด"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT status, pnl_pips, pnl_pct, rr_actual, direction, setup_type, stars
        FROM paper_trades WHERE status != 'open'
        ORDER BY id DESC
    """).fetchall()
    conn.close()

    if not rows:
        return {"total": 0, "message": "ยังไม่มี trade ที่ปิดแล้ว"}

    total  = len(rows)
    wins   = sum(1 for r in rows if r[0] == "win")
    losses = sum(1 for r in rows if r[0] == "loss")
    be     = sum(1 for r in rows if r[0] == "be")

    total_pips = sum(r[1] or 0 for r in rows)
    total_pct  = sum(r[2] or 0 for r in rows)
    avg_rr     = sum(r[3] or 0 for r in rows if r[3]) / total if total else 0

    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    # Best / Worst
    pips_list = [r[1] or 0 for r in rows]
    best  = max(pips_list) if pips_list else 0
    worst = min(pips_list) if pips_list else 0

    # Streak
    streak = 0
    streak_type = ""
    for r in rows:
        if r[0] == "win":
            if streak_type == "win":
                streak += 1
            else:
                streak = 1
                streak_type = "win"
            break
        elif r[0] == "loss":
            if streak_type == "loss":
                streak += 1
            else:
                streak = 1
                streak_type = "loss"
            break

    return {
        "total":      total,
        "wins":       wins,
        "losses":     losses,
        "be":         be,
        "win_rate":   round(win_rate, 1),
        "total_pips": round(total_pips, 1),
        "total_pct":  round(total_pct, 2),
        "avg_rr":     round(avg_rr, 2),
        "best_pips":  round(best, 1),
        "worst_pips": round(worst, 1),
        "streak":     streak,
        "streak_type": streak_type,
        "recent":     rows[:5],
    }


# ─── Format Messages ──────────────────────────────────────────────

def format_open_trade(t: dict) -> str:
    d = t["direction"]
    emoji = "🟢" if d == "BUY" else "🔴"
    stars = t.get("stars") or ""
    stype = t.get("setup_type") or ""
    return (
        f"{emoji} *#{t['id']} {d}* {stars} {stype}\n"
        f"  Entry: `{t['entry']}` | SL: `{t['sl']}` | TP: `{t['tp']}`\n"
        f"  SL: `{fmt_pts(t['sl_pips'])} จุด` | TP: `{fmt_pts(t['tp_pips'])} จุด` | RR: `1:{t['rr']}`\n"
        f"  🕐 {t['opened_at']}"
    )


def format_close_result(r: dict) -> str:
    icon = "✅" if r["status"] == "win" else "❌" if r["status"] == "loss" else "↔️"
    pips_sign = "+" if r["pnl_pips"] >= 0 else ""
    return (
        f"{icon} *Paper Trade #{r['id']} ปิดแล้ว*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Direction: {r['direction']}\n"
        f"Entry: `{r['entry']}` → Close: `{r['close']}`\n"
        f"P&L: `{fmt_pts(r['pnl_pips'], sign=True)} จุด` (${pips_sign}{r['pnl_dollar']})\n"
        f"RR Actual: `1:{r['rr_actual']}`\n"
        f"Status: *{r['status'].upper()}*"
    )


def format_pnl_summary(s: dict) -> str:
    if not s.get("total"):
        return "📋 ยังไม่มี paper trade ที่ปิดแล้วครับ"

    streak_txt = ""
    if s["streak"] > 1:
        streak_txt = f"\n🔥 Streak: `{s['streak']} {s['streak_type'].upper()}`"

    pips_sign = "+" if s["total_pips"] >= 0 else ""
    return (
        f"📋 *Paper Trade P&L Summary*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Total Trades: `{s['total']}`\n"
        f"✅ Win: `{s['wins']}` | ❌ Loss: `{s['losses']}` | ↔️ BE: `{s['be']}`\n"
        f"🎯 Win Rate: `{s['win_rate']}%`\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📊 Total P&L: `{fmt_pts(s['total_pips'], sign=True)} จุด` ({pips_sign}{s['total_pct']}%)\n"
        f"⚖️ Avg RR: `1:{s['avg_rr']}`\n"
        f"🏆 Best: `{fmt_pts(s['best_pips'], sign=True)} จุด` | 💀 Worst: `{fmt_pts(s['worst_pips'], sign=True)} จุด`"
        f"{streak_txt}"
    )
