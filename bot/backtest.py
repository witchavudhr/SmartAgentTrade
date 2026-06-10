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
from dotenv import load_dotenv

# โหลด .env ก่อนสิ่งอื่น (API keys สำหรับ NewsCalendar)
load_dotenv()

# เพิ่ม path สำหรับ import agents
sys.path.insert(0, str(Path(__file__).parent))

from agents.smc_engine import SMCEngine, summarize, detect_reversal, detect_trend_follow, get_session
from agents.news_calendar import NewsCalendar
from agents import smc_engine

smc = SMCEngine(swing_length=5)

# ── Thai session filter ────────────────────────────────────────────
def is_tradeable_session(dt) -> bool:
    """เช็คว่า timestamp นี้อยู่ใน Asian Late / London / NY session (Thai time)"""
    import pytz
    thai_tz = pytz.timezone("Asia/Bangkok")
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        local = dt.astimezone(thai_tz)
    else:
        local = pytz.utc.localize(dt).astimezone(thai_tz)
    h = local.hour + local.minute / 60.0
    is_tokyo  = 7.0 <= h < 14.0    # 07:00-14:00 — Tokyo session (ก่อน London เปิด)
    is_london = 14.0 <= h < 23.0
    is_ny     = h >= 19.0 or h < 4.0
    return is_tokyo or is_london or is_ny


def is_near_us_news(dt, buffer_min: int = 30) -> bool:
    """
    Hardcoded US pre-market block — block ±buffer_min ของ 08:30 ET และ 10:00 ET
    ทุกวันทำการ (ไม่ว่าจะมีข่าวหรือเปล่า)

    เหตุผล: ช่วง 08:00–09:00 ET คือ US pre-market opening —
    spread กว้าง ราคาผันผวน volatility สูง แม้วันไม่มีข่าว
    → backtest พิสูจน์แล้วว่า block ทุกวันให้ผลดีกว่า filter จาก calendar จริง
    (PF 1.40 vs 1.29, MaxDD 457p vs 1067p)

    Live bot ใช้ Finnhub calendar เพิ่มเติมใน news_scout.py สำหรับข่าว specific
    """
    import pytz
    et_tz = pytz.timezone("US/Eastern")
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        et = dt.astimezone(et_tz)
    else:
        et = pytz.utc.localize(dt).astimezone(et_tz)

    if et.weekday() >= 5:   # weekend
        return False

    h   = et.hour + et.minute / 60.0
    buf = buffer_min / 60.0
    return any(abs(h - t) <= buf for t in [8.5, 10.0])


