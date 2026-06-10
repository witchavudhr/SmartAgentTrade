"""
SMC Engine — Python port of LuxAlgo SMC + Liquidity Sweep
Logic จาก:
  - LuxAlgo Smart Money Concepts (OB, BOS, CHoCH, FVG, EQH/EQL)
  - SMC_Complete_4.pine (Liquidity Sweep)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional


# ─── Data Classes ───────────────────────────────────────────────────

@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str  # 'high' or 'low'

@dataclass
class OrderBlock:
    top: float
    bottom: float
    kind: str       # 'bullish' or 'bearish'
    index: int
    mitigated: bool = False

@dataclass
class FairValueGap:
    top: float
    bottom: float
    kind: str       # 'bullish' or 'bearish'
    index: int
    filled: bool = False

@dataclass
class Structure:
    kind: str       # 'BOS' or 'CHoCH'
    direction: str  # 'bullish' or 'bearish'
    level: float
    index: int

@dataclass
class LiquiditySweep:
    kind: str       # 'sweep_high' or 'sweep_low'
    level: float
    index: int
    recovered: bool  # กลับเข้า range แล้วมั้ย

@dataclass
class SMCResult:
    # Swing Points
    swing_highs: list[SwingPoint] = field(default_factory=list)
    swing_lows: list[SwingPoint] = field(default_factory=list)

    # Structure
    structures: list[Structure] = field(default_factory=list)
    current_bias: str = 'neutral'  # 'bullish' / 'bearish' / 'neutral'

    # Order Blocks
    order_blocks: list[OrderBlock] = field(default_factory=list)

    # Fair Value Gaps
    fvgs: list[FairValueGap] = field(default_factory=list)

    # Liquidity Sweep
    sweeps: list[LiquiditySweep] = field(default_factory=list)

    # Equal Highs/Lows
    equal_highs: list[float] = field(default_factory=list)
    equal_lows: list[float] = field(default_factory=list)

    # Summary
    last_bos: Optional[Structure] = None
    last_choch: Optional[Structure] = None
    last_sweep: Optional[LiquiditySweep] = None
    active_ob: Optional[OrderBlock] = None


# ─── Core Engine ────────────────────────────────────────────────────

class SMCEngine:
    def __init__(self, swing_length: int = 5, ob_filter_atr: bool = True, eql_threshold: float = 0.1):
        self.swing_length = swing_length
        self.ob_filter_atr = ob_filter_atr
        self.eql_threshold = eql_threshold

    def analyze(self, df: pd.DataFrame) -> SMCResult:
        """
        วิเคราะห์ข้อมูล OHLCV ครบทุก SMC concept
        df ต้องมี columns: open, high, low, close, volume
        """
        result = SMCResult()

        # 1. หา Swing Points
        highs, lows = self._find_swings(df)
        result.swing_highs = highs
        result.swing_lows = lows

        # 2. หา BOS / CHoCH
        structures, bias = self._find_structure(df, highs, lows)
        result.structures = structures
        result.current_bias = bias
        result.last_bos = next((s for s in reversed(structures) if s.kind == 'BOS'), None)
        result.last_choch = next((s for s in reversed(structures) if s.kind == 'CHoCH'), None)

        # 3. หา Order Blocks
        obs = self._find_order_blocks(df, structures)
        result.order_blocks = obs
        result.active_ob = next((ob for ob in reversed(obs) if not ob.mitigated), None)

        # 4. หา Fair Value Gaps
        result.fvgs = self._find_fvg(df)

        # 5. หา Liquidity Sweep (จาก SMC_Complete_4)
        sweeps = self._find_liquidity_sweep(df, highs, lows)
        result.sweeps = sweeps
        result.last_sweep = sweeps[-1] if sweeps else None

        # 6. หา Equal Highs/Lows
        result.equal_highs, result.equal_lows = self._find_equal_hl(highs, lows)

        return result

    # ─── Swing Points ─────────────────────────────────────────────

    def _find_swings(self, df: pd.DataFrame):
        """หา pivot high/low ด้วย lookback window"""
        n = len(df)
        length = self.swing_length
        highs, lows = [], []

        for i in range(length, n - length):
            window_high = df['high'].iloc[i - length: i + length + 1]
            window_low = df['low'].iloc[i - length: i + length + 1]

            if df['high'].iloc[i] == window_high.max():
                highs.append(SwingPoint(index=i, price=df['high'].iloc[i], kind='high'))

            if df['low'].iloc[i] == window_low.min():
                lows.append(SwingPoint(index=i, price=df['low'].iloc[i], kind='low'))

        return highs, lows

    # ─── Structure (BOS / CHoCH) ──────────────────────────────────

    def _find_structure(self, df: pd.DataFrame, highs: list, lows: list):
        """
        BOS:   ราคาทะลุ swing high/low ในทิศทางเดิม
        CHoCH: ราคาทะลุ swing high/low ทวนทิศทาง
        """
        structures = []
        current_bias = 'neutral'

        close = df['close']
        n = len(df)

        last_swing_high = highs[0].price if highs else None
        last_swing_low = lows[0].price if lows else None
        high_idx = 0
        low_idx = 0

        for i in range(1, n):
            # อัพเดท swing ที่ผ่านมาแล้ว
            while high_idx < len(highs) - 1 and highs[high_idx + 1].index <= i:
                high_idx += 1
                last_swing_high = highs[high_idx].price

            while low_idx < len(lows) - 1 and lows[low_idx + 1].index <= i:
                low_idx += 1
                last_swing_low = lows[low_idx].price

            if last_swing_high is None or last_swing_low is None:
                continue

            # Bullish break
            if close.iloc[i] > last_swing_high:
                if current_bias == 'bearish':
                    structures.append(Structure('CHoCH', 'bullish', last_swing_high, i))
                else:
                    structures.append(Structure('BOS', 'bullish', last_swing_high, i))
                current_bias = 'bullish'

            # Bearish break
            elif close.iloc[i] < last_swing_low:
                if current_bias == 'bullish':
                    structures.append(Structure('CHoCH', 'bearish', last_swing_low, i))
                else:
                    structures.append(Structure('BOS', 'bearish', last_swing_low, i))
                current_bias = 'bearish'

        return structures, current_bias

    # ─── Order Blocks ─────────────────────────────────────────────

    def _find_order_blocks(self, df: pd.DataFrame, structures: list):
        """
        Bullish OB: แท่งแดงสุดท้ายก่อน Bullish BOS/CHoCH
        Bearish OB: แท่งเขียวสุดท้ายก่อน Bearish BOS/CHoCH
        """
        obs = []
        atr = self._atr(df)

        for struct in structures:
            idx = struct.index
            if idx < 2:
                continue

            # หาแท่งก่อน BOS
            if struct.direction == 'bullish':
                # หาแท่งแดง (bearish candle) ล่าสุดก่อน BOS
                for j in range(idx - 1, max(idx - 10, 0), -1):
                    if df['close'].iloc[j] < df['open'].iloc[j]:  # bearish candle
                        ob = OrderBlock(
                            top=df['open'].iloc[j],
                            bottom=df['low'].iloc[j],
                            kind='bullish',
                            index=j
                        )
                        # Filter: OB ต้องมีขนาดพอสมควร
                        if not self.ob_filter_atr or (ob.top - ob.bottom) > atr.iloc[j] * 0.3:
                            obs.append(ob)
                        break

            elif struct.direction == 'bearish':
                # หาแท่งเขียว (bullish candle) ล่าสุดก่อน BOS
                for j in range(idx - 1, max(idx - 10, 0), -1):
                    if df['close'].iloc[j] > df['open'].iloc[j]:  # bullish candle
                        ob = OrderBlock(
                            top=df['high'].iloc[j],
                            bottom=df['close'].iloc[j],
                            kind='bearish',
                            index=j
                        )
                        if not self.ob_filter_atr or (ob.top - ob.bottom) > atr.iloc[j] * 0.3:
                            obs.append(ob)
                        break

        # เช็ค mitigation (ราคากลับมาทะลุ OB)
        for ob in obs:
            for i in range(ob.index + 1, len(df)):
                if ob.kind == 'bullish' and df['low'].iloc[i] < ob.bottom:
                    ob.mitigated = True
                    break
                elif ob.kind == 'bearish' and df['high'].iloc[i] > ob.top:
                    ob.mitigated = True
                    break

        return obs

    # ─── Fair Value Gaps ─────────────────────────────────────────

    def _find_fvg(self, df: pd.DataFrame):
        """
        Bullish FVG: low[i] > high[i-2]  (gap ระหว่างแท่ง)
        Bearish FVG: high[i] < low[i-2]
        """
        fvgs = []
        for i in range(2, len(df)):
            # Bullish FVG
            if df['low'].iloc[i] > df['high'].iloc[i - 2]:
                fvgs.append(FairValueGap(
                    top=df['low'].iloc[i],
                    bottom=df['high'].iloc[i - 2],
                    kind='bullish',
                    index=i
                ))
            # Bearish FVG
            elif df['high'].iloc[i] < df['low'].iloc[i - 2]:
                fvgs.append(FairValueGap(
                    top=df['low'].iloc[i - 2],
                    bottom=df['high'].iloc[i],
                    kind='bearish',
                    index=i
                ))

        # เช็ค filled
        for fvg in fvgs:
            for i in range(fvg.index + 1, len(df)):
                if fvg.kind == 'bullish' and df['low'].iloc[i] < fvg.bottom:
                    fvg.filled = True
                    break
                elif fvg.kind == 'bearish' and df['high'].iloc[i] > fvg.top:
                    fvg.filled = True
                    break

        return fvgs

    # ─── Liquidity Sweep ─────────────────────────────────────────

    def _find_liquidity_sweep(self, df: pd.DataFrame, highs: list, lows: list):
        """
        Sweep High: wick ทะลุ swing high แต่ close กลับเข้ามา
        Sweep Low:  wick ทะลุ swing low  แต่ close กลับเข้ามา
        """
        sweeps = []
        n = len(df)

        for swing in highs:
            i = swing.index
            level = swing.price
            # หาแท่งหลัง swing ที่ wick ทะลุแต่ close ไม่ทะลุ
            for j in range(i + 1, min(i + 20, n)):
                if df['high'].iloc[j] > level and df['close'].iloc[j] <= level:
                    sweeps.append(LiquiditySweep(
                        kind='sweep_high',
                        level=level,
                        index=j,
                        recovered=True
                    ))
                    break

        for swing in lows:
            i = swing.index
            level = swing.price
            for j in range(i + 1, min(i + 20, n)):
                if df['low'].iloc[j] < level and df['close'].iloc[j] >= level:
                    sweeps.append(LiquiditySweep(
                        kind='sweep_low',
                        level=level,
                        index=j,
                        recovered=True
                    ))
                    break

        return sorted(sweeps, key=lambda x: x.index)

    # ─── Equal Highs / Lows ───────────────────────────────────────

    def _find_equal_hl(self, highs: list, lows: list):
        """หา swing high/low ที่ราคาใกล้กันมาก (EQH/EQL)"""
        threshold = self.eql_threshold

        equal_highs = []
        for i in range(len(highs)):
            for j in range(i + 1, len(highs)):
                diff = abs(highs[i].price - highs[j].price) / highs[i].price * 100
                if diff <= threshold:
                    equal_highs.append(highs[i].price)
                    break

        equal_lows = []
        for i in range(len(lows)):
            for j in range(i + 1, len(lows)):
                diff = abs(lows[i].price - lows[j].price) / lows[i].price * 100
                if diff <= threshold:
                    equal_lows.append(lows[i].price)
                    break

        return equal_highs, equal_lows

    # ─── Helpers ──────────────────────────────────────────────────

    def _atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(period).mean()


# ─── Session Detection ────────────────────────────────────────────

def get_session() -> dict:
    """
    ตรวจสอบ session ปัจจุบัน (Thai Time UTC+7)
    Port มาจาก SMC By Beam indicator
    """
    import pytz
    from datetime import datetime

    thai_tz = pytz.timezone("Asia/Bangkok")
    now = datetime.now(thai_tz)
    t = now.hour + now.minute / 60.0

    is_sydney  = 5.0  <= t < 14.0
    is_tokyo   = 7.0  <= t < 16.0
    is_london  = 14.0 <= t < 23.0
    is_ny      = t >= 19.0 or t < 4.0
    is_overlap = is_london and is_ny  # 19:00–23:00

    if is_overlap:
        name, emoji = "LN+NY Overlap", "🟣"
    elif is_ny:
        name, emoji = "New York", "🔴"
    elif is_london:
        name, emoji = "London", "🟡"
    elif is_tokyo:
        name, emoji = "Tokyo", "🟢"
    elif is_sydney:
        name, emoji = "Sydney", "🔵"
    else:
        name, emoji = "Off-Hours", "⚫"

    return {
        "session": name,
        "emoji": emoji,
        "tradeable": is_london or is_ny,  # เทรดเฉพาะ London + NY
        "is_london": is_london,
        "is_ny": is_ny,
        "is_overlap": is_overlap,
        "is_tokyo": is_tokyo,
        "is_sydney": is_sydney,
        "time_thai": now.strftime("%H:%M"),
    }


# ─── Advanced Signals (port from SMC By Beam) ─────────────────────

def advanced_signals(df: pd.DataFrame, result: SMCResult) -> dict:
    """
    คำนวณ signals ขั้นสูงจาก SMC By Beam indicator:
      - HTF Bias H1/H4 (price vs 20-bar midpoint — ไม่ต้องเรียก Claude)
      - Confirmation Candle (body/wick ratio)
      - Sweep Age tracking (1–3 bars)
      - CHoCH Age (fresh ≤ 5 bars)
      - Momentum Filter (2.5×ATR — ป้องกัน news spike)
      - Confluence Score ★★★
      - Signal Type A/B/C
    """
    n = len(df)
    if n < 50:
        return {"error": "ข้อมูลน้อยเกินไป"}

    # ── ATR ──────────────────────────────────────────────────
    high_low   = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close  = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]

    current_close = df['close'].iloc[-1]
    last = df.iloc[-1]
    sig_range = last['high'] - last['low']

    # ── HTF Bias via price/range midpoint ────────────────────
    # H1 = 12 M5 bars × 20 H1 candles = 240 bars
    # H4 = 48 M5 bars × 20 H4 candles = 960 bars
    h1_w = min(240, n)
    h4_w = min(960, n)
    h1_mid = (df['high'].iloc[-h1_w:].max() + df['low'].iloc[-h1_w:].min()) / 2
    h4_mid = (df['high'].iloc[-h4_w:].max() + df['low'].iloc[-h4_w:].min()) / 2
    h1_bull = current_close > h1_mid
    h4_bull = current_close > h4_mid

    # ── Confirmation Candle ───────────────────────────────────
    body   = abs(last['close'] - last['open'])
    upper  = last['high'] - max(last['close'], last['open'])
    lower  = min(last['close'], last['open']) - last['low']
    r      = max(sig_range, 0.001)

    bull_conf = last['close'] > last['open'] and body / r >= 0.5
    bull_wick = last['close'] > last['open'] and upper < lower * 0.35
    bear_conf = last['close'] < last['open'] and body / r >= 0.5
    bear_wick = last['close'] < last['open'] and lower < body * 0.35
    bull_candle = bull_conf or bull_wick
    bear_candle = bear_conf or bear_wick

    # ── Sweep Age (ทะลุ 20-bar high/low ใน 3 บาร์ที่ผ่านมา) ──
    lb = min(20, n - 1)
    sweep_l_age = sweep_h_age = 999
    for i in range(1, min(4, n)):
        lo = df['low'].iloc[-(lb + i):-i].min() if n > lb + i else df['low'].iloc[:-i].min()
        if df['low'].iloc[-i] < lo:
            sweep_l_age = i
            break
    for i in range(1, min(4, n)):
        hi = df['high'].iloc[-(lb + i):-i].max() if n > lb + i else df['high'].iloc[:-i].max()
        if df['high'].iloc[-i] > hi:
            sweep_h_age = i
            break

    recent_sweep_l = sweep_l_age <= 3
    recent_sweep_h = sweep_h_age <= 3

    # Grab strength (wick % of range)
    bull_wr = (last['close'] - last['low'])  / r if last['close'] > last['open'] else 0
    bear_wr = (last['high']  - last['close']) / r if last['close'] < last['open'] else 0

    bull_grab_strong = recent_sweep_l and last['close'] > last['open'] and bull_wr > 0.65
    bear_grab_strong = recent_sweep_h and last['close'] < last['open'] and bear_wr > 0.65
    bull_grab        = recent_sweep_l and last['close'] > last['open'] and bull_wr > 0.50
    bear_grab        = recent_sweep_h and last['close'] < last['open'] and bear_wr > 0.50

    # ── CHoCH Age ────────────────────────────────────────────
    lc = result.last_choch
    choch_age      = (n - 1 - lc.index) if lc else 999
    recent_choch_bull = bool(lc and lc.direction == 'bullish' and choch_age <= 5)
    recent_choch_bear = bool(lc and lc.direction == 'bearish' and choch_age <= 5)

    # CHoCH + Sweep = Type C (highest quality ★★★)
    bull_choch_grab = recent_choch_bull and recent_sweep_l and last['close'] > last['open'] and bull_wr > 0.5
    bear_choch_grab = recent_choch_bear and recent_sweep_h and last['close'] < last['open'] and bear_wr > 0.5

    # ── Momentum Filter (2.5×ATR ใน 5 บาร์) ─────────────────
    bk = min(6, n)
    drop_5  = df['high'].iloc[-bk:-1].max() - current_close
    rally_5 = current_close - df['low'].iloc[-bk:-1].min()
    momentum_bear = drop_5  > atr * 2.5   # ตกแรง → ห้าม Long
    momentum_bull = rally_5 > atr * 2.5   # ขึ้นแรง → ห้าม Short

    # ── In OB ────────────────────────────────────────────────
    aob = result.active_ob
    in_bull_ob = bool(aob and aob.kind == 'bullish' and aob.bottom <= current_close <= aob.top)
    in_bear_ob = bool(aob and aob.kind == 'bearish' and aob.bottom <= current_close <= aob.top)

    # ── Bias ─────────────────────────────────────────────────
    bias_bull = result.current_bias == 'bullish'
    bias_bear = result.current_bias == 'bearish'
    struct_sweep_long  = recent_sweep_l and bull_grab
    struct_sweep_short = recent_sweep_h and bear_grab

    # ── Confluence Score ★★★ ─────────────────────────────────
    long_score  = ((1 if in_bull_ob         else 0) +
                   (1 if h1_bull            else 0) +
                   (1 if h4_bull            else 0) +
                   (1 if bull_choch_grab    else 0) +
                   (2 if struct_sweep_long  else 0))
    short_score = ((1 if in_bear_ob          else 0) +
                   (1 if not h1_bull         else 0) +
                   (1 if not h4_bull         else 0) +
                   (1 if bear_choch_grab     else 0) +
                   (2 if struct_sweep_short  else 0))

    def stars(s): return "★★★" if s >= 3 else "★★" if s == 2 else "★"

    # ── Signal Types ─────────────────────────────────────────
    # A = with-trend | B = sweep | B2 = strong sweep | C = CHoCH+sweep
    long_wt  = bias_bull and h1_bull and bull_candle
    long_sw  = bull_grab and (h4_bull or struct_sweep_long) and not momentum_bear
    long_sw2 = bull_grab_strong and not momentum_bear
    long_cs  = bull_choch_grab and not momentum_bear
    long_sig = long_wt or long_sw or long_sw2 or long_cs

    short_wt  = bias_bear and not h1_bull and bear_candle
    short_sw  = bear_grab and (not h4_bull or struct_sweep_short) and not momentum_bull
    short_sw2 = bear_grab_strong and not momentum_bull
    short_cs  = bear_choch_grab and not momentum_bull
    short_sig = short_wt or short_sw or short_sw2 or short_cs

    signal_type = None
    if long_sig:
        signal_type = "C_LONG" if long_cs else "B2_LONG" if long_sw2 else "B_LONG" if long_sw else "A_LONG"
    elif short_sig:
        signal_type = "C_SHORT" if short_cs else "B2_SHORT" if short_sw2 else "B_SHORT" if short_sw else "A_SHORT"

    return {
        # HTF Bias (data-driven, ไม่ต้องเรียก Claude)
        "h1_bull": h1_bull,
        "h4_bull": h4_bull,
        "h1_mid":  round(h1_mid, 2),
        "h4_mid":  round(h4_mid, 2),

        # Candle
        "bull_candle": bull_candle,
        "bear_candle": bear_candle,

        # Sweep
        "recent_sweep_low":   recent_sweep_l,
        "recent_sweep_high":  recent_sweep_h,
        "sweep_l_age_bars":   sweep_l_age,
        "sweep_h_age_bars":   sweep_h_age,
        "bull_grab":          bull_grab,
        "bear_grab":          bear_grab,
        "bull_grab_strong":   bull_grab_strong,
        "bear_grab_strong":   bear_grab_strong,

        # CHoCH
        "recent_choch_bull":  recent_choch_bull,
        "recent_choch_bear":  recent_choch_bear,
        "choch_age_bars":     choch_age,
        "bull_choch_grab":    bull_choch_grab,
        "bear_choch_grab":    bear_choch_grab,

        # Momentum (ATR filter)
        "momentum_bear":  momentum_bear,
        "momentum_bull":  momentum_bull,
        "atr":            round(atr, 2),

        # In OB
        "in_bull_ob": in_bull_ob,
        "in_bear_ob": in_bear_ob,

        # Signal
        "long_signal":  long_sig,
        "short_signal": short_sig,
        "signal_type":  signal_type,
        "long_score":   long_score,
        "short_score":  short_score,
        "long_stars":   stars(long_score)  if long_sig  else None,
        "short_stars":  stars(short_score) if short_sig else None,
    }


# ─── Trend Follow Detector ───────────────────────────────────────

def detect_trend_follow(df: pd.DataFrame, result: SMCResult, h4_bias: str) -> dict:
    """
    หา Trend Continuation setup — เข้าตอนราคา pullback มา OB/FVG แล้ว bounce

    Bullish Trend (h4_bias='bull'):
      1. ราคา pull back เข้า Bull OB หรือ Bull FVG
      2. Bull confirmation candle (body ≥ 50%, wick ล่างใหญ่)
      3. ไม่ใช่ CHoCH Bear (structure ยังเป็น bull)
      Entry: OB top / FVG midpoint
      SL: OB bottom / FVG bottom
      TP: nearest swing high หรือ EQH

    Bearish Trend (h4_bias='bear'):
      ตรงกันข้าม — pullback เข้า Bear OB / Bear FVG แล้ว reject
    """
    if h4_bias not in ("bull", "bear"):
        return {"trend_signal": None, "trend_score": 0}

    n = len(df)
    if n < 30:
        return {"trend_signal": None, "trend_score": 0}

    adv = advanced_signals(df, result)
    if "error" in adv:
        return {"trend_signal": None, "trend_score": 0}

    last          = df.iloc[-1]
    current_price = last["close"]
    atr           = adv.get("atr", 1.0)

    score   = 0
    reasons = []

    if h4_bias == "bull":
        # ── Bullish Trend Follow ────────────────────────────────
        direction = "BUY"

        # 1. ราคาอยู่ใน Bull OB zone
        in_ob = False
        ob_top, ob_bot = None, None
        if result.active_ob and result.active_ob.kind == "bullish":
            ob = result.active_ob
            if ob.bottom <= current_price <= ob.top + atr * 0.3:
                score  += 3
                in_ob   = True
                ob_top  = ob.top
                ob_bot  = ob.bottom
                reasons.append(f"In Bull OB ({ob.bottom:.1f}-{ob.top:.1f})")

        # 2. ราคาอยู่ใน Bull FVG
        in_fvg = False
        if result.fvgs:
            bull_fvgs = [f for f in result.fvgs if f.kind == "bullish" and not f.filled]
            for fvg in bull_fvgs[-2:]:
                if fvg.bottom <= current_price <= fvg.top + atr * 0.2:
                    score  += 2 if not in_ob else 1
                    in_fvg  = True
                    reasons.append(f"In Bull FVG ({fvg.bottom:.1f}-{fvg.top:.1f})")
                    break

        # ถ้าไม่มี OB/FVG เลย → ไม่ใช่ trend follow setup
        if not in_ob and not in_fvg:
            return {"trend_signal": None, "trend_score": 0}

        # 3. Confirmation candle
        if adv.get("bull_candle"):
            score  += 2
            reasons.append("Bull Confirm Candle")
        elif adv.get("bull_grab"):
            score  += 1
            reasons.append("Bull Grab")

        # 4. Structure ยังเป็น bull (ไม่มี CHoCH bear ใหม่)
        if not adv.get("recent_choch_bear"):
            score  += 1
            reasons.append("Structure Bull")

        # 5. H1 aligned
        if adv.get("h1_bull"):
            score  += 1
            reasons.append("H1 Bull")

        # 6. ไม่มี bear momentum
        if not adv.get("momentum_bear"):
            score  += 1
            reasons.append("No Bear Momentum")

        # ── SL / TP ────────────────────────────────────────────
        if ob_bot:
            sl = round(ob_bot - atr * 0.15, 2)
        else:
            sl = round(current_price - atr * 1.0, 2)

        sl_distance = current_price - sl

        # TP: nearest swing high หรือ EQH ที่อยู่เหนือ entry
        eqh = sorted(result.equal_highs, reverse=True)
        tp_candidates = []
        if result.swing_highs:
            above = [s.price for s in result.swing_highs if s.price > current_price + atr * 0.5]
            if above:
                tp_candidates.append(min(above))
        if eqh:
            above_eq = [lv for lv in eqh if lv > current_price + atr * 0.5]
            if above_eq:
                tp_candidates.append(min(above_eq))
        min_tp = current_price + sl_distance * 2.0
        tp = round(max(min(tp_candidates), min_tp) if tp_candidates else min_tp, 2)

    else:
        # ── Bearish Trend Follow ────────────────────────────────
        direction = "SELL"

        in_ob = False
        ob_top, ob_bot = None, None
        if result.active_ob and result.active_ob.kind == "bearish":
            ob = result.active_ob
            if ob.bottom - atr * 0.3 <= current_price <= ob.top:
                score += 3
                in_ob  = True
                ob_top = ob.top
                ob_bot = ob.bottom
                reasons.append(f"In Bear OB ({ob.bottom:.1f}-{ob.top:.1f})")

        in_fvg = False
        if result.fvgs:
            bear_fvgs = [f for f in result.fvgs if f.kind == "bearish" and not f.filled]
            for fvg in bear_fvgs[-2:]:
                if fvg.bottom - atr * 0.2 <= current_price <= fvg.top:
                    score  += 2 if not in_ob else 1
                    in_fvg  = True
                    reasons.append(f"In Bear FVG ({fvg.bottom:.1f}-{fvg.top:.1f})")
                    break

        if not in_ob and not in_fvg:
            return {"trend_signal": None, "trend_score": 0}

        if adv.get("bear_candle"):
            score  += 2
            reasons.append("Bear Confirm Candle")
        elif adv.get("bear_grab"):
            score  += 1
            reasons.append("Bear Grab")

        if not adv.get("recent_choch_bull"):
            score  += 1
            reasons.append("Structure Bear")

        if not adv.get("h1_bull"):
            score  += 1
            reasons.append("H1 Bear")

        if not adv.get("momentum_bull"):
            score  += 1
            reasons.append("No Bull Momentum")

        if ob_top:
            sl = round(ob_top + atr * 0.15, 2)
        else:
            sl = round(current_price + atr * 1.0, 2)

        sl_distance = sl - current_price

        eql = sorted(result.equal_lows)
        tp_candidates = []
        if result.swing_lows:
            below = [s.price for s in result.swing_lows if s.price < current_price - atr * 0.5]
            if below:
                tp_candidates.append(max(below))
        if eql:
            below_eq = [lv for lv in eql if lv < current_price - atr * 0.5]
            if below_eq:
                tp_candidates.append(max(below_eq))
        min_tp = current_price - sl_distance * 2.0
        tp = round(min(max(tp_candidates), min_tp) if tp_candidates else min_tp, 2)

    if score < 4:
        return {"trend_signal": None, "trend_score": score}

    sl_pips = round(abs(current_price - sl) * 10, 1)
    tp_pips = round(abs(current_price - tp) * 10, 1)
    rr      = round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0

    stars = "★★★" if score >= 7 else "★★" if score >= 5 else "★"

    return {
        "trend_signal":  direction,
        "trend_score":   score,
        "trend_stars":   stars,
        "trend_reasons": reasons,
        "h4_bias":       h4_bias,
        "stop_loss":     sl,
        "take_profit":   tp,
        "sl_pips":       sl_pips,
        "tp_pips":       tp_pips,
        "rr":            rr,
        "setup_type":    "TREND_FOLLOW",
    }


# ─── Reversal Detector ───────────────────────────────────────────

def detect_reversal(df: pd.DataFrame, result: SMCResult) -> dict:
    """
    หาจุดกลับตัวโดยเฉพาะ — ไม่ใช่แค่ signal ทั่วไป

    Bullish Reversal (ราคากำลังจะกลับขึ้น):
      1. Sweep below EQL / Weak Low / OB bottom  ← ดูด liquidity ก่อนกลับ
      2. CHoCH bullish บน M5 (เปลี่ยนจาก bear → bull)
      3. Confirmation: bull candle wick ใหญ่ + body ≥50%
      4. ไม่มี momentum ลงแรง (momentum_bear = False)

    Bearish Reversal (ราคากำลังจะกลับลง):
      1. Sweep above EQH / Strong High / OB top
      2. CHoCH bearish บน M5
      3. Confirmation: bear candle wick ใหญ่ + body ≥50%
      4. ไม่มี momentum ขึ้นแรง

    Reversal Score (0-10):
      CHoCH fresh (≤5 bars): +3  ← หัวใจของการกลับตัว
      Sweep occurred:        +2  ← ดูด liquidity แล้ว
      Confirmation candle:   +2  ← ยืนยันการปฏิเสธ
      In OB zone:            +1  ← เพิ่มความน่าเชื่อถือ
      H1 aligned:            +1  ← HTF เริ่มยืนยัน
      No momentum against:   +1  ← ปลอดภัยจาก news spike
    """
    n = len(df)
    if n < 20:
        return {"reversal_signal": None, "reversal_score": 0}

    # ดึงข้อมูลจาก advanced_signals
    adv = advanced_signals(df, result)
    if "error" in adv:
        return {"reversal_signal": None, "reversal_score": 0}

    last = df.iloc[-1]
    current_price = last['close']
    sig_range = last['high'] - last['low']
    atr = adv.get("atr", 1.0)

    # ── ระบุ Key Levels ที่อาจโดน Sweep ───────────────────────
    # Weak Low = swing low ล่าสุดที่ structure เป็น bearish
    # Weak High = swing high ล่าสุดที่ structure เป็น bullish
    # EQL = equal lows (ดูด liquidity zone)
    # EQH = equal highs

    eql_levels = sorted(result.equal_lows)
    eqh_levels = sorted(result.equal_highs, reverse=True)

    # Weak Low/High จาก swing points
    weak_low  = min([s.price for s in result.swing_lows[-5:]], default=None) if result.swing_lows else None
    weak_high = max([s.price for s in result.swing_highs[-5:]], default=None) if result.swing_highs else None

    # ── Bullish Reversal Score ────────────────────────────────
    bull_score = 0
    bull_reasons = []

    # 1. CHoCH bullish fresh (สำคัญสุด)
    if adv.get("recent_choch_bull"):
        bull_score += 3
        bull_reasons.append(f"CHoCH Bull ({adv['choch_age_bars']} bars ago)")

    # 2. Sweep low (ดูด liquidity ก่อนกลับ)
    if adv.get("recent_sweep_low"):
        bull_score += 2
        bull_reasons.append(f"Sweep Low ({adv['sweep_l_age_bars']} bars ago)")
    elif weak_low and abs(current_price - weak_low) < atr * 0.5:
        bull_score += 1
        bull_reasons.append(f"Near Weak Low {weak_low:.1f}")

    # EQL swept
    if eql_levels and any(abs(current_price - lv) < atr * 0.3 for lv in eql_levels[-3:]):
        bull_score += 1
        bull_reasons.append("Near EQL")

    # 3. Confirmation candle
    if adv.get("bull_candle"):
        bull_score += 2
        bull_reasons.append("Bull Confirm Candle")
    elif adv.get("bull_grab"):
        bull_score += 1
        bull_reasons.append("Bull Grab")

    # 4. In bull OB
    if adv.get("in_bull_ob"):
        bull_score += 1
        bull_reasons.append("In Bull OB")

    # 5. H1 aligned
    if adv.get("h1_bull"):
        bull_score += 1
        bull_reasons.append("H1 Bull")

    # 6. No bear momentum (ปลอดภัย)
    if not adv.get("momentum_bear"):
        bull_score += 1
        bull_reasons.append("No Momentum Block")

    # ── Bearish Reversal Score ────────────────────────────────
    bear_score = 0
    bear_reasons = []

    if adv.get("recent_choch_bear"):
        bear_score += 3
        bear_reasons.append(f"CHoCH Bear ({adv['choch_age_bars']} bars ago)")

    if adv.get("recent_sweep_high"):
        bear_score += 2
        bear_reasons.append(f"Sweep High ({adv['sweep_h_age_bars']} bars ago)")
    elif weak_high and abs(current_price - weak_high) < atr * 0.5:
        bear_score += 1
        bear_reasons.append(f"Near Weak High {weak_high:.1f}")

    if eqh_levels and any(abs(current_price - lv) < atr * 0.3 for lv in eqh_levels[:3]):
        bear_score += 1
        bear_reasons.append("Near EQH")

    if adv.get("bear_candle"):
        bear_score += 2
        bear_reasons.append("Bear Confirm Candle")
    elif adv.get("bear_grab"):
        bear_score += 1
        bear_reasons.append("Bear Grab")

    if adv.get("in_bear_ob"):
        bear_score += 1
        bear_reasons.append("In Bear OB")

    if not adv.get("h1_bull"):
        bear_score += 1
        bear_reasons.append("H1 Bear")

    if not adv.get("momentum_bull"):
        bear_score += 1
        bear_reasons.append("No Momentum Block")

    # ── ตัดสิน ────────────────────────────────────────────────
    def grade(s):
        if s >= 7: return "★★★", "HIGH"
        if s >= 5: return "★★",  "MODERATE"
        if s >= 3: return "★",   "LOW"
        return None, None

    # เลือก direction ที่ score สูงกว่า (ต้องอย่างน้อย 3)
    reversal_signal  = None
    reversal_score   = 0
    reversal_stars   = None
    reversal_grade   = None
    reversal_reasons = []

    if bull_score >= 3 and bull_score >= bear_score:
        reversal_signal  = "BUY"
        reversal_score   = bull_score
        reversal_stars, reversal_grade = grade(bull_score)
        reversal_reasons = bull_reasons

        # คำนวณ entry zone จาก active OB หรือ current price
        aob = result.active_ob
        if aob and aob.kind == "bullish":
            entry_low  = aob.bottom
            entry_high = aob.top
        else:
            entry_low  = round(current_price - atr * 0.3, 2)
            entry_high = round(current_price + atr * 0.3, 2)

        # SL: ใต้ swing low ล่าสุด (15 bars) + buffer เล็กน้อย
        recent_lows = [s.price for s in result.swing_lows[-5:]
                       if (n - 1 - s.index) <= 15] if result.swing_lows else []
        if recent_lows:
            nearest_sl = min(recent_lows)
            sl_dist    = current_price - nearest_sl
            # ถ้า swing low ไกลเกิน ATR×2.5 ใช้ ATR×1.0 แทน
            sl = round(current_price - min(sl_dist + atr * 0.1, atr * 2.5), 2)
        else:
            sl = round(current_price - atr * 1.0, 2)

        sl_distance = current_price - sl

        # TP: หา swing high ที่ใกล้ที่สุดที่อยู่ เหนือ entry (ไม่ใช่ max ทั้งหมด)
        tp_candidates = []
        if result.swing_highs:
            above = [s.price for s in result.swing_highs if s.price > current_price + atr * 0.5]
            if above:
                tp_candidates.append(min(above))   # nearest swing high above entry
        if eqh_levels:
            above_eq = [lv for lv in eqh_levels if lv > current_price + atr * 0.5]
            if above_eq:
                tp_candidates.append(min(above_eq))
        # TP ต้องให้ RR ≥ 2.0 เสมอ
        min_tp = current_price + sl_distance * 2.0
        if tp_candidates:
            tp = round(max(min(tp_candidates), min_tp), 2)  # ใกล้ที่สุด แต่ไม่ต่ำกว่า 2:1
        else:
            tp = round(current_price + sl_distance * 2.0, 2)  # fallback 2:1

    elif bear_score >= 3 and bear_score > bull_score:
        reversal_signal  = "SELL"
        reversal_score   = bear_score
        reversal_stars, reversal_grade = grade(bear_score)
        reversal_reasons = bear_reasons

        aob = result.active_ob
        if aob and aob.kind == "bearish":
            entry_low  = aob.bottom
            entry_high = aob.top
        else:
            entry_low  = round(current_price - atr * 0.3, 2)
            entry_high = round(current_price + atr * 0.3, 2)

        # SL: เหนือ swing high ล่าสุด (15 bars) + buffer
        recent_highs = [s.price for s in result.swing_highs[-5:]
                        if (n - 1 - s.index) <= 15] if result.swing_highs else []
        if recent_highs:
            nearest_sl = max(recent_highs)
            sl_dist    = nearest_sl - current_price
            sl = round(current_price + min(sl_dist + atr * 0.1, atr * 2.5), 2)
        else:
            sl = round(current_price + atr * 1.0, 2)

        sl_distance = sl - current_price

        # TP: หา swing low ที่ใกล้ที่สุดที่อยู่ ใต้ entry
        tp_candidates = []
        if result.swing_lows:
            below = [s.price for s in result.swing_lows if s.price < current_price - atr * 0.5]
            if below:
                tp_candidates.append(max(below))   # nearest swing low below entry
        if eql_levels:
            below_eq = [lv for lv in eql_levels if lv < current_price - atr * 0.5]
            if below_eq:
                tp_candidates.append(max(below_eq))
        min_tp = current_price - sl_distance * 2.0
        if tp_candidates:
            tp = round(min(max(tp_candidates), min_tp), 2)
        else:
            tp = round(current_price - sl_distance * 2.0, 2)

    else:
        return {
            "reversal_signal": None,
            "reversal_score": max(bull_score, bear_score),
            "bull_score": bull_score,
            "bear_score": bear_score,
            "atr": atr,
        }

    sl_pips = round(abs(current_price - sl) * 10, 1)
    tp_pips = round(abs(current_price - tp) * 10, 1)
    rr      = round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0

    return {
        "reversal_signal":  reversal_signal,
        "reversal_score":   reversal_score,
        "reversal_stars":   reversal_stars,
        "reversal_grade":   reversal_grade,
        "reversal_reasons": reversal_reasons,
        "bull_score":       bull_score,
        "bear_score":       bear_score,
        "entry_zone":       [entry_low, entry_high],
        "stop_loss":        sl,
        "take_profit":      tp,
        "sl_pips":          sl_pips,
        "tp_pips":          tp_pips,
        "rr":               rr,
        "weak_low":         weak_low,
        "weak_high":        weak_high,
        "eql_levels":       eql_levels[-3:],
        "eqh_levels":       eqh_levels[:3],
        "atr":              atr,
    }


# ─── Format Summary ───────────────────────────────────────────────

def summarize(result: SMCResult, current_price: float, df: pd.DataFrame = None) -> dict:
    """
    แปลง SMCResult เป็น dict สรุปสำหรับส่งให้ Claude
    ถ้าส่ง df มาด้วย จะรวม advanced_signals + session อัตโนมัติ
    """
    last_sweep = result.last_sweep
    active_ob  = result.active_ob
    last_bos   = result.last_bos
    last_choch = result.last_choch

    # เช็คว่า price อยู่ใน OB มั้ย
    in_ob = False
    if active_ob and active_ob.bottom <= current_price <= active_ob.top:
        in_ob = True

    # FVG ที่ยังไม่ถูก fill
    open_fvgs   = [f for f in result.fvgs if not f.filled]
    nearest_fvg = (min(open_fvgs, key=lambda f: abs((f.top + f.bottom) / 2 - current_price))
                   if open_fvgs else None)

    summary = {
        "current_price": current_price,
        "bias": result.current_bias,

        "last_bos": {
            "direction": last_bos.direction,
            "level": last_bos.level,
        } if last_bos else None,

        "last_choch": {
            "direction": last_choch.direction,
            "level": last_choch.level,
        } if last_choch else None,

        "last_sweep": {
            "kind": last_sweep.kind,
            "level": last_sweep.level,
            "recovered": last_sweep.recovered,
        } if last_sweep else None,

        "active_ob": {
            "kind": active_ob.kind,
            "top": active_ob.top,
            "bottom": active_ob.bottom,
            "in_ob": in_ob,
        } if active_ob else None,

        "nearest_fvg": {
            "kind": nearest_fvg.kind,
            "top": nearest_fvg.top,
            "bottom": nearest_fvg.bottom,
        } if nearest_fvg else None,

        "equal_highs":       result.equal_highs[-3:] if result.equal_highs else [],
        "equal_lows":        result.equal_lows[-3:]  if result.equal_lows  else [],
        "swing_high_count":  len(result.swing_highs),
        "swing_low_count":   len(result.swing_lows),
        "total_structures":  len(result.structures),
        "total_sweeps":      len(result.sweeps),
    }

    # ── Advanced Signals + Reversal (จาก df) ─────────────────
    if df is not None:
        try:
            adv  = advanced_signals(df, result)
            sess = get_session()
            rev  = detect_reversal(df, result)

            summary["session"]   = sess
            summary["advanced"]  = adv
            summary["reversal"]  = rev

            # shortcuts ที่ใช้บ่อย
            summary["signal_type"]       = adv.get("signal_type")
            summary["long_stars"]        = adv.get("long_stars")
            summary["short_stars"]       = adv.get("short_stars")
            summary["h1_bull"]           = adv.get("h1_bull")
            summary["h4_bull"]           = adv.get("h4_bull")
            summary["momentum_bear"]     = adv.get("momentum_bear")
            summary["momentum_bull"]     = adv.get("momentum_bull")
            summary["tradeable_session"] = sess.get("tradeable", True)

            # reversal shortcuts
            summary["reversal_signal"] = rev.get("reversal_signal")
            summary["reversal_stars"]  = rev.get("reversal_stars")
            summary["reversal_score"]  = rev.get("reversal_score", 0)
        except Exception:
            pass

    return summary
