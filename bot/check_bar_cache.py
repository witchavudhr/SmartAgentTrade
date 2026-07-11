"""
check_bar_cache.py — เช็คว่า bar_cache สะสมข้อมูลครบมั้ย มี gap รึเปล่า
รันบนเครื่อง Windows ที่บอทรันอยู่ (ไม่ต้องมี MT5 เปิดก็เช็คได้ — อ่านจาก
data/bar_cache.db ตรงๆ)

วิธีใช้:
    cd bot
    python check_bar_cache.py                      # เช็คทั้งหมดที่มี
    python check_bar_cache.py --date 2026-07-13     # เช็คเฉพาะวันนั้น
"""
import argparse
import sys
from datetime import timedelta

sys.path.insert(0, ".")
from agents.bar_cache import load_range


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="เช็คเฉพาะวันนี้ (YYYY-MM-DD)")
    parser.add_argument("--gap-threshold", type=int, default=10, help="นาที — ถือว่าเป็น gap ถ้าห่างเกินนี้")
    args = parser.parse_args()

    if args.date:
        df = load_range(f"{args.date} 00:00:00", f"{args.date} 23:59:59")
    else:
        df = load_range()

    if df.empty:
        print("❌ ยังไม่มีข้อมูลใน bar_cache เลย — บอทยังไม่เคย scan สำเร็จ หรือยังไม่ถึงรอบแรก")
        return

    print(f"📊 มีทั้งหมด {len(df)} แท่ง")
    print(f"   ช่วง: {df['time'].min()} -> {df['time'].max()}")

    gaps = []
    times = df["time"].sort_values().reset_index(drop=True)
    for i in range(1, len(times)):
        diff = times[i] - times[i - 1]
        if diff > timedelta(minutes=args.gap_threshold):
            gaps.append((times[i - 1], times[i]))

    if gaps:
        print(f"\n⚠️  พบ {len(gaps)} gap (เกิน {args.gap_threshold} นาที):")
        for g_start, g_end in gaps:
            print(f"   {g_start} -> {g_end}  (ขาด {(g_end - g_start)})")
    else:
        print(f"\n✅ ไม่มี gap เลย — ข้อมูลครบต่อเนื่องทุก 5 นาที")

    # เช็คว่าครบตามช่วง session scan (06:30-19:00) มั้ย ถ้าระบุ --date
    if args.date:
        expected_start = f"{args.date} 06:30:00"
        expected_end   = f"{args.date} 19:00:00"
        first = df["time"].min()
        last  = df["time"].max()
        print(f"\n📅 คาดหวัง session {expected_start} -> {expected_end}")
        print(f"   มีจริง: {first} -> {last}")
        if str(first) > expected_start:
            print(f"   ⚠️  แท่งแรกมาช้ากว่าที่ควร (ขาดช่วงต้น session)")
        if str(last) < expected_end:
            print(f"   ⚠️  แท่งล่าสุดยังไม่ถึงเวลาปัจจุบัน/สิ้นสุด session")


if __name__ == "__main__":
    main()
