"""
bar_cache.py — เก็บแท่ง M5 ที่ scan สดเจอ (ข้อมูลจาก live tick, สะอาด ไม่มี gap)
สะสมไว้ใน local SQLite ทีละ scan — นานไปจะได้ history ของตัวเองที่ไม่พึ่ง
MT5 copy_rates_* ย้อนหลัง (ซึ่งพบว่ามี gap ประจำ 06:50-08:00 ทุกวันสำหรับ XAUUSD)

ใช้ INSERT OR IGNORE กัน error ตอนแท่งซ้ำ (ทุก scan ดึงย้อนหลัง 7 วันมาเสมอ
แต่มีแค่แท่งใหม่ที่ไม่เคยเห็นเท่านั้นที่จะถูกเพิ่มจริง)
"""
import sqlite3
from pathlib import Path

import pandas as pd

_DB_PATH = Path(__file__).parent.parent / "data" / "bar_cache.db"


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS m5_bars (
            time   TEXT PRIMARY KEY,
            open   REAL NOT NULL,
            high   REAL NOT NULL,
            low    REAL NOT NULL,
            close  REAL NOT NULL,
            volume INTEGER
        )
    """)
    conn.commit()
    return conn


def save_bars(df: pd.DataFrame) -> int:
    """
    บันทึกแท่ง M5 ที่เพิ่ง scan สดได้ลง cache — best-effort, ไม่ block scan หลัก
    df ต้องมี datetime index + columns open/high/low/close/volume
    คืนจำนวนแท่งใหม่ที่บันทึกเพิ่ม (0 ถ้าไม่มีอะไรใหม่หรือ error)
    """
    if df is None or df.empty:
        return 0
    try:
        conn = _get_conn()
        rows = [
            (
                t.strftime("%Y-%m-%d %H:%M:%S"),
                float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                int(r["volume"]) if pd.notna(r.get("volume")) else None,
            )
            for t, r in df.iterrows()
        ]
        cur = conn.executemany(
            "INSERT OR IGNORE INTO m5_bars (time, open, high, low, close, volume) VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        added = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.close()
        return added
    except Exception:
        return 0


def load_range(start: str = None, end: str = None) -> pd.DataFrame:
    """โหลดแท่งที่สะสมไว้ (ทั้งหมด หรือเฉพาะช่วง start/end แบบ 'YYYY-MM-DD HH:MM:SS')"""
    conn = sqlite3.connect(_DB_PATH)
    q = "SELECT * FROM m5_bars"
    params = []
    if start and end:
        q += " WHERE time >= ? AND time <= ?"
        params = [start, end]
    q += " ORDER BY time"
    df = pd.read_sql(q, conn, params=params)
    conn.close()
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"])
    return df


def export_csv(out_path: str) -> int:
    """export cache ทั้งหมดเป็น CSV รูปแบบเดียวกับ mt5_export_*.csv — คืนจำนวนแท่งที่ export"""
    df = load_range()
    if df.empty:
        return 0
    df = df.rename(columns={"volume": "tick_volume"})
    df.to_csv(out_path, index=False)
    return len(df)


def merge_with_cache(df_mt5: pd.DataFrame) -> pd.DataFrame:
    """
    เอา df ที่เพิ่งดึงจาก MT5 (copy_rates_from_pos — อาจมี gap ย้อนหลังหลายวัน)
    มา merge กับ local cache (จาก live scan สะสม — สะอาดกว่า) เติมแท่งที่ MT5
    รอบนี้ดึงมาไม่ครบ ให้ analysis (has_signal, sweep detection ฯลฯ) เห็นครบจริง
    ไม่ทับข้อมูลจาก MT5 rond นี้ถ้ามีอยู่แล้ว (MT5 สดกว่า cache เสมอ)
    """
    if df_mt5 is None or df_mt5.empty:
        return df_mt5
    try:
        import datetime as _dt
        start = df_mt5.index.min().strftime("%Y-%m-%d %H:%M:%S")
        # ใช้เวลาปัจจุบันจริงเป็นขอบบน ไม่ใช่ df_mt5.index.max() — ถ้า gap
        # ดันไปอยู่ที่ขอบท้ายสุดพอดี (แท่งล่าสุดหายไป) max() จะขยับลดลงตามไปด้วย
        # ทำให้ query cache แคบเกินและพลาดแท่งที่ควรเติมกลับมา
        end = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cached = load_range(start, end)
        if cached.empty:
            return df_mt5
        cached = cached.set_index("time").rename(columns={"volume": "volume"})[
            ["open", "high", "low", "close", "volume"]
        ]
        # MT5 รอบนี้เป็นตัวหลัก — cache เติมเฉพาะ timestamp ที่ MT5 ไม่มี
        missing_idx = cached.index.difference(df_mt5.index)
        if len(missing_idx) == 0:
            return df_mt5
        merged = pd.concat([df_mt5, cached.loc[missing_idx]]).sort_index()
        return merged
    except Exception:
        return df_mt5
