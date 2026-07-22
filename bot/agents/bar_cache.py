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
        last_t = df.index.max()
        last_row = df.loc[last_t]
        print(
            f"[bar_cache] 💾 saved {added} new bar(s) — latest: {last_t} "
            f"O={last_row['open']} H={last_row['high']} L={last_row['low']} C={last_row['close']}"
        )
        return added
    except Exception as e:
        print(f"[bar_cache] ⚠️ save_bars failed: {e}")
        return 0


def load_range(start: str = None, end: str = None) -> pd.DataFrame:
    """โหลดแท่งที่สะสมไว้ (ทั้งหมด หรือเฉพาะช่วง start/end แบบ 'YYYY-MM-DD HH:MM:SS')"""
    conn = _get_conn()  # ใช้ _get_conn() แทน sqlite3.connect() ตรงๆ — กัน error
    # "no such table" ถ้า save_bars() ยังไม่เคยถูกเรียกเลย (table ยังไม่ถูกสร้าง)
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


def export_day_csv(date_str: str, out_path: str) -> int:
    """export เฉพาะแท่งของวันที่ระบุเป็น CSV (time,open,high,low,close,tick_volume)
    ใช้กับ /barcheck ให้ user เอาไปเทียบกับกราฟจริงทีละแท่งได้ — คืนจำนวนแท่ง

    time = เวลาไทย (ตรงกับ MT5 terminal จริง — ยืนยันแล้วจาก MT5 Data Window)
    chart_time = time + 4 ชม. — ตรงกับที่ TradingView broker server แสดง
    (ยืนยันจากแท่งเดียวกันจริง: MT5=15:10 ↔ TradingView chart=19:10)
    เพิ่มไว้ให้เทียบกับ TradingView ได้เลยไม่ต้องคำนวณเอง
    """
    df = load_range(f"{date_str} 00:00:00", f"{date_str} 23:59:59")
    if df.empty:
        return 0
    df.insert(1, "chart_time", df["time"] + pd.Timedelta(hours=4))
    df = df.rename(columns={"volume": "tick_volume"})
    df.to_csv(out_path, index=False)
    return len(df)


def export_csv(out_path: str) -> int:
    """export cache ทั้งหมดเป็น CSV รูปแบบเดียวกับ mt5_export_*.csv — คืนจำนวนแท่งที่ export"""
    df = load_range()
    if df.empty:
        return 0
    df = df.rename(columns={"volume": "tick_volume"})
    df.to_csv(out_path, index=False)
    return len(df)


