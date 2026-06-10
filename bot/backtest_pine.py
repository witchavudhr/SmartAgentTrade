"""
backtest_pine.py — Backtest ด้วย signal logic จาก SMC_Complete_4.pine โดยตรง
Port ของ Pine Script lines 666–755 (signal detection section)

Score scale ตาม Pine:
  ★★★ = score >= 3  (OB+H1+H4+CHoCH_grab+Struct_sweep×2, max=6)
  ★★  = score == 2
  ★   = score <= 1

News filter: hardcoded ±30min @ 08:30 ET ทุกวันทำการ (เหมือน backtest.py)
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import numpy as np
import yfinance as yf
import pytz
import argparse
from dotenv import load_dotenv

load_dotenv()

# Reuse shared utilities จาก backtest.py
from backtest import (
    download_data, simulate_trade,
    is_tradeable_session, is_session_open_bar, is_near_us_news,
)


# ─── Pine SMC State Machine ───────────────────────────────────────────────────

class PineSMC:
    """
    Stateful bar-by-bar processor — mirrors Pine Script SMC By Beam logic.

    Swing pivot: size=50  (Pine swingsLengthInput default)
    CHoCH valid: 5 bars   (recent_choch_bull = choch_bull_age <= 5)
    Sweep valid: 3 bars   (recent_sweep_h = sweep_h_age <= 3)
    Grab window: 20 bars  (grab_h = ta.highest(high[1], 20))
    """
    SWING = 50

    def __init__(self):
        self.swing_high         = None
        self.swing_high_crossed = False
        self.swing_low          = None
        self.swing_low_crossed  = False
        self.swing_trend        = 0       # 0=undefined, 1=bull, -1=bear

        self.choch_bull_age = 999
        self.choch_bear_age = 999
        self.sweep_h_age    = 999
        self.sweep_l_age    = 999
        self._prev_leg      = None

    # ── internal helpers ──────────────────────────────────────────────

    def _detect_leg(self, df: pd.DataFrame, i: int):
        """
        Pine leg(size) at bar i.
        high[size] > ta.highest(size) → BEARISH_LEG (swing high s bars ago)
        low[size]  < ta.lowest(size)  → BULLISH_LEG  (swing low  s bars ago)
        """
        s = self.SWING
        if i < s:
            return None
        pivot_bar = df.iloc[i - s]
        after     = df.iloc[i - s + 1 : i + 1]   # bars that came AFTER the pivot
        if pivot_bar['high'] > after['high'].max():
            return 0   # BEARISH_LEG: swing high confirmed
        if pivot_bar['low'] < after['low'].min():
            return 1   # BULLISH_LEG: swing low confirmed
        return None

    # ── main entry ────────────────────────────────────────────────────

    def process_bar(self, df: pd.DataFrame, i: int) -> dict | None:
        """
        Process bar i sequentially.  Returns signal dict or None.
        Must be called for every bar in order.
        """
        if i < self.SWING + 25:
            return None

        bar   = df.iloc[i]
        close = bar['close'];  high = bar['high']
        low   = bar['low'];    open_ = bar['open']

        # ── 1. Pivot detection ────────────────────────────────────────
        leg = self._detect_leg(df, i)
        if leg is not None and leg != self._prev_leg:
            pivot_bar = df.iloc[i - self.SWING]
            if leg == 1:                          # bullish leg → swing LOW
                self.swing_low          = pivot_bar['low']
                self.swing_low_crossed  = False
            else:                                 # bearish leg → swing HIGH
                self.swing_high         = pivot_bar['high']
                self.swing_high_crossed = False
        self._prev_leg = leg

        # ── 2. CHoCH / BOS detection ──────────────────────────────────
        bull_choch_now = False
        bear_choch_now = False

        if self.swing_high is not None and not self.swing_high_crossed:
            if close > self.swing_high:
                self.swing_high_crossed = True
                if self.swing_trend == -1:        # was bearish → Bull CHoCH
                    bull_choch_now      = True
                    self.choch_bull_age = 0
                self.swing_trend = 1

        if self.swing_low is not None and not self.swing_low_crossed:
            if close < self.swing_low:
                self.swing_low_crossed = True
                if self.swing_trend == 1:         # was bullish → Bear CHoCH
                    bear_choch_now      = True
                    self.choch_bear_age = 0
                self.swing_trend = -1

        if not bull_choch_now:
            self.choch_bull_age = min(self.choch_bull_age + 1, 999)
        if not bear_choch_now:
            self.choch_bear_age = min(self.choch_bear_age + 1, 999)

        # ── 3. Sweep detection ─────────────────────────────────────────
        if i < 21:
            return None
        prev20  = df.iloc[i - 20 : i]             # grab_h/grab_l: previous 20 bars
        grab_h  = prev20['high'].max()
        grab_l  = prev20['low'].min()

        self.sweep_h_age = 0 if high > grab_h else min(self.sweep_h_age + 1, 999)
        self.sweep_l_age = 0 if low  < grab_l else min(self.sweep_l_age + 1, 999)

        recent_sweep_h    = self.sweep_h_age <= 3
        recent_sweep_l    = self.sweep_l_age <= 3
        recent_choch_bull = self.choch_bull_age <= 5
        recent_choch_bear = self.choch_bear_age <= 5

        # ── 4. ATR(14) ────────────────────────────────────────────────
        atr_14 = (df.iloc[max(0, i-14):i]['high'] - df.iloc[max(0, i-14):i]['low']).mean()
        if atr_14 <= 0:
            return None

        # ── 5. Candle confirmation ────────────────────────────────────
        sig_range = high - low
        if sig_range < 0.001:
            return None
        body = abs(close - open_)

        bull_conf = close > open_ and body / sig_range >= 0.5
        bear_conf = close < open_ and body / sig_range >= 0.5
        bull_wick = close > open_ and (high - close) < (close - low) * 0.35
        bear_wick = close < open_ and (close - low) < (open_ - close) * 0.35
        bull_ok   = bull_conf or bull_wick
        bear_ok   = bear_conf or bear_wick

        # ── 6. Grab conditions ─────────────────────────────────────────
        bull_grab_strong = recent_sweep_l and close > open_ and (close - low) / sig_range > 0.65
        bear_grab_strong = recent_sweep_h and close < open_ and (high - close) / sig_range > 0.65
        bull_grab        = recent_sweep_l and close > open_ and (close - low) / sig_range > 0.50
        bear_grab        = recent_sweep_h and close < open_ and (high - close) / sig_range > 0.50

        # ── 7. CHoCH + Sweep (★★★ premium) ────────────────────────────
        bull_choch_grab = recent_choch_bull and low < grab_l and close > open_ and (close - low) / sig_range > 0.5
        bear_choch_grab = recent_choch_bear and high > grab_h and close < open_ and (high - close) / sig_range > 0.5

        # ── 8. Structural sweep ────────────────────────────────────────
        struct_sweep_long  = recent_sweep_l and bull_grab
        struct_sweep_short = recent_sweep_h and bear_grab

        # ── 9. Extreme levels (50-bar) ─────────────────────────────────
        ext = df.iloc[max(0, i-50):i]
        at_extreme_low  = low  < ext['low'].min()
        at_extreme_high = high > ext['high'].max()

        # ── 10. Momentum filter ────────────────────────────────────────
        bars6 = df.iloc[max(0, i-6):i]
        drop_5  = bars6['high'].max() - close
        rally_5 = close - bars6['low'].min()
        momentum_bear = drop_5  > atr_14 * 2.5
        momentum_bull = rally_5 > atr_14 * 2.5

        # ── 11. Bias ───────────────────────────────────────────────────
        bias_bull = self.swing_trend == 1
        bias_bear = self.swing_trend == -1

        # H1 / H4 from M5 (12 bars = 1H, 48 bars = 4H)
        h1 = df.iloc[max(0, i-12):i]
        h4 = df.iloc[max(0, i-48):i]
        if len(h1) < 6 or len(h4) < 12:
            return None
        h1_mid  = (h1['high'].max() + h1['low'].min()) / 2
        h4_mid  = (h4['high'].max() + h4['low'].min()) / 2
        h1_bull = close > h1_mid
        h4_bull = close > h4_mid

        # OB: price near recent swing pivot
        in_bull_ob = self.swing_low  is not None and abs(close - self.swing_low)  < atr_14 * 2
        in_bear_ob = self.swing_high is not None and abs(close - self.swing_high) < atr_14 * 2

        # ── 12. Signal conditions (Pine lines 725–742) ─────────────────
        long_wt  = bias_bull and h1_bull and bull_ok
        long_sw  = bull_grab   and (h4_bull or struct_sweep_long)  and not momentum_bear
        long_sw2 = bull_grab_strong and (not momentum_bear or at_extreme_low)
        long_cs  = bull_choch_grab  and (not momentum_bear or at_extreme_low)
        long_sig = long_wt or long_sw or long_sw2 or long_cs

        short_wt  = bias_bear and not h1_bull and bear_ok
        short_sw  = bear_grab   and (not h4_bull or struct_sweep_short) and not momentum_bull
        short_sw2 = bear_grab_strong and (not momentum_bull or at_extreme_high)
        short_cs  = bear_choch_grab  and (not momentum_bull or at_extreme_high)
        short_sig = short_wt or short_sw or short_sw2 or short_cs

        if not long_sig and not short_sig:
            return None
        if long_sig and short_sig:       # ambiguous
            return None

        direction = "BUY" if long_sig else "SELL"

        # ── 13. Confluence score (Pine scale, max=6) ──────────────────
        if direction == "BUY":
            score = (
                (1 if in_bull_ob else 0) +
                (1 if h1_bull    else 0) +
                (1 if h4_bull    else 0) +
                (1 if bull_choch_grab    else 0) +
                (2 if struct_sweep_long  else 0)
            )
        else:
            score = (
                (1 if in_bear_ob     else 0) +
                (1 if not h1_bull    else 0) +
                (1 if not h4_bull    else 0) +
                (1 if bear_choch_grab    else 0) +
                (2 if struct_sweep_short else 0)
            )

        stars = "★★★" if score >= 3 else "★★" if score >= 2 else "★"

        # ── 14. SL / TP ────────────────────────────────────────────────
        rr_target = 2.0
        if direction == "BUY":
            sl_level = min(grab_l, low) - atr_14 * 0.2
            sl_dist  = max(close - sl_level, atr_14 * 0.5)
            tp_level = close + sl_dist * rr_target
        else:
            sl_level = max(grab_h, high) + atr_14 * 0.2
            sl_dist  = max(sl_level - close, atr_14 * 0.5)
            tp_level = close - sl_dist * rr_target

        sl_pips = round(sl_dist * 10, 1)
        tp_pips = round(sl_dist * rr_target * 10, 1)

        setup_type = ("CS" if (bull_choch_grab or bear_choch_grab) else
                      "SW" if (struct_sweep_long or struct_sweep_short) else "WT")

        reasons = []
        if bull_choch_grab or bear_choch_grab: reasons.append("CHoCH+Sweep")
        if struct_sweep_long or struct_sweep_short: reasons.append("Struct Sweep")
        if long_wt or short_wt:                    reasons.append("WithTrend")

        return {
            "signal":     direction,
            "score":      score,
            "stars":      stars,
            "setup_type": setup_type,
            "entry":      close,
            "sl":         sl_level,   # key ที่ simulate_trade ต้องการ
            "tp":         tp_level,
            "sl_pips":    sl_pips,
            "tp_pips":    tp_pips,
            "rr":         rr_target,
            "h4_bias":    "bull" if h4_bull else "bear",
            "reasons":    reasons,
        }


# ─── Backtest Runner ──────────────────────────────────────────────────────────

def run_pine_backtest(days=30, min_stars=2, min_rr=1.5, max_hold=48,
                      cooldown=20, session_open_only=True, news_filter=True):
    """
    min_stars: 1=★+, 2=★★+ (Pine default), 3=★★★ only
    """
    df = download_data(days)
    n  = len(df)

    if news_filter:
        print("📰 News filter: hardcoded ±30min @ 08:30 ET ทุกวันทำการ")

    mode_str = []
    if session_open_only: mode_str.append("session-open ±30min")
    if news_filter:       mode_str.append("news block 08:30 ET ±30min")
    stars_label = "★" * min_stars
    print(f"\n🔍 Pine SMC scan {n} candles | min={stars_label}+ | mode: {', '.join(mode_str) or 'full'}")
    print(f"   min_rr={min_rr}, cooldown={cooldown} bars\n")

    engine          = PineSMC()
    trades          = []
    last_signal_bar = -cooldown
    news_blocked    = 0
    warm            = PineSMC.SWING + 25

    for i in range(warm, n - 1):
        if (i - last_signal_bar) < cooldown:
            continue

        bar_time = df.index[i]

        if not is_tradeable_session(bar_time):
            continue
        if session_open_only and not is_session_open_bar(bar_time):
            continue
        if news_filter and is_near_us_news(bar_time):
            news_blocked += 1
            continue

        signal = engine.process_bar(df, i)
        if signal is None:
            continue

        # Star filter (Pine scale: ★=1, ★★=2, ★★★=3)
        if signal["score"] < min_stars:
            continue
        if signal["rr"] < min_rr:
            continue

        trade = simulate_trade(df, i + 1, signal, max_hold)
        trade["bar_idx"]  = i
        trade["bar_time"] = str(bar_time)[:16]
        trade["stars"]    = signal["stars"]
        trade["setup_type"] = signal.get("setup_type", "??")
        trade["score"]    = signal["score"]
        trade["h4_bias"]  = signal.get("h4_bias", "?")
        trade["reasons"]  = ", ".join(r for r in signal.get("reasons", []) if r)

        trades.append(trade)
        last_signal_bar = i

        outcome_sym = "✅" if trade["outcome"] == "win" else "❌"
        pnl_str     = f"{trade['pnl_pips']:+.1f}p"
        note        = f" | max_hold" if trade.get("note") == "max_hold" else ""
        print(
            f"  {outcome_sym} [{trade['bar_time']}] {signal['stars']} {signal['signal']:<4} "
            f"H4={signal['h4_bias']:<4} score={signal['score']} "
            f"| entry={signal['entry']:.2f} → {trade['exit']:.2f} "
            f"| {pnl_str} | RR={trade['rr_actual']:.2f}{note}"
        )

    if news_filter:
        print(f"\n📰 News filter blocked {news_blocked} bars")

    # ── Print summary ─────────────────────────────────────────────────
    _print_results(trades, days, min_stars)
    return trades


def _print_results(trades, days, min_stars):
    if not trades:
        print("\n⚠️  ไม่มี trade เลย")
        return

    wins   = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]

    win_rate   = len(wins) / len(trades) * 100 if trades else 0
    total_pips = sum(t["pnl_pips"] for t in trades)
    gross_win  = sum(t["pnl_pips"] for t in wins)
    gross_loss = abs(sum(t["pnl_pips"] for t in losses))
    pf         = round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf")
    avg_win    = gross_win  / len(wins)   if wins   else 0
    avg_loss   = gross_loss / len(losses) if losses else 0

    # Max drawdown
    running = peak = max_dd = 0
    for t in trades:
        running += t["pnl_pips"]
        peak     = max(peak, running)
        max_dd   = max(max_dd, peak - running)

    stars_label = "★" * min_stars
    print(f"""
