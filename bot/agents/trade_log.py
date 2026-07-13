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
from datetime import datetime, date, timedelta, timezone

_TH = timezone(timedelta(hours=7))  # Thailand UTC+7


def _ts_to_th(ts) -> str | None:
    """Unix timestamp (UTC) → TH time string (always UTC+7, regardless of OS timezone)"""
    if not ts or not isinstance(ts, (int, float)):
        return None
    return datetime.fromtimestamp(ts, tz=_TH).strftime("%Y-%m-%d %H:%M:%S")
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
        CREATE TABLE IF NOT EXISTS mt5_transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT NOT NULL,
            ticket       INTEGER,
            trade_id     INTEGER,
            direction    TEXT,
            symbol       TEXT DEFAULT 'XAUUSD',
            lot          REAL,
            open_price   REAL,
            close_price  REAL,
            sl           REAL,
            tp           REAL,
            pnl_pips     REAL,
            profit_usd   REAL,
            commission   REAL,
            swap         REAL,
            net_usd      REAL,
            open_time    TEXT,
            close_time   TEXT,
            comment      TEXT
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
        ("actual_entry",   "REAL"),
        ("actual_exit",    "REAL"),
        ("duration_min",   "INTEGER"),
        ("mt5_ticket",     "INTEGER"),
        ("bias_condition", "TEXT"),   # เช่น "H4_BULL+H1_BEAR" — ใช้ weekly feedback
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


def log_trade(analysis: dict, action: str, mt5_ticket: int = None) -> int:
    """
    บันทึก trade หลัง user กด Confirm/Skip
    analysis มาจาก supervisor.run() → result["analysis"]
    Returns trade_id (ใช้กับ /outcome)
    """
    init_db()

    entry      = analysis.get("entry_zone") or []
    key_factors = analysis.get("key_factors", [])
    rr_val     = analysis.get("rr_ratio") or analysis.get("rr")

    # สร้าง bias_condition จาก stages.bias — ใช้ weekly feedback loop
    _bias_s = analysis.get("stages", {}).get("bias", {})
    _h4 = (_bias_s.get("h4_bias") or analysis.get("h4_bias") or "?").upper()[:4]
    _h1 = (_bias_s.get("h1_bias") or "?").upper()[:4]
    bias_condition = f"H4_{_h4}+H1_{_h1}"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        INSERT INTO trades (
            timestamp, pair, session, signal, setup_type, stars, score,
            confidence, h4_bias,
            entry_low, entry_high, stop_loss, take_profit,
            sl_pips, tp_pips, rr_plan, lot, risk_pct,
            key_factors, reasoning, action, mt5_ticket, bias_condition
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?
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
        mt5_ticket,
        bias_condition,
    ))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def check_zone_cooldown(signal: str, entry_price: float,
                         cooldown_hours: float = 4.0, price_tolerance: float = 10.0) -> dict | None:
    """
    กันเทรดซ้ำโซนราคาใกล้เคียงกันในเวลาไม่ห่างกัน — เช่น sweep-based signal เกิด
    ซ้ำที่ระดับใกล้ๆ level ที่เพิ่ง confirmed ไปหมาดๆ (สาเหตุ: บอทวิเคราะห์ใหม่ทุก
    scan ไม่มี memory ว่าเพิ่งเทรดโซนนี้ไป ถ้า swing point ใหม่เกิดใกล้ๆ ของเดิม
    จะถูกมองเป็นสัญญาณสดทันที)

    คืน trade เก่าที่ชนกัน (dict) ถ้ามีทิศทางเดียวกัน + entry ห่างกัน ≤price_tolerance
    ภายใน cooldown_hours ชม.ที่ผ่านมา — None ถ้าไม่ชนกับใคร
    """
    if entry_price is None:
        return None
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, timestamp, signal, entry_low, setup_type FROM trades
        WHERE action IN ('confirmed', 'mt5_import') AND signal = ?
          AND timestamp >= datetime('now', 'localtime', ?)
        ORDER BY timestamp DESC
    """, (signal, f'-{cooldown_hours} hours')).fetchall()
    conn.close()
    for r in rows:
        if r["entry_low"] is not None and abs(r["entry_low"] - entry_price) <= price_tolerance:
            return dict(r)
    return None


def get_weekly_trades() -> list[dict]:
    """ดึง trades ทั้งหมด 7 วันที่ผ่านมา สำหรับ weekly feedback loop"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT t.id, t.timestamp, t.signal, t.setup_type, t.session,
               t.bias_condition, t.h4_bias, t.confidence, t.rr_plan,
               t.outcome, t.pnl_pips, t.actual_entry, t.actual_exit,
               t.reasoning,
               tx.net_usd
        FROM trades t
        LEFT JOIN mt5_transactions tx ON tx.trade_id = t.id
        WHERE t.action IN ('confirmed', 'mt5_import')
          AND t.timestamp >= datetime('now', 'localtime', '-7 days')
        ORDER BY t.timestamp
    """).fetchall()
    conn.close()
    cols = ['id','timestamp','signal','setup_type','session','bias_condition',
            'h4_bias','confidence','rr_plan','outcome','pnl_pips',
            'actual_entry','actual_exit','reasoning','net_usd']
    return [dict(zip(cols, r)) for r in rows]


def transaction_exists(ticket: int) -> bool:
    """เช็คว่า ticket นี้ถูกบันทึกใน mt5_transactions แล้วหรือยัง (กันซ้ำ)"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT 1 FROM mt5_transactions WHERE ticket=? LIMIT 1", (ticket,)
    ).fetchone()
    conn.close()
    return row is not None


