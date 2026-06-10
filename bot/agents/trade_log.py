"""
Trade Log — บันทึกทุก live trade ลง SQLite + CSV export
ข้อมูลที่เก็บ:
  - Signal ที่ bot แจ้ง (entry, sl, tp, score, stars, session)
  - Action ที่ user กด (confirmed / skipped)
  - Outcome หลังเทรดจริง (win / loss / be, pnl_pips, actual exit)
  - Export CSV ผ่าน /export command
"""

import sqlite3
import csv
import json
from datetime import datetime, date, timedelta
from pathlib import Path

DB_PATH  = Path(__file__).parent.parent / "data" / "trade_log.db"
CSV_PATH = Path(__file__).parent.parent / "data" / "trades_export.csv"


# ── Schema ────────────────────────────────────────────────────────

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT NOT NULL,
            pair         TEXT DEFAULT 'XAUUSD',
            session      TEXT,
            signal       TEXT NOT NULL,
            setup_type   TEXT,
            stars        TEXT,
            score        INTEGER,
            confidence   INTEGER,
            h4_bias      TEXT,
            entry_low    REAL,
            entry_high   REAL,
            stop_loss    REAL,
            take_profit  REAL,
            sl_pips      REAL,
            tp_pips      REAL,
            rr_plan      REAL,
            lot          REAL,
            risk_pct     REAL,
            key_factors  TEXT,
            reasoning    TEXT,
            action       TEXT NOT NULL,
            outcome      TEXT DEFAULT 'pending',
            actual_entry REAL,
            actual_exit  REAL,
            pnl_pips     REAL,
            duration_min INTEGER,
            notes        TEXT
        )
    """)
    # Migrate old databases — เพิ่ม column ใหม่โดยไม่ทำลายข้อมูลเก่า
    existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    migrations = [
        ("session",      "TEXT"),
        ("setup_type",   "TEXT"),
        ("stars",        "TEXT"),
        ("score",        "INTEGER"),
        ("h4_bias",      "TEXT"),
        ("sl_pips",      "REAL"),
        ("tp_pips",      "REAL"),
        ("rr_plan",      "REAL"),
        ("lot",          "REAL"),
        ("risk_pct",     "REAL"),
        ("actual_entry", "REAL"),
        ("actual_exit",  "REAL"),
        ("duration_min", "INTEGER"),
    ]
    for col, typ in migrations:
        if col not in existing:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
    conn.commit()
    conn.close()


# ── Write ─────────────────────────────────────────────────────────

def log_trade(analysis: dict, action: str) -> int:
    """
    บันทึก trade หลัง user กด Confirm/Skip
    analysis มาจาก supervisor.run() → result["analysis"]
    Returns trade_id (ใช้กับ /outcome)
    """
    init_db()

    entry      = analysis.get("entry_zone") or []
    key_factors = analysis.get("key_factors", [])
    # รองรับทั้ง rr_ratio (Claude analysis) และ rr (rule-only signal)
    rr_val     = analysis.get("rr_ratio") or analysis.get("rr")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        INSERT INTO trades (
            timestamp, pair, session, signal, setup_type, stars, score,
            confidence, h4_bias,
            entry_low, entry_high, stop_loss, take_profit,
            sl_pips, tp_pips, rr_plan, lot, risk_pct,
            key_factors, reasoning, action
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?
        )
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        analysis.get("pair", "XAUUSD"),
        analysis.get("session"),
        analysis.get("signal"),
        analysis.get("setup_type"),
        analysis.get("reversal_stars") or analysis.get("stars"),
        analysis.get("reversal_score") or analysis.get("score"),
        analysis.get("confidence"),
        analysis.get("h4_bias") or analysis.get("smc_bias"),
        entry[0] if len(entry) > 0 else analysis.get("entry"),
        entry[1] if len(entry) > 1 else None,
        analysis.get("stop_loss") or analysis.get("sl"),
        analysis.get("take_profit") or analysis.get("tp"),
        analysis.get("sl_pips"),
        analysis.get("tp_pips"),
        rr_val,
        analysis.get("lot"),
        analysis.get("risk_pct"),
        json.dumps(key_factors, ensure_ascii=False),
        analysis.get("reasoning"),
        action,
    ))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def update_outcome(trade_id: int, outcome: str,
                   pnl_pips: float = None,
                   actual_entry: float = None,
                   actual_exit: float = None,
                   duration_min: int = None,
                   notes: str = None):
    """
    อัพเดทผลลัพธ์หลังเทรดจริง
    outcome: 'win' | 'loss' | 'be'
    เรียกผ่าน /outcome [id] [win/loss/be] [pips] [exit_price]
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE trades
        SET outcome      = ?,
            pnl_pips     = ?,
            actual_entry = COALESCE(?, actual_entry),
            actual_exit  = ?,
            duration_min = ?,
            notes        = COALESCE(?, notes)
        WHERE id = ?
    """, (outcome, pnl_pips, actual_entry, actual_exit, duration_min, notes, trade_id))
    conn.commit()
    conn.close()


# ── Read ──────────────────────────────────────────────────────────

def get_trade(trade_id: int) -> dict | None:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT * FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    cols = [d[0] for d in conn.description] if False else [
        "id","timestamp","pair","session","signal","setup_type","stars","score",
        "confidence","h4_bias","entry_low","entry_high","stop_loss","take_profit",
        "sl_pips","tp_pips","rr_plan","lot","risk_pct","key_factors","reasoning",
        "action","outcome","actual_entry","actual_exit","pnl_pips","duration_min","notes"
    ]
    return dict(zip(cols, row))


def get_all_trades(action_filter: str = None, limit: int = 200) -> list[dict]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    if action_filter:
        rows = conn.execute(
            "SELECT * FROM trades WHERE action=? ORDER BY id DESC LIMIT ?",
            (action_filter, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    cols = [
        "id","timestamp","pair","session","signal","setup_type","stars","score",
        "confidence","h4_bias","entry_low","entry_high","stop_loss","take_profit",
        "sl_pips","tp_pips","rr_plan","lot","risk_pct","key_factors","reasoning",
        "action","outcome","actual_entry","actual_exit","pnl_pips","duration_min","notes"
    ]
    return [dict(zip(cols, r)) for r in rows]


def get_summary() -> dict:
    init_db()
    conn = sqlite3.connect(DB_PATH)

    total     = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    confirmed = conn.execute("SELECT COUNT(*) FROM trades WHERE action='confirmed'").fetchone()[0]
    skipped   = conn.execute("SELECT COUNT(*) FROM trades WHERE action='skipped'").fetchone()[0]
    wins      = conn.execute("SELECT COUNT(*) FROM trades WHERE outcome='win'").fetchone()[0]
    losses    = conn.execute("SELECT COUNT(*) FROM trades WHERE outcome='loss'").fetchone()[0]
    be        = conn.execute("SELECT COUNT(*) FROM trades WHERE outcome='be'").fetchone()[0]
    pending   = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE outcome='pending' AND action='confirmed'"
    ).fetchone()[0]

    pnl_row   = conn.execute(
        "SELECT COALESCE(SUM(pnl_pips),0), COALESCE(AVG(pnl_pips),0) "
        "FROM trades WHERE outcome IN ('win','loss','be') AND action='confirmed'"
    ).fetchone()
    total_pips, avg_pips = pnl_row

    gross_win  = conn.execute(
        "SELECT COALESCE(SUM(pnl_pips),0) FROM trades WHERE outcome='win'"
    ).fetchone()[0]
    gross_loss = abs(conn.execute(
        "SELECT COALESCE(SUM(pnl_pips),0) FROM trades WHERE outcome='loss'"
    ).fetchone()[0])
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else None

    win_rate  = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0

    best_setup = conn.execute("""
        SELECT setup_type, signal, COUNT(*) cnt
        FROM trades WHERE outcome='win'
        GROUP BY setup_type, signal
        ORDER BY cnt DESC LIMIT 1
    """).fetchone()

    conn.close()
    return {
        "total_signals": total,
        "confirmed":     confirmed,
        "skipped":       skipped,
        "wins":          wins,
        "losses":        losses,
        "be":            be,
        "pending":       pending,
        "win_rate":      win_rate,
        "total_pips":    round(total_pips, 1),
        "avg_pips":      round(avg_pips, 1),
        "profit_factor": pf,
        "best_setup":    f"{best_setup[0]} {best_setup[1]}" if best_setup else "-",
    }


def get_daily_breakdown(days: int = 7) -> list[dict]:
    """P&L แยกตามวัน ย้อนหลัง N วัน"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT DATE(timestamp) as day,
               COUNT(*) as trades,
               SUM(CASE WHEN outcome='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses,
               COALESCE(SUM(pnl_pips), 0) as pips
        FROM trades
        WHERE action='confirmed'
          AND DATE(timestamp) >= DATE('now', ?)
        GROUP BY day
        ORDER BY day DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [{"day": r[0], "trades": r[1], "wins": r[2], "losses": r[3], "pips": round(r[4], 1)}
            for r in rows]


# ── Export ────────────────────────────────────────────────────────

def export_csv() -> Path:
    trades = get_all_trades(action_filter="confirmed")
    CSV_PATH.parent.mkdir(exist_ok=True)
    fields = [
        "id","timestamp","session","signal","setup_type","stars","score",
        "confidence","h4_bias","entry_low","stop_loss","take_profit",
        "sl_pips","tp_pips","rr_plan","lot","risk_pct",
        "action","outcome","actual_entry","actual_exit","pnl_pips","duration_min","notes"
    ]
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trades)
    return CSV_PATH


