"""
Trade Log — บันทึกทุก trade ลง SQLite
ใช้สำหรับ:
  - ติดตาม performance
  - Phase 4: AI อ่านและเรียนรู้จาก log
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trade_log.db"


def init_db():
    """สร้าง database และ table ถ้ายังไม่มี"""
    DB_PATH.parent.mkdir(exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            pair        TEXT DEFAULT 'XAUUSD',
            signal      TEXT NOT NULL,
            confidence  INTEGER,
            entry_low   REAL,
            entry_high  REAL,
            stop_loss   REAL,
            take_profit REAL,
            rr_ratio    REAL,
            smc_bias    TEXT,
            had_sweep   INTEGER,
            key_factors TEXT,
            reasoning   TEXT,
            action      TEXT NOT NULL,
            outcome     TEXT DEFAULT 'pending',
            pnl_pips    REAL,
            notes       TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_trade(analysis: dict, action: str) -> int:
    """
    บันทึก trade หลังกด Confirm หรือ Skip
    action: 'confirmed' หรือ 'skipped'
    คืนค่า trade_id
    """
    init_db()

    entry = analysis.get("entry_zone")
    key_factors = analysis.get("key_factors", [])

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        INSERT INTO trades (
            timestamp, pair, signal, confidence,
            entry_low, entry_high, stop_loss, take_profit, rr_ratio,
            smc_bias, had_sweep, key_factors, reasoning, action
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "XAUUSD",
        analysis.get("signal"),
        analysis.get("confidence"),
        entry[0] if entry else None,
        entry[1] if entry else None,
        analysis.get("stop_loss"),
        analysis.get("take_profit"),
        analysis.get("rr_ratio"),
        analysis.get("smc_bias"),
        1 if analysis.get("had_sweep") else 0,
        json.dumps(key_factors),
        analysis.get("reasoning"),
        action
    ))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return trade_id


def update_outcome(trade_id: int, outcome: str, pnl_pips: float = None, notes: str = None):
    """
    อัพเดทผลลัพธ์ของ trade หลัง TP/SL โดน
    outcome: 'win' / 'loss' / 'breakeven'
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE trades
        SET outcome = ?, pnl_pips = ?, notes = ?
        WHERE id = ?
    """, (outcome, pnl_pips, notes, trade_id))
    conn.commit()
    conn.close()


def get_summary() -> dict:
    """สรุปสถิติทั้งหมด"""
    init_db()
    conn = sqlite3.connect(DB_PATH)

    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    confirmed = conn.execute("SELECT COUNT(*) FROM trades WHERE action='confirmed'").fetchone()[0]
    skipped = conn.execute("SELECT COUNT(*) FROM trades WHERE action='skipped'").fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM trades WHERE outcome='win'").fetchone()[0]
    losses = conn.execute("SELECT COUNT(*) FROM trades WHERE outcome='loss'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM trades WHERE outcome='pending' AND action='confirmed'").fetchone()[0]

    win_rate = round(wins / confirmed * 100, 1) if confirmed > 0 else 0

    # หา setup ที่ดีที่สุด
    best_setup = conn.execute("""
        SELECT signal, smc_bias, COUNT(*) as cnt
        FROM trades
        WHERE outcome='win'
        GROUP BY signal, smc_bias
        ORDER BY cnt DESC
        LIMIT 1
    """).fetchone()

    conn.close()

    return {
        "total_signals": total,
        "confirmed": confirmed,
        "skipped": skipped,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": f"{win_rate}%",
        "best_setup": f"{best_setup[0]} ({best_setup[1]} bias)" if best_setup else "ยังไม่มีข้อมูล"
    }


def get_recent_trades(limit: int = 10) -> list:
    """ดึง trade ล่าสุด"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT timestamp, signal, confidence, action, outcome, rr_ratio, reasoning
        FROM trades
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    return [{
        "timestamp": r[0],
        "signal": r[1],
        "confidence": r[2],
        "action": r[3],
        "outcome": r[4],
        "rr_ratio": r[5],
        "reasoning": r[6]
    } for r in rows]


def format_report() -> str:
    """แปลงสรุปเป็นข้อความสำหรับ Telegram"""
    s = get_summary()
    recent = get_recent_trades(5)

    lines = [
        "📊 *Trade Log Report*",
        "━━━━━━━━━━━━━━━━━",
        f"📡 Signal ทั้งหมด: `{s['total_signals']}`",
        f"✅ Confirmed: `{s['confirmed']}`",
        f"❌ Skipped: `{s['skipped']}`",
        f"",
        f"🏆 Win: `{s['wins']}` | Loss: `{s['losses']}` | Pending: `{s['pending']}`",
        f"📈 Win Rate: `{s['win_rate']}`",
        f"⭐ Best Setup: `{s['best_setup']}`",
        f"",
        f"📋 *5 รายการล่าสุด*",
        "━━━━━━━━━━━━━━━━━",
    ]

    if not recent:
        lines.append("ยังไม่มี trade ที่บันทึก")
    else:
        for t in recent:
            icon = "✅" if t["outcome"] == "win" else "❌" if t["outcome"] == "loss" else "⏳"
            action_icon = "👆" if t["action"] == "confirmed" else "⏭"
            lines.append(
                f"{icon} {action_icon} `{t['signal']}` conf:{t['confidence']}% "
                f"| {t['timestamp'][:10]}"
            )

    return "\n".join(lines)
