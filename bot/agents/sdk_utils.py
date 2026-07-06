"""
sdk_utils.py — shared helper สำหรับ claude_agent_sdk
ใช้แทน client.messages.create() ทุกที่
รันผ่าน Claude Code subscription — ไม่มีค่า API
"""

import asyncio
import os
import threading
import time

os.environ.pop("ANTHROPIC_API_KEY", None)

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

_SDK_TIMEOUT = 90  # วินาที default


async def _query_async(prompt: str) -> str:
    gen = query(prompt=prompt, options=ClaudeAgentOptions(allowed_tools=[]))
    try:
        async for msg in gen:
            if isinstance(msg, ResultMessage):
                return msg.result or ""
        return ""
    finally:
        await gen.aclose()


def sdk_query(prompt: str, label: str = "SDK", timeout: int = _SDK_TIMEOUT) -> str:
    """
    Sync wrapper ใช้ daemon thread — ถ้า SDK ค้างเกิน timeout วินาที
    thread ถูก abandon (daemon) ไม่บล็อก call ถัดไป
    ป้องกัน rate-limit recovery failure ที่ asyncio.run() ค้างค้างข้ามรอบ
    """
    result: list = [None]
    error:  list = [None]

    def _run():
        try:
            result[0] = asyncio.run(_query_async(prompt))
        except Exception as e:
            error[0] = e

    t0 = time.time()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout)

    elapsed = round(time.time() - t0, 1)

    if t.is_alive():
        # thread ยัง hang อยู่ — abandon (daemon thread ไม่บล็อก process)
        raise TimeoutError(
            f"[{label}] SDK ไม่ตอบภายใน {elapsed}s ({timeout}s) — abandoned daemon thread"
        )

    if error[0] is not None:
        raise error[0]

    raw = result[0] or ""
    print(f"[{label}] SDK response {elapsed}s: {raw[:80]}")
    return raw
