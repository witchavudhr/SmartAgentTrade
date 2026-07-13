"""
POS Guard — Per-position trailing stop (Python port of SmartPartialClose.mq5 PosGuard)

Logic (mirrors MQL5 CheckPerPosGuard exactly):
  1. Poll open positions every POSGUARD_CHECK_INTERVAL seconds
  2. Each position: when raw profit ≥ trigger_usd → lock SL at open + lock_ticks
  3. If step_ticks > 0 → staircase trail:
       P0 = trigger price (open ± trigger_ticks)
       step = floor((current_price - P0) / step_ticks * tick_size)
       guard_sl = initial_sl + step * step_ticks * tick_size
  4. High-water mark: SL never moves backward
  5. Only moves SL in protective direction

Config (.env):
  POSGUARD_ENABLED=true
  POSGUARD_TRIGGER_USD=5.0     # profit $ ก่อน lock
  POSGUARD_LOCK_TICKS=10.0     # ticks เหนือ open (initial BE guard)
  POSGUARD_STEP_TICKS=0.0      # trail step ticks (0 = lock only, ไม่ trail)
  POSGUARD_CHECK_INTERVAL=10   # วินาที ระหว่างเช็ค
"""

import asyncio
import logging
import math
from config.settings import (
    POSGUARD_ENABLED,
    POSGUARD_TRIGGER_USD,
    POSGUARD_LOCK_TICKS,
    POSGUARD_STEP_TICKS,
    POSGUARD_CHECK_INTERVAL,
)

logger = logging.getLogger(__name__)

# XAUUSD: tick size = 0.01 ($), tick value per 1.0 lot = $1.00
TICK_SIZE  = 0.01
TICK_VALUE = 1.0   # $ per tick per 1.0 lot


# ── Core math ─────────────────────────────────────────────────────────────────

def _current_price_from_pos(pos: dict) -> float:
    """Reconstruct current price from MT5 position profit field"""
    lot = pos["lot"]
    tv  = TICK_VALUE * lot
    if tv == 0:
        return pos["entry"]
    if pos["direction"] == "BUY":
        return pos["entry"] + (pos["profit"] / tv) * TICK_SIZE
    else:
        return pos["entry"] - (pos["profit"] / tv) * TICK_SIZE


def compute_guard_sl(pos: dict, current_price: float | None = None) -> float | None:
    """
    คำนวณ SL ที่ POS Guard ต้องการสำหรับ position นี้
    คืน float = ราคา SL ใหม่  หรือ  None ถ้า profit ยังไม่ถึง trigger
    """
    if current_price is None:
        current_price = _current_price_from_pos(pos)

    is_buy     = pos["direction"] == "BUY"
    open_price = pos["entry"]
    lot        = pos["lot"]

    # raw profit ไม่รวม swap (ใช้ราคาเพื่อ trigger ที่แม่น)
    if is_buy:
        price_dist = current_price - open_price
    else:
        price_dist = open_price - current_price
    raw_profit = (price_dist / TICK_SIZE) * TICK_VALUE * lot

    if raw_profit < POSGUARD_TRIGGER_USD:
        return None

    # Initial BE guard: open + lock_ticks
    lock_dist  = POSGUARD_LOCK_TICKS * TICK_SIZE
    initial_sl = round(open_price + lock_dist if is_buy else open_price - lock_dist, 2)
    guard_sl   = initial_sl

    if POSGUARD_STEP_TICKS > 0:
        tick_val_lot = TICK_VALUE * lot
        trigger_ticks = (POSGUARD_TRIGGER_USD / tick_val_lot) if tick_val_lot > 0 else 0
        step_dist     = POSGUARD_STEP_TICKS * TICK_SIZE

        # P0 = ราคาที่ trigger เกิดขึ้น
        if is_buy:
            p0           = round(open_price + trigger_ticks * TICK_SIZE, 2)
            dist_from_p0 = current_price - p0
        else:
            p0           = round(open_price - trigger_ticks * TICK_SIZE, 2)
            dist_from_p0 = p0 - current_price

        if dist_from_p0 >= 0 and step_dist > 0:
            n = int(math.floor(dist_from_p0 / step_dist + 1e-9))
            if is_buy:
                step_sl = round(initial_sl + n * step_dist, 2)
            else:
                step_sl = round(initial_sl - n * step_dist, 2)

            if n >= 1:
                logger.debug(
                    f"PosGuard STAIR | tk:{pos['ticket']} open:{open_price} "
                    f"P0:{p0} cur:{current_price:.2f} dist:{dist_from_p0:.2f} "
                    f"n:{n} stepSL:{step_sl}"
                )

            if (is_buy and step_sl > guard_sl) or (not is_buy and step_sl < guard_sl):
                guard_sl = step_sl

        # High-water mark: ถ้า existSL ดีกว่า step ที่คำนวณได้ → คง existSL ไว้
        exist_sl = pos.get("sl") or 0.0
        if exist_sl:
            if (is_buy  and exist_sl > guard_sl):
                guard_sl = exist_sl
            elif (not is_buy and exist_sl < guard_sl):
                guard_sl = exist_sl

    return guard_sl


