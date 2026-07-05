"""
sdk_utils.py — shared helper สำหรับ claude_agent_sdk
ใช้แทน client.messages.create() ทุกที่
รันผ่าน Claude Code subscription — ไม่มีค่า API
"""

import asyncio
import os
import time

os.environ.pop("ANTHROPIC_API_KEY", None)

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

_SDK_TIMEOUT = 60  # วินาที default


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
    Sync wrapper — เรียกได้จาก thread ปกติ (asyncio.run สร้าง event loop ใหม่)
    คืน raw text response (string)
    """
    t0 = time.time()
    raw = asyncio.run(_query_async(prompt))
    elapsed = round(time.time() - t0, 1)
    print(f"[{label}] SDK response {elapsed}s: {raw[:80]}")
    if elapsed > timeout:
        raise TimeoutError(f"[{label}] SDK took {elapsed}s > {timeout}s")
    return raw
