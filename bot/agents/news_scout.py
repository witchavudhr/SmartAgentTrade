"""
News Scout — เช็ค Economic Calendar
แจ้งเตือนก่อนข่าว High Impact 30 นาที
ป้องกันไม่ให้เทรดในช่วงข่าว
"""

import requests
import anthropic
import json
import os
from datetime import datetime, timedelta
from config.settings import ANTHROPIC_API_KEY, MODEL_SMART, NEWS_BLOCK_MINUTES, NEWS_ENABLED
from agents.json_utils import safe_json_parse

FMP_API_KEY     = os.getenv("FMP_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# บล็อกหลังข่าวออกแล้วด้วย (ราคายังผันผวน)
NEWS_BLOCK_AFTER_MINUTES = 30

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Cache
_cache: dict = {"result": None, "timestamp": None}
CACHE_MINUTES = 30

# Keywords ที่กระทบ Gold
GOLD_KEYWORDS = [
    "USD", "US", "Federal Reserve", "Fed", "FOMC",
    "CPI", "NFP", "Non-Farm", "GDP", "Inflation",
    "Interest Rate", "Powell", "Treasury",
    "Gold", "XAU", "DXY", "Dollar"
]

HIGH_IMPACT_EVENTS = [
    "Non-Farm Payroll", "NFP",
    "CPI", "Core CPI",
    "FOMC", "Fed Rate Decision", "Interest Rate Decision",
    "GDP", "Unemployment Rate",
    "PPI", "PCE", "Retail Sales",
    "ISM Manufacturing", "ISM Services"
]


def _fetch_from_forexfactory() -> list:
    """ดึง economic calendar จาก ForexFactory JSON feed — ฟรี ไม่ต้อง API key
    Format: {"title":..., "country":..., "date":"2026-06-17T08:30:00-04:00", "impact":...}
    """
    try:
        urls = [
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
        ]
        result = []
        for url in urls:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue
            for e in resp.json():
                try:
                    date_str = e.get("date", "")
                    if not date_str:
                        continue
                    # ISO format: "2026-06-17T08:30:00-04:00"
                    dt = datetime.fromisoformat(date_str)
                    dt_local = dt.astimezone().replace(tzinfo=None)
                    result.append({
                        "event":    e.get("title", ""),
                        "date":     dt_local.strftime("%Y-%m-%d %H:%M:%S"),
                        "country":  e.get("country", ""),
                        "impact":   e.get("impact", "Low").capitalize(),
                        "actual":   e.get("actual"),
                        "estimate": e.get("forecast"),
                        "previous": e.get("previous"),
                    })
                except Exception:
                    continue
        print(f"[news_scout] ForexFactory: {len(result)} events")
        return result
    except Exception as e:
        print(f"[news_scout] ForexFactory error: {e}")
        return []


def fetch_calendar() -> list:
    """
    ดึง economic calendar — ลองตามลำดับ:
    1. MT5 terminal calendar (ไม่ต้อง API key)
    2. FMP API (ถ้ามี FMP_API_KEY)
    3. Finnhub API (ถ้ามี FINNHUB_API_KEY)
    4. คืน None (ไม่รู้ข้อมูล — ไม่ให้บอกว่า "ปลอดภัย")
    """
    # ── 1. ForexFactory JSON feed (ฟรี ไม่ต้อง API key) ──
    ff_events = _fetch_from_forexfactory()
    if ff_events:
        return ff_events

    today    = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    # ── 2. FMP API ──────────────────────────────────────────
    if FMP_API_KEY:
        try:
            resp = requests.get(
                "https://financialmodelingprep.com/api/v3/economic_calendar",
                params={"from": today, "to": tomorrow, "apikey": FMP_API_KEY},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data:
                    return data
        except Exception:
            pass

    # ── 3. Finnhub economic calendar ────────────────────────
    if FINNHUB_API_KEY:
        try:
            resp = requests.get(
                "https://finnhub.io/api/v1/calendar/economic",
                params={"from": today, "to": tomorrow, "token": FINNHUB_API_KEY},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                events = data.get("economicCalendar", [])
                normalized = []
                for e in events:
                    normalized.append({
                        "event":   e.get("event", ""),
                        "date":    e.get("time", ""),
                        "country": e.get("country", ""),
                        "impact":  "High" if e.get("impact", 0) >= 3 else "Medium" if e.get("impact", 0) >= 2 else "Low",
                        "actual":  e.get("actual"),
                        "estimate": e.get("estimate"),
                        "previous": e.get("prev"),
                    })
                if normalized:
                    return normalized
        except Exception:
            pass

    # ── 4. ดึงไม่ได้ — คืน None (ไม่ใช่ [] เพื่อแยก "ไม่มีข่าว" ออกจาก "ดึงไม่ได้") ──
    return None


def filter_gold_events(events: list) -> list:
    """กรองเฉพาะข่าวที่กระทบ Gold"""
    filtered = []
    for event in events:
        name = event.get("event", "")
        country = event.get("country", "")
        impact = event.get("impact", "")

        is_high_impact = impact in ["High", "3"] or any(h in name for h in HIGH_IMPACT_EVENTS)
        is_usd_related = country in ["US", "USD"] or any(k in name for k in GOLD_KEYWORDS)

        if is_high_impact and is_usd_related:
            filtered.append(event)

    return filtered


def get_next_event(within_hours: int = 72) -> dict | None:
    """หาข่าว High Impact ที่กระทบ Gold 'ตัวถัดไป' (ในอนาคต) ไม่จำกัด window
    คืน {event, date, impact, hours_until, minutes_until, day_label} หรือ None
    """
    events = fetch_calendar()
    if not events:
        return None
    gold_events = filter_gold_events(events)
    now = datetime.now()

    future = []
    for ev in gold_events:
        try:
            et = datetime.strptime(ev.get("date", "")[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        mins = (et - now).total_seconds() / 60
        if mins > 0:                     # เฉพาะข่าวที่ยังไม่เกิด
            future.append((mins, et, ev))

    if not future:
        return None
    mins, et, ev = min(future, key=lambda x: x[0])

    # day label ภาษาไทย
    days_diff = (et.date() - now.date()).days
    if days_diff == 0:
        day_label = "วันนี้"
    elif days_diff == 1:
        day_label = "พรุ่งนี้"
    else:
        day_label = et.strftime("%a %d/%m")

    return {
        "event":         ev.get("event", ""),
        "date":          et.strftime("%Y-%m-%d %H:%M"),
        "impact":        ev.get("impact", ""),
        "minutes_until": round(mins),
        "hours_until":   round(mins / 60, 1),
        "day_label":     day_label,
        "time_label":    et.strftime("%H:%M"),
    }


def get_upcoming_news(minutes_ahead: int = 120) -> list | None:
    """
    หาข่าวในช่วง block window:
    - ก่อนข่าว: 0 ถึง minutes_ahead
    - หลังข่าวออก: ยัง block อีก NEWS_BLOCK_AFTER_MINUTES นาที (ราคายังผันผวน)
    คืน None ถ้าดึงข้อมูลไม่ได้ (ต่างจาก [] ที่หมายถึง "ไม่มีข่าว")
    """
    events = fetch_calendar()
    if events is None:
        return None  # ไม่รู้ — ให้ caller ตัดสินใจ
    gold_events = filter_gold_events(events)

    now = datetime.now()
    upcoming = []

    for event in gold_events:
        try:
            event_time_str = event.get("date", "")
            event_time = datetime.strptime(event_time_str[:19], "%Y-%m-%d %H:%M:%S")
            minutes_until = (event_time - now).total_seconds() / 60

            # ก่อนข่าว: รอ minutes_ahead นาที
            # หลังข่าว: block ต่ออีก NEWS_BLOCK_AFTER_MINUTES นาที
            in_block_window = -NEWS_BLOCK_AFTER_MINUTES <= minutes_until <= minutes_ahead
            if in_block_window:
                event["minutes_until"] = round(minutes_until)
                event["is_past"] = minutes_until < 0
                upcoming.append(event)
        except Exception:
            continue

    return sorted(upcoming, key=lambda x: x.get("minutes_until", 999))


def should_block_trade() -> tuple[bool, str]:
    """
    เช็คว่าควรบล็อก trade มั้ย
    Returns: (should_block, reason)
    Block window: NEWS_BLOCK_MINUTES ก่อนข่าว + NEWS_BLOCK_AFTER_MINUTES หลังข่าว
    """
    if not NEWS_ENABLED:
        return False, ""

    upcoming = get_upcoming_news(minutes_ahead=NEWS_BLOCK_MINUTES)

    if upcoming is None:
        return True, "⚠️ ดึงข้อมูลข่าวไม่ได้ — ระวังเทรดช่วงนี้"

    if upcoming:
        event = upcoming[0]
        mins  = event.get("minutes_until", 0)
        name  = event.get("event", "Unknown")
        if event.get("is_past"):
            passed = abs(mins)
            remaining = NEWS_BLOCK_AFTER_MINUTES - passed
            return True, f"⚠️ ข่าว **{name}** ออกไปแล้ว {passed} นาที — รอ {remaining} นาทีก่อนกลับมาเทรด"
        return True, f"⚠️ ข่าว **{name}** ในอีก {mins} นาที — block {NEWS_BLOCK_MINUTES} นาทีก่อนข่าว"

    return False, ""


def analyze(force: bool = False, signal_direction: str | None = None) -> dict:
    """
    วิเคราะห์ข่าววันนี้ — cache 30 นาที
    signal_direction: "BUY"/"SELL" จาก Chart Analyst → Sonnet โหวต YES/NO
    ถ้าไม่มีข่าว → vote YES อัตโนมัติ (rule-based ไม่เรียก Claude)
    """
    global _cache

    if not force and _cache["result"] and _cache["timestamp"]:
        age = (datetime.now() - _cache["timestamp"]).total_seconds() / 60
        if age < CACHE_MINUTES:
            cached = dict(_cache["result"])
            cached["from_cache"] = True
            return cached

    upcoming = get_upcoming_news(minutes_ahead=240)
    blocked, block_reason = should_block_trade()

    if upcoming is None:
        # ดึงข้อมูลไม่ได้ — vote NO เพื่อความปลอดภัย
        result = {
            "vote": "NO",
            "vote_reasoning": "ดึงข้อมูล economic calendar ไม่ได้ — ระวังไว้ก่อน",
            "risk_level": "medium",
            "safe_to_trade": False,
            "block_until": None,
            "key_event": None,
            "gold_impact": "unknown",
            "reasoning": "ไม่สามารถดึงข้อมูลข่าวได้ (MT5/API ไม่ตอบสนอง) — แนะนำตรวจ ForexFactory เอง"
        }
    elif upcoming:
        news_text = json.dumps(upcoming, indent=2, ensure_ascii=False)
        signal_ctx = f"\nChart Analyst เสนอเข้า: {signal_direction}" if signal_direction else ""
        prompt = f"""คุณคือ News Scout Agent ผู้เชี่ยวชาญข่าวเศรษฐกิจที่กระทบ Gold
หน้าที่: วิเคราะห์ข่าวแล้ว VOTE YES/NO ว่าตอนนี้ปลอดภัยพอที่จะเข้า trade หรือไม่
{signal_ctx}

ข่าวที่จะเกิดใน 4 ชั่วโมงข้างหน้า:
{news_text}

{"⚠️ มีข่าว High Impact ภายใน " + str(NEWS_BLOCK_MINUTES) + " นาที — block_reason: " + block_reason if blocked else "ไม่มีข่าวที่ block อยู่ในขณะนี้"}

วิเคราะห์:
1. ข่าวไหนกระทบ Gold มากสุด และเมื่อไหร่?
2. spread จะกว้าง / ราคาผันผวนมั้ยในช่วงนี้?
3. ควรรอหลังข่าวผ่านมั้ย?

ตอบเป็น JSON:
{{
  "vote": "YES/NO",
  "vote_reasoning": "เหตุผลที่โหวต 1-2 ประโยค — ระบุว่าข่าวกระทบ timing ยังไง",
  "risk_level": "high/medium/low",
  "safe_to_trade": true/false,
  "block_until": "HH:MM" หรือ null,
  "key_event": "ชื่อข่าวสำคัญสุด" หรือ null,
  "gold_impact": "bullish/bearish/neutral/volatile",
  "reasoning": "อธิบาย news context สั้นๆ ภาษาไทย"
}}"""

        from agents.sdk_utils import sdk_query
        raw = sdk_query(prompt, label="NewsScout")
        result = safe_json_parse(
            raw,
            fallback={
                "vote": "NO" if blocked else "YES",
                "vote_reasoning": "มีข่าว High Impact — ระวังด้วย" if blocked else "ข่าวยังห่างพอ",
                "risk_level": "medium",
                "safe_to_trade": not blocked,
                "block_until": None,
                "key_event": upcoming[0].get("event") if upcoming else None,
                "gold_impact": "volatile",
                "reasoning": "JSON parse error — ใช้ fallback"
            }
        )
    else:
        # ไม่มีข่าว → vote YES ทันที ไม่ต้องเรียก Claude
        result = {
            "vote": "YES",
            "vote_reasoning": "ไม่มีข่าว High Impact ใน 4 ชั่วโมงข้างหน้า — ปลอดภัยเต็มที่",
            "risk_level": "low",
            "safe_to_trade": True,
            "block_until": None,
            "key_event": None,
            "gold_impact": "neutral",
            "reasoning": "ไม่มีข่าว High Impact ใน 4 ชั่วโมงข้างหน้า"
        }

    result["upcoming_events"] = upcoming
    result["next_event"] = get_next_event()   # ข่าวถัดไป (ไม่จำกัด window)
    result["is_blocked"] = blocked
    result["block_reason"] = block_reason
    result["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["from_cache"] = False

    _cache["result"] = result
    _cache["timestamp"] = datetime.now()

    return result


def format_news_message(news: dict) -> str:
    """แปลงข่าวเป็นข้อความ Telegram"""

    risk_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(news.get("risk_level"), "⚪")
    safe = "✅ เทรดได้" if news.get("safe_to_trade") else "🚫 ห้ามเทรด"

    events = news.get("upcoming_events", [])
    events_str = ""
    for e in events[:4]:
        mins = e.get("minutes_until", "?")
        events_str += f"\n  ⏰ `{mins} นาที` — {e.get('event', '')} ({e.get('impact', '')})"

    if not events_str:
        events_str = "\n  ✅ ไม่มีข่าวสำคัญ"

    return (
        f"📰 *News Scout Report*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{risk_icon} Risk Level: *{news.get('risk_level', '?').upper()}*\n"
        f"🎯 Status: *{safe}*\n"
        f"📊 Gold Impact: `{news.get('gold_impact', '?')}`\n"
        f"\n"
        f"📅 ข่าวใน 4 ชั่วโมงข้างหน้า:{events_str}\n"
        f"\n"
        f"📝 {news.get('reasoning', '')}\n"
        f"⏰ {news.get('analyzed_at', '')}"
    )
