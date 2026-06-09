"""
Backtest — ทดสอบ Reversal Strategy ย้อนหลัง
ดึงข้อมูล XAUUSD M5 แล้วรัน SMC + Reversal detector ทุก candle

Rules:
  - ไม่ look-ahead: ที่ bar[i] ใช้ข้อมูลเฉพาะ bar[0..i]
  - Entry: open ของ bar ถัดไป (bar[i+1])
  - Exit: TP หรือ SL hit — ดูจาก high/low ของแต่ละ bar
  - Session filter: London (14:00-23:00) + NY (19:00-04:00) ไทย
  - Cooldown: 20 bars หลัง signal
  - Max hold: 48 bars (4 ชั่วโมง) — ถ้าไม่ถึง TP/SL ปิดที่ราคาปิด

Usage:
  cd bot
  python backtest.py [--days 30] [--min-score 3] [--min-rr 1.5]
"""

import sys
import argparse
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

# เพิ่ม path สำหรับ import agents
sys.path.insert(0, str(Path(__file__).parent))

from agents.smc_engine import SMCEngine, summarize, detect_reversal, get_session
from agents import smc_engine

smc = SMCEngine(swing_length=5)

# ── Thai session filter ────────────────────────────────────────────
def is_tradeable_session(dt) -> bool:
    """เช็คว่า timestamp นี้อยู่ใน London หรือ NY session (Thai time)"""
    import pytz
    thai_tz = pytz.timezone("Asia/Bangkok")
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        local = dt.astimezone(thai_tz)
    else:
        local = pytz.utc.localize(dt).astimezone(thai_tz)
    h = local.hour + local.minute / 60.0
    is_london = 14.0 <= h < 23.0
    is_ny     = h >= 19.0 or h < 4.0
    return is_london or is_ny


# ── Download Data ─────────────────────────────────────────────────
def download_data(days: int = 30) -> pd.DataFrame:
    print(f"📥 ดึงข้อมูล XAUUSD M5 ย้อนหลัง {days} วัน...")
    ticker = yf.Ticker("GC=F")
    df = ticker.history(period=f"{days}d", interval="5m")
    if df.empty:
        raise ValueError("ดึงข้อมูลไม่ได้")
    df.columns = [c.lower() for c in df.columns]
    df = df[['open','high','low','close','volume']].dropna()
    print(f"✅ ได้ {len(df)} candles ({df.index[0].date()} → {df.index[-1].date()})")
    return df


# ── Run Single Bar ────────────────────────────────────────────────
def scan_bar(df_slice: pd.DataFrame) -> dict | None:
    """รัน reversal detector บน df_slice (ข้อมูลถึงแค่ bar ปัจจุบัน)"""
    if len(df_slice) < 50:
        return None
    try:
        result  = smc.analyze(df_slice)
        current = round(df_slice['close'].iloc[-1], 2)
        rev     = detect_reversal(df_slice, result)
        if rev.get("reversal_signal"):
            return {
                "signal":  rev["reversal_signal"],
                "score":   rev["reversal_score"],
                "stars":   rev.get("reversal_stars"),
                "grade":   rev.get("reversal_grade"),
                "reasons": rev.get("reversal_reasons", []),
                "entry":   current,                     # entry ที่ราคาปัจจุบัน (ใช้ open bar ถัดไปจริง)
                "sl":      rev["stop_loss"],
                "tp":      rev["take_profit"],
                "sl_pips": rev["sl_pips"],
                "tp_pips": rev["tp_pips"],
                "rr":      rev["rr"],
            }
    except Exception as e:
        pass
    return None


# ── Simulate Trade ─────────────────────────────────────────────────
def simulate_trade(df: pd.DataFrame, entry_bar_idx: int, signal: dict, max_hold: int = 48) -> dict:
    """
    จำลองการ execute trade หลัง signal
    Entry: open ของ bar entry_bar_idx
    Exit:  TP หรือ SL หรือ max_hold bars
    """
    entry_price = df['open'].iloc[entry_bar_idx]
    sl          = signal['sl']
    tp          = signal['tp']
    direction   = signal['signal']

    sl_pips = round(abs(entry_price - sl) * 10, 1)
    tp_pips = round(abs(entry_price - tp) * 10, 1)
    rr_plan = round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0

    for i in range(entry_bar_idx, min(entry_bar_idx + max_hold, len(df))):
        bar = df.iloc[i]
        if direction == "BUY":
            if bar['low'] <= sl:
                pnl_pips = round((sl - entry_price) * 10, 1)
                return _trade_result("loss", i - entry_bar_idx, entry_price, sl, pnl_pips, rr_plan, signal)
            if bar['high'] >= tp:
                pnl_pips = round((tp - entry_price) * 10, 1)
                return _trade_result("win", i - entry_bar_idx, entry_price, tp, pnl_pips, rr_plan, signal)
        else:  # SELL
            if bar['high'] >= sl:
                pnl_pips = round((entry_price - sl) * 10, 1)
                return _trade_result("loss", i - entry_bar_idx, entry_price, sl, pnl_pips, rr_plan, signal)
            if bar['low'] <= tp:
                pnl_pips = round((entry_price - tp) * 10, 1)
                return _trade_result("win", i - entry_bar_idx, entry_price, tp, pnl_pips, rr_plan, signal)

    # Max hold ─ ปิดที่ราคาปิดของ bar สุดท้าย
    close_price = df['close'].iloc[min(entry_bar_idx + max_hold - 1, len(df) - 1)]
    pnl_pips = round(((close_price - entry_price) if direction == "BUY" else (entry_price - close_price)) * 10, 1)
    outcome = "win" if pnl_pips > 0 else "loss" if pnl_pips < 0 else "be"
    return _trade_result(outcome, max_hold, entry_price, close_price, pnl_pips, rr_plan, signal, "max_hold")


