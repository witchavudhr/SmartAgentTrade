import sys
import os

# เพิ่ม path ให้ import config และ agents ได้
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.notifier import run

if __name__ == "__main__":
    run()
