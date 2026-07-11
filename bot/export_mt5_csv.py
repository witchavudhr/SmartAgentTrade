"""
export_mt5_csv.py — ดึง M5 OHLCV จาก MT5 จริง แล้ว export เป็น CSV
รันบนเครื่อง Windows ที่บอทรันอยู่ (มี MetaTrader5 package + terminal login แล้ว)

วิธีใช้:
    cd bot
    python export_mt5_csv.py                # export 7 วันล่าสุด (ค่า default)
    python export_mt5_csv.py --days 10      # เปลี่ยนจำนวนวันย้อนหลัง

ผลลัพธ์: ไฟล์ mt5_export_YYYYMMDD_HHMMSS.csv ในโฟลเดอร์เดียวกัน
เอาไฟล์นี้ส่งกลับมาให้ Claude วิเคราะห์ pattern เทียบกับกราฟจริงได้เลย

หมายเหตุ: ใช้ copy_rates_range (ระบุช่วงเวลาตรงๆ) แทน copy_rates_from_pos
เพราะ copy_rates_from_pos พึ่ง history cache ในเครื่อง — ถ้า terminal ไม่เคย
scroll กราฟไปช่วงนั้นมาก่อน จะได้แท่งไม่ครบ (เจอ gap ซ้ำเวลาเดิมทุกวัน)
copy_rates_range บังคับให้ terminal ขอข้อมูลจาก broker server ตรงช่วงที่ระบุ
พร้อม retry เติม gap ที่ยังขาดหลัง fetch รอบแรก
"""
import argparse
import sys
import time
from datetime import datetime, timedelta

import MetaTrader5 as mt5

sys.path.insert(0, ".")
from config.settings import MT5_SYMBOL

_GAP_THRESHOLD_MIN = 10   # ถือว่าเป็น gap ถ้าห่างเกินนี้ (นาที)
_MAX_RETRIES        = 3
_RETRY_DELAY_SEC    = 2


def _fetch_range(symbol, date_from, date_to):
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, date_from, date_to)
    return list(rates) if rates is not None else []


def _find_gaps(rates, threshold_min=_GAP_THRESHOLD_MIN):
    gaps = []
    for i in range(1, len(rates)):
        prev_t = datetime.fromtimestamp(rates[i - 1]["time"])
        cur_t  = datetime.fromtimestamp(rates[i]["time"])
        if (cur_t - prev_t) > timedelta(minutes=threshold_min):
            gaps.append((prev_t, cur_t))
    return gaps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="จำนวนวันย้อนหลัง (default 7)")
    parser.add_argument("--symbol", type=str, default=MT5_SYMBOL)
    args = parser.parse_args()

    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        sys.exit(1)

    date_to   = datetime.now()
    date_from = date_to - timedelta(days=args.days)

    print(f"📥 ดึง {args.symbol} M5 จาก {date_from} ถึง {date_to} ...")
    rates = _fetch_range(args.symbol, date_from, date_to)

    if not rates:
        print(f"ไม่มีข้อมูล: {mt5.last_error()}")
        mt5.shutdown()
        sys.exit(1)

    # ── ตรวจ + เติม gap ──────────────────────────────────────
    for attempt in range(_MAX_RETRIES):
        gaps = _find_gaps(rates)
        if not gaps:
            break
        print(f"⚠️  พบ {len(gaps)} gap (รอบที่ {attempt + 1}) — กำลัง backfill...")
        for g_start, g_end in gaps:
            # ขยายขอบเขตเล็กน้อยกันพลาดแท่งขอบ
            fill = _fetch_range(args.symbol, g_start - timedelta(minutes=5), g_end + timedelta(minutes=5))
            rates.extend(fill)
        # dedup ตาม timestamp แล้ว sort ใหม่
        seen = {}
        for r in rates:
            seen[r["time"]] = r
        rates = sorted(seen.values(), key=lambda r: r["time"])
        time.sleep(_RETRY_DELAY_SEC)

    mt5.shutdown()

    remaining_gaps = _find_gaps(rates)
    if remaining_gaps:
        print(f"⚠️  ยังเหลือ {len(remaining_gaps)} gap หลัง retry {_MAX_RETRIES} รอบ (อาจเป็นช่วงตลาดปิดจริง):")
        for g_start, g_end in remaining_gaps:
            print(f"     {g_start} -> {g_end}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"mt5_export_{ts}.csv"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("time,open,high,low,close,tick_volume\n")
        for r in rates:
            t = datetime.fromtimestamp(r["time"]).strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{t},{r['open']},{r['high']},{r['low']},{r['close']},{r['tick_volume']}\n")

    print(f"✅ export แล้ว: {out_path} ({len(rates)} แท่ง, {args.days} วัน, symbol={args.symbol})")


if __name__ == "__main__":
    main()
