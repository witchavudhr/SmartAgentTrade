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


# ─── Format Summary ───────────────────────────────────────────────

def summarize(result: SMCResult, current_price: float) -> dict:
    """แปลง SMCResult เป็น dict สรุปสำหรับส่งให้ Claude"""

    last_sweep = result.last_sweep
    active_ob = result.active_ob
    last_bos = result.last_bos
    last_choch = result.last_choch

    # เช็คว่า price อยู่ใน OB มั้ย
    in_ob = False
    ob_kind = None
    if active_ob:
        if active_ob.bottom <= current_price <= active_ob.top:
            in_ob = True
            ob_kind = active_ob.kind

    # FVG ที่ยังไม่ถูก fill
    open_fvgs = [f for f in result.fvgs if not f.filled]
    nearest_fvg = None
    if open_fvgs:
        nearest_fvg = min(open_fvgs, key=lambda f: abs((f.top + f.bottom) / 2 - current_price))

    return {
        "current_price": current_price,
        "bias": result.current_bias,

        "last_bos": {
            "direction": last_bos.direction,
            "level": last_bos.level
        } if last_bos else None,

        "last_choch": {
            "direction": last_choch.direction,
            "level": last_choch.level
        } if last_choch else None,

        "last_sweep": {
            "kind": last_sweep.kind,
            "level": last_sweep.level,
            "recovered": last_sweep.recovered
        } if last_sweep else None,

        "active_ob": {
            "kind": active_ob.kind,
            "top": active_ob.top,
            "bottom": active_ob.bottom,
            "in_ob": in_ob
        } if active_ob else None,

        "nearest_fvg": {
            "kind": nearest_fvg.kind,
            "top": nearest_fvg.top,
            "bottom": nearest_fvg.bottom
        } if nearest_fvg else None,

        "equal_highs": result.equal_highs[-3:] if result.equal_highs else [],
        "equal_lows": result.equal_lows[-3:] if result.equal_lows else [],

        "swing_high_count": len(result.swing_highs),
        "swing_low_count": len(result.swing_lows),
        "total_structures": len(result.structures),
        "total_sweeps": len(result.sweeps),
    }