def _trade_result(outcome, bars_held, entry, exit_price, pnl_pips, rr_plan, signal, note=""):
    pnl_dollar = pnl_pips * 0.01 * 100  # 0.01 lot × Gold pip value
    sl_pips    = signal.get("sl_pips", 1)
    rr_actual  = round(abs(pnl_pips) / sl_pips, 2) if sl_pips > 0 else 0
    return {
        "outcome":    outcome,
        "bars_held":  bars_held,
        "entry":      entry,
        "exit":       round(exit_price, 2),
        "pnl_pips":   pnl_pips,
        "pnl_dollar": round(pnl_dollar, 2),
        "rr_actual":  rr_actual,
        "rr_plan":    rr_plan,
        "sl_pips":    sl_pips,
        "tp_pips":    signal.get("tp_pips", 0),
        "score":      signal.get("score", 0),
        "stars":      signal.get("stars", ""),
        "signal":     signal.get("signal"),
        "reasons":    signal.get("reasons", []),
        "note":       note,
    }


# ── Main Backtest ─────────────────────────────────────────────────
def run_backtest(days=30, min_score=3, min_rr=1.5, max_hold=48, cooldown=20):
    df = download_data(days)
    n  = len(df)

    trades     = []
    last_signal_bar = -cooldown  # cooldown reset

    print(f"\n🔍 สแกน {n} candles (min_score={min_score}, min_rr={min_rr})...\n")

    # Warm-up: ต้องการ 50 bars ขั้นต่ำ
    warm = 50

    for i in range(warm, n - 1):
        # Cooldown check
        if (i - last_signal_bar) < cooldown:
            continue

        # Session filter
        bar_time = df.index[i]
        if not is_tradeable_session(bar_time):
            continue

        # Scan ด้วยข้อมูลถึง bar i (ไม่ look-ahead)
        df_slice = df.iloc[:i + 1]
        signal   = scan_bar(df_slice)

        if signal is None:
            continue

        # Filter
        if signal["score"] < min_score:
            continue
        if signal["rr"] < min_rr:
            continue

        # Simulate trade (entry ที่ bar i+1)
        trade = simulate_trade(df, i + 1, signal, max_hold)
        trade["bar_idx"]  = i
        trade["bar_time"] = str(bar_time)[:16]
        trades.append(trade)
        last_signal_bar = i

        # Print progress
        icon = "✅" if trade["outcome"] == "win" else "❌" if trade["outcome"] == "loss" else "↔️"
        print(
            f"  {icon} [{trade['bar_time']}] {trade['signal']:4s} {trade['stars'] or '':<3} "
            f"score={trade['score']} | entry={trade['entry']} → {trade['exit']} | "
            f"{'+' if trade['pnl_pips'] >= 0 else ''}{trade['pnl_pips']}p | "
            f"RR={trade['rr_actual']} | {trade['note'] or ''}"
        )

    return trades