def is_session_open_bar(dt) -> bool:
    """
    เช็คว่า bar นี้อยู่ใน ±30 นาที ของ key scan times หรือเปล่า
    ตรงกับบอทจริงที่ scan 4 รอบ/วัน:
      13:45 — London Open   (±30 min = 13:15-14:15)
      15:45 — London Mid    (±30 min = 15:15-16:15)
      18:45 — NY Pre-market (±30 min = 18:15-19:15)
      22:45 — NY Peak       (±30 min = 22:15-23:15)
    """
    import pytz
    thai_tz = pytz.timezone("Asia/Bangkok")
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        local = dt.astimezone(thai_tz)
    else:
        local = pytz.utc.localize(dt).astimezone(thai_tz)

    h = local.hour + local.minute / 60.0
    # key scan times ±0.5 ชั่วโมง (Thai time)
    # 01:00  Late NY Afternoon       (18:00 UTC / 14:00 EDT)
    # 03:00  NY Close Momentum       (20:00 UTC / 16:00 EDT)
    # 07:15  Tokyo Open              (00:15 UTC)
    # 13:45  London Open             (06:45 UTC)
    # 15:45  London Mid              (08:45 UTC)  ← WR 62% ดีที่สุด
    # 20:15  NY Pre-Open             (13:15 UTC / 09:15 EDT)
    # 22:45  NY Peak                 (15:45 UTC / 11:45 EDT)
    # Tokyo Mid 10:00-12:00 Thai: ทดสอบแล้ว แม้ score≥7 ก็ไม่มี edge (-311p/30d)
    scan_centers = [1.0, 3.0, 7.25, 13.75, 15.75, 20.25, 22.75]
    return any(abs(h - c) <= 0.5 for c in scan_centers)


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
def scan_bar(df_slice: pd.DataFrame, h4_bias: str = "neutral") -> dict | None:
    """
    Hybrid detector: Reversal + Trend Follow
    - Reversal: CHoCH + Sweep (ทุก bias)
    - Trend Follow: OB/FVG pullback (เฉพาะ H4 bull/bear)
    เลือก score สูงกว่า / Reversal ≥7 override เสมอ
    """
    if len(df_slice) < 50:
        return None
    try:
        result  = smc.analyze(df_slice)
        current = round(df_slice["close"].iloc[-1], 2)

        # ── Reversal ──────────────────────────────────────────
        rev        = detect_reversal(df_slice, result)
        rev_signal = rev.get("reversal_signal")
        rev_score  = rev.get("reversal_score", 0)

        # ── Trend Follow ──────────────────────────────────────
        trend        = {}
        trend_signal = None
        trend_score  = 0
        if h4_bias in ("bull", "bear"):
            trend        = detect_trend_follow(df_slice, result, h4_bias)
            trend_signal = trend.get("trend_signal")
            trend_score  = trend.get("trend_score", 0)

        # ── เลือก signal ──────────────────────────────────────
        # Reversal ★★★ (≥7) override เสมอ — หายากและ quality สูง
        if rev_signal and rev_score >= 7:
            return _build_signal(current, rev, "REVERSAL")

        # Trend Follow ชนะถ้า score สูงกว่า (และ ≥5)
        if trend_signal and trend_score >= 5:
            if rev_score < trend_score:
                return _build_signal_trend(current, trend)

        # Reversal ★★ (5-6) fallback
        if rev_signal and rev_score >= 5:
            return _build_signal(current, rev, "REVERSAL")

        return None

    except Exception:
        return None


def _build_signal(current, rev, setup_type):
    return {
        "signal":     rev["reversal_signal"],
        "score":      rev["reversal_score"],
        "stars":      rev.get("reversal_stars"),
        "reasons":    rev.get("reversal_reasons", []),
        "entry":      current,
        "sl":         rev["stop_loss"],
        "tp":         rev["take_profit"],
        "sl_pips":    rev["sl_pips"],
        "tp_pips":    rev["tp_pips"],
        "rr":         rev["rr"],
        "setup_type": setup_type,
    }


def _build_signal_trend(current, trend):
    return {
        "signal":     trend["trend_signal"],
        "score":      trend["trend_score"],
        "stars":      trend.get("trend_stars"),
        "reasons":    trend.get("trend_reasons", []),
        "entry":      current,
        "sl":         trend["stop_loss"],
        "tp":         trend["take_profit"],
        "sl_pips":    trend["sl_pips"],
        "tp_pips":    trend["tp_pips"],
        "rr":         trend["rr"],
        "setup_type": "TREND",
    }