# ── Format ────────────────────────────────────────────────────────

def format_report() -> str:
    s      = get_summary()
    daily  = get_daily_breakdown(7)
    recent = get_all_trades(action_filter="confirmed", limit=5)

    pips_str = f"{s['total_pips']:+.1f}" if s['total_pips'] != 0 else "0"
    pf_str   = str(s['profit_factor']) if s['profit_factor'] else "N/A"

    lines = [
        "📊 *Live Trade Report*",
        "━━━━━━━━━━━━━━━━━",
        f"📡 Signal ทั้งหมด: `{s['total_signals']}`  |  Confirmed: `{s['confirmed']}`  |  Skip: `{s['skipped']}`",
        f"🏆 W: `{s['wins']}` | L: `{s['losses']}` | BE: `{s['be']}` | ⏳ Pending: `{s['pending']}`",
        f"📈 Win Rate: `{s['win_rate']}%`  |  PF: `{pf_str}`",
        f"💰 Total P&L: `{pips_str} pips`  |  Avg: `{s['avg_pips']:+.1f}p`",
        f"⭐ Best Setup: `{s['best_setup']}`",
        "",
        "📅 *P&L รายวัน (7 วันล่าสุด)*",
        "━━━━━━━━━━━━━━━━━",
    ]

    if not daily:
        lines.append("ยังไม่มีข้อมูล")
    else:
        for d in daily:
            bar  = "🟢" if d["pips"] >= 0 else "🔴"
            wr   = f"{d['wins']/(d['wins']+d['losses'])*100:.0f}%" if (d["wins"]+d["losses"]) > 0 else "-"
            lines.append(
                f"{bar} `{d['day']}` — {d['trades']}T  W{d['wins']}/L{d['losses']}  "
                f"WR:{wr}  `{d['pips']:+.1f}p`"
            )

    lines += ["", "📋 *5 Confirmed Trade ล่าสุด*", "━━━━━━━━━━━━━━━━━"]

    if not recent:
        lines.append("ยังไม่มี trade")
    else:
        for t in recent:
            icon   = "✅" if t["outcome"] == "win" else "❌" if t["outcome"] == "loss" else "⏳" if t["outcome"] == "pending" else "↔️"
            stars  = t.get("stars") or ""
            pips_t = f"{t['pnl_pips']:+.1f}p" if t["pnl_pips"] is not None else "pending"
            lines.append(
                f"{icon} #{t['id']} `{t['signal']}` {stars}  "
                f"score={t.get('score') or '-'}  "
                f"{t['timestamp'][:16]}  `{pips_t}`"
            )

    lines += ["", "📤 Export CSV: `/export`  |  อัพเดทผล: `/outcome [id] [win/loss/be] [pips] [exit]`"]
    return "\n".join(lines)


def format_trade_list(trades: list[dict]) -> str:
    """แสดง trade list แบบตาราง — ใช้ใน /trades"""
    if not trades:
        return "ยังไม่มี trade ที่บันทึก"

    lines = ["📋 *Trade History*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for t in trades:
        icon   = "✅" if t["outcome"] == "win" else "❌" if t["outcome"] == "loss" else "⏳" if t["outcome"] == "pending" else "↔️"
        act    = "👆" if t["action"] == "confirmed" else "⏭"
        stars  = t.get("stars") or ""
        sess   = t.get("session") or "-"
        pips_t = f"`{t['pnl_pips']:+.1f}p`" if t["pnl_pips"] is not None else "`pending`"
        entry  = t.get("entry_low") or "-"
        sl     = t.get("stop_loss") or "-"
        tp     = t.get("take_profit") or "-"
        lines.append(
            f"{icon}{act} *#{t['id']}* `{t['signal']}` {stars} "
            f"score={t.get('score') or '-'}  {sess}\n"
            f"   Entry:`{entry}` SL:`{sl}` TP:`{tp}`\n"
            f"   {t['timestamp'][:16]}  {pips_t}"
            + (f"  _{t['notes']}_" if t.get("notes") else "")
        )
    return "\n".join(lines)