# ── Print Stats ───────────────────────────────────────────────────
def print_stats(trades: list, min_score: int):
    if not trades:
        print("\n❌ ไม่มี trade เลย — ลองลด min_score หรือ min_rr")
        return

    total  = len(trades)
    wins   = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    be     = [t for t in trades if t["outcome"] == "be"]

    win_rate   = len(wins) / (len(wins) + len(losses)) * 100 if (wins or losses) else 0
    total_pips = sum(t["pnl_pips"] for t in trades)
    avg_win    = np.mean([t["pnl_pips"] for t in wins])   if wins   else 0
    avg_loss   = np.mean([t["pnl_pips"] for t in losses]) if losses else 0
    avg_rr     = np.mean([t["rr_actual"] for t in trades])
    avg_hold   = np.mean([t["bars_held"] for t in trades])

    # Profit factor
    gross_win  = sum(t["pnl_pips"] for t in wins)
    gross_loss = abs(sum(t["pnl_pips"] for t in losses))
    pf         = round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf")

    # Max drawdown (running)
    running = 0
    peak    = 0
    max_dd  = 0
    for t in trades:
        running += t["pnl_pips"]
        peak = max(peak, running)
        dd   = peak - running
        max_dd = max(max_dd, dd)

    # Breakdown by stars
    star_stats = {}
    for t in trades:
        s = t.get("stars") or "?"
        if s not in star_stats:
            star_stats[s] = {"total": 0, "wins": 0, "pips": 0}
        star_stats[s]["total"] += 1
        if t["outcome"] == "win":
            star_stats[s]["wins"] += 1
        star_stats[s]["pips"] += t["pnl_pips"]

    # By score group
    high_score  = [t for t in trades if t["score"] >= 7]
    med_score   = [t for t in trades if 5 <= t["score"] < 7]
    low_score   = [t for t in trades if t["score"] < 5]

    def group_wr(g):
        w = sum(1 for t in g if t["outcome"] == "win")
        l = sum(1 for t in g if t["outcome"] == "loss")
        return f"{w/(w+l)*100:.0f}%" if (w+l) > 0 else "-"

    pips_sign = "+" if total_pips >= 0 else ""

    print(f"""
╔══════════════════════════════════════════════════════╗
║           📊  BACKTEST RESULTS — REVERSAL SMC         ║
╠══════════════════════════════════════════════════════╣
║  Trades: {total:>4}  (Win: {len(wins)} | Loss: {len(losses)} | BE: {len(be)})
║  Win Rate:    {win_rate:.1f}%
║  Total P&L:   {pips_sign}{total_pips:.1f} pips
║  Profit Factor: {pf}
╠══════════════════════════════════════════════════════╣
║  Avg Win:     +{avg_win:.1f} pips
║  Avg Loss:    {avg_loss:.1f} pips
║  Avg RR:      1:{avg_rr:.2f}
║  Avg Hold:    {avg_hold:.1f} bars (~{avg_hold*5/60:.1f} hr)
║  Max Drawdown: {max_dd:.1f} pips
╠══════════════════════════════════════════════════════╣
║  By Score:
║    ★★★ (≥7): {len(high_score):>3} trades | WR {group_wr(high_score):>5} | {sum(t['pnl_pips'] for t in high_score):+.1f}p
║    ★★  (5-6): {len(med_score):>3} trades | WR {group_wr(med_score):>5} | {sum(t['pnl_pips'] for t in med_score):+.1f}p
║    ★   (<5): {len(low_score):>3} trades | WR {group_wr(low_score):>5} | {sum(t['pnl_pips'] for t in low_score):+.1f}p
╚══════════════════════════════════════════════════════╝
""")

    # Top 5 wins / worst 5 losses
    sorted_trades = sorted(trades, key=lambda t: t["pnl_pips"], reverse=True)
    print("🏆 Top 5 Wins:")
    for t in sorted_trades[:5]:
        print(f"  +{t['pnl_pips']}p  [{t['bar_time']}] {t['signal']} score={t['score']} | {', '.join(t['reasons'][:2])}")

    print("\n💀 Worst 5 Losses:")
    for t in sorted_trades[-5:]:
        print(f"  {t['pnl_pips']}p  [{t['bar_time']}] {t['signal']} score={t['score']} | {', '.join(t['reasons'][:2])}")

    print("\n📋 Recommendations:")
    if win_rate >= 55 and pf >= 1.5:
        print("  ✅ Strategy ดูดีครับ — พิจารณาเพิ่ม min_score เพื่อกรอง signal คุณภาพต่ำออก")
    elif win_rate >= 45 and pf >= 1.2:
        print("  🟡 พอใช้ได้ — ลอง min_score=5 (★★ขึ้นไป) หรือเพิ่ม min_rr")
    else:
        print("  ❌ Win rate ต่ำหรือ profit factor ต่ำ — ลองปรับ scoring weights")

    if high_score and len(high_score) > 2:
        hs_wr = sum(1 for t in high_score if t["outcome"]=="win") / len(high_score) * 100
        if hs_wr > win_rate + 10:
            print(f"  💡 ★★★ trades มี WR {hs_wr:.0f}% — ลองเทรดเฉพาะ score ≥7")


# ── Entry Point ───────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest Reversal Strategy")
    parser.add_argument("--days",      type=int,   default=30,  help="จำนวนวันย้อนหลัง")
    parser.add_argument("--min-score", type=int,   default=3,   help="คะแนน reversal ขั้นต่ำ (0-10)")
    parser.add_argument("--min-rr",    type=float, default=1.5, help="RR ขั้นต่ำ")
    parser.add_argument("--max-hold",  type=int,   default=48,  help="ถือนานสุด N bars (default=48=4hr)")
    parser.add_argument("--cooldown",  type=int,   default=20,  help="Cooldown bars หลัง signal")
    args = parser.parse_args()

    print("=" * 54)
    print("  🔄 REVERSAL STRATEGY BACKTEST — XAUUSD M5")
    print("=" * 54)
    print(f"  Days: {args.days} | Min Score: {args.min_score} | Min RR: {args.min_rr}")
    print(f"  Max Hold: {args.max_hold} bars | Cooldown: {args.cooldown} bars")
    print("=" * 54)

    trades = run_backtest(
        days      = args.days,
        min_score = args.min_score,
        min_rr    = args.min_rr,
        max_hold  = args.max_hold,
        cooldown  = args.cooldown,
    )
    print_stats(trades, args.min_score)