# ── Simulate Trade ─────────────────────────────────────────────────
def simulate_trade(df: pd.DataFrame, entry_bar_idx: int, signal: dict,
                   max_hold: int = 48, trail_pips: float = 0,
                   breakeven: bool = False) -> dict:
    """
    จำลองการ execute trade หลัง signal
    Entry: open ของ bar entry_bar_idx
    Exit:  TP หรือ SL หรือ max_hold bars

    trail_pips > 0: Trailing Stop แบบ step
      - ราคาวิ่ง +trail_pips → SL ย้ายมา entry (กันทุน)
      - ราคาวิ่ง +2×trail_pips → SL ย้ายมา entry+trail_pips (ล็อคกำไร)
      - เลื่อนทุก trail_pips ตลอดทาง
    breakeven=True (legacy): เลื่อน SL → entry เมื่อถึง 1:1 RR
    """
    entry_price = df['open'].iloc[entry_bar_idx]
    sl          = signal['sl']
    tp          = signal['tp']
    direction   = signal['signal']
    sl_dist     = abs(entry_price - sl)

    sl_pips = round(sl_dist * 10, 1)
    tp_pips = round(abs(entry_price - tp) * 10, 1)
    rr_plan = round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0

    trail_step   = trail_pips / 10.0   # แปลง pips → price unit (Gold: 1 pip = $0.1)
    trail_locked = 0                   # ระดับ lock ล่าสุด (เป็น price)
    trail_active = False

    for i in range(entry_bar_idx, min(entry_bar_idx + max_hold, len(df))):
        bar = df.iloc[i]

        if direction == "BUY":
            # ── Trailing Stop ─────────────────────────────────────
            if trail_step > 0:
                profit_now = bar['high'] - entry_price        # max profit ใน bar นี้
                levels     = int(profit_now / trail_step)      # ถึงระดับไหนแล้ว
                if levels >= 1:
                    # SL ใหม่ = entry + (levels-1) × step  (level 1 = BE, level 2 = +1 step, ...)
                    new_sl = entry_price + (levels - 1) * trail_step
                    if new_sl > sl:                            # เลื่อนขึ้นเท่านั้น
                        sl           = new_sl
                        trail_locked = new_sl
                        trail_active = True

            if bar['low'] <= sl:
                pnl_pips = round((sl - entry_price) * 10, 1)
                outcome  = "win" if sl > entry_price else "be" if sl == entry_price else "loss"
                note     = "trail" if trail_active else ("BE" if breakeven else "")
                return _trade_result(outcome, i - entry_bar_idx, entry_price, sl, pnl_pips, rr_plan, signal, note)
            if bar['high'] >= tp:
                pnl_pips = round((tp - entry_price) * 10, 1)
                return _trade_result("win", i - entry_bar_idx, entry_price, tp, pnl_pips, rr_plan, signal)

        else:  # SELL
            # ── Trailing Stop ─────────────────────────────────────
            if trail_step > 0:
                profit_now = entry_price - bar['low']
                levels     = int(profit_now / trail_step)
                if levels >= 1:
                    new_sl = entry_price - (levels - 1) * trail_step
                    if new_sl < sl:
                        sl           = new_sl
                        trail_locked = new_sl
                        trail_active = True

            if bar['high'] >= sl:
                pnl_pips = round((entry_price - sl) * 10, 1)
                outcome  = "win" if sl < entry_price else "be" if sl == entry_price else "loss"
                note     = "trail" if trail_active else ("BE" if breakeven else "")
                return _trade_result(outcome, i - entry_bar_idx, entry_price, sl, pnl_pips, rr_plan, signal, note)
            if bar['low'] <= tp:
                pnl_pips = round((entry_price - tp) * 10, 1)
                return _trade_result("win", i - entry_bar_idx, entry_price, tp, pnl_pips, rr_plan, signal)

    # Max hold ─ ปิดที่ราคาปิดของ bar สุดท้าย
    close_price = df['close'].iloc[min(entry_bar_idx + max_hold - 1, len(df) - 1)]
    pnl_pips    = round(((close_price - entry_price) if direction == "BUY" else (entry_price - close_price)) * 10, 1)
    outcome     = "win" if pnl_pips > 0 else "loss" if pnl_pips < 0 else "be"
    note        = "max_hold" + ("+trail" if trail_active else "")
    return _trade_result(outcome, max_hold, entry_price, close_price, pnl_pips, rr_plan, signal, note)


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
def get_h4_bias(df_slice: pd.DataFrame) -> str:
    """
    H4 bias แบบ fast — เปรียบ close กับ midpoint ของ range 20 H4 bars
    Return: "bull" | "bear" | "neutral"
    """
    try:
        # Resample M5 → H4
        df_h4 = df_slice.resample("4h").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum"
        }).dropna()
        if len(df_h4) < 5:
            return "neutral"
        last_n = df_h4.iloc[-20:] if len(df_h4) >= 20 else df_h4
        mid = (last_n["high"].max() + last_n["low"].min()) / 2
        close = df_h4["close"].iloc[-1]
        if close > mid * 1.001:
            return "bull"
        elif close < mid * 0.999:
            return "bear"
        return "neutral"
    except Exception:
        return "neutral"