def should_move_sl(pos: dict, guard_sl: float) -> bool:
    """เช็คว่าต้องเลื่อน SL หรือไม่ (ทิศ protective เท่านั้น, ไม่ถอยหลัง)"""
    is_buy   = pos["direction"] == "BUY"
    exist_sl = pos.get("sl") or 0.0
    point    = 0.001  # tolerance

    if is_buy:
        return guard_sl > exist_sl + point
    else:
        return (exist_sl == 0) or (guard_sl < exist_sl - point)


# ── Job / Loop ────────────────────────────────────────────────────────────────

def _check_once_sync() -> list[dict]:
    """
    ส่วน MT5 native call ทั้งหมดของ PosGuard — รันผ่าน run_in_executor เพราะ
    mt5_executor ไม่มี timeout ในตัว ถ้าเรียกตรงบน event loop แล้ว terminal ค้าง
    จะบล็อกบอททั้งตัว (เหมือนที่เจอใน trade_monitor — job นี้รันถี่กว่ามาก
    ทุก POSGUARD_CHECK_INTERVAL วินาที เสี่ยงกว่าอีก)
    """
    try:
        from agents import mt5_executor
    except ImportError:
        return []

    if not mt5_executor.is_available():
        return []

    positions = mt5_executor.get_open_positions()
    results   = []

    for pos in positions:
        current_price = _current_price_from_pos(pos)
        guard_sl      = compute_guard_sl(pos, current_price)
        if guard_sl is None:
            continue
        if not should_move_sl(pos, guard_sl):
            continue

        res = mt5_executor.modify_sl(pos["ticket"], guard_sl)
        entry = {
            "ticket":  pos["ticket"],
            "old_sl":  pos.get("sl", 0),
            "new_sl":  guard_sl,
            "profit":  pos["profit"],
            "current": round(current_price, 2),
        }
        entry.update(res)

        if res.get("ok"):
            logger.info(
                f"PosGuard OK | tk:{pos['ticket']} {pos['direction']} "
                f"open:{pos['entry']} SL:{pos.get('sl',0)}→{guard_sl} "
                f"profit:${pos['profit']:.2f} cur:{current_price:.2f}"
            )
        else:
            logger.warning(f"PosGuard FAIL | tk:{pos['ticket']} {res.get('error')}")

        results.append(entry)

    return results


async def check_once() -> list[dict]:
    """
    เช็ค 1 รอบ — คืน list ของ {ticket, old_sl, new_sl, profit, ok/error}
    เรียกจาก PTB job_queue หรือ standalone loop
    """
    if not POSGUARD_ENABLED:
        return []
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check_once_sync)


# ── Config summary ────────────────────────────────────────────────────────────

def get_config() -> dict:
    return {
        "enabled":        POSGUARD_ENABLED,
        "trigger_usd":    POSGUARD_TRIGGER_USD,
        "lock_ticks":     POSGUARD_LOCK_TICKS,
        "step_ticks":     POSGUARD_STEP_TICKS,
        "check_interval": POSGUARD_CHECK_INTERVAL,
        "mode":           "trail" if POSGUARD_STEP_TICKS > 0 else "lock-only",
    }


def describe() -> str:
    """สรุป config เป็น string สำหรับ Telegram"""
    cfg = get_config()
    if not cfg["enabled"]:
        return "⚪ POS Guard: *disabled*"
    mode_str = (
        f"staircase trail `{cfg['step_ticks']}` ticks/step"
        if cfg["mode"] == "trail"
        else "lock-only (no trail)"
    )
    return (
        f"🛡 *POS Guard: active*\n"
        f"  Trigger: `${cfg['trigger_usd']}` profit\n"
        f"  Lock: `{cfg['lock_ticks']}` ticks above open\n"
        f"  Mode: {mode_str}\n"
        f"  Check every: `{cfg['check_interval']}s`"
    )
