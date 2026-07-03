import sys
import os

# pop ก่อน import อื่นทั้งหมด — settings.py โหลด .env ทีหลังก็ตาม
# claude_agent_sdk อ่าน env ตอน import ดังนั้นต้องเคลียร์ตรงนี้ก่อน
os.environ.pop("ANTHROPIC_API_KEY", None)

# เพิ่ม path ให้ import config และ agents ได้
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.notifier import run

if __name__ == "__main__":
    run()