def resample_m15(df_m5: pd.DataFrame) -> pd.DataFrame:
    """แปลง M5 bars เป็น M15 (aggregate) — ไม่ต้องเก็บ M15 แยก table"""
    if df_m5 is None or df_m5.empty:
        return df_m5
    d = df_m5.set_index("time") if "time" in df_m5.columns else df_m5
    m15 = d.resample("15min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna()
    return m15


def resample_m30(df_m5: pd.DataFrame) -> pd.DataFrame:
    """แปลง M5 bars เป็น M30 (aggregate) — เหมือน resample_m15 แต่ 30 นาที"""
    if df_m5 is None or df_m5.empty:
        return df_m5
    d = df_m5.set_index("time") if "time" in df_m5.columns else df_m5
    m30 = d.resample("30min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna()
    return m30


def today_summary(date_str: str, session_start: str = "06:00", session_end: str = None) -> dict:
    """
    สรุปข้อมูลของวันนี้ที่สะสมไว้ — ใช้ตอบคำสั่ง Telegram
    คืน dict: m5_count, m15_count, first_time, last_time, gaps (list), expected_start
    """
    import datetime as _dt
    end_bound = session_end or _dt.datetime.now().strftime("%H:%M")
    df = load_range(f"{date_str} 00:00:00", f"{date_str} 23:59:59")
    if df.empty:
        return {"m5_count": 0, "m15_count": 0, "gaps": [], "first_time": None, "last_time": None}

    gaps = []
    times = df["time"].sort_values().reset_index(drop=True)
    for i in range(1, len(times)):
        diff = times[i] - times[i - 1]
        if diff > pd.Timedelta(minutes=10):
            gaps.append((times[i - 1], times[i]))

    m15 = resample_m15(df)

    return {
        "m5_count":  len(df),
        "m15_count": len(m15),
        "gaps":      gaps,
        "first_time": times.iloc[0],
        "last_time":  times.iloc[-1],
        "expected_start": f"{date_str} {session_start}:00",
        "checked_at": f"{date_str} {end_bound}",
    }


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
        gap_list = sorted(missing_idx)
        print(
            f"[bar_cache] 🩹 merged {len(missing_idx)} bar(s) from cache to fill MT5 gap "
            f"({gap_list[0]} .. {gap_list[-1]})"
        )
        return merged
    except Exception as e:
        print(f"[bar_cache] ⚠️ merge_with_cache failed: {e}")
        return df_mt5


def get_extended_history(df_mt5: pd.DataFrame, max_days: int = 45) -> pd.DataFrame:
    """
    ขยาย lookback ให้ analysis เห็นย้อนหลังได้ไกลกว่าที่ MT5 ดึงมาในรอบนี้ (7 วัน)
    โดยใช้ cache ที่สะสมไว้ — สำหรับหา weekly/monthly BSL/SSL pool เก่าที่ยังไม่
    ถูก sweep (pool ที่ MT5 bulk fetch มองไม่เห็นเพราะจำกัดแค่ 2016 แท่ง)

    df_mt5 (7 วันล่าสุด, สดที่สุด) เป็นตัวหลักเสมอสำหรับ timestamp ที่มีอยู่จริง
    — แต่ MT5 copy_rates_* มี gap ประจำ 06:50-08:00 ทุกวันสำหรับ XAUUSD (ตามที่
    เขียนไว้บนสุดของไฟล์นี้) ซึ่งทำให้ pivot/swing detection (ที่พึ่ง positional
    window ต่อเนื่อง) พลาด swing high/low จริงที่เกิดใกล้ๆ ช่วง gap นั้นไปเงียบๆ
    เดิม cache ถูกใช้แค่ "ต่อขยายย้อนหลัง" เกิน 7 วันเท่านั้น ไม่เคยถูกใช้ "เติม
    ช่องว่าง" ภายในหน้าต่าง 7 วันล่าสุดเลย ทั้งที่ cache เก็บแท่งจาก live scan
    ไว้แบบไม่มี gap (ยกเว้นช่วงที่บอทดับจริงๆ) — เติมจาก cache ทุก timestamp ที่
    MT5 fetch รอบนี้ไม่มีให้ครบ ไม่ใช่แค่ก่อน df_mt5.index.min() อีกต่อไป
    """
    if df_mt5 is None or df_mt5.empty:
        return df_mt5
    try:
        cutoff_start = (df_mt5.index.min() - pd.Timedelta(days=max_days)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_end   = df_mt5.index.max().strftime("%Y-%m-%d %H:%M:%S")
        cached = load_range(cutoff_start, cutoff_end)
        if cached.empty:
            return df_mt5
        cached = cached.set_index("time")[["open", "high", "low", "close", "volume"]]
        # เติมเฉพาะ timestamp ที่ df_mt5 "ไม่มี" (gap จริง) — timestamp ที่ซ้ำกัน
        # ให้ df_mt5 (สดกว่า) ชนะเสมอ ไม่ใช่แค่กันซ้อนก่อน min() แบบเดิม
        missing = cached[~cached.index.isin(df_mt5.index)]
        if missing.empty:
            return df_mt5
        extended = pd.concat([missing, df_mt5]).sort_index()
        _gap_fill = missing[missing.index >= df_mt5.index.min()]
        print(
            f"[bar_cache] 📚 extended history: +{len(missing)} bar(s) จาก cache "
            f"({missing.index.min()} .. {missing.index.max()}) รวมเป็น {len(extended)} แท่ง"
            + (f" — รวม {len(_gap_fill)} bar(s) เติม gap ภายในหน้าต่าง MT5 เอง" if not _gap_fill.empty else "")
        )
        return extended
    except Exception as e:
        print(f"[bar_cache] ⚠️ get_extended_history failed: {e}")
        return df_mt5


def cleanup_old(keep_days: int = 60) -> int:
    """ลบแท่งเก่าเกิน keep_days วัน กัน DB บวมไม่จำกัด (default เก็บ ~2 เดือน)
    คืนจำนวนแท่งที่ลบไป — เรียกเป็นระยะ (เช่น วันละครั้ง) ไม่ต้องเรียกทุก scan"""
    try:
        import datetime as _dt
        cutoff = (_dt.datetime.now() - _dt.timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
        conn = _get_conn()
        cur = conn.execute("DELETE FROM m5_bars WHERE time < ?", (cutoff,))
        conn.commit()
        deleted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.close()
        if deleted:
            print(f"[bar_cache] 🧹 cleanup: ลบ {deleted} แท่งที่เก่ากว่า {keep_days} วัน")
        return deleted
    except Exception as e:
        print(f"[bar_cache] ⚠️ cleanup_old failed: {e}")
        return 0
