"""
sdk_utils.py — shared helper สำหรับเรียก Claude
api_query() = เรียกตรงผ่าน Anthropic API (pay-per-token) — ใช้งานจริงตอนนี้
sdk_query()/_query_async() = path เดิมผ่าน claude_agent_sdk (subscription) — เก็บไว้เป็น fallback
"""

import asyncio
import os
import threading
import time

import anthropic

from config.settings import ANTHROPIC_API_KEY

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def api_query(prompt: str, model: str, label: str = "API",
              max_tokens: int = 1024, timeout: float = 60, system: str = None) -> str:
    """
    เรียก Claude ตรงผ่าน Anthropic API (client.messages.create) — single-turn
    text-in/text-out ไม่ใช้ tools/streaming เหมาะกับ prompt สั้นๆ (~600-2500 in tokens)
    ที่ใช้ใน scan loop ของบอท

    system: ใส่กฎ format (เช่น "ตอบ JSON เท่านั้น") ไว้ตรงนี้แทนที่จะฝังใน prompt —
    system-level instruction บอทให้ทำตามเคร่งกว่า user turn เดียวที่มีทั้งกฎ+ข้อมูลปนกัน
    (เจอปัญหาจริง: Sonnet เขียน markdown อธิบายยาวก่อน JSON ทำให้โดน max_tokens ตัดก่อนถึง JSON)
    """
    t0 = time.time()
    kwargs = {"model": model, "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]}
    if system:
        kwargs["system"] = system
    try:
        resp = _client.with_options(timeout=timeout).messages.create(**kwargs)
    except anthropic.RateLimitError as e:
        elapsed = round(time.time() - t0, 1)
        print(f"[{label}] ⚠️ rate limit ({elapsed}s): {e}")
        raise
    except anthropic.APIStatusError as e:
        elapsed = round(time.time() - t0, 1)
        print(f"[{label}] ⚠️ API error {e.status_code} ({elapsed}s): {e.message}")
        raise
    except anthropic.APIConnectionError as e:
        elapsed = round(time.time() - t0, 1)
        print(f"[{label}] ⚠️ connection error ({elapsed}s): {e}")
        raise

    text = "".join(b.text for b in resp.content if b.type == "text")
    elapsed = round(time.time() - t0, 1)
    u = resp.usage
    print(f"[{label}] API response {elapsed}s ({u.input_tokens}in/{u.output_tokens}out): {text[:80]}")
    return text


# ── Legacy path — claude_agent_sdk (subscription, ไม่มีค่า API) ──────────────
# ไม่ได้ใช้แล้วตอนนี้ (ย้ายไป api_query() ทั้งหมด) เก็บไว้เผื่อสลับกลับ

_SDK_TIMEOUT = 90  # วินาที default


async def _query_async(prompt: str) -> str:
    os.environ.pop("ANTHROPIC_API_KEY", None)
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage
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
        raise TimeoutError(
            f"[{label}] SDK ไม่ตอบภายใน {elapsed}s ({timeout}s) — abandoned daemon thread"
        )

    if error[0] is not None:
        raise error[0]

    raw = result[0] or ""
    print(f"[{label}] SDK response {elapsed}s: {raw[:80]}")
    return raw