def find_trade_by_ticket(ticket: int) -> dict | None:
    """หา trade row จาก mt5_ticket — คืน dict หรือ None"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM trades WHERE mt5_ticket=? ORDER BY id DESC LIMIT 1", (ticket,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def log_mt5_transaction(trade_id: int, direction: str, deal: dict, open_trade: dict):
    """
    บันทึก MT5 transaction จริงหลัง position ปิด
    deal มาจาก get_last_deal_for_ticket() หรือ get_latest_closed_deal()
    """
    init_db()
    close_px  = deal.get("close_price")
    open_px   = open_trade.get("entry", 0)
    lot       = open_trade.get("lot")
    ticket    = deal.get("ticket") or open_trade.get("mt5_ticket")
    profit    = deal.get("profit", 0) or 0
    comm      = deal.get("commission", 0) or 0
    swap      = deal.get("swap", 0) or 0
    net       = round(profit + comm + swap, 2)
    pnl_raw   = (close_px - open_px) if direction == "BUY" else (open_px - close_px)
    pnl_pips  = round(pnl_raw * 10, 1) if (close_px and open_px) else None

    _deal_time = deal.get("time") or deal.get("close_time")
    close_time = _ts_to_th(_deal_time) or _deal_time

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO mt5_transactions (
            timestamp, ticket, trade_id, direction, symbol, lot,
            open_price, close_price, sl, tp,
            pnl_pips, profit_usd, commission, swap, net_usd,
            open_time, close_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ticket,
        trade_id,
        direction,
        open_trade.get("pair", "XAUUSD"),
        lot,
        open_px,
        close_px,
        open_trade.get("original_sl"),
        open_trade.get("tp"),
        pnl_pips,
        round(profit, 2),
        round(comm, 2),
        round(swap, 2),
        net,
        open_trade.get("opened_at"),
        close_time,
    ))
    conn.commit()
    conn.close()
    return net


def get_mt5_summary(days: int = 30) -> dict:
    """สรุป P&L จริงจาก MT5 transactions"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    since = f"-{days} days"
    rows = conn.execute("""
        SELECT profit_usd, commission, swap, net_usd, pnl_pips, direction
        FROM mt5_transactions
        WHERE timestamp >= DATE('now', ?)
    """, (since,)).fetchall()
    conn.close()

    if not rows:
        return {"total_net_usd": 0, "total_pips": 0, "total_commission": 0,
                "total_swap": 0, "count": 0}
    return {
        "count":            len(rows),
        "total_gross_usd":  round(sum(r[0] or 0 for r in rows), 2),
        "total_commission": round(sum(r[1] or 0 for r in rows), 2),
        "total_swap":       round(sum(r[2] or 0 for r in rows), 2),
        "total_net_usd":    round(sum(r[3] or 0 for r in rows), 2),
        "total_pips":       round(sum(r[4] or 0 for r in rows), 1),
    }


