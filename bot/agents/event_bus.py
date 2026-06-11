"""
Event Bus — bridge ระหว่าง bot pipeline กับ Web UI
Thread-safe: bot emit จาก asyncio thread, FastAPI poll จาก uvicorn thread
"""
import threading
import time

_events: list = []
_lock = threading.Lock()
_idx_counter = 0


def emit(event_type: str, data: dict):
    global _idx_counter
    with _lock:
        _idx_counter += 1
        _events.append({
            "type": event_type,
            "data": data,
            "ts":   time.time(),
            "idx":  _idx_counter,
        })
        if len(_events) > 100:
            _events.pop(0)


def get_since(idx: int) -> list:
    with _lock:
        return [e for e in _events if e["idx"] > idx]


def get_recent(n: int = 20) -> list:
    with _lock:
        return _events[-n:]


def current_idx() -> int:
    with _lock:
        return _idx_counter