def run_backtest(days=30, min_score=3, min_rr=1.5, max_hold=48, cooldown=20,
                 session_open_only=True, h4_filter=True, news_filter=True,
                 max_sl=0, breakeven=False, trail_pips=0):
    df = download_data(days)
    n  = len(df)

    # ── News Block: Hybrid — Finnhub calendar + always-block US pre-market ─
    # Logic:
    #   1) Real Finnhub events → block ±30min รอบ event จริง
    #   2) ทุกวันทำการ → always block 08:30 ET ±30min (US pre-market spread กว้างทุกวัน)
    #   3) วันที่ไม่มีข่าว Finnhub → block 08:30 ET ±60min (ระวังมากขึ้น)
    news_cal     = None
    news_blocked = 0
    if news_filter:
        from_date = df.index[0].strftime("%Y-%m-%d")
        to_date   = df.index[-1].strftime("%Y-%m-%d")
        news_cal  = NewsCalendar()
        n_events  = news_cal.load(from_date, to_date)
        src       = news_cal.source
        print(f"📰 News filter (hybrid): {n_events} Finnhub events ({src})")
        print(f"   + always-block 08:30 ET ±30min | no-news days → ±60min")

    trades          = []
    last_signal_bar = -cooldown

    mode_str = []
    if session_open_only: mode_str.append("session-open ±30min")
    if h4_filter:         mode_str.append("H4 trend filter")
    if news_filter:       mode_str.append(f"news hybrid (Finnhub + 08:30 ET always)")
    if max_sl > 0:        mode_str.append(f"max-SL {max_sl}p")
    if trail_pips > 0:   mode_str.append(f"trail@{trail_pips:.0f}p")
    if breakeven:         mode_str.append("breakeven@1R")
    print(f"\n🔍 สแกน {n} candles | mode: {', '.join(mode_str) or 'full'}")
    print(f"   min_score={min_score}, min_rr={min_rr}, cooldown={cooldown} bars\n")

    warm = 50  # warm-up

    for i in range(warm, n - 1):
        # Cooldown check
        if (i - last_signal_bar) < cooldown:
            continue

        bar_time = df.index[i]

        # Session filter: ต้องอยู่ใน London/NY
        if not is_tradeable_session(bar_time):
            continue

        # Session open filter: เฉพาะช่วง ±30 min ของ key scan times
        if session_open_only and not is_session_open_bar(bar_time):
            continue

        # News filter: hybrid mode
        if news_filter:
            # 1) Finnhub real events → block ±30min
            if news_cal is not None:
                blocked, _ = news_cal.is_blocked(bar_time, buffer_min=30)
                if blocked:
                    news_blocked += 1
                    continue

            # 2) วันนี้มีข่าว Finnhub ไหม?
            import pytz as _pytz
            _et = bar_time.astimezone(_pytz.timezone("US/Eastern")) \
                  if hasattr(bar_time, "tzinfo") and bar_time.tzinfo \
                  else _pytz.utc.localize(bar_time).astimezone(_pytz.timezone("US/Eastern"))
            _has_news_today = news_cal is not None and bool(news_cal.get_day_summary(_et.date()))

            # 3) Always block 08:30 ET — ±60min วันไม่มีข่าว, ±30min วันมีข่าว
            _buf = 30 if _has_news_today else 60
            if is_near_us_news(bar_time, buffer_min=_buf):
                news_blocked += 1
                continue

        # Scan — fixed 300-bar window (ไม่ look-ahead)
        window   = 300
        df_slice = df.iloc[max(0, i - window + 1) : i + 1]

        # คำนวณ H4 bias ก่อน scan
        h4     = get_h4_bias(df_slice) if h4_filter else "neutral"
        signal = scan_bar(df_slice, h4_bias=h4)

        if signal is None:
            continue

        # H4 filter:
        # - REVERSAL: อนุญาต counter-trend เสมอ (CHoCH catches genuine reversals)
        # - TREND: อนุญาต counter-trend ได้ถ้า score≥7 (strong setup)
        if h4_filter:
            is_counter = (h4 == "bear" and signal["signal"] == "BUY") or \
                         (h4 == "bull" and signal["signal"] == "SELL")
            if is_counter and signal.get("setup_type") != "REVERSAL" and signal.get("score", 0) < 7:
                continue
            signal["h4_bias"] = h4

        # Score / RR filter
        if signal["score"] < min_score:
            continue
        if signal["rr"] < min_rr:
            continue


        # Max SL filter — ตัด trade ที่ SL กว้างเกินไป
        if max_sl > 0 and signal.get("sl_pips", 0) > max_sl:
            continue

        # Simulate trade
        trade = simulate_trade(df, i + 1, signal, max_hold,
                               trail_pips=trail_pips, breakeven=breakeven)
        trade["bar_idx"]    = i
        trade["bar_time"]   = str(bar_time)[:16]
        trade["h4_bias"]    = signal.get("h4_bias", "?")
        trade["setup_type"] = signal.get("setup_type", "")
        trades.append(trade)
        last_signal_bar = i

        setup     = signal.get("setup_type", "")
        setup_tag = "🔄REV" if setup == "REVERSAL" else "📈TRD" if setup == "TREND" else "   "
        icon      = "✅" if trade["outcome"] == "win" else "❌" if trade["outcome"] == "loss" else "↔️"
        print(
            f"  {icon} [{trade['bar_time']}] {setup_tag} {trade['signal']:4s} H4={trade['h4_bias']:7s} "
            f"{trade['stars'] or '':<3} score={trade['score']} | "
            f"entry={trade['entry']} → {trade['exit']} | "
            f"{'+' if trade['pnl_pips'] >= 0 else ''}{trade['pnl_pips']}p | "
            f"RR={trade['rr_actual']} | {trade['note'] or ''}"
        )

    if news_filter:
        src = news_cal.source if news_cal else "-"
        print(f"\n📰 News filter blocked {news_blocked} bars (hybrid: {src} + 08:30 ET always)")

    return trades