def get_uncaptured_tickets() -> list[int]:
    """ticket ที่บอทเปิด (มี mt5_ticket) แต่ยังไม่มีใน mt5_transactions
    ใช้ query MT5 ตรงด้วย position filter (กรณี date-range query ไม่ครบ)
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT DISTINCT t.mt5_ticket
        FROM trades t
        WHERE t.mt5_ticket IS NOT NULL
          AND t.mt5_ticket NOT IN (SELECT ticket FROM mt5_transactions WHERE ticket IS NOT NULL)
    """).fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_trades_pending_outcome() -> list[dict]:
    """trades ที่มี mt5_transaction แล้ว (ปิดไปแล้ว) แต่ outcome ยัง None/pending ใน trades table
    ใช้ใน sync Pass 3 — fix outcome จาก transaction data ที่มีอยู่แล้ว
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT t.id, t.signal, t.actual_entry, t.entry_low, t.mt5_ticket,
               tx.close_price, tx.open_price, tx.direction, tx.pnl_pips
        FROM trades t
        JOIN mt5_transactions tx ON tx.trade_id = t.id
        WHERE (t.outcome IS NULL OR t.outcome = 'pending')
          AND tx.close_price IS NOT NULL
        ORDER BY t.id DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_transactions(limit: int = 15) -> list[dict]:
    """ดึง mt5_transactions ล่าสุด — ใช้กับคำสั่ง /tx"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT ticket, trade_id, direction, pnl_pips, net_usd,
               open_price, close_price, close_time
        FROM mt5_transactions
        ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_transactions_by_period(period: str = 'today') -> dict:
    """
    ดึง mt5_transactions แยกตามช่วงเวลา — ใช้กับ /txreport
    period: 'today' | 'week' | 'month' | 'year'
    timestamps เป็น TH time (local time บนเครื่อง bot)
    """
    init_db()
    now = datetime.now()

    if period in ('today', 'd', 'day'):
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = f"วันนี้ {now.strftime('%d/%m/%Y')}"
        period_key = 'today'
    elif period in ('week', 'w'):
        since = now - timedelta(days=7)
        label = f"7 วันย้อนหลัง ({since.strftime('%d/%m')}–{now.strftime('%d/%m/%Y')})"
        period_key = 'week'
    elif period in ('month', 'm'):
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"เดือนนี้ {now.strftime('%B %Y')}"
        period_key = 'month'
    elif period in ('year', 'y'):
        since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"ปีนี้ {now.strftime('%Y')}"
        period_key = 'year'
    else:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = f"วันนี้ {now.strftime('%d/%m/%Y')}"
        period_key = 'today'

    since_str = since.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, ticket, trade_id, direction, symbol, lot,
               open_price, close_price, sl, tp,
               pnl_pips, profit_usd, commission, swap, net_usd,
               open_time, close_time, timestamp
        FROM mt5_transactions
        WHERE CASE
            WHEN typeof(COALESCE(close_time, timestamp)) = 'integer'
            THEN datetime(COALESCE(close_time, timestamp), 'unixepoch', '+7 hours')
            ELSE COALESCE(close_time, timestamp)
        END >= ?
        ORDER BY COALESCE(close_time, timestamp) ASC
    """, (since_str,)).fetchall()
    conn.close()

    txs = [dict(r) for r in rows]

    wins   = [t for t in txs if (t.get('pnl_pips') or 0) > 0]
    losses = [t for t in txs if (t.get('pnl_pips') or 0) < 0]
    be_    = [t for t in txs if (t.get('pnl_pips') or 0) == 0]

    total_gross = round(sum(t.get('profit_usd') or 0 for t in txs), 2)
    total_comm  = round(sum(t.get('commission') or 0 for t in txs), 2)
    total_swap  = round(sum(t.get('swap') or 0 for t in txs), 2)
    total_net   = round(sum(t.get('net_usd') or 0 for t in txs), 2)
    total_pips  = round(sum(t.get('pnl_pips') or 0 for t in txs), 1)

    wr = round(len(wins) / (len(wins) + len(losses)) * 100) if (wins or losses) else 0

    return {
        'label':            label,
        'period':           period_key,
        'since':            since_str,
        'txs':              txs,
        'count':            len(txs),
        'wins':             len(wins),
        'losses':           len(losses),
        'be':               len(be_),
        'win_rate':         wr,
        'total_gross_usd':  total_gross,
        'total_commission': total_comm,
        'total_swap':       total_swap,
        'total_net_usd':    total_net,
        'total_pips':       total_pips,
    }


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


def update_mt5_ticket(trade_id: int, ticket: int):
    """บันทึก MT5 ticket ให้กับ trade ใน DB — เรียกหลัง MT5 open_trade() สำเร็จ"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE trades SET mt5_ticket=? WHERE id=?", (ticket, trade_id))
    conn.commit()
    conn.close()


