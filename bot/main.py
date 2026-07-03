import sys
import os

# ใช้ claude_agent_sdk ผ่าน Claude Code subscription — ต้องไม่มี ANTHROPIC_API_KEY
os.environ.pop("ANTHROPIC_API_KEY", None)

# เพิ่ม path ให้ import config และ agents ได้
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.notifier import run

if __name__ == "__main__":
    run()
