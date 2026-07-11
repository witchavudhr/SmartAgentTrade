"""
patch_mt5_gaps.py — ลองดึงเฉพาะช่วงที่ขาด (06:50-08:00 ทุกวัน) มาต่อกับไฟล์ CSV เดิม
รันบนเครื่อง Windows ที่มี MT5 + MetaTrader5 package

วิธีใช้:
    cd bot
    python patch_mt5_gaps.py --csv mt5_export_20260711_184439.csv

ผลลัพธ์: ไฟล์ใหม่ mt5_export_..._patched.csv — รวมของเดิม + ส่วนที่ดึงมาเติมได้
ถ้าดึงไม่ได้จริงๆ จะ print แจ้งว่าช่วงไหนยังขาดอยู่ (ยืนยันว่าเป็นรูจริงใน MT5 history)
"""
import argparse
import sys
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import pandas as pd

sys.path.insert(0, ".")
from config.settings import MT5_SYMBOL


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=str, required=True, help="ไฟล์ CSV เดิมที่มี gap")
    parser.add_argument("--symbol", type=str, default=MT5_SYMBOL)
    args = parser.parse_args()

    df = pd.read_csv(args.csv, parse_dates=["time"])
    df = df.sort_values("time").reset_index(drop=True)

    gaps = []
    for i in range(1, len(df)):
        prev_t = df.loc[i - 1, "time"]
        cur_t  = df.loc[i, "time"]
        if (cur_t - prev_t) > timedelta(minutes=10):
            gaps.append((prev_t, cur_t))

    if not gaps:
        print("✅ ไม่มี gap ในไฟล์นี้แล้ว ไม่ต้อง patch")
        return

    print(f"พบ {len(gaps)} gap — กำลังลองดึงมาเติม:")
    for g_start, g_end in gaps:
        print(f"  {g_start} -> {g_end}")

    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        sys.exit(1)

    patched_rows = []
    still_missing = []
    for g_start, g_end in gaps:
        rates = mt5.copy_rates_range(
            args.symbol, mt5.TIMEFRAME_M5,
            g_start - timedelta(minutes=2), g_end + timedelta(minutes=2)
        )
        if rates is None or len(rates) == 0:
            still_missing.append((g_start, g_end))
            continue
        for r in rates:
            t = datetime.fromtimestamp(r["time"])
            if g_start < t < g_end:
                patched_rows.append({
                    "time": t, "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"], "tick_volume": r["tick_volume"],
                })

    mt5.shutdown()

    if patched_rows:
        print(f"\n✅ ดึงมาเติมได้ {len(patched_rows)} แท่ง")
        patch_df = pd.DataFrame(patched_rows)
        merged = pd.concat([df, patch_df], ignore_index=True)
        merged = merged.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
        out_path = args.csv.replace(".csv", "_patched.csv")
        merged.to_csv(out_path, index=False)
        print(f"บันทึกไฟล์รวมแล้ว: {out_path} ({len(merged)} แท่ง)")
    else:
        print("\n⚠️ ดึงมาเติมไม่ได้เลยสักแท่ง — ยืนยันว่าช่วงนี้หายไปจาก MT5 history จริงๆ")

    if still_missing:
        print(f"\n⚠️ ยังขาดอยู่ {len(still_missing)} ช่วง (ดึงไม่ได้แม้แต่แท่งเดียว):")
        for g_start, g_end in still_missing:
            print(f"  {g_start} -> {g_end}")


if __name__ == "__main__":
    main()