def get_pending_trades() -> list[dict]:
    """ดึง confirmed trades ที่ยัง pending outcome (สำหรับ /pending command)"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT id, timestamp, signal, setup_type, entry_low, entry_high, stop_loss, mt5_ticket
        FROM trades
        WHERE outcome='pending' AND action='confirmed'
        ORDER BY id DESC LIMIT 20
    """).fetchall()
    conn.close()
    result = []
    for row in rows:
        result.append({
            "id": row[0], "timestamp": row[1], "signal": row[2],
            "setup_type": row[3], "entry_low": row[4], "entry_high": row[5],
            "stop_loss": row[6], "mt5_ticket": row[7],
        })
    return result


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


def _pips_to_usd(pips, lot):
    """XAUUSD: 1 pip = $0.10/lot → pnl_usd = pips * lot * 10"""
    return (pips or 0) * (lot or 0.01) * 10


_PERIOD_FILTER = {
    "today": "timestamp >= strftime('%Y-%m-%d 00:00:00', 'now', 'localtime')",
    "week":  "timestamp >= datetime('now', 'localtime', '-6 days')",
    "month": "timestamp >= datetime('now', 'localtime', 'start of month')",
    "year":  "timestamp >= datetime('now', 'localtime', 'start of year')",
    "all":   "1=1",
}
# mt5_transactions ใช้ close_time (อาจเป็น unix int หรือ string)
def _mt5_col():
    """close_time เก็บเป็น TH string แล้ว — ถ้าเป็น int ให้แปลง unix→TH (+7h)"""
    return "CASE WHEN typeof(close_time)='integer' THEN datetime(close_time,'unixepoch','+7 hours') ELSE close_time END"

_MT5_PERIOD_FILTER = {
    # ใช้ >= midnight (ไม่ใช่ = today) เพราะ trade ที่ปิดหลังเที่ยงคืน จะมี DATE = "พรุ่งนี้"
    # ทำให้ตรงกับ get_transactions_by_period ที่ใช้ close_time >= today 00:00:00
    "today": f"{_mt5_col()} >= strftime('%Y-%m-%d 00:00:00', 'now', 'localtime')",
    "week":  f"{_mt5_col()} >= datetime('now', 'localtime', '-6 days')",
    "month": f"{_mt5_col()} >= datetime('now', 'localtime', 'start of month')",
    "year":  f"{_mt5_col()} >= datetime('now', 'localtime', 'start of year')",
    "all":   "1=1",
}


