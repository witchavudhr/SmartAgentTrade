"""
json_utils — safe JSON parsing สำหรับ Claude responses
ป้องกัน JSONDecodeError เมื่อ response ถูกตัดกลางคัน (max_tokens hit)
"""


def fmt_pts(pips, sign: bool = False) -> str:
    """แปลง pips (internal) → จุด (display) สำหรับ Gold XAUUSD
    1 pip = $0.10 = 10 จุด  |  1 จุด = $0.01
    sign=True → แสดง +/- นำหน้า (สำหรับ P&L)
    """
    pts = round((pips or 0) * 10)
    if sign:
        return f"{pts:+,}"
    return f"{pts:,}"

import json
import re


def safe_json_parse(text: str, fallback: dict | None = None) -> dict:
    """
    Parse JSON จาก Claude response — มี 3 ชั้น fallback:
    1. Parse ปกติ
    2. Repair: ปิด bracket/brace/string ที่ขาด
    3. Extract key-value คู่ที่ parse ได้บางส่วน
    คืน fallback dict ถ้าทำไม่ได้เลย
    """
    if fallback is None:
        fallback = {}

    # ── Strip markdown code block ──────────────────
    t = text.strip()

    # ── Sanitize newlines inside JSON strings ──────
    # Claude บางทีใส่ newline จริงๆ ใน string value → json.loads ถือว่า unterminated
    t = _sanitize_newlines(t)
    if "```json" in t:
        t = t.split("```json")[1].split("```")[0].strip()
    elif "```" in t:
        parts = t.split("```")
        if len(parts) >= 2:
            t = parts[1].strip()
            if t.startswith("json"):
                t = t[4:].strip()

    # ── 1. Normal parse ────────────────────────────
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    # ── 2. Repair truncated JSON ───────────────────
    repaired = _repair_json(t)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # ── 3. Partial extraction ──────────────────────
    partial = _extract_partial(t)
    if partial:
        return partial

    return fallback


def _sanitize_newlines(text: str) -> str:
    """แทนที่ newline จริงๆ ภายใน JSON string value ด้วย space"""
    result = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            escape_next = False
            result.append(ch)
            continue
        if ch == '\\' and in_string:
            escape_next = True
            result.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch in ('\n', '\r'):
            result.append(' ')
            continue
        result.append(ch)
    return ''.join(result)


def _repair_json(text: str) -> str | None:
    """
    พยายามปิด JSON ที่ถูกตัดกลางคัน:
    - ตัด trailing `,` หรือ `,\n`
    - ปิด string ที่ยังเปิดค้าง
    - ปิด [] และ {} ที่ยังเปิดค้าง
    """
    t = text.strip()
    if not t.startswith("{"):
        return None

    # ตัด trailing comma + whitespace
    t = re.sub(r',\s*$', '', t)

    # นับ open/close brackets
    depth_brace  = 0
    depth_bracket = 0
    in_string    = False
    escape_next  = False
    last_complete = 0

    for i, ch in enumerate(t):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth_brace += 1
        elif ch == '}':
            depth_brace -= 1
            if depth_brace == 0:
                last_complete = i
        elif ch == '[':
            depth_bracket += 1
        elif ch == ']':
            depth_bracket -= 1

    # ถ้า string เปิดค้าง → ปิดก่อน
    suffix = ""
    if in_string:
        suffix += '"'

    # ตัดค่าที่ไม่สมบูรณ์ออก (ถ้า in_string แสดงว่าค่าสุดท้าย truncated)
    # หา key-value คู่ล่าสุดที่สมบูรณ์
    if in_string and last_complete > 0:
        # ตัดกลับไปถึงหลัง `}` สุดท้ายที่ balance แล้วเพิ่ม `}`
        t = t[:last_complete + 1]
        return t

    # ปิด bracket ที่ค้าง
    suffix += "]" * depth_bracket
    suffix += "}" * depth_brace

    return t + suffix


def _extract_partial(text: str) -> dict | None:
    """
    ดึง key-value pairs ที่ parse ได้จาก partial JSON
    ใช้ regex เพื่อดึง "key": value ที่สมบูรณ์
    """
    result = {}

    # จับ string values: "key": "value"
    for m in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', text):
        result[m.group(1)] = m.group(2)

    # จับ number values: "key": 123
    for m in re.finditer(r'"(\w+)"\s*:\s*(-?\d+\.?\d*)', text):
        key = m.group(1)
        if key not in result:
            try:
                result[key] = float(m.group(2)) if '.' in m.group(2) else int(m.group(2))
            except ValueError:
                pass

    # จับ bool values: "key": true/false
    for m in re.finditer(r'"(\w+)"\s*:\s*(true|false|null)', text):
        key = m.group(1)
        if key not in result:
            val = m.group(2)
            result[key] = True if val == "true" else False if val == "false" else None

    return result if result else None
