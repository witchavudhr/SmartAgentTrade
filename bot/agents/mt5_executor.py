"""
MT5 Executor — เปิด/ปิด trade ใน MetaTrader 5 โดยตรง
ใช้ได้เฉพาะ Windows + ติดตั้ง MetaTrader5 package แล้ว

pip install MetaTrader5
"""

import time
from config.settings import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER,
    MT5_SYMBOL, MT5_MAGIC, MT5_ENABLED,
    MT5_MAX_SLIPPAGE_PIPS, MT5_PATH,
)

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False


# ── Connection ────────────────────────────────────────────────────────────────

def _connect() -> tuple[bool, str]:
    """เชื่อม MT5 — คืน (success, error_msg)"""
    if not _MT5_AVAILABLE:
        return False, "MetaTrader5 package ไม่ได้ติดตั้ง (Windows only)"
    if not MT5_ENABLED:
        return False, "MT5_ENABLED=false ใน .env"
    kwargs = dict(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if MT5_PATH:
        kwargs["path"] = MT5_PATH
    if not mt5.initialize(**kwargs):
        err = mt5.last_error()
        return False, f"initialize() ล้มเหลว: {err}"
    return True, ""


def disconnect():
    if _MT5_AVAILABLE:
        mt5.shutdown()


def get_account_info() -> dict:
    """ดูข้อมูล account (balance, equity, margin)"""
    ok, err = _connect()
    if not ok:
        return {"error": err}
    info = mt5.account_info()
    disconnect()
    if not info:
        return {"error": str(mt5.last_error())}
    return {
        "login":   info.login,
        "balance": info.balance,
        "equity":  info.equity,
        "margin":  info.margin,
        "free_margin": info.margin_free,
        "currency": info.currency,
        "server":  info.server,
        "leverage": info.leverage,
    }


# ── Trade Execution ───────────────────────────────────────────────────────────

def open_trade(
    direction: str,      # "BUY" or "SELL"
    lot: float,
    sl: float,
    tp: float,
    comment: str = "SmartAgentTrade",
) -> dict:
    """
    เปิด Market Order ทันที
    คืน {"ticket": int, "price": float, "time": str} หรือ {"error": str}
    """
    if not MT5_ENABLED:
        return {"error": "MT5_ENABLED=false — dry run only"}

    ok, err = _connect()
    if not ok:
        return {"error": err}

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

    # ดึงราคา ask/bid ล่าสุด
    tick = mt5.symbol_info_tick(MT5_SYMBOL)
    if tick is None:
        disconnect()
        return {"error": f"ดึง tick ของ {MT5_SYMBOL} ไม่ได้"}

    price = tick.ask if direction == "BUY" else tick.bid

    # ตรวจ SL/TP valid ก่อน send — ป้องกัน Retcode 10016
    if tp and tp > 0:
        if direction == "BUY" and tp <= price:
            disconnect()
            return {"error": f"Invalid stops: BUY TP {tp} ต้องสูงกว่าราคา {round(price,2)} — entry zone ห่างจากตลาดมากเกินไป"}
        if direction == "SELL" and tp >= price:
            disconnect()
            return {"error": f"Invalid stops: SELL TP {tp} ต้องต่ำกว่าราคา {round(price,2)} — entry zone ห่างจากตลาดมากเกินไป"}
    if sl:
        if direction == "BUY" and sl >= price:
            disconnect()
            return {"error": f"Invalid stops: BUY SL {sl} ต้องต่ำกว่าราคา {round(price,2)}"}
        if direction == "SELL" and sl <= price:
            disconnect()
            return {"error": f"Invalid stops: SELL SL {sl} ต้องสูงกว่าราคา {round(price,2)}"}

    # slippage = MT5_MAX_SLIPPAGE_PIPS points (XAUUSD: 1 pip = 10 points)
    slippage_points = MT5_MAX_SLIPPAGE_PIPS * 10

    request = {
        "action":    mt5.TRADE_ACTION_DEAL,
        "symbol":    MT5_SYMBOL,
        "volume":    lot,
        "type":      order_type,
        "price":     price,
        "sl":        sl,
        "tp":        tp,
        "deviation": slippage_points,
        "magic":     MT5_MAGIC,
        "comment":   comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    disconnect()

    if result is None:
        return {"error": f"order_send คืน None: {mt5.last_error()}"}

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return {
            "error":   f"Retcode {result.retcode}: {_retcode_desc(result.retcode)}",
            "retcode": result.retcode,
        }

    return {
        "ticket":   result.order,
        "price":    result.price,
        "volume":   result.volume,
        "time":     time.strftime("%Y-%m-%d %H:%M:%S"),
        "comment":  comment,
    }


def close_trade(ticket: int, lot: float | None = None) -> dict:
    """
    ปิด position ด้วย ticket number
    lot=None → ปิดทั้งหมด
    """
    ok, err = _connect()
    if not ok:
        return {"error": err}

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        disconnect()
        return {"error": f"ไม่พบ position ticket {ticket}"}

    pos = positions[0]
    close_lot = lot or pos.volume
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(MT5_SYMBOL)
    price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask

    request = {
        "action":    mt5.TRADE_ACTION_DEAL,
        "symbol":    MT5_SYMBOL,
        "volume":    close_lot,
        "type":      close_type,
        "position":  ticket,
        "price":     price,
        "deviation": MT5_MAX_SLIPPAGE_PIPS * 10,
        "magic":     MT5_MAGIC,
        "comment":   "Close by SmartAgentTrade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    disconnect()

    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else "None"
        return {"error": f"ปิด trade ไม่สำเร็จ: retcode {code}"}

    return {
        "closed_ticket": ticket,
        "close_price":   result.price,
        "volume":        result.volume,
        "time":          time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def modify_sl(ticket: int, new_sl: float) -> dict:
    """
    เลื่อน SL ของ position ด้วย ticket number
    คืน {"ok": True} หรือ {"error": str}
    """
    ok, err = _connect()
    if not ok:
        return {"error": err}

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        disconnect()
        return {"error": f"ไม่พบ position ticket {ticket}"}

    pos = positions[0]
    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "symbol":   MT5_SYMBOL,
        "position": ticket,
        "sl":       new_sl,
        "tp":       pos.tp,
    }

    result = mt5.order_send(request)
    disconnect()

    if result is None or result.retcode not in (mt5.TRADE_RETCODE_DONE, 10025):
        code = result.retcode if result else "None"
        return {"error": f"modify SL ไม่สำเร็จ: retcode {code} — {_retcode_desc(code) if result else ''}"}

    return {"ok": True, "ticket": ticket, "new_sl": new_sl}


def get_open_positions() -> list[dict] | None:
    """คืน list ของ open positions ปัจจุบัน — คืน None ถ้า connect ล้มเหลว"""
    ok, err = _connect()
    if not ok:
        return None
    positions = mt5.positions_get(symbol=MT5_SYMBOL) or []
    disconnect()
    return [
        {
            "ticket":    p.ticket,
            "direction": "BUY" if p.type == 0 else "SELL",
            "lot":       p.volume,
            "entry":     p.price_open,
            "sl":        p.sl,
            "tp":        p.tp,
            "profit":    p.profit,
            "comment":   p.comment,
        }
        for p in positions
    ]


def get_last_deal_for_ticket(ticket: int) -> dict | None:
    """ดึง deal ปิดล่าสุดของ ticket จาก MT5 history (ย้อนหลัง 7 วัน, retry 3 ครั้ง)"""
    from datetime import datetime, timedelta
    import time as _time
    # DEAL_ENTRY_OUT=1, DEAL_ENTRY_INOUT=2, DEAL_ENTRY_OUT_BY=3
    EXIT_TYPES = {1, 2, 3}
    for attempt in range(3):
        ok, err = _connect()
        if not ok:
            print(f"[mt5] get_last_deal_for_ticket connect fail (attempt {attempt+1}): {err}")
            _time.sleep(1)
            continue
        date_from = datetime.now() - timedelta(days=7)
        date_to   = datetime.now() + timedelta(hours=1)
        deals = mt5.history_deals_get(date_from, date_to) or []
        disconnect()
        close_deals = [
            d for d in deals
            if d.position_id == ticket and d.entry in EXIT_TYPES
        ]
        if close_deals:
            d = close_deals[-1]
            return {"close_price": d.price, "profit": d.profit, "volume": d.volume,
                    "time": d.time, "commission": d.commission, "swap": d.swap}
        print(f"[mt5] get_last_deal_for_ticket: ticket={ticket} deals={len(deals)} close_deals=0 (attempt {attempt+1})")
        _time.sleep(2)
    return None


def get_latest_closed_deal(symbol: str | None = None, hours: int = 4) -> dict | None:
    """fallback: ดึง deal ปิดล่าสุดใน X ชั่วโมงที่ผ่านมา (ใช้เมื่อไม่รู้ ticket)
    symbol=None → ใช้ MT5_SYMBOL; ถ้า match symbol ไม่เจอ → ใช้ deal ปิดล่าสุดทุก symbol
    """
    from datetime import datetime, timedelta
    import time as _time
    sym = symbol or MT5_SYMBOL
    EXIT_TYPES = {1, 2, 3}
    for attempt in range(3):
        ok, err = _connect()
        if not ok:
            print(f"[mt5] get_latest_closed_deal connect fail (attempt {attempt+1}): {err}")
            _time.sleep(1)
            continue
        date_from = datetime.now() - timedelta(hours=hours)
        date_to   = datetime.now() + timedelta(hours=1)
        deals = mt5.history_deals_get(date_from, date_to) or []
        disconnect()
        all_closes = [d for d in deals if d.entry in EXIT_TYPES]
        # ลอง match symbol ก่อน — ถ้าไม่เจอ (broker มี suffix) ใช้ทุก symbol
        close_deals = [d for d in all_closes if d.symbol == sym] or all_closes
        if close_deals:
            d = sorted(close_deals, key=lambda x: x.time)[-1]
            return {"close_price": d.price, "profit": d.profit, "volume": d.volume,
                    "time": d.time, "ticket": d.position_id,
                    "commission": d.commission, "swap": d.swap}
        print(f"[mt5] get_latest_closed_deal: sym={sym} deals={len(deals)} closes=0 (attempt {attempt+1})")
        _time.sleep(2)
    return None


def get_position_deals(ticket: int) -> dict | None:
    """ดึง deal ทั้งหมดของ position ตาม ticket โดยตรง (position filter)
    เชื่อถือได้กว่า date-range query — ใช้กับไม้ที่รู้ ticket แน่นอน
    คืน dict รวม open+close หรือ None ถ้ายังไม่ปิด/ไม่พบ
    """
    import time as _time
    for attempt in range(3):
        ok, err = _connect()
        if not ok:
            print(f"[mt5] get_position_deals connect fail (attempt {attempt+1}): {err}")
            _time.sleep(1)
            continue
        deals = mt5.history_deals_get(position=ticket) or []
        disconnect()
        if not deals:
            print(f"[mt5] get_position_deals: ticket={ticket} ไม่มี deal (attempt {attempt+1})")
            _time.sleep(2)
            continue

        od = next((d for d in deals if d.entry == 0), None)
        closes = [d for d in deals if d.entry in (1, 2, 3)]
        if not closes:
            return None  # ยังไม่ปิด
        last = sorted(closes, key=lambda x: x.time)[-1]
        return {
            "position_id": ticket,
            "symbol":      last.symbol,
            "direction":   ("BUY" if od.type == 0 else "SELL") if od else None,
            "open_price":  od.price if od else None,
            "open_time":   od.time if od else None,
            "close_price": last.price,
            "close_time":  last.time,
            "volume":      od.volume if od else last.volume,
            "profit":      sum(d.profit for d in closes),
            "commission":  sum(d.commission for d in deals),
            "swap":        sum(d.swap for d in deals),
        }
    return None


def get_closed_positions(hours: int = 24) -> list[dict]:
    """ดึง closed positions ทั้งหมดใน X ชั่วโมง — จับคู่ open+close deal ตาม position_id
    ใช้สำหรับ background sync เก็บ transaction ทุกไม้ (ไม่พึ่งการจับตอนปิดพอดี)
    """
    from datetime import datetime, timedelta
    ok, err = _connect()
    if not ok:
        print(f"[mt5] get_closed_positions connect fail: {err}")
        return []
    date_from = datetime.now() - timedelta(hours=hours)
    date_to   = datetime.now() + timedelta(hours=1)
    deals = mt5.history_deals_get(date_from, date_to) or []
    disconnect()

    pos_open, pos_close = {}, {}
    for d in deals:
        pid = d.position_id
        if d.entry == 0:                       # IN
            if pid not in pos_open:
                pos_open[pid] = d
        elif d.entry in (1, 2, 3):             # OUT / INOUT / OUT_BY
            # รวม commission/swap/profit ของทุก partial close + เก็บ close ล่าสุด
            if pid not in pos_close:
                pos_close[pid] = {"deals": [], "last": d}
            pos_close[pid]["deals"].append(d)
            if d.time >= pos_close[pid]["last"].time:
                pos_close[pid]["last"] = d

    result = []
    for pid, cinfo in pos_close.items():
        od = pos_open.get(pid)
        last = cinfo["last"]
        deals_list = cinfo["deals"]
        total_profit = sum(x.profit for x in deals_list)
        total_comm   = sum(x.commission for x in deals_list)
        total_swap   = sum(x.swap for x in deals_list)
        result.append({
            "position_id": pid,
            "symbol":      last.symbol,
            "direction":   ("BUY" if od.type == 0 else "SELL") if od else None,
            "open_price":  od.price if od else None,
            "open_time":   od.time if od else None,
            "close_price": last.price,
            "close_time":  last.time,
            "volume":      od.volume if od else last.volume,
            "profit":      total_profit,
            "commission":  total_comm,
            "swap":        total_swap,
        })
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_available() -> bool:
    return _MT5_AVAILABLE and MT5_ENABLED


def get_current_price(direction: str) -> float | None:
    """คืนราคาตลาดปัจจุบัน — ask สำหรับ BUY, bid สำหรับ SELL"""
    ok, _ = _connect()
    if not ok:
        return None
    tick = mt5.symbol_info_tick(MT5_SYMBOL)
    disconnect()
    if tick is None:
        return None
    return tick.ask if direction == "BUY" else tick.bid


def _retcode_desc(code: int) -> str:
    _codes = {
        10004: "Requote",
        10006: "Request rejected",
        10007: "Request canceled",
        10008: "Order placed",
        10009: "Request completed",
        10010: "Only part filled",
        10011: "Request handling error",
        10012: "Request timed out",
        10013: "Invalid request",
        10014: "Invalid volume",
        10015: "Invalid price",
        10016: "Invalid stops",
        10017: "Trade disabled",
        10018: "Market closed",
        10019: "Not enough money",
        10020: "Prices changed",
        10021: "No quotes",
        10022: "Invalid expiry",
        10023: "Order changed",
        10024: "Too many requests",
        10025: "No changes",
        10026: "Autotrading disabled by server",
        10027: "Autotrading disabled by client",
        10028: "Request locked",
        10029: "Order or position frozen",
        10030: "Invalid fill type",
        10031: "No connection",
        10032: "Not allowed",
        10033: "Too many positions",
        10034: "Volume limit",
        10035: "Invalid order",
    }
    return _codes.get(code, f"Unknown code {code}")