# ── Print Trade History ───────────────────────────────────────────
def print_trade_history(trades: list):
    """แสดง trade history ในรูปแบบตาราง"""
    if not trades:
        return

    # Header
    print("\n" + "═" * 108)
    print("  📋  TRADE HISTORY")
    print("═" * 108)
    print(
        f"  {'#':>3}  {'Date/Time':<17} {'Type':<7} {'Dir':<5} {'H4':<8} "
        f"{'Str':>3}  {'Score':>5}  {'Entry':>8} {'Exit':>8}  "
        f"{'P&L(p)':>8}  {'RR':>5}  {'Result':<12}"
    )
    print("─" * 108)

    running_pnl = 0.0
    for idx, t in enumerate(trades, 1):
        setup     = t.get("setup_type", "")
        setup_tag = "🔄REV" if setup == "REVERSAL" else "📈TRD" if setup == "TREND" else "  ?"
        icon      = "✅ WIN" if t["outcome"] == "win" else "❌ LOSS" if t["outcome"] == "loss" else "↔️ BE"
        note      = f" ({t['note']})" if t.get("note") else ""
        running_pnl += t["pnl_pips"]
        pnl_str   = f"{t['pnl_pips']:+.1f}"
        run_str   = f"{running_pnl:+.1f}"
        stars     = t.get("stars") or ""

        print(
            f"  {idx:>3}  {t['bar_time']:<17} {setup_tag:<7} {t['signal']:<5} "
            f"{t['h4_bias']:<8} {stars:>3}  {t['score']:>5}  "
            f"{t['entry']:>8.2f} {t['exit']:>8.2f}  "
            f"{pnl_str:>8}  {t['rr_actual']:>5.2f}  {icon}{note}"
        )

    print("─" * 108)
    total_pnl = sum(t["pnl_pips"] for t in trades)
    wins      = sum(1 for t in trades if t["outcome"] == "win")
    losses    = sum(1 for t in trades if t["outcome"] == "loss")
    print(f"  {'TOTAL':>3}  {'':17} {'':7} {'':5} {'':8} {'':3}  {'':5}  "
          f"  {'':8} {'':8}  {total_pnl:>+8.1f}  {'':5}  "
          f"W:{wins} / L:{losses}")
    print("═" * 108)


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
    if win_rate >= 45 and pf >= 1.5:
        print("  ✅ Strategy แข็งแกร่ง — พร้อม live test ได้เลย")
    elif pf >= 1.2:
        print(f"  🟡 Profitable (PF {pf}) — ใช้งานได้ แต่ควรเพิ่ม min_score หรือปรับ SL")
    elif pf >= 1.0:
        print(f"  🟢 PF {pf} ≥ 1.0 — strategy มีกำไร แต่ margin เล็ก ระวัง overfitting")
    else:
        print(f"  ❌ PF {pf} < 1.0 — ยังขาดทุน ลองปรับ scoring / SL / session filter")

    if high_score and len(high_score) > 2:
        hs_wr = sum(1 for t in high_score if t["outcome"]=="win") / len(high_score) * 100
        if hs_wr > win_rate + 10:
            print(f"  💡 ★★★ trades มี WR {hs_wr:.0f}% — ลองเทรดเฉพาะ score ≥7")


