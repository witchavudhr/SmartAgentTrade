"""
News Scout — เช็ค Economic Calendar
แจ้งเตือนก่อนข่าว High Impact 30 นาที
ป้องกันไม่ให้เทรดในช่วงข่าว
"""

import requests
import anthropic
import json
from datetime import datetime, timedelta
from config.settings import ANTHROPIC_API_KEY, MODEL_FAST, NEWS_BLOCK_MINUTES

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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


def fetch_calendar() -> list:
    """ดึง economic calendar จาก ForexFactory (scraping) หรือ fallback API"""
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        # ใช้ FMP API (Financial Modeling Prep) — free tier 250 calls/day
        url = f"https://financialmodelingprep.com/api/v3/economic_calendar"
        params = {
            "from": today,
            "to": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),
            "apikey": "demo"  # ใส่ key จริงใน .env ทีหลัง
        }
        resp = requests.get(url, params=params, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data

    except Exception:
        pass

    # Fallback: mock data สำหรับทดสอบ
    now = datetime.now()
    return [
        {
            "event": "Core CPI (MoM)",
            "date": (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            "country": "US",
            "impact": "High",
            "actual": None,
            "estimate": "0.3%",
            "previous": "0.4%"
        },
        {
            "event": "FOMC Meeting Minutes",
            "date": (now + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"),
            "country": "US",
            "impact": "High",
            "actual": None,
            "estimate": None,
            "previous": None
        }
    ]


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


def get_upcoming_news(minutes_ahead: int = 120) -> list:
    """หาข่าวที่จะเกิดใน X นาทีข้างหน้า"""
    events = fetch_calendar()
    gold_events = filter_gold_events(events)

    now = datetime.now()
    upcoming = []

    for event in gold_events:
        try:
            event_time_str = event.get("date", "")
            event_time = datetime.strptime(event_time_str[:19], "%Y-%m-%d %H:%M:%S")
            minutes_until = (event_time - now).total_seconds() / 60

            if 0 <= minutes_until <= minutes_ahead:
                event["minutes_until"] = round(minutes_until)
                upcoming.append(event)
        except Exception:
            continue

    return sorted(upcoming, key=lambda x: x.get("minutes_until", 999))


def should_block_trade() -> tuple[bool, str]:
    """
    เช็คว่าควรบล็อก trade มั้ย
    Returns: (should_block, reason)
    """
    upcoming = get_upcoming_news(minutes_ahead=NEWS_BLOCK_MINUTES)

    if upcoming:
        event = upcoming[0]
        mins = event.get("minutes_until", 0)
        name = event.get("event", "Unknown")
        return True, f"⚠️ ข่าว **{name}** ในอีก {mins} นาที"

    return False, ""


def analyze() -> dict:
    """วิเคราะห์ข่าววันนี้และแจ้งความเสี่ยง"""
    upcoming = get_upcoming_news(minutes_ahead=240)  # 4 ชั่วโมงข้างหน้า
    blocked, block_reason = should_block_trade()

    # ให้ Claude วิเคราะห์ผลกระทบ
    if upcoming:
        news_text = json.dumps(upcoming, indent=2, ensure_ascii=False)
        prompt = f"""คุณคือ News Scout ผู้เชี่ยวชาญข่าวเศรษฐกิจที่กระทบ Gold

ข่าวที่จะเกิดใน 4 ชั่วโมงข้างหน้า:
{news_text}

วิเคราะห์:
1. ข่าวไหนกระทบ Gold มากสุด?
2. ตลาดน่าจะ react ยังไง (Dollar แข็ง/อ่อน → Gold ลง/ขึ้น)?
3. ช่วงไหนที่ไม่ควรเทรดเลย?

ตอบเป็น JSON:
{{
  "risk_level": "high/medium/low",
  "safe_to_trade": true/false,
  "block_until": "HH:MM" หรือ null,
  "key_event": "ชื่อข่าวสำคัญสุด" หรือ null,
  "gold_impact": "bullish/bearish/neutral/volatile",
  "reasoning": "อธิบายสั้นๆ ภาษาไทย"
}}"""

        response = client.messages.create(
            model=MODEL_FAST,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        result = json.loads(text)
    else:
        result = {
            "risk_level": "low",
            "safe_to_trade": True,
            "block_until": None,
            "key_event": None,
            "gold_impact": "neutral",
            "reasoning": "ไม่มีข่าว High Impact ใน 4 ชั่วโมงข้างหน้า"
        }

    result["upcoming_events"] = upcoming
    result["is_blocked"] = blocked
    result["block_reason"] = block_reason
    result["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
