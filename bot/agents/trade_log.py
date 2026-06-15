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
from agents.json_utils import fmt_pts

DB_PATH  = Path(__file__).parent.parent / "data" / "trade_log.db"
CSV_PATH = Path(__file__).parent.parent / "data" / "trades_export.csv"


# ── Schema ────────────────────────────────────────────────────────

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    # ── scan_log: บันทึกทุก scan รวม rejected ─────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL,
            session       TEXT,
            signal        TEXT,
            confidence    INTEGER,
            vote_score    INTEGER,
            chart_vote    INTEGER,
            bias_vote     INTEGER,
            news_vote     INTEGER,
            approved      INTEGER,
            reject_reason TEXT,
            entry         REAL,
            sl            REAL,
            tp            REAL,
            rr            REAL,
            h4_bias       TEXT,
            setup_type    TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT
        )
    """)

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

def log_scan(result: dict):
    """
    บันทึกทุก scan result รวม rejected
    เรียกหลัง supervisor.run() ทุกครั้ง ก่อนเช็ค approved/rejected
    """
    init_db()
    analysis  = result.get("analysis") or {}
    votes     = result.get("votes", {})
    entry_raw = analysis.get("entry_zone") or analysis.get("entry")
    entry_val = (
        (entry_raw[0] + entry_raw[1]) / 2 if isinstance(entry_raw, list)
        else float(entry_raw) if entry_raw else None
    )
    from agents.smc_engine import get_session
    sess = get_session()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO scan_log (
            timestamp, session, signal, confidence, vote_score,
            chart_vote, bias_vote, news_vote,
            approved, reject_reason,
            entry, sl, tp, rr, h4_bias, setup_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        sess.get("session"),
        analysis.get("signal") or result.get("final_signal"),
        analysis.get("confidence"),
        result.get("vote_score", 0),
        1 if votes.get("chart") else 0,
        1 if votes.get("bias")  else 0,
        1 if votes.get("news")  else 0,
        1 if result.get("approved") else 0,
        result.get("reject_reason"),
        entry_val,
        analysis.get("stop_loss") or analysis.get("sl"),
        analysis.get("take_profit") or analysis.get("tp"),
        analysis.get("rr_ratio") or analysis.get("rr"),
        analysis.get("h4_bias") or analysis.get("smc_bias"),
        analysis.get("setup_type"),
    ))
    conn.commit()
    conn.close()


def get_performance_summary(days: int = 30) -> str:
    """
    Compressed performance summary สำหรับ inject ใน Supervisor prompt
    ~100-150 tokens — บอทเรียนรู้ pattern ที่ผ่านมา
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    since = f"-{days} days"

    # ── scan stats ─────────────────────────────────────────────────
    total_scans = conn.execute(
        "SELECT COUNT(*) FROM scan_log WHERE timestamp >= DATE('now', ?)", (since,)
    ).fetchone()[0]

    approved_scans = conn.execute(
        "SELECT COUNT(*) FROM scan_log WHERE approved=1 AND timestamp >= DATE('now', ?)", (since,)
    ).fetchone()[0]

    # ── trade stats ────────────────────────────────────────────────
    trade_rows = conn.execute("""
        SELECT outcome, pnl_pips, session, confidence, signal
        FROM trades
        WHERE action='confirmed' AND timestamp >= DATE('now', ?)
    """, (since,)).fetchall()

    wins = sum(1 for r in trade_rows if r[0] == "win")
    losses = sum(1 for r in trade_rows if r[0] == "loss")
    total_pips = sum((r[1] or 0) for r in trade_rows if r[0] in ("win", "loss"))
    gross_win  = sum((r[1] or 0) for r in trade_rows if r[0] == "win")
    gross_loss = abs(sum((r[1] or 0) for r in trade_rows if r[0] == "loss"))
    wr = round(wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    pf = round(gross_win / gross_loss, 1) if gross_loss > 0 else None

    # ── best session ───────────────────────────────────────────────
    session_stats = {}
    for outcome, _, session, _, _ in trade_rows:
        if not session: continue
        s = session_stats.setdefault(session, {"w": 0, "l": 0})
        if outcome == "win":   s["w"] += 1
        if outcome == "loss":  s["l"] += 1
    best_session = max(
        ((k, v["w"] / (v["w"] + v["l"])) for k, v in session_stats.items() if v["w"] + v["l"] > 0),
        key=lambda x: x[1], default=("—", 0)
    )

    # ── confidence split ───────────────────────────────────────────
    hi_w = sum(1 for r in trade_rows if r[0] == "win"  and (r[3] or 0) >= 70)
    hi_l = sum(1 for r in trade_rows if r[0] == "loss" and (r[3] or 0) >= 70)
    lo_w = sum(1 for r in trade_rows if r[0] == "win"  and (r[3] or 0) < 70)
    lo_l = sum(1 for r in trade_rows if r[0] == "loss" and (r[3] or 0) < 70)
    hi_wr = round(hi_w / (hi_w + hi_l) * 100) if (hi_w + hi_l) > 0 else None
    lo_wr = round(lo_w / (lo_w + lo_l) * 100) if (lo_w + lo_l) > 0 else None

    # ── best/worst setup ───────────────────────────────────────────
    best_row = conn.execute("""
        SELECT signal, session, setup_type, COUNT(*) c
        FROM trades WHERE outcome='win' AND timestamp >= DATE('now', ?)
        GROUP BY signal, session, setup_type ORDER BY c DESC LIMIT 1
    """, (since,)).fetchone()

    worst_row = conn.execute("""
        SELECT signal, session, setup_type, COUNT(*) c
        FROM trades WHERE outcome='loss' AND timestamp >= DATE('now', ?)
        GROUP BY signal, session, setup_type ORDER BY c DESC LIMIT 1
    """, (since,)).fetchone()

    conn.close()

    if total_scans == 0:
        return f"[Performance {days}d: ยังไม่มีข้อมูล]"

    lines = [
        f"[Performance {days}d: {total_scans} scans → {approved_scans} approved → {wins+losses} trades]",
        f"[W{wins}/L{losses} WR{wr}% PF{pf or 'N/A'} P&L{total_pips:+.0f}p]",
    ]
    if hi_wr is not None or lo_wr is not None:
        lines.append(f"[Conf≥70%→WR{hi_wr}% | Conf<70%→WR{lo_wr}%]")
    if best_session[1] > 0:
        lines.append(f"[BestSession:{best_session[0]} WR{round(best_session[1]*100)}%]")
    if best_row:
        lines.append(f"[BestSetup:{best_row[1]} {best_row[0]} {best_row[2] or ''}]")
    if worst_row:
        lines.append(f"[WorstSetup:{worst_row[1]} {worst_row[0]} {worst_row[2] or ''}]")

    return " ".join(lines)


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


def get_recent_loss_context(limit: int = 5) -> str:
    """
    ดึง loss ล่าสุด + เหตุผล (จาก notes field) สำหรับ inject เข้า supervisor prompt
    เป็น feedback loop: supervisor รู้ว่า loss ครั้งก่อนเกิดเพราะอะไร
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT id, timestamp, signal, setup_type, session, confidence,
               actual_entry, actual_exit, pnl_pips, notes, reasoning
        FROM trades
        WHERE action='confirmed' AND outcome='loss'
        ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    if not rows:
        return ""

    lines = [f"📉 *Loss ล่าสุด {len(rows)} ครั้ง — Supervisor ต้องอ่านก่อนตัดสินใจ:*"]
    for r in rows:
        tid, ts, sig, setup, sess, conf, entry, exit_, pips, notes, reasoning = r
        pips_str = f"{pips:+.1f}p" if pips else "?"
        note_str = f" | Why: {notes}" if notes else ""
        lines.append(
            f"  #{tid} [{ts[5:16]}] {sig} {setup or ''} conf={conf}% sess={sess or '?'}"
            f" → {pips_str}{note_str}"
        )

    return "\n".join(lines)


def save_loss_digest(digest: str):
    """บันทึก loss lesson digest ลง meta cache."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO meta (key, value, updated_at) VALUES ('loss_digest', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    """, (digest, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def get_loss_lesson_digest() -> str:
    """
    ดึง digest ที่ Sonnet สรุปไว้แล้ว (cache) สำหรับ inject เข้า supervisor prompt
    ถ้ายังไม่มี cache → ใช้ get_recent_loss_context() แบบ raw แทนชั่วคราว
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT value FROM meta WHERE key='loss_digest'"
    ).fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return get_recent_loss_context(limit=3)


def get_raw_losses_for_digest(limit: int = 10) -> list[dict]:
    """ดึง loss ล่าสุดสำหรับ generate digest ใหม่."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT id, timestamp, signal, setup_type, session, confidence,
               actual_entry, actual_exit, pnl_pips, notes
        FROM trades
        WHERE action='confirmed' AND outcome='loss'
        ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [
        {
            "id": r[0], "timestamp": r[1], "signal": r[2],
            "setup_type": r[3], "session": r[4], "confidence": r[5],
            "entry": r[6], "exit": r[7], "pnl_pips": r[8], "notes": r[9],
        }
        for r in rows
    ]


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


def get_recent_scans(n: int = 9) -> list[dict]:
    """Return last N scan_log entries in dashboard scan_log format."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT timestamp, approved, signal, setup_type, vote_score, reject_reason
        FROM scan_log
        ORDER BY id DESC LIMIT ?
    """, (n,)).fetchall()
    conn.close()
    result = []
    for ts, approved, signal, setup_type, vote_score, reject_reason in reversed(rows):
        t = ts[11:16] if ts else "--:--"  # "HH:MM" from "YYYY-MM-DD HH:MM:SS"
        if approved:
            result.append({
                "time": t,
                "result": "ok",
                "text": f"{signal or 'BUY'} {setup_type or 'OB'}",
                "sub": f"APPROVED · {vote_score or 0}/3",
            })
        else:
            result.append({
                "time": t,
                "result": "no",
                "text": "REJECTED",
                "sub": reject_reason or "No setup",
            })
    return result


def get_dashboard_stats() -> dict:
    """Real stats for dashboard — today P&L + all-time win rate."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    today = datetime.now().strftime("%Y-%m-%d")

    today_trades = conn.execute("""
        SELECT outcome, pnl_pips FROM trades
        WHERE action='confirmed' AND DATE(timestamp)=?
    """, (today,)).fetchall()

    today_pnl = sum(r[1] or 0 for r in today_trades)
    today_wins = sum(1 for r in today_trades if r[0] == "win")
    today_losses = sum(1 for r in today_trades if r[0] == "loss")
    open_trades = sum(1 for r in today_trades if r[0] == "pending")

    summary = get_summary()

    recent_trades = conn.execute("""
        SELECT pnl_pips, outcome FROM trades
        WHERE action='confirmed' ORDER BY id DESC LIMIT 10
    """).fetchall()
    conn.close()

    trades_bar = []
    for pnl, outcome in reversed(recent_trades):
        r = "o" if outcome == "pending" else ("w" if outcome == "win" else "l")
        trades_bar.append({"pnl": round(pnl or 0, 1), "r": r})

    today_win_pips = [r[1] or 0 for r in today_trades if r[0] == "win"]
    return {
        "today_pnl": round(today_pnl, 2),
        "open_pnl": 0.0,
        "win_rate": round(today_wins / (today_wins + today_losses) * 100, 1) if (today_wins + today_losses) > 0 else summary["win_rate"],
        "wins": today_wins,
        "losses": today_losses,
        "best_trade": round(max(today_win_pips, default=0), 2),
        "trades": trades_bar,
    }


def get_latest_dashboard_signal() -> dict | None:
    """Return latest scan result as dashboard signal dict (for server startup)."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("""
        SELECT timestamp, signal, setup_type, confidence, vote_score,
               approved, reject_reason, entry, sl, tp, rr,
               chart_vote, bias_vote, news_vote
        FROM scan_log ORDER BY id DESC LIMIT 1
    """).fetchone()
    conn.close()
    if not row is None:
        ts, signal, setup_type, confidence, vote_score, approved, reject_reason, \
            entry, sl, tp, rr, chart_v, bias_v, news_v = row
        confidence = confidence or 0
        stars = "★★★" if confidence >= 80 else "★★" if confidence >= 60 else "★"
        direction = signal if signal in ("BUY", "SELL") else "BUY"
        t = ts[11:16] if ts else "--:--"
        return {
            "direction": direction,
            "setup_type": setup_type or "—",
            "stars": stars if approved else "",
            "entry": entry or 0.0,
            "sl": sl or 0.0,
            "tp": tp or 0.0,
            "lot": 0.01,
            "rr": rr or 0.0,
            "approved": bool(approved),
            "time": t,
            "pnl": 0.0,
            "votes": {
                "chart": bool(chart_v),
                "bias": bool(bias_v),
                "news": bool(news_v),
                "risk": True,
            },
            "reason": reject_reason or "",
        }
    return None


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


def get_today_summary() -> dict:
    """สรุปผลเทรดวันนี้ — ใช้ใน daily close alert"""
    init_db()
    today = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT outcome, pnl_pips, signal, stars, score, timestamp
        FROM trades
        WHERE action='confirmed' AND DATE(timestamp) = ?
        ORDER BY id
    """, (today,)).fetchall()
    conn.close()

    trades, wins, losses, total_pips = [], 0, 0, 0.0
    for outcome, pnl, signal, stars, score, ts in rows:
        trades.append({"outcome": outcome, "pnl_pips": pnl, "signal": signal,
                       "stars": stars, "score": score, "time": ts[11:16]})
        if outcome == "win":
            wins += 1
            total_pips += pnl or 0
        elif outcome == "loss":
            losses += 1
            total_pips += pnl or 0

    wr = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0
    return {
        "date":        today,
        "trades":      trades,
        "total":       len(trades),
        "wins":        wins,
        "losses":      losses,
        "pending":     sum(1 for t in trades if t["outcome"] == "pending"),
        "total_pips":  round(total_pips, 1),
        "win_rate":    wr,
    }