# ── Entry Point ───────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest Reversal Strategy")
    parser.add_argument("--days",             type=int,   default=30,  help="จำนวนวันย้อนหลัง")
    parser.add_argument("--min-score",        type=int,   default=5,   help="คะแนน reversal ขั้นต่ำ (0-10)")
    parser.add_argument("--min-rr",           type=float, default=1.5, help="RR ขั้นต่ำ")
    parser.add_argument("--max-hold",         type=int,   default=48,  help="ถือนานสุด N bars (default=48=4hr)")
    parser.add_argument("--cooldown",         type=int,   default=20,  help="Cooldown bars หลัง signal")
    parser.add_argument("--no-session-open",  action="store_true",     help="ปิด session open filter (scan ทุก bar)")
    parser.add_argument("--no-h4-filter",     action="store_true",     help="ปิด H4 trend filter")
    parser.add_argument("--no-news-filter",   action="store_true",     help="ปิด US news block filter")
    parser.add_argument("--max-sl",           type=float, default=0,   help="SL สูงสุด (pips) — 0=ไม่กำหนด")
    parser.add_argument("--trail",            type=float, default=0,   help="Trailing stop step (pips) — เลื่อน SL ทุก N pips ที่กำไร (เช่น 1000)")
    parser.add_argument("--breakeven",        action="store_true",     help="เลื่อน SL → entry เมื่อราคาถึง 1:1 RR (legacy)")
    args = parser.parse_args()

    session_open_only = not args.no_session_open
    h4_filter         = not args.no_h4_filter
    news_filter       = not args.no_news_filter

    print("=" * 60)
    print("  🔄 REVERSAL STRATEGY BACKTEST — XAUUSD M5")
    print("=" * 60)
    print(f"  Days: {args.days} | Min Score: {args.min_score} | Min RR: {args.min_rr}")
    print(f"  Max Hold: {args.max_hold} bars | Cooldown: {args.cooldown} bars")
    print(f"  Session Open Filter: {'ON ±30min' if session_open_only else 'OFF (all bars)'}")
    print(f"  H4 Trend Filter:     {'ON (no counter-trend)' if h4_filter else 'OFF'}")
    print(f"  News Filter (US):    {'ON ±30min 08:30/10:00 ET' if news_filter else 'OFF'}")
    print(f"  Max SL Cap:          {f'{args.max_sl:.0f} pips' if args.max_sl > 0 else 'OFF (ไม่จำกัด)'}")
    print(f"  Trailing Stop:       {f'ON every {args.trail:.0f}p → BE then lock profit' if args.trail > 0 else 'OFF'}")
    print(f"  Breakeven Stop:      {'ON @ 1:1 RR → SL=entry' if args.breakeven else 'OFF'}")
    print("=" * 60)

    trades = run_backtest(
        days             = args.days,
        min_score        = args.min_score,
        min_rr           = args.min_rr,
        max_hold         = args.max_hold,
        cooldown         = args.cooldown,
        session_open_only= session_open_only,
        h4_filter        = h4_filter,
        news_filter      = news_filter,
        max_sl           = args.max_sl,
        breakeven        = args.breakeven,
        trail_pips       = args.trail,
    )
    print_trade_history(trades)
    print_stats(trades, args.min_score)
