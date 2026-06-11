"""
State Manager — บันทึก bot state ลงดิสก์
ป้องกันข้อมูลหายเมื่อ bot restart หรือย้าย session window

State ที่เซฟ:
  - is_running         bot หยุด/ทำงานอยู่
  - last_scan          เวลา scan ล่าสุด
  - pending_signal     signal ที่รอ user กด Confirm/Skip
  - pending_trade_id   trade_id ที่ log_trade() คืนมา (สำหรับ callback)
  - session_windows    scan windows ที่ตั้งไว้

เซฟทุกครั้งที่ state เปลี่ยน → โหลดใหม่ตอน startup
"""

import json
from datetime import datetime
from pathlib import Path

STATE_PATH = Path(__file__).parent.parent / "data" / "bot_state.json"

# Default state
_DEFAULT: dict = {
    "is_running":       True,
    "last_scan":        None,
    "pending_signal":   None,
    "pending_trade_id": None,
    "scan_windows":     [1.0, 3.0, 7.25, 11.0, 13.75, 15.75, 20.25, 22.75],
    "open_trade":       None,   # trade ที่กำลัง monitor trailing/reentry
    "updated_at":       None,
}


def load() -> dict:
    """โหลด state จาก disk — คืน default ถ้าไม่มีไฟล์"""
    STATE_PATH.parent.mkdir(exist_ok=True)
    if not STATE_PATH.exists():
        return dict(_DEFAULT)
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        # merge กับ default เพื่อรองรับ key ใหม่ที่เพิ่งเพิ่ม
        merged = dict(_DEFAULT)
        merged.update(data)
        return merged
    except Exception:
        return dict(_DEFAULT)


def save(state: dict) -> None:
    """เซฟ state ลง disk ทันที (atomic write)"""
    STATE_PATH.parent.mkdir(exist_ok=True)
    state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)   # atomic rename


def set_field(state: dict, key: str, value) -> None:
    """อัพเดท field เดียวแล้ว save ทันที"""
    state[key] = value
    save(state)


def clear_pending(state: dict) -> None:
    """ล้าง pending_signal และ pending_trade_id หลัง Confirm/Skip"""
    state["pending_signal"]   = None
    state["pending_trade_id"] = None
    save(state)


def describe(state: dict) -> str:
    """สรุป state สำหรับ /status"""
    status  = "🟢 กำลังทำงาน" if state.get("is_running") else "🔴 หยุดอยู่"
    last    = state.get("last_scan") or "ยังไม่ได้สแกน"
    updated = state.get("updated_at") or "-"
    pending = state.get("pending_signal")

    pending_str = "ไม่มี"
    if pending:
        sig = pending.get("signal") or pending.get("final_signal") or "?"
        tid = state.get("pending_trade_id")
        pending_str = f"รอ Confirm: `{sig}`" + (f" (Trade #{tid})" if tid else "")

    windows = state.get("scan_windows", [])
    win_str = ", ".join(f"{w:g}:00" for w in windows) if windows else "-"

    return (
        f"📊 *Bot Status*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"สถานะ: {status}\n"
        f"สแกนล่าสุด: `{last}`\n"
        f"Pending Signal: {pending_str}\n"
        f"Scan Windows (Thai): `{win_str}`\n"
        f"State บันทึกล่าสุด: `{updated}`\n"
        f"💾 State ถูกเซฟลงดิสก์ทุกครั้งที่เปลี่ยน"
    )
