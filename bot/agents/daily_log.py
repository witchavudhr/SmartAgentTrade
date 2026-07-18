"""
Daily console log — เก็บ stdout/stderr ทั้งหมด (print() ทุกที่ในโปรเจกต์) ลงไฟล์
แยกตามวัน เพื่อเอาไปทำ feedback loop / ย้อนดูว่าวันนั้นเกิดอะไรขึ้นบ้าง
ไม่แทนที่ print() เดิม — ยังโชว์ในหน้าจอเหมือนเดิมทุกอย่าง แค่ mirror ไปเขียนไฟล์ด้วย
"""

import sys
import threading
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "data" / "logs"


class _TeeStream:
    """เขียนออกทั้ง stream เดิม (console) และไฟล์ log ของวันนั้น — สลับไฟล์อัตโนมัติ
    เมื่อข้ามวัน (เทียบ date ทุกครั้งที่ write เพราะบอทรันข้ามคืนได้)"""

    def __init__(self, original):
        self._original = original
        self._lock = threading.Lock()
        self._day = None
        self._fh = None

    def _ensure_file(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._day:
            if self._fh:
                self._fh.close()
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._fh = open(LOG_DIR / f"console_{today}.log", "a", encoding="utf-8")
            self._day = today

    def write(self, data):
        with self._lock:
            self._original.write(data)
            self._ensure_file()
            self._fh.write(data)
            self._fh.flush()

    def flush(self):
        with self._lock:
            self._original.flush()
            if self._fh:
                self._fh.flush()

    def isatty(self):
        return False


def setup_daily_console_log():
    """เรียกครั้งเดียวตอนบอทเริ่มรัน (main.py) — ก่อน import อะไรที่ print เยอะๆ"""
    if not isinstance(sys.stdout, _TeeStream):
        sys.stdout = _TeeStream(sys.stdout)
    if not isinstance(sys.stderr, _TeeStream):
        sys.stderr = _TeeStream(sys.stderr)


def log_path_for(day: str) -> Path:
    return LOG_DIR / f"console_{day}.log"