╔══════════════════════════════════════════════════════╗
║     📊  PINE SMC BACKTEST — XAUUSD M5 ({days}d)       ║
╠══════════════════════════════════════════════════════╣
║  Min Stars:   {stars_label} (Pine scale: ★★★=score≥3)
║  Trades:   {len(trades):>3}  (Win: {len(wins)} | Loss: {len(losses)})
║  Win Rate:    {win_rate:.1f}%
║  Total P&L:   {total_pips:+.1f} pips
║  Profit Factor: {pf}
╠══════════════════════════════════════════════════════╣
║  Avg Win:     {avg_win:+.1f} pips
║  Avg Loss:    {avg_loss:+.1f} pips
║  Max Drawdown: {max_dd:.1f} pips
╠══════════════════════════════════════════════════════╣""")

    # By star tier
    for tier, lo, hi in [("★★★", 3, 99), ("★★ ", 2, 2), ("★  ", 0, 1)]:
        t_list = [t for t in trades if lo <= t["score"] <= hi]
        if not t_list:
            continue
        t_wins  = [t for t in t_list if t["outcome"] == "win"]
        t_pnl   = sum(t["pnl_pips"] for t in t_list)
        t_wr    = len(t_wins) / len(t_list) * 100
        print(f"║  {tier} (score {lo}-{hi if hi<99 else '+'}):"
              f"  {len(t_list):>3} trades | WR {t_wr:>4.0f}% | {t_pnl:+.1f}p")

    # By setup type
    print("╠══════════════════════════════════════════════════════╣")
    for stype in ["CS", "SW", "WT"]:
        t_list = [t for t in trades if t.get("setup_type") == stype]
        if not t_list:
            continue
        t_wins = [t for t in t_list if t["outcome"] == "win"]
        t_pnl  = sum(t["pnl_pips"] for t in t_list)
        t_wr   = len(t_wins) / len(t_list) * 100
        label  = {"CS":"CHoCH+Sweep","SW":"Struct Sweep","WT":"WithTrend"}[stype]
        print(f"║  {label:<14}: {len(t_list):>3} trades | WR {t_wr:>4.0f}% | {t_pnl:+.1f}p")

    verdict = ("✅ Profitable!" if pf >= 1.3 else
               "🟡 Marginal"   if pf >= 1.0 else
               "❌ Losing")
    print(f"╚══════════════════════════════════════════════════════╝\n  {verdict} (PF {pf})")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pine SMC Backtest")
    ap.add_argument("--days",       type=int,   default=30)
    ap.add_argument("--min-stars",  type=int,   default=2,   help="1=★+, 2=★★+, 3=★★★ only")
    ap.add_argument("--min-rr",     type=float, default=1.5)
    ap.add_argument("--max-hold",   type=int,   default=48)
    ap.add_argument("--cooldown",   type=int,   default=20)
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--no-news",    action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("  🌲 PINE SMC BACKTEST — XAUUSD M5")
    print("=" * 60)
    print(f"  Days: {args.days} | Min Stars: {'★'*args.min_stars}+ | Min RR: {args.min_rr}")
    print(f"  Session Filter: {'ON' if not args.no_session else 'OFF'}")
    print(f"  News Filter:    {'ON ±30min 08:30 ET' if not args.no_news else 'OFF'}")
    print("=" * 60)

    run_pine_backtest(
        days             = args.days,
        min_stars        = args.min_stars,
        min_rr           = args.min_rr,
        max_hold         = args.max_hold,
        cooldown         = args.cooldown,
        session_open_only= not args.no_session,
        news_filter      = not args.no_news,
    )