def format_today_summary() -> str:
    """แปลง today summary เป็นข้อความ Telegram"""
    s = get_today_summary()
    if s["total"] == 0:
        return f"📅 *สรุปวันนี้ {s['date']}*\n━━━━━━━━━━━━━━━━━\nไม่มี trade วันนี้"

    total_closed = s["wins"] + s["losses"]
    bar = "🟢" if s["total_pips"] >= 0 else "🔴"
    wr_str = f"{s['win_rate']}%" if total_closed > 0 else "-"

    lines = [
        f"📅 *สรุปวันนี้ {s['date']}*",
        "━━━━━━━━━━━━━━━━━",
        f"📊 เทรดทั้งหมด: `{s['total']}` ไม้  (ปิดแล้ว `{total_closed}` | รอ `{s['pending']}`)",
        f"{bar} W`{s['wins']}` / L`{s['losses']}` | WR: `{wr_str}`",
        f"💰 P&L รวม: `{fmt_pts(s['total_pips'], sign=True)} จุด`",
        "",
        "*รายการเทรดวันนี้:*",
    ]
    for t in s["trades"]:
        if t["outcome"] == "win":
            icon = "✅"
            pips = f"+{fmt_pts(t['pnl_pips'])} จุด"
        elif t["outcome"] == "loss":
            icon = "❌"
            pips = f"{fmt_pts(t['pnl_pips'], sign=True)} จุด"
        elif t["outcome"] == "be":
            icon = "➖"
            pips = "0 จุด (BE)"
        else:
            icon = "⏳"
            pips = "รอปิด"
        lines.append(f"  {icon} `{t['time']}` {t['signal']} {t['stars'] or ''} — `{pips}`")

    if s["pending"] > 0:
        lines.append(f"\n_⏳ {s['pending']} ไม้ยังไม่ปิด — `/closetrade [ราคา]` เพื่อบันทึกผล_")

    return "\n".join(lines)


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
        f"💰 Total P&L: `{fmt_pts(s['total_pips'], sign=True)} จุด`  |  Avg: `{fmt_pts(s['avg_pips'], sign=True)} จุด`",
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
                f"WR:{wr}  `{fmt_pts(d['pips'], sign=True)} จุด`"
            )

    lines += ["", "📋 *5 Confirmed Trade ล่าสุด*", "━━━━━━━━━━━━━━━━━"]

    if not recent:
        lines.append("ยังไม่มี trade")
    else:
        for t in recent:
            icon   = "✅" if t["outcome"] == "win" else "❌" if t["outcome"] == "loss" else "⏳" if t["outcome"] == "pending" else "↔️"
            stars  = t.get("stars") or ""
            pips_t = f"{fmt_pts(t['pnl_pips'], sign=True)} จุด" if t["pnl_pips"] is not None else "pending"
            lines.append(
                f"{icon} #{t['id']} `{t['signal']}` {stars}  "
                f"score={t.get('score') or '-'}  "
                f"{t['timestamp'][:16]}  `{pips_t}`"
            )

    lines += ["", "📤 Export CSV: `/export`  |  อัพเดทผล: `/outcome [id] [win/loss/be] [จุด] [exit]`"]
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
        pips_t = f"`{fmt_pts(t['pnl_pips'], sign=True)} จุด`" if t["pnl_pips"] is not None else "`pending`"
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