def get_dashboard_stats(period: str = "today") -> dict:
    """Real stats for dashboard. period: today|week|month|year|all"""
    init_db()
    conn = sqlite3.connect(DB_PATH)

    date_filter = _PERIOD_FILTER.get(period, _PERIOD_FILTER["today"])
    mt5_date_filter = _MT5_PERIOD_FILTER.get(period, _MT5_PERIOD_FILTER["today"])

    # ── ดึงจาก mt5_transactions (ข้อมูล MT5 จริง) ────────────────
    # ใช้ COALESCE(close_time, timestamp) เพื่อรวม record ที่ close_time=NULL
    # (close_time=NULL เกิดจาก bug เก่าที่ get_position_deals ส่ง key "close_time" แต่ log_mt5_transaction อ่าน "time")
    _ctf_col = "COALESCE(close_time, timestamp)"
    _ctf_map = {
        "today": f"{_ctf_col} >= strftime('%Y-%m-%d 00:00:00', 'now', 'localtime')",
        "week":  f"{_ctf_col} >= datetime('now', 'localtime', '-6 days')",
        "month": f"{_ctf_col} >= datetime('now', 'localtime', 'start of month')",
        "year":  f"{_ctf_col} >= datetime('now', 'localtime', 'start of year')",
        "all":   "1=1",
    }
    _ctf_filter = _ctf_map.get(period, _ctf_map["today"])
    mt5_rows = conn.execute(f"""
        SELECT net_usd, pnl_pips FROM mt5_transactions
        WHERE {_ctf_filter}
    """).fetchall()

    # ── fallback: trades table (outcome บันทึกด้วยมือ) ───────────
    base_filter = f"action IN ('confirmed','mt5_import') AND {date_filter}"
    manual_trades = conn.execute(f"""
        SELECT outcome, pnl_pips, COALESCE(lot, 0.01) FROM trades
        WHERE {base_filter} AND outcome IN ('win','loss','be')
    """).fetchall()

    recent_scans = conn.execute("""
        SELECT approved, confidence FROM scan_log
        ORDER BY id DESC LIMIT 20
    """).fetchall()

    scan_counts = conn.execute(f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN approved=1 THEN 1 ELSE 0 END) AS approved,
            SUM(CASE WHEN approved=0 THEN 1 ELSE 0 END) AS rejected
        FROM scan_log WHERE {date_filter}
    """).fetchone()

    best_setup_row = conn.execute(f"""
        SELECT setup_type FROM scan_log
        WHERE approved=1 AND {date_filter}
        ORDER BY confidence DESC LIMIT 1
    """).fetchone()

    pending_count = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE outcome='pending' AND action='confirmed'"
    ).fetchone()[0]

    conn.close()

    # ── คำนวณ stats — ใช้ mt5_transactions ถ้ามีข้อมูล ──────────
    if mt5_rows:
        # ใช้ net_usd เป็นหลัก ถ้า NULL fallback เป็น pnl_pips (r[1])
        def _net(r): return r[0] if r[0] is not None else (r[1] * 0.1 if r[1] is not None else 0)
        period_pnl    = round(sum(_net(r) for r in mt5_rows), 2)
        period_wins   = sum(1 for r in mt5_rows if _net(r) > 0)
        period_losses = sum(1 for r in mt5_rows if _net(r) < 0)
        best_pnl      = round(max(_net(r) for r in mt5_rows if _net(r) > 0), 2) if period_wins else 0.0
    else:
        # fallback ถ้า mt5_transactions ไม่มีข้อมูลช่วงนี้
        period_pnl    = sum(_pips_to_usd(r[1], r[2]) for r in manual_trades)
        period_wins   = sum(1 for r in manual_trades if r[0] == "win")
        period_losses = sum(1 for r in manual_trades if r[0] == "loss")
        best_pnl      = 0.0

    win_rate = (
        round(period_wins / (period_wins + period_losses) * 100, 1)
        if (period_wins + period_losses) > 0 else 0
    )

    trades_bar = []
    for approved, confidence in reversed(recent_scans):
        trades_bar.append({
            "pnl": round((confidence or 50) / 10, 1),
            "r": "w" if approved else "l",
        })

    scans_approved = int(scan_counts[1] or 0) if scan_counts else 0
    scans_rejected = int(scan_counts[2] or 0) if scan_counts else 0
    scans_total    = scans_approved + scans_rejected
    approval_rate  = round(scans_approved / scans_total * 100, 1) if scans_total > 0 else 0

    return {
        "period":         period,
        "today_pnl":      round(period_pnl, 2),
        "open_pnl":       0.0,
        "win_rate":       win_rate,
        "wins":           period_wins,
        "losses":         period_losses,
        "pending":        pending_count,
        "best_trade":     best_pnl,
        "best_setup":     best_setup_row[0] if best_setup_row else "",
        "trades":         trades_bar,
        "scans_approved": scans_approved,
        "scans_rejected": scans_rejected,
        "approval_rate":  approval_rate,
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
