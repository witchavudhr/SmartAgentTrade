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
    wick_extreme: float = 0.0  # จุดสุดของ wick จริง (low สำหรับ sweep_low, high สำหรับ sweep_high)

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
    active_ob: Optional[OrderBlock] = None       # most recent non-mitigated (any kind)
    active_bull_ob: Optional[OrderBlock] = None  # most recent non-mitigated bullish OB
    active_bear_ob: Optional[OrderBlock] = None  # most recent non-mitigated bearish OB


# ─── Core Engine ────────────────────────────────────────────────────

class SMCEngine:
    def __init__(self, swing_length: int = 5, ob_length: int = 5,
                 ob_filter_atr: bool = True, eql_threshold: float = 0.1,
                 ob_mitigation: str = 'highlow'):
        self.swing_length = swing_length
        self.ob_length = ob_length      # LuxAlgo internal OB uses size=5 (hardcoded)
        self.ob_filter_atr = ob_filter_atr
        self.eql_threshold = eql_threshold
        # 'highlow' = LuxAlgo default (wick ทะลุก็ลบ) | 'close' = ต้อง body close ทะลุ (OB อยู่นานกว่า)
        self.ob_mitigation = ob_mitigation

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
        # เลือก OB ที่ใกล้ราคาปัจจุบันที่สุด (ไม่ใช่ most recently formed)
        # ราคาต้องออกจาก OB อย่างน้อย MIN_OB_DEPARTURE pts ถึงจะ confirm (ไม่งั้น OB ยังไม่ชัด)
        _price = df['close'].iloc[-1]
        MIN_OB_DEPARTURE = 3.0  # 30 pts — ราคาต้องพ้น OB edge อย่างน้อย 30 pts
        _bull_all = [ob for ob in obs if not ob.mitigated and ob.kind == 'bullish']
        _bear_all = [ob for ob in obs if not ob.mitigated and ob.kind == 'bearish']
        _bull_below = [ob for ob in _bull_all if ob.top <= _price - MIN_OB_DEPARTURE]
        _bear_above = [ob for ob in _bear_all if ob.bottom >= _price + MIN_OB_DEPARTURE]
        result.active_bull_ob = (min(_bull_below, key=lambda ob: _price - ob.top)
                                 if _bull_below else
                                 min(_bull_all, key=lambda ob: abs(ob.top - _price)) if _bull_all else None)
        result.active_bear_ob = (min(_bear_above, key=lambda ob: ob.bottom - _price)
                                 if _bear_above else
                                 min(_bear_all, key=lambda ob: abs(ob.bottom - _price)) if _bear_all else None)
        result.active_ob = result.active_bull_ob or result.active_bear_ob

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

    def _find_order_blocks(self, df: pd.DataFrame, structures: list = None):
        """
        OB detection — port จาก LuxAlgo SMC internal order blocks (size=5):

        Algorithm (เหมือน LuxAlgo Pine ทุกบรรทัด):
          1. ATR(200) Wilder's RMA + parsedHigh/parsedLow (swap ถ้า high-vol bar)
          2. leg() alternating: high[size]>ta.highest(size) → BEARISH_LEG,
                                low[size]<ta.lowest(size)  → BULLISH_LEG
             — pivot จะ update เฉพาะตอน leg เปลี่ยนทิศ (not every new high/low!)
          3. Bullish OB: ta.crossover(close, swingHigh) → หา argmin(parsedLows) ใน leg
          4. Bearish OB: ta.crossunder(close, swingLow) → หา argmax(parsedHighs) ใน leg
          5. Mitigation HIGHLOW: Bear ลบเมื่อ high>top, Bull ลบเมื่อ low<bottom
        """
        n = len(df)
        size = self.ob_length   # LuxAlgo internal OB: hardcoded size=5
        if n < size * 2 + 2:
            return []

        high  = df['high'].to_numpy()
        low   = df['low'].to_numpy()
        close = df['close'].to_numpy()

        # ── 1. ATR(200) Wilder's RMA ──────────────────────────────────────────
        tr = np.maximum.reduce([
            high[1:] - low[1:],
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ])
        tr_full = np.concatenate([[high[0] - low[0]], tr])
        alpha = 1.0 / 200
        atr = np.zeros(n)
        atr[0] = tr_full[0]
        for _j in range(1, n):
            atr[_j] = alpha * tr_full[_j] + (1.0 - alpha) * atr[_j - 1]

        rng = high - low
        high_vol    = rng >= (2.0 * atr)
        parsed_high = np.where(high_vol, low,  high)   # highVol ? low : high
        parsed_low  = np.where(high_vol, high, low)    # highVol ? high : low

        # ── 2. LuxAlgo alternating leg detection ─────────────────────────────
        # leg() ใน Pine: ถ้า high[size]>ta.highest(size) → BEARISH_LEG
        #                ถ้า low[size]<ta.lowest(size)   → BULLISH_LEG
        # pivot จะ update เฉพาะตอน ta.change(leg) != 0 (leg เปลี่ยนทิศ)
        BEARISH_LEG = 0
        BULLISH_LEG = 1

        obs: list[OrderBlock] = []
        leg = BEARISH_LEG   # Pine: var leg = 0
        sh_level = sh_idx = None;  sh_crossed = False
        sl_level = sl_idx = None;  sl_crossed = False

        for i in range(size, n):
            p = i - size   # bar ที่กำลัง confirm (size bars ก่อน i)

            # Pine: high[size] > ta.highest(size) — ta.highest(size) = max(high[0..size-1])
            right_h = high[p + 1: i + 1]   # size bars: high[p+1] … high[i]
            right_l = low[p + 1:  i + 1]

            new_leg_high = len(right_h) == size and high[p] > right_h.max()
            new_leg_low  = len(right_l) == size and low[p]  < right_l.min()

            prev_leg = leg
            if new_leg_high:
                leg = BEARISH_LEG
            elif new_leg_low:
                leg = BULLISH_LEG

            if leg != prev_leg:
                # leg เปลี่ยนทิศ → record pivot
                if leg == BULLISH_LEG:
                    # BEARISH→BULLISH: new swing LOW at bar p
                    sl_level, sl_idx, sl_crossed = low[p], p, False
                else:
                    # BULLISH→BEARISH: new swing HIGH at bar p
                    sh_level, sh_idx, sh_crossed = high[p], p, False

            # ── Bullish OB: ta.crossover(close, swingHigh) ──────────
            if (sh_level is not None and not sh_crossed
                    and close[i] > sh_level
                    and (i == 0 or close[i - 1] <= sh_level)):
                leg_lows = parsed_low[sh_idx: i]   # slice [pivot, BOS) ไม่รวม bar i
                if len(leg_lows) > 0:
                    ob_idx = sh_idx + int(np.argmin(leg_lows))
                    obs.append(OrderBlock(
                        top=float(high[ob_idx]),
                        bottom=float(low[ob_idx]),
                        kind='bullish',
                        index=int(ob_idx),
                    ))
                sh_crossed = True

            # ── Bearish OB: ta.crossunder(close, swingLow) ──────────
            if (sl_level is not None and not sl_crossed
                    and close[i] < sl_level
                    and (i == 0 or close[i - 1] >= sl_level)):
                leg_highs = parsed_high[sl_idx: i]  # slice [pivot, BOS) ไม่รวม bar i
                if len(leg_highs) > 0:
                    ob_idx = sl_idx + int(np.argmax(leg_highs))
                    obs.append(OrderBlock(
                        top=float(high[ob_idx]),
                        bottom=float(low[ob_idx]),
                        kind='bearish',
                        index=int(ob_idx),
                    ))
                sl_crossed = True

        # ── 5. Mitigation ──────────────────────────────────────────────────
        bull_src = low  if self.ob_mitigation == 'highlow' else close
        bear_src = high if self.ob_mitigation == 'highlow' else close
        for ob in obs:
            for i in range(ob.index + 1, n):
                if ob.kind == 'bullish' and bull_src[i] < ob.bottom:
                    ob.mitigated = True
                    break
                elif ob.kind == 'bearish' and bear_src[i] > ob.top:
                    ob.mitigated = True
                    break

        # เก็บแค่ 5 OB ล่าสุดต่อฝั่ง (เหมือน internalOrderBlocksSizeInput=5)
        bulls = [o for o in obs if o.kind == 'bullish'][-5:]
        bears = [o for o in obs if o.kind == 'bearish'][-5:]
        return bulls + bears

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
            # สแกนทุก bar หลัง swing (ไม่จำกัด 20) เพราะ sweep อาจเกิดช้ามาก
            for j in range(i + 1, n):
                if df['high'].iloc[j] > level:
                    sweep_idx = None
                    wick_high = df['high'].iloc[j]  # track จุดสูงสุดของ wick จริง
                    # ขยาย recovery window เป็น 8 bars รองรับ slow recovery หลัง sweep
                    for k in range(j, min(j + 8, n)):
                        wick_high = max(wick_high, df['high'].iloc[k])
                        if df['close'].iloc[k] <= level:
                            sweep_idx = k
                            break
                    if sweep_idx is not None:
                        sweeps.append(LiquiditySweep(
                            kind='sweep_high',
                            level=level,
                            index=sweep_idx,
                            recovered=True,
                            wick_extreme=round(wick_high, 2),
                        ))
                    break  # หยุดที่ sweep bar แรก ไม่ว่าจะ recover หรือไม่

        for swing in lows:
            i = swing.index
            level = swing.price
            # สแกนทุก bar หลัง swing — Weak Low อาจถูก sweep ช้ามาก (หลาย session)
            for j in range(i + 1, n):
                if df['low'].iloc[j] < level:
                    sweep_idx = None
                    wick_low = df['low'].iloc[j]  # track จุดต่ำสุดของ wick จริง
                    # ขยาย recovery window เป็น 8 bars
                    for k in range(j, min(j + 8, n)):
                        wick_low = min(wick_low, df['low'].iloc[k])
                        if df['close'].iloc[k] >= level:
                            sweep_idx = k
                            break
                    if sweep_idx is not None:
                        sweeps.append(LiquiditySweep(
                            kind='sweep_low',
                            level=level,
                            index=sweep_idx,
                            recovered=True,
                            wick_extreme=round(wick_low, 2),
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

    market_closed, holiday_name = is_market_holiday()
    is_late_ny = (t >= 0.0 and t < 4.0)  # 00:00–04:00 Thai — เหลือแค่ NY liquidity ต่ำ

    return {
        "session": name,
        "emoji": emoji,
        "tradeable": (6.5 <= t < 19.0) and not market_closed,
        "market_closed": market_closed,
        "holiday_name": holiday_name,
        "is_london": is_london,
        "is_ny": is_ny,
        "is_overlap": is_overlap,
        "is_tokyo": is_tokyo,
        "is_sydney": is_sydney,
        "is_late_ny": is_late_ny,
        "time_thai": now.strftime("%H:%M"),
    }


def classify_liquidity(result: "SMCResult", current_price: float, timeframe: str = "M5",
                        df: pd.DataFrame = None, swing_highs: list = None,
                        swing_lows: list = None) -> dict:
    """
    จัด rank liquidity pools:
      BSL (Buy-Side Liquidity)  = เหนือราคา = stop ของคน Short
      SSL (Sell-Side Liquidity) = ใต้ราคา   = stop ของคน Long

    คืน dict พร้อม bsl_pools, ssl_pools, nearest_bsl, nearest_ssl
    แต่ละ pool: {level, type, size, dist_pts, swept, time, age_bars}
    time/age_bars มีค่าเมื่อส่ง df เข้ามา — ใช้ trace กลับไปดูแท่งจริงบนกราฟได้
    ว่า swing point นี้เกิดขึ้นเมื่อไหร่

    swing_highs/swing_lows (optional): ใช้ override result.swing_highs/lows —
    สำหรับ pool detection เราอยากใช้ swing length สั้นกว่า struct (50 bars)
    ตรงกับ Pine indicator (i_pool_swing=15) ไม่งั้นต้องรอยืนยันนาน 12+ ชม. ก่อน
    pool ใหม่จะโผล่ ทั้งที่กราฟ (indicator) เห็นเร็วกว่ามาก — ถ้าไม่ส่งมาจะ fallback
    ไปใช้ result.swing_highs/lows (struct length) เหมือนเดิม
    """
    swing_highs = swing_highs if swing_highs is not None else result.swing_highs
    swing_lows  = swing_lows  if swing_lows  is not None else result.swing_lows
    def _bar_meta(idx):
        if df is None or idx is None or idx < 0 or idx >= len(df):
            return None, None
        age = (len(df) - 1 - idx)
        t = df.index[idx]
        return (t.strftime("%Y-%m-%d %H:%M") if hasattr(t, "strftime") else str(t)), age

    def _breached(idx, level, kind):
        """
        เช็คว่าราคาเคยวิ่งทะลุ level นี้ไปแล้วมั้ย (ไม่ว่าจะมี sweep+reversal
        ตามนิยาม _find_liquidity_sweep หรือไม่) — ถ้าราคาแค่ทะลุผ่านตรงๆ ต่อเนื่อง
        ไม่เด้งกลับใน 8 แท่ง (เช่น trending strong ผ่านไปเลย) _find_liquidity_sweep
        จะไม่นับเป็น sweep เลย ทำให้ pool ค้างสถานะ "ยังไม่ swept" ทั้งที่ order
        ที่วางรอไว้ตรงนั้นถูก execute ไปแล้วจริงๆ ตั้งแต่ราคาแตะระดับนั้นครั้งแรก
        """
        if df is None or idx is None or idx + 1 >= len(df):
            return False
        after = df.iloc[idx + 1:]
        if kind == "low":
            return bool((after["low"] < level).any())
        return bool((after["high"] > level).any())
    eqh_set = set(round(v, 2) for v in (result.equal_highs or []))
    eql_set = set(round(v, 2) for v in (result.equal_lows  or []))
    swept_highs = {round(s.level, 2) for s in (result.sweeps or []) if s.kind == "sweep_high"}
    swept_lows  = {round(s.level, 2) for s in (result.sweeps or []) if s.kind == "sweep_low"}

    bsl_pools = []
    for sh in (swing_highs or []):
        lv = round(sh.price, 2)
        if lv <= current_price:
            continue
        is_major = any(abs(lv - e) < 1.0 for e in eqh_set)
        swept    = any(abs(lv - s) < 1.5 for s in swept_highs) or _breached(sh.index, lv, "high")
        _t, _age = _bar_meta(sh.index)
        bsl_pools.append({
            "level":     lv,
            "type":      "EQH" if is_major else "SwingHigh",
            "size":      "major" if is_major else "minor",
            "dist_pts":  round((lv - current_price) * 10, 1),
            "swept":     swept,
            "timeframe": timeframe,
            "time":      _t,
            "age_bars":  _age,
        })

    ssl_pools = []
    for sl in (swing_lows or []):
        lv = round(sl.price, 2)
        if lv >= current_price:
            continue
        is_major = any(abs(lv - e) < 1.0 for e in eql_set)
        swept    = any(abs(lv - s) < 1.5 for s in swept_lows) or _breached(sl.index, lv, "low")
        _t, _age = _bar_meta(sl.index)
        ssl_pools.append({
            "level":     lv,
            "type":      "EQL" if is_major else "SwingLow",
            "size":      "major" if is_major else "minor",
            "dist_pts":  round((current_price - lv) * 10, 1),
            "swept":     swept,
            "timeframe": timeframe,
            "time":      _t,
            "age_bars":  _age,
        })

    bsl_pools.sort(key=lambda x: x["dist_pts"])
    ssl_pools.sort(key=lambda x: x["dist_pts"])

    intact_bsl = [p for p in bsl_pools if not p["swept"]]
    intact_ssl = [p for p in ssl_pools if not p["swept"]]

    def find_inducement(pools, target):
        if not target or target["size"] == "minor":
            return None
        minor = [p for p in pools if p["size"] == "minor" and p["dist_pts"] < target["dist_pts"]]
        return minor[0] if minor else None

    nearest_bsl = intact_bsl[0] if intact_bsl else None
    nearest_ssl = intact_ssl[0] if intact_ssl else None

    return {
        "bsl_pools":      bsl_pools,       # ทั้งหมด ไม่ cap
        "ssl_pools":      ssl_pools,
        "nearest_bsl":    nearest_bsl,
        "nearest_ssl":    nearest_ssl,
        "bsl_inducement": find_inducement(intact_bsl, nearest_bsl),
        "ssl_inducement": find_inducement(intact_ssl, nearest_ssl),
    }


def find_liquidity_zones(df: pd.DataFrame, current_price: float, lookback: int = 500,
                          cluster_width: float = 1.5, min_visits: int = 3,
                          visit_gap_bars: int = 5, min_span_bars: int = 80,
                          max_zones_per_side: int = 3) -> list[dict]:
    """
    หาโซนสะสม (liquidity accumulation zone) — ราคาเทสซ้ำๆ ในช่วงแคบหลายชั่วโมง
    ต่างจาก fractal swing (จุดเดียวต่ำสุด/สูงสุดใน window) — นี่จับ "โซน" ที่มี
    touch density สูง แม้จะไม่มีจุดไหนเป็น the-single-lowest-point ก็ตาม

    Rally ใหญ่ๆ มักวิ่งไปกิน liquidity zone (โซนสะสม) ก่อน แล้วค่อยไป sweep SSL/BSL
    ต่อเพื่อกิน stop ของคนเล่นตามเทรนด์

    วิธี: fixed-anchor clustering ตามราคา (กัน chaining ข้ามไปไกล) แล้วนับ
    "visit" ที่แยกกันจริง (ห่างกัน > visit_gap_bars ถึงนับเป็น visit ใหม่)
    แทนการนับ touch ดิบ — กันไม่ให้ 1 แท่งลงยาวติดกันถูกนับเป็นโซนสะสมปลอมๆ
    zone ต้องมี visit >= min_visits และกินเวลา >= min_span_bars
    """
    n = len(df)
    if n < min_span_bars:
        return []
    lb = min(lookback, n)
    recent = df.iloc[-lb:].reset_index(drop=True)

    zones = []
    for col, kind in (("low", "support"), ("high", "resistance")):
        prices = recent[col].values
        order = sorted(range(len(prices)), key=lambda i: prices[i])

        side_zones = []
        i = 0
        while i < len(order):
            cluster = [order[i]]
            j = i + 1
            # fixed-anchor: เทียบกับ cluster[0] เสมอ กัน chaining ไหลข้ามไปไกล
            while j < len(order) and prices[order[j]] - prices[cluster[0]] <= cluster_width:
                cluster.append(order[j])
                j += 1
            idxs = sorted(cluster)
            visits = 1
            for k in range(1, len(idxs)):
                if idxs[k] - idxs[k - 1] > visit_gap_bars:
                    visits += 1
            span = idxs[-1] - idxs[0]
            if visits >= min_visits and span >= min_span_bars:
                cluster_prices = [prices[k] for k in cluster]
                price_low  = round(min(cluster_prices), 2)
                price_high = round(max(cluster_prices), 2)
                mid        = round(sum(cluster_prices) / len(cluster_prices), 2)
                if not (kind == "support" and mid >= current_price) and \
                   not (kind == "resistance" and mid <= current_price):
                    side_zones.append({
                        "price_low":            price_low,
                        "price_high":           price_high,
                        "mid":                  mid,
                        "kind":                 kind,
                        "touch_count":          len(cluster),
                        "visits":               visits,
                        "span_bars":            int(span),
                        "last_touch_bars_ago":  int(lb - 1 - idxs[-1]),
                        "dist_pts":             round(abs(current_price - mid) * 10, 1),
                    })
            i = j

        # เก็บเฉพาะ zone ที่ใกล้ราคาปัจจุบันสุด max_zones_per_side อัน
        # (ไม่ใช่ visit เยอะสุด — โซนไกลๆ ที่ visit เยอะไม่ relevant เท่าโซนใกล้ที่เพิ่งผ่านเกณฑ์)
        side_zones.sort(key=lambda z: z["dist_pts"])
        zones.extend(side_zones[:max_zones_per_side])

    zones.sort(key=lambda z: z["dist_pts"])
    return zones


def is_market_holiday() -> tuple[bool, str]:
    """
    เช็คว่าตลาด Forex/Gold ปิดสนิทวันนี้มั้ย
    คืน: (is_hard_closed, holiday_name)

    Hard close (block scan): ตลาด Forex ปิดจริง — Christmas, New Year's, Good Friday
    Soft close (warn only): CME Gold ลด volume แต่ Spot Gold ยังเทรดได้ผ่าน broker
    """
    import holidays as hol
    from datetime import date

    from datetime import datetime, timezone, timedelta as _td
    _THAI = timezone(_td(hours=7))
    now_thai = datetime.now(_THAI)
    today = now_thai.date()

    # XAUUSD market hours:
    #   ปิด: ศุกร์ ~23:00 Thai ถึง อาทิตย์ ~22:00 Thai
    #   เปิด: อาทิตย์ 22:00 Thai เป็นต้นไป (US Sunday electronic open)
    dow = today.weekday()  # Mon=0 ... Sat=5 Sun=6

    if dow == 5:  # Saturday — ปิดทั้งวัน
        return True, "Weekend (Saturday)"

    if dow == 6:  # Sunday — เปิดหลัง 22:00 Thai เท่านั้น
        if now_thai.hour < 22:
            return True, "Weekend (Sunday ตลาดยังไม่เปิด — เปิด 22:00)"

    if dow == 0 and (now_thai.hour < 6 or (now_thai.hour == 6 and now_thai.minute < 30)):
        # Monday ก่อน 06:30 Thai — ยังไม่ถึง Pre-Event session
        return True, "Monday (ตลาดยังเงียบ — เปิด 06:30)"

    # Hard close: Forex ปิดสนิทหรือใกล้เคียง
    hard_close_keywords = {
        "Christmas Day",
        "New Year's Day",
        "Good Friday",
    }

    us_holidays = hol.UnitedStates(years=today.year, subdiv=None)
    if today in us_holidays:
        hname = us_holidays[today]
        if any(h in hname for h in hard_close_keywords):
            return True, f"🇺🇸 {hname}"

    uk_holidays = hol.UnitedKingdom(years=today.year)
    if today in uk_holidays:
        hname = uk_holidays[today]
        if any(h in hname for h in hard_close_keywords):
            return True, f"🇬🇧 {hname}"

    # Soft close — US bank holiday, CME ลด volume แต่ Spot Gold ยังเทรดได้
    # คืน (False, holiday_name) เพื่อให้ caller แสดง warning แต่ไม่ block
    soft_close_keywords = {
        "Martin Luther King Jr. Day",
        "Presidents' Day",
        "Memorial Day",
        "Juneteenth",
        "Independence Day",
        "Labor Day",
        "Thanksgiving",
    }
    if today in us_holidays:
        hname = us_holidays[today]
        if any(h in hname for h in soft_close_keywords):
            return False, f"⚠️ US Holiday ({hname}) — CME ลด volume, Spot ยังเทรดได้"

    return False, ""


def get_holiday_warning() -> str:
    """คืน warning string ถ้าเป็น soft holiday, ไม่งั้น คืน ''"""
    _, msg = is_market_holiday()
    return msg if msg.startswith("⚠️") else ""


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

    # ── เช็คย้อน 3 แท่งล่าสุด ไม่ใช่แค่แท่งปัจจุบัน ──────────────
    # แก้ entry lag: rejection candle เกิดแล้ว แต่ระบบรอ pullback+continuation
    # กว่าจะ trigger — เช็คว่า 3 แท่งที่ผ่านมามี rejection candle มั้ย ถ้ามี
    # ให้ bull_candle/bear_candle = True ทันที ไม่ต้องรอแท่งล่าสุดเป๊ะๆ
    _lookback_n = min(3, n)
    for _k in range(1, _lookback_n + 1):
        _row   = df.iloc[-_k]
        _body  = abs(_row['close'] - _row['open'])
        _upper = _row['high'] - max(_row['close'], _row['open'])
        _lower = min(_row['close'], _row['open']) - _row['low']
        _r     = max(_row['high'] - _row['low'], 0.001)
        if _row['close'] > _row['open'] and (_body / _r >= 0.5 or _upper < _lower * 0.35):
            bull_candle = True
        if _row['close'] < _row['open'] and (_body / _r >= 0.5 or _lower < _body * 0.35):
            bear_candle = True

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

    # ── Liquidity Zone Sweep (โซนสะสม — ราคาเทสซ้ำๆ หลายชั่วโมง) ──
    # คนละกลไกกับ sweep 20-bar ด้านบน — ใช้ find_liquidity_zones ที่ไม่พึ่ง fractal
    # swing เลย จับ "โซน" ที่ fractal/EQL/EQH มองไม่เห็น (เช่น โซนสะสมข้ามวัน)
    zone_sweep_bull       = False
    zone_sweep_bear       = False
    zone_sweep_bull_age   = 999
    zone_sweep_bear_age   = 999
    zone_sweep_bull_level = None
    zone_sweep_bear_level = None
    zone_sweep_bull_edge  = None   # price_low ของโซน — ใช้อ้างอิงวาง SL
    zone_sweep_bear_edge  = None   # price_high ของโซน — ใช้อ้างอิงวาง SL
    try:
        _zones      = find_liquidity_zones(df, current_close)
        _sup_zones  = [z for z in _zones if z["kind"] == "support"]
        _res_zones  = [z for z in _zones if z["kind"] == "resistance"]
        _check_bars = min(5, n)
        for _z in _sup_zones:
            for _i in range(1, _check_bars + 1):
                _bar = df.iloc[-_i]
                if _bar['low'] < _z['price_low'] and _bar['close'] >= _z['price_low']:
                    zone_sweep_bull       = True
                    zone_sweep_bull_age   = _i - 1
                    zone_sweep_bull_level = _z['mid']
                    zone_sweep_bull_edge  = _z['price_low']
                    break
            if zone_sweep_bull:
                break
        for _z in _res_zones:
            for _i in range(1, _check_bars + 1):
                _bar = df.iloc[-_i]
                if _bar['high'] > _z['price_high'] and _bar['close'] <= _z['price_high']:
                    zone_sweep_bear       = True
                    zone_sweep_bear_age   = _i - 1
                    zone_sweep_bear_level = _z['mid']
                    zone_sweep_bear_edge  = _z['price_high']
                    break
            if zone_sweep_bear:
                break
    except Exception:
        pass

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
    # เช็ค OB ทุกตัว (ไม่ใช่แค่ active_ob ตัวเดียว) เพื่อจับ bull/bear OB แยกกัน
    # ต้องตรวจ direction: Bull OB ต้องเป็น retest (ราคาเคยอยู่เหนือ OB แล้ว pull back ลงมา)
    #                     Bear OB ต้องเป็น retest (ราคาเคยอยู่ใต้ OB แล้ว pull up ขึ้นมา)
    # เงื่อนไขเพิ่ม 2 ข้อ (จาก user feedback):
    #   1. ต้องมี rejection candle ยืนยัน — เข้า OB เฉยๆ ไม่พอ ต้องเห็นแท่งปฏิเสธจริง
    #   2. OB ต้องห่างจากจุดที่ราคาวิ่งมา ≥$10 — ไม่งั้นไม่มี room ให้ sweep ผ่านง่ายเกินไป
    OB_MIN_DISTANCE = 10.0  # ดอลลาร์ — ต้องมี room วิ่งมาอย่างน้อยเท่านี้ก่อนแตะ OB
    all_obs = result.order_blocks or []
    recent_high_20 = df['high'].iloc[-21:-1].max() if len(df) >= 21 else df['high'].max()
    recent_low_20  = df['low'].iloc[-21:-1].min()  if len(df) >= 21 else df['low'].min()
    in_bull_ob = any(
        ob.kind == 'bullish' and not ob.mitigated
        and ob.bottom <= current_close <= ob.top
        and recent_high_20 > ob.top                          # ราคาเคยเหนือ OB → pull back ลงมา retest
        and (recent_high_20 - ob.top) >= OB_MIN_DISTANCE      # ต้องมี room วิ่งมาก่อนแตะ OB
        and bull_candle                                       # ต้องมี rejection candle ยืนยัน
        for ob in all_obs
    )
    in_bear_ob = any(
        ob.kind == 'bearish' and not ob.mitigated
        and ob.bottom <= current_close <= ob.top
        and recent_low_20 < ob.bottom                         # ราคาเคยใต้ OB → pull up ขึ้นมา retest
        and (ob.bottom - recent_low_20) >= OB_MIN_DISTANCE     # ต้องมี room วิ่งมาก่อนแตะ OB
        and bear_candle                                        # ต้องมี rejection candle ยืนยัน
        for ob in all_obs
    )

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

        # Recent candle high/low (20-bar) — sweep reference for SL placement + OB min-distance check
        "recent_high_20": round(recent_high_20, 2),
        "recent_low_20":  round(recent_low_20,  2),

        # Liquidity Zone Sweep (โซนสะสม)
        "zone_sweep_bull":       zone_sweep_bull,
        "zone_sweep_bear":       zone_sweep_bear,
        "zone_sweep_bull_age":   zone_sweep_bull_age,
        "zone_sweep_bear_age":   zone_sweep_bear_age,
        "zone_sweep_bull_level": zone_sweep_bull_level,
        "zone_sweep_bear_level": zone_sweep_bear_level,
        "zone_sweep_bull_edge":  zone_sweep_bull_edge,   # price_low โซน — SL reference
        "zone_sweep_bear_edge":  zone_sweep_bear_edge,   # price_high โซน — SL reference

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


# ─── Swing Entry Detector ────────────────────────────────────────
# ไม่ใช่ reversal — เป็นการเล่น swing ใน OB ช่วงนั้น
# ต่อให้ downtrend ก็ยังมี swing ขึ้น | uptrend ก็มี swing ลง
# จุดที่เข้าคือ OB / EQL / EQH ที่ราคา sweep แล้ว bounce
# TP = next swing high/low เท่านั้น ไม่ได้คาดว่าจะกลับ trend

def detect_reversal(df: pd.DataFrame, result: SMCResult) -> dict:
    """Alias kept for backward compat — calls detect_swing_entry"""
    return detect_swing_entry(df, result)


def detect_swing_entry(df: pd.DataFrame, result: SMCResult) -> dict:
    """
    หาจุดเข้า swing — เล่นใน swing นั้นๆ ไม่ใช่คาด reversal

    Bullish Swing (เล่น swing ขึ้นใน swing นั้น แม้ trend ใหญ่จะลง):
      1. Sweep below EQL / Weak Low / OB bottom  ← ดูด liquidity ก่อน bounce
      2. CHoCH bullish บน M5 (เปลี่ยนทิศ swing นี้)
      3. Confirmation: bull candle wick ใหญ่ + body ≥50%
      4. ไม่มี momentum ลงแรง (momentum_bear = False)
      TP = nearest swing high ข้างบน (ไม่ใช่ all-time high)

    Bearish Swing (เล่น swing ลง แม้ trend ใหญ่จะขึ้น):
      ตรงกันข้าม — sweep high → CHoCH bear → bear candle → TP = nearest swing low
      TP = nearest swing low ข้างล่าง

    Swing Score (0-10):
      CHoCH fresh (≤5 bars): +3  ← หัวใจของ swing เปลี่ยนทิศ
      Sweep occurred:        +2  ← ดูด liquidity แล้ว
      Confirmation candle:   +2  ← ยืนยัน rejection
      In OB zone:            +1  ← zone ที่ราคา respect
      H1 aligned:            +1  ← H1 เริ่มเปลี่ยนทิศ swing
      No momentum against:   +1  ← ไม่ถูก news spike
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

    # 1b. Liquidity Zone Sweep (โซนสะสม — สำคัญเทียบเท่า CHoCH ตาม user)
    # ราคาวิ่งไปกินโซนสะสมก่อนมักตามด้วย sweep SSL/BSL ต่อ — น้ำหนักสูงสุดเท่า CHoCH
    if adv.get("zone_sweep_bull"):
        bull_score += 3
        bull_reasons.append(f"Liquidity Zone Swept @{adv.get('zone_sweep_bull_level')} ({adv['zone_sweep_bull_age']} bars ago)")

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

    # Liquidity Zone Sweep (โซนสะสม — สำคัญเทียบเท่า CHoCH ตาม user)
    if adv.get("zone_sweep_bear"):
        bear_score += 3
        bear_reasons.append(f"Liquidity Zone Swept @{adv.get('zone_sweep_bear_level')} ({adv['zone_sweep_bear_age']} bars ago)")

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

    # ── บังคับต้องมี sweep จริงก่อนถึงจะนับ (user feedback) ──────────
    # ห้ามเข้าแค่เพราะ CHoCH/candle/OB โดยไม่มี sweep หรือ zone-sweep เลย —
    # ไม่งั้นเข้าตอนราคายังแค่ sideways ทำโซนสะสม ยังไม่มีอะไรเกิดขึ้นจริง
    _bull_has_sweep = bool(adv.get("recent_sweep_low") or adv.get("zone_sweep_bull"))
    _bear_has_sweep = bool(adv.get("recent_sweep_high") or adv.get("zone_sweep_bear"))
    if not _bull_has_sweep:
        bull_score = 0
        bull_reasons = []
    if not _bear_has_sweep:
        bear_score = 0
        bear_reasons = []

    # ── ตัดสิน ────────────────────────────────────────────────
    def grade(s):
        if s >= 7: return "★★★", "HIGH"
        if s >= 5: return "★★",  "MODERATE"
        if s >= 3: return "★",   "LOW"
        return None, None

    # เลือก direction ที่ score สูงกว่า (ต้องอย่างน้อย 3)
    swing_signal  = None
    swing_score   = 0
    swing_stars   = None
    swing_grade   = None
    swing_reasons = []

    if bull_score >= 3 and bull_score >= bear_score:
        swing_signal  = "BUY"
        swing_score   = bull_score
        swing_stars, swing_grade = grade(bull_score)
        swing_reasons = bull_reasons

        # คำนวณ entry zone จาก active OB หรือ current price
        aob = result.active_ob
        if aob and aob.kind == "bullish":
            entry_low  = aob.bottom
            entry_high = aob.top
        else:
            entry_low  = round(current_price - atr * 0.3, 2)
            entry_high = round(current_price + atr * 0.3, 2)

        # SL: อ้างอิงระดับที่ถูก sweep จริงก่อน (แม่นสุด) → fallback เป็น swing low
        # ลำดับความสำคัญ: liquidity zone ที่ถูก sweep > sweep (mechanism A, wick_extreme) > nearest swing low
        # buffer = $5 คงที่ (ไม่ scale ตาม ATR) — user ยืนยัน: SL ควรเป็น swept level + $5 เสมอ
        SL_BUFFER = 5.0
        _zone_edge  = adv.get("zone_sweep_bull_edge")
        _last_sweep = result.last_sweep
        if adv.get("zone_sweep_bull") and _zone_edge:
            sl = round(_zone_edge - SL_BUFFER, 2)
        elif (_last_sweep and _last_sweep.kind == "sweep_low"
              and (n - 1 - _last_sweep.index) <= 30):
            sl = round(_last_sweep.wick_extreme - SL_BUFFER, 2)
        else:
            recent_lows = [s.price for s in result.swing_lows[-5:]
                           if (n - 1 - s.index) <= 15] if result.swing_lows else []
            if recent_lows:
                nearest_sl = min(recent_lows)
                sl_dist    = current_price - nearest_sl
                # ถ้า swing low ไกลเกิน ATR×2.5 ใช้ ATR×1.0 แทน
                sl = round(current_price - min(sl_dist + SL_BUFFER, atr * 2.5), 2)
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
        swing_signal  = "SELL"
        swing_score   = bear_score
        swing_stars, swing_grade = grade(bear_score)
        swing_reasons = bear_reasons

        aob = result.active_ob
        if aob and aob.kind == "bearish":
            entry_low  = aob.bottom
            entry_high = aob.top
        else:
            entry_low  = round(current_price - atr * 0.3, 2)
            entry_high = round(current_price + atr * 0.3, 2)

        # SL: อ้างอิงระดับที่ถูก sweep จริงก่อน (แม่นสุด) → fallback เป็น swing high
        # buffer = $5 คงที่ (ไม่ scale ตาม ATR)
        SL_BUFFER   = 5.0
        _zone_edge  = adv.get("zone_sweep_bear_edge")
        _last_sweep = result.last_sweep
        if adv.get("zone_sweep_bear") and _zone_edge:
            sl = round(_zone_edge + SL_BUFFER, 2)
        elif (_last_sweep and _last_sweep.kind == "sweep_high"
              and (n - 1 - _last_sweep.index) <= 30):
            sl = round(_last_sweep.wick_extreme + SL_BUFFER, 2)
        else:
            recent_highs = [s.price for s in result.swing_highs[-5:]
                            if (n - 1 - s.index) <= 15] if result.swing_highs else []
            if recent_highs:
                nearest_sl = max(recent_highs)
                sl_dist    = nearest_sl - current_price
                sl = round(current_price + min(sl_dist + SL_BUFFER, atr * 2.5), 2)
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
            "reversal_signal": None,   # backward-compat key
            "swing_signal": None,
            "swing_score": max(bull_score, bear_score),
            "bull_score": bull_score,
            "bear_score": bear_score,
            "atr": atr,
        }

    sl_pips = round(abs(current_price - sl) * 10, 1)
    tp_pips = round(abs(current_price - tp) * 10, 1)
    rr      = round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0

    return {
        # backward-compat keys (used by chart_analyst / supervisor)
        "reversal_signal":  swing_signal,
        "reversal_score":   swing_score,
        "reversal_stars":   swing_stars,
        "reversal_grade":   swing_grade,
        "reversal_reasons": swing_reasons,
        # new preferred keys
        "swing_signal":  swing_signal,
        "swing_score":   swing_score,
        "swing_stars":   swing_stars,
        "swing_grade":   swing_grade,
        "swing_reasons": swing_reasons,
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


# ─── AMD Pattern Detector ────────────────────────────────────────

def detect_amd_pattern(df: pd.DataFrame, result: SMCResult, lookback: int = 40) -> dict:
    """
    AMD (Accumulation / Manipulation / Distribution) Pattern
    Wyckoff: Spring (bullish) / Upthrust (bearish)

    Bullish AMD (Spring):
      Range sideways → Sweep below EQL (fake breakdown) → CHoCH bull → BOS up → AMD_BUY

    Bearish AMD (Upthrust):
      Range sideways → Sweep above EQH (fake breakout) → CHoCH bear → BOS down → AMD_SELL

    Score:
      Range + EQL/EQH present: +1
      EQL swept (Spring):       +3
      EQH swept (Upthrust):     +3
      CHoCH opposite sweep:     +3
      BOS confirming:           +2
      HTF aligned:              +1
    """
    n = len(df)
    if n < 20:
        return {"amd_signal": None, "amd_score": 0, "amd_phase": None, "amd_type": None}

    high_low   = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close  = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]

    current_price = df['close'].iloc[-1]
    lb = min(lookback, n - 1)
    recent_df = df.iloc[-lb:]

    score = 0
    reasons = []
    amd_signal = None
    amd_phase = "none"
    amd_type = None
    sweep_level_bull = None
    sweep_level_bear = None

    range_top    = recent_df['high'].max()
    range_bottom = recent_df['low'].min()

    # ── 1. Accumulation: Range + EQL/EQH ─────────────────────────
    has_eqh = len(result.equal_highs) >= 1
    has_eql = len(result.equal_lows)  >= 1
    range_height = range_top - range_bottom

    if (has_eqh or has_eql) and range_height < atr * 6:
        score += 1
        reasons.append(f"Range {range_bottom:.1f}–{range_top:.1f} EQH={has_eqh} EQL={has_eql}")
        amd_phase = "accumulation"

    # ── 2. Manipulation: EQL sweep (Spring) ──────────────────────
    for eql in sorted(result.equal_lows):
        swept = recent_df[recent_df['low'] < eql]
        if swept.empty:
            continue
        sweep_iloc = int(np.argmin(recent_df['low'].values))
        after = recent_df.iloc[sweep_iloc + 1:]
        if after.empty or after['close'].iloc[-1] <= eql:
            continue
        sweep_age = len(recent_df) - 1 - sweep_iloc
        if sweep_age > 20:
            continue
        sweep_level_bull = eql
        score += 3
        reasons.append(f"Spring: EQL {eql:.1f} swept ({sweep_age}b ago)")
        amd_type = "Spring"
        amd_phase = "manipulation"
        break

    # ── 2. Manipulation: EQH sweep (Upthrust) — ถ้ายังไม่เจอ Spring
    if sweep_level_bull is None:
        for eqh in sorted(result.equal_highs, reverse=True):
            swept = recent_df[recent_df['high'] > eqh]
            if swept.empty:
                continue
            sweep_iloc = int(np.argmax(recent_df['high'].values))
            after = recent_df.iloc[sweep_iloc + 1:]
            if after.empty or after['close'].iloc[-1] >= eqh:
                continue
            sweep_age = len(recent_df) - 1 - sweep_iloc
            if sweep_age > 20:
                continue
            sweep_level_bear = eqh
            score += 3
            reasons.append(f"Upthrust: EQH {eqh:.1f} swept ({sweep_age}b ago)")
            amd_type = "Upthrust"
            amd_phase = "manipulation"
            break

    # ── 3. Distribution: CHoCH + BOS after sweep ──────────────────
    lc      = result.last_choch
    last_bos = result.last_bos
    choch_age = (n - 1 - lc.index) if lc else 999

    if sweep_level_bull and lc and lc.direction == 'bullish' and choch_age <= 12:
        score += 3
        reasons.append(f"CHoCH Bull after Spring ({choch_age}b ago)")
        amd_phase = "distribution"
        amd_signal = "AMD_BUY"
        if last_bos and last_bos.direction == 'bullish':
            bos_age = n - 1 - last_bos.index
            if bos_age <= 20:
                score += 2
                reasons.append(f"BOS Bull ({bos_age}b ago)")

    elif sweep_level_bear and lc and lc.direction == 'bearish' and choch_age <= 12:
        score += 3
        reasons.append(f"CHoCH Bear after Upthrust ({choch_age}b ago)")
        amd_phase = "distribution"
        amd_signal = "AMD_SELL"
        if last_bos and last_bos.direction == 'bearish':
            bos_age = n - 1 - last_bos.index
            if bos_age <= 20:
                score += 2
                reasons.append(f"BOS Bear ({bos_age}b ago)")

    # ── 4. HTF alignment ──────────────────────────────────────────
    h1_w = min(240, n)
    h4_w = min(960, n)
    h1_mid = (df['high'].iloc[-h1_w:].max() + df['low'].iloc[-h1_w:].min()) / 2
    h4_mid = (df['high'].iloc[-h4_w:].max() + df['low'].iloc[-h4_w:].min()) / 2
    h1_bull = current_price > h1_mid
    h4_bull = current_price > h4_mid

    if amd_signal == "AMD_BUY" and (h1_bull or h4_bull):
        score += 1
        reasons.append("HTF Bull aligned")
    elif amd_signal == "AMD_SELL" and (not h1_bull or not h4_bull):
        score += 1
        reasons.append("HTF Bear aligned")

    def _stars(s):
        return "★★★" if s >= 8 else "★★" if s >= 5 else "★"

    if not amd_signal:
        return {
            "amd_signal": None,
            "amd_score":  score,
            "amd_phase":  amd_phase,
            "amd_type":   amd_type,
            "amd_reasons": reasons,
        }

    return {
        "amd_signal":       amd_signal,
        "amd_score":        score,
        "amd_stars":        _stars(score),
        "amd_phase":        amd_phase,
        "amd_type":         amd_type,
        "amd_reasons":      reasons,
        "amd_range_top":    round(range_top, 2),
        "amd_range_bottom": round(range_bottom, 2),
        "amd_sweep_level":  round(sweep_level_bull or sweep_level_bear, 2),
        "atr":              round(atr, 2),
    }


# ─── EQL / EQH Sweep Detector ────────────────────────────────────

def detect_eql_eqh_sweep(df: pd.DataFrame, result: SMCResult, lookback: int = 2016) -> dict:
    """
    ตรวจ EQL_SWEEP_BUY / EQH_SWEEP_SELL — Liquidity Sweep Entry

    EQL_SWEEP_BUY:
      - มี EQL (equal lows) → ดูด sell-side liquidity อยู่
      - ใน lookback bars ราคาเคยลงไปต่ำกว่า EQL (sweep)
      - ตอนนี้ราคากลับขึ้นเหนือ EQL แล้ว
      - ไม่มี new low หลัง sweep (buyer เริ่มควบคุม)

    EQH_SWEEP_SELL:
      - มี EQH (equal highs) → ดูด buy-side liquidity อยู่
      - ใน lookback bars ราคาเคยขึ้นไปสูงกว่า EQH (sweep)
      - ตอนนี้ราคาลงมาต่ำกว่า EQH แล้ว
      - ไม่มี new high หลัง sweep (seller เริ่มควบคุม)
    """
    if df is None or len(df) < 10:
        return {"eql_sweep_signal": None, "eqh_sweep_signal": None}

    current_price = df['close'].iloc[-1]
    lb = min(lookback, len(df) - 1)
    recent = df.iloc[-lb:]

    eql_sweep_signal = None
    eqh_sweep_signal = None

    # ── EQL Sweep → BUY ────────────────────────────────────────
    for eql in sorted(result.equal_lows):
        swept = recent[recent['low'] < eql]
        if swept.empty:
            continue
        sweep_low  = swept['low'].min()
        sweep_iloc = recent['low'].values.argmin()

        # sweep ต้องไม่เก่าเกิน 48 bars (4 ชั่วโมง M5) — ป้องกัน stale signal
        bars_since_sweep = len(recent) - 1 - sweep_iloc
        if bars_since_sweep > 48:
            continue

        # ราคาปัจจุบันต้องกลับขึ้นมาเหนือ EQL แล้ว
        if current_price <= eql:
            continue

        # แท่ง sweep ต้อง close กลับเหนือ EQL (rejection candle) หรือแท่งถัดไปต้องปิดเหนือ
        sweep_bar = recent.iloc[sweep_iloc]
        after = recent.iloc[sweep_iloc + 1:]
        rejection_on_sweep = sweep_bar['close'] >= eql
        rejection_after    = (not after.empty) and (after['close'].iloc[0] >= eql)
        if not (rejection_on_sweep or rejection_after):
            continue  # sweep แล้วแต่ยังไม่มี rejection close — รอก่อน

        # ไม่ควรมี new low หลังจุด sweep
        if not after.empty and after['low'].min() < sweep_low:
            continue  # มี new low — ยังไม่ bounce จริง

        eql_sweep_signal = {
            "signal":    "EQL_SWEEP_BUY",
            "eql_level": round(eql, 2),
            "sweep_low": round(sweep_low, 2),
            "recovery":  round(current_price - eql, 2),
        }
        break

    # ── EQH Sweep → SELL ───────────────────────────────────────
    for eqh in sorted(result.equal_highs, reverse=True):
        swept = recent[recent['high'] > eqh]
        if swept.empty:
            continue
        sweep_high  = swept['high'].max()
        sweep_iloc  = recent['high'].values.argmax()

        # sweep ต้องไม่เก่าเกิน 48 bars (4 ชั่วโมง M5)
        bars_since_sweep = len(recent) - 1 - sweep_iloc
        if bars_since_sweep > 48:
            continue

        # ราคาปัจจุบันต้องลงมาต่ำกว่า EQH แล้ว
        if current_price >= eqh:
            continue

        # แท่ง sweep ต้อง close กลับต่ำกว่า EQH (rejection candle) หรือแท่งถัดไป
        sweep_bar = recent.iloc[sweep_iloc]
        after = recent.iloc[sweep_iloc + 1:]
        rejection_on_sweep = sweep_bar['close'] <= eqh
        rejection_after    = (not after.empty) and (after['close'].iloc[0] <= eqh)
        if not (rejection_on_sweep or rejection_after):
            continue  # sweep แล้วแต่ยังไม่มี rejection close — รอก่อน

        # ไม่ควรมี new high หลังจุด sweep
        if not after.empty and after['high'].max() > sweep_high:
            continue  # มี new high — ยังไม่ reject จริง

        eqh_sweep_signal = {
            "signal":     "EQH_SWEEP_SELL",
            "eqh_level":  round(eqh, 2),
            "sweep_high": round(sweep_high, 2),
            "rejection":  round(eqh - current_price, 2),
        }
        break

    return {"eql_sweep_signal": eql_sweep_signal, "eqh_sweep_signal": eqh_sweep_signal}


# ─── Format Summary ───────────────────────────────────────────────

def _sweep_obj_by_kind(sweeps: list, kind: str, df: pd.DataFrame = None) -> dict | None:
    """หา sweep ล่าสุดของ 'ทิศเดียว' (sweep_high หรือ sweep_low) โดยเฉพาะ —
    ไม่ถูกทับด้วย sweep ของอีกทิศที่เกิดทีหลัง (ต่างจาก result.last_sweep เดี่ยว)"""
    _matches = [s for s in sweeps if s.kind == kind]
    if not _matches:
        return None
    sw = _matches[-1]
    return {
        "kind": "high" if kind == "sweep_high" else "low",
        "level": sw.level,
        "recovered": sw.recovered,
        "wick_extreme": sw.wick_extreme,
        "age_bars": (len(df) - 1 - sw.index) if df is not None else None,
    }


def summarize(result: SMCResult, current_price: float, df: pd.DataFrame = None,
              pool_swing_highs: list = None, pool_swing_lows: list = None,
              pool_sweeps: list = None) -> dict:
    """
    แปลง SMCResult เป็น dict สรุปสำหรับส่งให้ Claude
    ถ้าส่ง df มาด้วย จะรวม advanced_signals + session อัตโนมัติ
    """
    last_sweep    = result.last_sweep
    active_ob     = result.active_ob
    last_bos      = result.last_bos
    last_choch    = result.last_choch

    # เลือก OB ที่ใกล้ current_price มากที่สุด (closest) แทน most-recently-formed
    # สำหรับ Bull OB → OB ที่อยู่ต่ำกว่าราคา ใกล้ top สุด (demand zone บนสุด)
    # สำหรับ Bear OB → OB ที่อยู่สูงกว่าราคา ใกล้ bottom สุด (supply zone ล่างสุด)
    all_obs = result.order_blocks or []
    _bull_obs = [ob for ob in all_obs if not ob.mitigated and ob.kind == 'bullish']
    _bear_obs = [ob for ob in all_obs if not ob.mitigated and ob.kind == 'bearish']

    _MIN_DEP = 3.0  # 30 pts — ราคาต้องพ้น OB edge อย่างน้อยก่อนจะ confirm

    if _bull_obs:
        # Bull OB ที่ top ใกล้ราคามากสุด (ราคาต้องพ้น top ≥30pts — OB confirmed)
        below = [ob for ob in _bull_obs if ob.top <= current_price - _MIN_DEP]
        active_bull_ob = min(below, key=lambda ob: current_price - ob.top) if below else \
                         min(_bull_obs, key=lambda ob: abs(ob.top - current_price))
    else:
        active_bull_ob = None

    if _bear_obs:
        # Bear OB ที่ bottom ใกล้ราคามากสุด (ราคาต้องพ้น bottom ≥30pts — OB confirmed)
        above = [ob for ob in _bear_obs if ob.bottom >= current_price + _MIN_DEP]
        active_bear_ob = min(above, key=lambda ob: ob.bottom - current_price) if above else \
                         min(_bear_obs, key=lambda ob: abs(ob.bottom - current_price))
    else:
        active_bear_ob = None

    # เช็คว่า price อยู่ใน OB มั้ย (เช็คแยก bull/bear)
    in_bull_ob = bool(active_bull_ob and active_bull_ob.bottom <= current_price <= active_bull_ob.top)
    in_bear_ob = bool(active_bear_ob and active_bear_ob.bottom <= current_price <= active_bear_ob.top)
    in_ob = in_bull_ob or in_bear_ob

    # FVG ที่ยังไม่ถูก fill
    open_fvgs   = [f for f in result.fvgs if not f.filled]
    nearest_fvg = (min(open_fvgs, key=lambda f: abs((f.top + f.bottom) / 2 - current_price))
                   if open_fvgs else None)
    # แยก bull/bear FVG ที่อยู่ฝั่งที่ราคาน่าจะ pull back ไปแตะ — ใช้เป็น
    # candidate ใน Buy/Sell Zone Guide (คู่กับ OB/liquidity zone)
    _bull_fvgs_below = [f for f in open_fvgs if f.kind == "bullish" and f.top <= current_price]
    _bear_fvgs_above = [f for f in open_fvgs if f.kind == "bearish" and f.bottom >= current_price]
    nearest_bull_fvg = (min(_bull_fvgs_below, key=lambda f: current_price - f.top)
                        if _bull_fvgs_below else None)
    nearest_bear_fvg = (min(_bear_fvgs_above, key=lambda f: f.bottom - current_price)
                        if _bear_fvgs_above else None)

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
            "kind": "high" if last_sweep.kind == "sweep_high" else "low",
            "level": last_sweep.level,
            "recovered": last_sweep.recovered,
            "wick_extreme": last_sweep.wick_extreme,
            # age ของ sweep object ตัวนี้เอง (bars ago) — ต้องใช้ตัวนี้ ไม่ใช่
            # advanced_signals()'s sweep_l/h_age_bars ซึ่งเป็นคนละ mechanism
            # (20-bar rolling, คนละตัวกับ last_sweep ที่สแกนประวัติทั้งหมด)
            "age_bars": (len(df) - 1 - last_sweep.index) if df is not None else None,
        } if last_sweep else None,

        # last_sweep เป็น sweep ล่าสุด "ไม่ว่าทิศไหน" — ถ้า SSL sweep เกิดทีหลัง BSL
        # sweep, last_sweep จะทับเป็น SSL ทำให้ประเมิน SELL setup (ต้องใช้ BSL sweep)
        # ผิดตัวไปเลย (เห็น last_sweep.kind='low' แล้วสรุปว่า "ไม่มี BSL sweep"
        # ทั้งที่จริงมี BSL sweep อยู่ก่อนหน้า แค่ไม่ใช่ตัวล่าสุดสุด) — เก็บแยกทั้งสอง
        # ทิศไว้เสมอ ให้ chart_analyst_agent เลือกใช้ตัวที่ตรงกับทิศที่กำลังประเมิน
        #
        # ใช้ pool_sweeps (จาก swing_length=15 engine) แทน result.sweeps (swing_length=50)
        # ถ้ามีส่งมา — result.sweeps ต้องรอ swing high/low ยืนยันนาน 50 bars (~4ชม. บน M5)
        # ก่อนจะเข้าไปอยู่ใน swing_highs/lows เลย กว่าจะ "sweep" อะไรได้ก็เป็นสวิงที่เก่าไป
        # หลายชม.แล้ว พลาด sweep ที่กำลังเกิดจริงตอนนี้ (ตรงกับที่ Pine indicator เห็น
        # BSL Rejected ด้วย swing length 15 เร็วกว่ามาก) — ไม่ส่งมาจะ fallback ไปใช้
        # result.sweeps เหมือนเดิม (backward compat)
        "last_sweep_high": _sweep_obj_by_kind(pool_sweeps if pool_sweeps is not None else result.sweeps, "sweep_high", df),
        "last_sweep_low":  _sweep_obj_by_kind(pool_sweeps if pool_sweeps is not None else result.sweeps, "sweep_low", df),

        "active_ob": {
            "kind": active_ob.kind,
            "top": active_ob.top,
            "bottom": active_ob.bottom,
            "in_ob": in_ob,
        } if active_ob else None,

        "active_bull_ob": {
            "top":    active_bull_ob.top,
            "bottom": active_bull_ob.bottom,
            "in_ob":  in_bull_ob,
        } if active_bull_ob else None,

        "active_bear_ob": {
            "top":    active_bear_ob.top,
            "bottom": active_bear_ob.bottom,
            "in_ob":  in_bear_ob,
        } if active_bear_ob else None,

        "nearest_fvg": {
            "kind": nearest_fvg.kind,
            "top": nearest_fvg.top,
            "bottom": nearest_fvg.bottom,
        } if nearest_fvg else None,

        "nearest_bull_fvg": {
            "top": nearest_bull_fvg.top,
            "bottom": nearest_bull_fvg.bottom,
            "dist_pts": round((current_price - nearest_bull_fvg.top) * 10, 1),
        } if nearest_bull_fvg else None,

        "nearest_bear_fvg": {
            "top": nearest_bear_fvg.top,
            "bottom": nearest_bear_fvg.bottom,
            "dist_pts": round((nearest_bear_fvg.bottom - current_price) * 10, 1),
        } if nearest_bear_fvg else None,

        "equal_highs":       result.equal_highs[-3:] if result.equal_highs else [],
        "equal_lows":        result.equal_lows[-3:]  if result.equal_lows  else [],
        "swing_high_count":  len(result.swing_highs),
        "swing_low_count":   len(result.swing_lows),
        "total_structures":  len(result.structures),
        "total_sweeps":      len(result.sweeps),
    }

    # nearest swing high (above price) and swing low (below price) — ใช้เป็น TP hint
    if result.swing_highs:
        above_highs = sorted(
            [s.price for s in result.swing_highs if s.price > current_price],
            key=lambda p: p - current_price
        )
        summary["nearest_swing_high"] = round(above_highs[0], 2) if above_highs else None
        # top 3 เผื่อ LLM เลือก
        summary["swing_highs_above"] = [round(p, 2) for p in above_highs[:3]]
    else:
        summary["nearest_swing_high"] = None
        summary["swing_highs_above"]  = []

    if result.swing_lows:
        below_lows = sorted(
            [s.price for s in result.swing_lows if s.price < current_price],
            key=lambda p: current_price - p
        )
        summary["nearest_swing_low"] = round(below_lows[0], 2) if below_lows else None
        summary["swing_lows_below"]  = [round(p, 2) for p in below_lows[:3]]
    else:
        summary["nearest_swing_low"] = None
        summary["swing_lows_below"]  = []

    # ── OTE Zone (ICT Optimal Trade Entry: 61.8%–78.6% retracement) ─
    _sh = summary.get("nearest_swing_high")
    _sl = summary.get("nearest_swing_low")
    if _sh and _sl and _sh > _sl:
        _range = _sh - _sl
        # OTE for BUY: ราคา retrace 61.8-78.6% จาก swing high ลงมา
        _ote_buy_high  = round(_sh - _range * 0.618, 2)
        _ote_buy_low   = round(_sh - _range * 0.786, 2)
        # OTE for SELL: ราคา retrace 61.8-78.6% จาก swing low ขึ้นไป
        _ote_sell_low  = round(_sl + _range * 0.618, 2)
        _ote_sell_high = round(_sl + _range * 0.786, 2)
        summary["ote"] = {
            "swing_high":    _sh,
            "swing_low":     _sl,
            "range_pts":     round(_range * 10, 0),
            "ote_buy_zone":  [_ote_buy_low,  _ote_buy_high],
            "ote_sell_zone": [_ote_sell_low, _ote_sell_high],
            "in_ote_buy":    _ote_buy_low  <= current_price <= _ote_buy_high,
            "in_ote_sell":   _ote_sell_low <= current_price <= _ote_sell_high,
        }
    else:
        summary["ote"] = None

    # ── PDH/PDL + Round Numbers ───────────────────────────────
    if df is not None:
        try:
            df_tmp = df.copy()
            df_tmp.index = pd.to_datetime(df_tmp.index)
            df_tmp["_date"] = df_tmp.index.date
            today_date = df_tmp["_date"].iloc[-1]
            prev_df = df_tmp[df_tmp["_date"] < today_date]
            if not prev_df.empty:
                prev_day = prev_df["_date"].max()
                prev_day_df = prev_df[prev_df["_date"] == prev_day]
                summary["pdh"] = round(prev_day_df["high"].max(), 2)
                summary["pdl"] = round(prev_day_df["low"].min(), 2)
            else:
                summary["pdh"] = None
                summary["pdl"] = None
            # Round numbers (multiples of 50) ใกล้ราคา ±300 pts
            step = 50.0
            base = round(current_price / step) * step
            summary["round_levels"] = sorted([
                round(base + i * step, 2)
                for i in range(-3, 4)
                if abs((base + i * step) - current_price) <= 300
            ])
        except Exception:
            summary["pdh"] = None
            summary["pdl"] = None
            summary["round_levels"] = []

    # ── Liquidity Map ─────────────────────────────────────────
    try:
        summary["liquidity"] = classify_liquidity(result, current_price, df=df,
                                                    swing_highs=pool_swing_highs,
                                                    swing_lows=pool_swing_lows)
    except Exception:
        summary["liquidity"] = {}

    # ── Liquidity Zones (โซนสะสม — ราคาเทสซ้ำๆ, จับไม่ได้ด้วย fractal swing) ──
    if df is not None:
        try:
            zones = find_liquidity_zones(df, current_price)
            summary["liquidity_zones"] = {
                "support":    [z for z in zones if z["kind"] == "support"][:3],
                "resistance": [z for z in zones if z["kind"] == "resistance"][:3],
            }
        except Exception:
            summary["liquidity_zones"] = {"support": [], "resistance": []}

    # ── Advanced Signals + Swing Entry (จาก df) ──────────────
    if df is not None:
        try:
            adv  = advanced_signals(df, result)
            sess = get_session()
            rev  = detect_swing_entry(df, result)

            summary["session"]   = sess
            summary["advanced"]  = adv
            summary["reversal"]  = rev   # backward-compat key
            summary["swing"]     = rev   # new key

            # shortcuts ที่ใช้บ่อย
            summary["signal_type"]       = adv.get("signal_type")
            summary["long_stars"]        = adv.get("long_stars")
            summary["short_stars"]       = adv.get("short_stars")
            summary["h1_bull"]           = adv.get("h1_bull")
            summary["h4_bull"]           = adv.get("h4_bull")
            summary["momentum_bear"]     = adv.get("momentum_bear")
            summary["momentum_bull"]     = adv.get("momentum_bull")
            summary["tradeable_session"] = sess.get("tradeable", True)

            # swing shortcuts (+ backward-compat reversal_*)
            summary["reversal_signal"] = rev.get("swing_signal")
            summary["reversal_stars"]  = rev.get("swing_stars")
            summary["reversal_score"]  = rev.get("swing_score", 0)
            summary["swing_signal"]    = rev.get("swing_signal")
            summary["swing_stars"]     = rev.get("swing_stars")
            summary["swing_score"]     = rev.get("swing_score", 0)
        except Exception:
            pass

        # EQL / EQH Liquidity Sweep detection
        # EQL = SSL (sell-side liquidity = equal lows)
        # EQH = BSL (buy-side liquidity = equal highs)
        # → ถ้า sweep เกิด ให้ยก last_sweep ขึ้นมา CASE F ด้วย
        try:
            eql_eqh = detect_eql_eqh_sweep(df, result)
            summary["eql_sweep_signal"] = eql_eqh.get("eql_sweep_signal")
            summary["eqh_sweep_signal"] = eql_eqh.get("eqh_sweep_signal")

            # Unify EQL/EQH sweep กับ last_sweep — ให้ CASE F มองเห็นเหมือนกัน
            _eql_sig = eql_eqh.get("eql_sweep_signal")
            _eqh_sig = eql_eqh.get("eqh_sweep_signal")
            if _eql_sig and not summary.get("last_sweep"):
                # EQL swept = SSL swept → BUY opportunity
                summary["last_sweep"] = {
                    "kind":      "SSL",
                    "level":     _eql_sig["eql_level"],
                    "recovered": True,
                    "source":    "EQL",
                    "sweep_extreme": _eql_sig["sweep_low"],
                }
            elif _eqh_sig and not summary.get("last_sweep"):
                # EQH swept = BSL swept → SELL opportunity
                summary["last_sweep"] = {
                    "kind":      "BSL",
                    "level":     _eqh_sig["eqh_level"],
                    "recovered": True,
                    "source":    "EQH",
                    "sweep_extreme": _eqh_sig["sweep_high"],
                }
        except Exception:
            summary["eql_sweep_signal"] = None
            summary["eqh_sweep_signal"] = None

        # AMD (Accumulation/Manipulation/Distribution) pattern
        try:
            amd = detect_amd_pattern(df, result)
            summary["amd"] = amd
        except Exception:
            summary["amd"] = {"amd_signal": None, "amd_score": 0}

        # ── Recent OB Rejection (ตรวจว่าเพิ่งมี rejection ที่ OB ใน 12 แท่งล่าสุด) ──
        # ใช้เมื่อ price ออกจาก OB ไปแล้ว แต่ rejection เพิ่งเกิด → CASE G/I ยังใช้ได้
        # 12 แท่ง = 60 นาทีบน M5 → จำ OB rejection ได้นานพอสำหรับ Pattern 3
        try:
            _lookback = 12
            _recent   = df.iloc[-_lookback:].reset_index(drop=True)

            _bear_rej = None
            _bull_rej = None

            if active_bear_ob:
                _ob_bot = active_bear_ob.bottom
                _ob_top = active_bear_ob.top
                for _i, _row in _recent.iterrows():
                    if _row['high'] >= _ob_bot:          # wick/body แตะ Bear OB
                        _this_bear = _row['close'] < _row['open']
                        _next_bear = False
                        if _i + 1 < len(_recent):
                            _nxt = _recent.iloc[_i + 1]
                            _next_bear = (_nxt['close'] < _nxt['open']
                                          and _nxt['close'] < _ob_bot)
                        if _this_bear or _next_bear:
                            _bear_rej = {
                                "ob_zone": [round(_ob_bot, 2), round(_ob_top, 2)],
                                "bars_ago": int(_lookback - 1 - _i),
                                "direction": "SELL",
                            }
                            break

            if active_bull_ob:
                _ob_bot = active_bull_ob.bottom
                _ob_top = active_bull_ob.top
                for _i, _row in _recent.iterrows():
                    if _row['low'] <= _ob_top:           # wick/body แตะ Bull OB
                        _this_bull = _row['close'] > _row['open']
                        _next_bull = False
                        if _i + 1 < len(_recent):
                            _nxt = _recent.iloc[_i + 1]
                            _next_bull = (_nxt['close'] > _nxt['open']
                                          and _nxt['close'] > _ob_top)
                        if _this_bull or _next_bull:
                            _bull_rej = {
                                "ob_zone": [round(_ob_bot, 2), round(_ob_top, 2)],
                                "bars_ago": int(_lookback - 1 - _i),
                                "direction": "BUY",
                            }
                            break

            summary["recent_bear_ob_rejection"] = _bear_rej
            summary["recent_bull_ob_rejection"] = _bull_rej
        except Exception:
            summary["recent_bear_ob_rejection"] = None
            summary["recent_bull_ob_rejection"] = None

        # ── OB Quality: ระยะห่าง OB จาก sweep level ──────────────────────
        # OB ที่ดีต้องไม่ใกล้ sweep เกิน (smart money ต้องมีพื้นที่ accumulate ก่อน sweep)
        # OB ใกล้ sweep level เกิน = ไม่มีพื้นที่สะสม → rejection อ่อน
        try:
            _ob_quality = {}
            if last_sweep and active_bear_ob:
                # BSL sweep (high swept) + Bear OB
                if last_sweep.kind == 'high':
                    _gap = (last_sweep.level - active_bear_ob.top) * 10  # แปลงเป็น points
                    _ob_quality["bear_ob_sweep_gap_pts"] = round(_gap, 0)
                    _ob_quality["bear_ob_quality"] = (
                        "HIGH" if _gap >= 100 else
                        "MEDIUM" if _gap >= 50 else
                        "LOW"  # OB ใกล้ sweep เกิน — ไม่มีพื้นที่ accumulate
                    )
            if last_sweep and active_bull_ob:
                # SSL sweep (low swept) + Bull OB
                if last_sweep.kind == 'low':
                    _gap = (active_bull_ob.bottom - last_sweep.level) * 10
                    _ob_quality["bull_ob_sweep_gap_pts"] = round(_gap, 0)
                    _ob_quality["bull_ob_quality"] = (
                        "HIGH" if _gap >= 100 else
                        "MEDIUM" if _gap >= 50 else
                        "LOW"
                    )
            summary["ob_quality"] = _ob_quality
        except Exception:
            summary["ob_quality"] = {}

        # ── Post-Sweep Continuation Pullback ──────────────────────────────
        # หลัง sweep + rejection → ราคาวิ่งตามทิศ → ตอนนี้มี pullback เล็กน้อย → entry ตามทิศ
        #
        # user feedback: 2 path —
        #  (1) FAST: sweep แล้วดู 2-3 แท่ง ถ้า pullback เข้าได้เลย (เดิม window 30
        #      bars หลวมเกินไป ทำให้เข้าช้า)
        #  (2) BIG-MOVE RETEST: ถ้าราคาวิ่งต่อไปไกล (≥500pts) แล้ว pullback กลับมา
        #      "ใกล้ๆ จุด sweep" (ไม่ใช่แค่ 15% ของ move) ให้ถือว่ายัง valid แม้จะ
        #      ผ่านมาหลายแท่งแล้วก็ตาม — เป็นการ retest level เดิมหลัง trend ไปไกล
        #
        # ใช้ sweep แยกทิศจาก result.sweeps ตรงๆ (ไม่ใช่ last_sweep ตัวเดียวรวมทุก
        # ทิศ) เหตุผลเดียวกับที่แก้ chart_analyst_agent.py ไปแล้ว — ถ้า sweep อีกทิศ
        # เกิดทีหลัง last_sweep จะทับ มองไม่เห็นว่า sweep ทิศที่ตรวจอยู่นี้ยัง valid
        try:
            _FAST_BARS   = 8      # ~2-3 แท่ง pullback + แท่ง sweep เอง ให้ buffer นิดหน่อย
            _BIG_MOVE_PT = 500    # จุด (×10 convention เดียวกับ pullback_pts) — ~$50
            _RETEST_TOL  = 20     # จุด — ราคาต้องกลับมาใกล้ sweep level แค่ไหนถึงนับว่า "ใกล้ๆ"

            _high_sweeps = [s for s in (result.sweeps or []) if s.kind == 'sweep_high']
            _low_sweeps  = [s for s in (result.sweeps or []) if s.kind == 'sweep_low']
            _last_high_sw = _high_sweeps[-1] if _high_sweeps else None
            _last_low_sw  = _low_sweeps[-1] if _low_sweeps else None
            _sw_age_h = (len(df) - 1 - _last_high_sw.index) if _last_high_sw is not None else 999
            _sw_age_l = (len(df) - 1 - _last_low_sw.index) if _last_low_sw is not None else 999

            _post_cont = None

            if _last_high_sw is not None:
                # BSL swept → expect SELL continuation
                _sw_lvl    = _last_high_sw.level
                _bars_back = min(_sw_age_h, len(df) - 1)
                _low_since = df['low'].iloc[-_bars_back:].min() if _bars_back > 0 else current_price
                _initial_drop = (_sw_lvl - _low_since) * 10
                _pullback_now = (current_price - _low_since) * 10
                _dist_to_sweep = abs(_sw_lvl - current_price) * 10
                _level_held = current_price < _sw_lvl  # ยังไม่ทะลุ sweep high กลับขึ้นไป

                _fast_ok = _sw_age_h <= _FAST_BARS and _initial_drop >= 30 and _pullback_now >= 10
                _retest_ok = _initial_drop >= _BIG_MOVE_PT and _dist_to_sweep <= _RETEST_TOL

                if _level_held and (_fast_ok or _retest_ok):
                    _pb_pct = (_pullback_now / _initial_drop) if _initial_drop else 0
                    if _fast_ok and _pb_pct < 0.15:
                        pass  # fast path ยังต้องมี pullback อย่างน้อย 15% ของ initial move
                    else:
                        _post_cont = {
                            "direction":       "SELL",
                            "sweep_level":     round(_sw_lvl, 2),
                            "sweep_age_bars":  _sw_age_h,
                            "initial_drop_pts": round(_initial_drop, 0),
                            "pullback_pts":    round(_pullback_now, 0),
                            "pullback_pct":    round(_pb_pct * 100, 0),
                            "low_since_sweep": round(_low_since, 2),
                            "level_held":      True,
                            "retest_after_big_move": bool(_retest_ok and not _fast_ok),
                        }

            if _post_cont is None and _last_low_sw is not None:
                # SSL swept → expect BUY continuation
                _sw_lvl     = _last_low_sw.level
                _bars_back  = min(_sw_age_l, len(df) - 1)
                _high_since = df['high'].iloc[-_bars_back:].max() if _bars_back > 0 else current_price
                _initial_rise = (_high_since - _sw_lvl) * 10
                _pullback_now = (_high_since - current_price) * 10
                _dist_to_sweep = abs(current_price - _sw_lvl) * 10
                _level_held = current_price > _sw_lvl  # ยังไม่ทะลุ sweep low กลับลงไป

                _fast_ok = _sw_age_l <= _FAST_BARS and _initial_rise >= 30 and _pullback_now >= 10
                _retest_ok = _initial_rise >= _BIG_MOVE_PT and _dist_to_sweep <= _RETEST_TOL

                if _level_held and (_fast_ok or _retest_ok):
                    _pb_pct = (_pullback_now / _initial_rise) if _initial_rise else 0
                    if _fast_ok and _pb_pct < 0.15:
                        pass
                    else:
                        _post_cont = {
                            "direction":        "BUY",
                            "sweep_level":      round(_sw_lvl, 2),
                            "sweep_age_bars":   _sw_age_l,
                            "initial_rise_pts": round(_initial_rise, 0),
                            "pullback_pts":     round(_pullback_now, 0),
                            "pullback_pct":     round(_pb_pct * 100, 0),
                            "high_since_sweep": round(_high_since, 2),
                            "level_held":       True,
                            "retest_after_big_move": bool(_retest_ok and not _fast_ok),
                        }

            summary["post_sweep_continuation"] = _post_cont
        except Exception:
            summary["post_sweep_continuation"] = None

        # ── CHoCH + Sweep → Rejection (CASE K) ──────────────────────────
        # Pattern: CHoCH confirms reversal → sweep near CHoCH level → rejection → BUY/SELL alert
        try:
            summary["choch_sweep_setup"] = _detect_choch_sweep_setup(df, result, current_price)
        except Exception:
            summary["choch_sweep_setup"] = None

        # ── Post-BOS CHoCH Retest (CASE L) ───────────────────────────────
        # Pattern: BOS bearish → minor bullish CHoCH (bounce) → price returns to CHoCH level → SELL
        #          BOS bullish → minor bearish CHoCH (dip) → price returns to CHoCH level → BUY
        try:
            summary["post_bos_choch_retest"] = _detect_post_bos_choch_retest(df, result, current_price)
        except Exception:
            summary["post_bos_choch_retest"] = None

    return summary


def _detect_choch_sweep_setup(df, result, current_price: float) -> dict | None:
    """
    CASE K — CHoCH + Liquidity Sweep → Rejection
    มาบ่อยมาก: CHoCH ยืนยัน reversal → sweep ย้อนกลับ → rejection → BUY/SELL
    """
    n = len(df)
    last_choch = result.last_choch
    if not last_choch or n < 5:
        return None

    choch_age = n - 1 - last_choch.index
    if choch_age > 30:  # CHoCH เก่าเกิน (30 bars = 2.5h บน M5)
        return None

    # BUY setup: bullish CHoCH + SSL sweep (sweep_low)
    # SELL setup: bearish CHoCH + BSL sweep (sweep_high)
    target_kind = "sweep_low" if last_choch.direction == "bullish" else "sweep_high"

    best_sweep = None
    best_age = 999
    for s in (result.sweeps or []):
        if s.kind != target_kind:
            continue
        s_age = n - 1 - s.index
        if s_age <= 20 and s_age < best_age:
            best_sweep = s
            best_age = s_age

    if not best_sweep:
        return None

    direction = "BUY" if last_choch.direction == "bullish" else "SELL"

    # ตรวจ rejection candle ในช่วงหลัง sweep (≤5 แท่ง)
    has_rejection = False
    sweep_idx = best_sweep.index
    for i in range(sweep_idx, min(sweep_idx + 6, n)):
        row = df.iloc[i]
        body = abs(row["close"] - row["open"])
        total = row["high"] - row["low"]
        if total < 0.01:
            continue
        if direction == "BUY" and row["close"] > row["open"] and body / total > 0.4:
            has_rejection = True
            break
        elif direction == "SELL" and row["close"] < row["open"] and body / total > 0.4:
            has_rejection = True
            break

    if has_rejection and best_age <= 5:
        confidence = "HIGH"
    elif best_age <= 12:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "direction":           direction,
        "choch_level":         round(last_choch.level, 2),
        "sweep_level":         round(best_sweep.level, 2),
        "choch_age_bars":      int(choch_age),
        "sweep_age_bars":      int(best_age),
        "rejection_confirmed": has_rejection,
        "confidence":          confidence,
        "current_price":       round(current_price, 2),
    }


def _detect_post_bos_choch_retest(df, result, current_price: float) -> dict | None:
    """
    CASE L — Post-BOS CHoCH Retest (Trend Continuation)

    Pattern:
      BOS bearish → minor bullish CHoCH (bounce) → price returns near CHoCH level → SELL
      BOS bullish → minor bearish CHoCH (dip) → price returns near CHoCH level → BUY

    Entry zone = CHoCH swing high (for SELL) / swing low (for BUY)
    ↳ ที่วงกลมในชาร์ต: BOS ลง → แท่งเขียวเด้ง (CHoCH) → แดงแรกหลังถึง CHoCH high = entry
    """
    n = len(df)
    last_bos   = result.last_bos
    last_choch = result.last_choch

    if not last_bos or not last_choch or n < 10:
        return None

    bos_age   = n - 1 - last_bos.index
    choch_age = n - 1 - last_choch.index

    # BOS ต้องยังไม่เก่าเกิน (60 bars = 5h M5)
    if bos_age > 60:
        return None

    # CHoCH ต้องเกิดหลัง BOS (choch_age < bos_age = CHoCH ล่าสุดกว่า)
    if choch_age >= bos_age:
        return None

    # CHoCH ต้องทวนทิศ BOS (= retracement ไม่ใช่ reversal ใหม่)
    #   BOS bearish → CHoCH bullish (bounce ขึ้น)
    #   BOS bullish → CHoCH bearish (dip ลง)
    if last_bos.direction == "bearish" and last_choch.direction != "bullish":
        return None
    if last_bos.direction == "bullish" and last_choch.direction != "bearish":
        return None

    direction   = "SELL" if last_bos.direction == "bearish" else "BUY"
    choch_level = last_choch.level
    dist        = abs(current_price - choch_level)

    # price ต้องอยู่ใกล้ CHoCH level (≤25pts) — กำลัง approach หรือ retest
    if dist > 25:
        return None

    # คำนวณ CHoCH swing extreme (high สำหรับ SELL, low สำหรับ BUY)
    # หาค่า max high / min low ในช่วง ±5 bars รอบ CHoCH
    ch_i   = last_choch.index
    window = df.iloc[max(0, ch_i - 3): min(n, ch_i + 6)]
    if direction == "SELL":
        swing_extreme = float(window["high"].max())
        sl_ref = round(swing_extreme + 3.0, 2)
    else:
        swing_extreme = float(window["low"].min())
        sl_ref = round(swing_extreme - 3.0, 2)

    # retracement depth — deep retracement (>70% of BOS move) = อาจ reverse จริง ไม่ใช่ pullback
    bos_level = last_bos.level
    bos_start_idx = last_bos.index
    if direction == "SELL":
        # BOS bearish: BOS level = swing high ที่ถูกทะลุลงมา (หรือ swing low ที่ break)
        # retracement = ระยะที่ price วิ่งกลับขึ้นหลัง BOS
        price_at_bos = float(df["close"].iloc[bos_start_idx])
        low_since_bos = float(df["low"].iloc[bos_start_idx:n].min())
        bos_move = price_at_bos - low_since_bos
        retracement = choch_level - low_since_bos
    else:
        price_at_bos = float(df["close"].iloc[bos_start_idx])
        high_since_bos = float(df["high"].iloc[bos_start_idx:n].max())
        bos_move = high_since_bos - price_at_bos
        retracement = high_since_bos - choch_level

    retrace_pct = round((retracement / bos_move * 100) if bos_move > 0 else 0, 0)

    # ถ้า retracement > 80% = reversal ไม่ใช่ pullback → ไม่นับ
    if retrace_pct > 80:
        return None

    # ตรวจ rejection candle ใน 6 bars ล่าสุด ใกล้ CHoCH level
    has_rejection = False
    for i in range(max(0, n - 6), n):
        row  = df.iloc[i]
        body  = abs(row["close"] - row["open"])
        total = row["high"] - row["low"]
        if total < 0.01:
            continue
        near = abs(((row["high"] + row["low"]) / 2) - choch_level) <= 10
        if not near:
            continue
        if direction == "SELL" and row["close"] < row["open"] and body / total > 0.35:
            has_rejection = True
            break
        elif direction == "BUY" and row["close"] > row["open"] and body / total > 0.35:
            has_rejection = True
            break

    return {
        "direction":           direction,
        "bos_level":           round(bos_level, 2),
        "bos_age_bars":        int(bos_age),
        "choch_level":         round(choch_level, 2),
        "choch_age_bars":      int(choch_age),
        "swing_extreme":       round(swing_extreme, 2),
        "sl_ref":              sl_ref,
        "dist_pts":            round(dist, 1),
        "retrace_pct":         int(retrace_pct),
        "rejection_confirmed": has_rejection,
        "current_price":       round(current_price, 2),
    }
