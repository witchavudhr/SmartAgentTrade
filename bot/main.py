import sys
import os
import time
import traceback

# pop ก่อน import อื่นทั้งหมด — settings.py โหลด .env ทีหลังก็ตาม
# claude_agent_sdk อ่าน env ตอน import ดังนั้นต้องเคลียร์ตรงนี้ก่อน
os.environ.pop("ANTHROPIC_API_KEY", None)

# เพิ่ม path ให้ import config และ agents ได้
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.notifier import run

_RESTART_DELAY = 30   # วินาทีที่รอก่อน restart
_MAX_RESTART   = 999  # restart ได้กี่ครั้ง (ไม่จำกัดจริงๆ)

if __name__ == "__main__":
    attempt = 0
    while attempt < _MAX_RESTART:
        attempt += 1
        try:
            print(f"[main] 🚀 Starting bot (attempt #{attempt})")
            run()
            # run() จบแบบปกติ (ถูก Ctrl+C) → ออกไปเลย
            print("[main] Bot stopped normally.")
            break
        except KeyboardInterrupt:
            print("[main] Stopped by user.")
            break
        except Exception as e:
            print(f"[main] 💥 CRASH: {e}")
            print(traceback.format_exc()[-800:])
            print(f"[main] Restarting in {_RESTART_DELAY}s... (attempt #{attempt})")
            time.sleep(_RESTART_DELAY)
