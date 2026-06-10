"""
News Calendar — Real Economic Calendar
ดึงข้อมูลข่าว USD High Impact จาก API จริง
ใช้ใน backtest (bulk load) และ live bot (daily cache)

Priority:
  1. FMP API (Financial Modeling Prep) — ฟรี 250 calls/day
  2. Finnhub API — ฟรี 60 calls/min
  3. Heuristic fallback — กรณีไม่มี API key

ตัวอย่าง .env:
  FMP_API_KEY=your_key_here
  FINNHUB_API_KEY=your_key_here
"""

import os
import requests
import pytz
from datetime import datetime, date, timedelta

ET_TZ = pytz.timezone("US/Eastern")

# คำสำคัญที่ถือว่าเป็น High Impact USD event
HIGH_IMPACT_KEYWORDS = [
    "nonfarm", "nfp", "non-farm", "payroll",
    "consumer price", "cpi", "core cpi",
    "producer price", "ppi", "core ppi",
    "gdp", "gross domestic",
    "unemployment rate", "unemployment claims",
    "initial jobless", "continuing claims",
    "fomc", "federal reserve", "interest rate decision", "fed rate",
    "retail sales",
    "ism manufacturing", "ism services", "ism non-manufacturing",
    "jolts", "job openings",
    "pce", "personal consumption", "personal income",
    "michigan consumer", "consumer sentiment", "consumer confidence",
    "ism pmi", "manufacturing pmi", "services pmi",
    "housing starts", "existing home sales",
    "durable goods", "trade balance",
    "industrial production"
]


def _is_usd_high_impact(country: str, impact: str, name: str) -> bool:
    """เช็คว่า event นี้เป็น USD High Impact หรือเปล่า"""
    country = (country or "").upper().strip()
    impact  = (impact  or "").lower().strip()
    name    = (name    or "").lower()

    if country not in ("US", "USD", "UNITED STATES"):
        return False

    # Impact label check
    if impact in ("high", "3", "red", "high impact"):
        return True

    # Keyword fallback
    return any(k in name for k in HIGH_IMPACT_KEYWORDS)


def _parse_et_datetime(dt_str: str) -> tuple[date, float] | None:
    """
    Parse datetime string ที่เป็น ET แล้ว → (date_ET, hour_ET)
    Format: "2026-05-08 08:30:00" หรือ "2026-05-08T08:30:00"
    """
    try:
        clean = dt_str[:16].replace("T", " ")
        dt    = datetime.strptime(clean, "%Y-%m-%d %H:%M")
        return (dt.date(), dt.hour + dt.minute / 60.0)
    except Exception:
        return None


# ── API Fetchers ──────────────────────────────────────────────────

def _fetch_fmp(from_date: str, to_date: str) -> list[tuple[date, float, str]]:
    """
    Financial Modeling Prep — Economic Calendar
    https://financialmodelingprep.com/developer/docs#economic-calendar
    Returns (date_ET, hour_ET, event_name)
    FMP timestamps คือ US Eastern Time
    """
    api_key = os.getenv("FMP_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        resp = requests.get(
            "https://financialmodelingprep.com/api/v3/economic_calendar",
            params={"from": from_date, "to": to_date, "apikey": api_key},
            timeout=15
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        if not isinstance(data, list):
            return []

        result = []
        for e in data:
            country = e.get("country", "")
            impact  = e.get("impact", "")
            name    = e.get("event", "")

            if not _is_usd_high_impact(country, impact, name):
                continue

            parsed = _parse_et_datetime(e.get("date", ""))
            if parsed:
                result.append((*parsed, name))

        return result

    except Exception as ex:
        print(f"⚠️  FMP calendar error: {ex}")
        return []


def _fetch_finnhub(from_date: str, to_date: str) -> list[tuple[date, float, str]]:
    """
    Finnhub — Economic Calendar
    https://finnhub.io/docs/api/economic-calendar
    Returns (date_ET, hour_ET, event_name)
    Finnhub timestamps คือ US Eastern Time
    """
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": from_date, "to": to_date, "token": api_key},
            timeout=15
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        events = data.get("economicCalendar", [])

        result = []
        for e in events:
            country = e.get("country", "")
            impact  = e.get("impact", "")
            name    = e.get("event", "")

            if not _is_usd_high_impact(country, impact, name):
                continue

            try:
                # Finnhub returns UTC timestamps → convert to ET
                raw = e.get("time", "")
                clean = raw[:16].replace("T", " ")
                dt_utc = datetime.strptime(clean, "%Y-%m-%d %H:%M")
                dt_et  = pytz.utc.localize(dt_utc).astimezone(ET_TZ)
                result.append((dt_et.date(), dt_et.hour + dt_et.minute / 60.0, name))
            except Exception:
                continue

        return result

    except Exception as ex:
        print(f"⚠️  Finnhub calendar error: {ex}")
        return []


def _heuristic_fallback(from_date: str, to_date: str) -> list[tuple[date, float, str]]:
    """
    Heuristic สำหรับกรณีไม่มี API key
    ใช้ pattern รู้จักของ US economic releases:

    ทุกสัปดาห์:
      - Thursday 08:30 ET → Initial Jobless Claims
    รายเดือน:
      - 1st Friday 08:30 ET → NFP / Unemployment Rate
      - Tue/Wed วันที่ 10-15 08:30 ET → CPI / PPI
      - 1st Monday 10:00 ET → ISM Manufacturing PMI
      - 1st Wednesday 10:00 ET → ISM Services PMI (approx)
      - ~3rd Wednesday 14:00 ET → FOMC (approx — not precise)
    """
    start   = datetime.strptime(from_date, "%Y-%m-%d").date()
    end     = datetime.strptime(to_date,   "%Y-%m-%d").date()
    events  = []
    current = start

    while current <= end:
        wd  = current.weekday()   # 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri
        day = current.day

        # Thursday — Jobless Claims
        if wd == 3:
            events.append((current, 8.5, "Initial Jobless Claims (Heuristic)"))

        # 1st Friday of month — NFP
        if wd == 4 and day <= 7:
            events.append((current, 8.5, "NFP / Unemployment Rate (Heuristic)"))

        # Mid-month Tue/Wed (10-15) — CPI / PPI
        if wd in (1, 2) and 9 <= day <= 16:
            events.append((current, 8.5, "CPI / PPI (Heuristic)"))

        # 1st Monday 10:00 — ISM Manufacturing
        if wd == 0 and day <= 7:
            events.append((current, 10.0, "ISM Manufacturing (Heuristic)"))

        # 1st Wednesday 10:00 — ISM Services (approx)
        if wd == 2 and day <= 7:
            events.append((current, 10.0, "ISM Services (Heuristic)"))

        # ~3rd Friday — Michigan Consumer Sentiment
        if wd == 4 and 14 <= day <= 21:
            events.append((current, 10.0, "Michigan Sentiment (Heuristic)"))

        current += timedelta(days=1)

    return events


# ── NewsCalendar Class ────────────────────────────────────────────

class NewsCalendar:
    """
    Economic calendar — load once, query many times

    Usage (backtest):
        cal = NewsCalendar()
        n   = cal.load("2026-05-05", "2026-06-09")
        print(f"Loaded {n} events")
        blocked, reason = cal.is_blocked(some_datetime)

    Usage (live bot):
        cal = get_today_calendar()  # cached singleton
        blocked, reason = cal.is_blocked(datetime.now())
    """

    def __init__(self):
        self._events: list[tuple[date, float, str]] = []
        self._loaded = False
        self._source = "none"

    def load(self, from_date: str, to_date: str) -> int:
        """
        โหลด events สำหรับช่วงวันที่ที่กำหนด
        Return: จำนวน events ที่โหลดได้
        """
        # ลอง FMP ก่อน
        events = _fetch_fmp(from_date, to_date)
        if events:
            self._source = "FMP"
        else:
            # ลอง Finnhub
            events = _fetch_finnhub(from_date, to_date)
            if events:
                self._source = "Finnhub"
            else:
                # Fallback heuristic
                events = _heuristic_fallback(from_date, to_date)
                self._source = "Heuristic"

        self._events = events
        self._loaded = True
        return len(events)

    def is_blocked(self, dt, buffer_min: int = 30) -> tuple[bool, str]:
        """
        เช็คว่า datetime นี้โดน news block หรือเปล่า
        dt: datetime (รองรับทุก timezone)
        Returns: (is_blocked, reason_string)
        """
        if not self._loaded:
            return False, ""

        # Convert to ET
        if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
            et = dt.astimezone(ET_TZ)
        else:
            et = pytz.utc.localize(dt).astimezone(ET_TZ)

        # Weekend ไม่มี news
        if et.weekday() >= 5:
            return False, ""

        d   = et.date()
        h   = et.hour + et.minute / 60.0
        buf = buffer_min / 60.0

        for (ev_date, ev_hour, ev_name) in self._events:
            if ev_date == d and abs(h - ev_hour) <= buf:
                hh = int(ev_hour)
                mm = int((ev_hour % 1) * 60)
                return True, f"{ev_name} @ {hh:02d}:{mm:02d} ET"

        return False, ""

    def get_day_summary(self, d: date) -> list[str]:
        """ดึงรายการข่าวของวันที่กำหนด"""
        return [
            f"{int(h):02d}:{int((h%1)*60):02d} ET — {name}"
            for (ev_date, h, name) in self._events
            if ev_date == d
        ]

    def count_by_day(self) -> dict[date, int]:
        """นับจำนวน events แต่ละวัน (สำหรับ debug)"""
        result: dict[date, int] = {}
        for (d, _, _) in self._events:
            result[d] = result.get(d, 0) + 1
        return result

    @property
    def source(self) -> str:
        return self._source

    @property
    def total_events(self) -> int:
        return len(self._events)


# ── Live Bot Helpers ──────────────────────────────────────────────

_today_calendar: NewsCalendar | None = None
_today_calendar_date: date | None = None


def get_today_calendar() -> NewsCalendar:
    """
    Singleton calendar สำหรับ live bot — refresh ทุกวัน
    """
    global _today_calendar, _today_calendar_date
    today = datetime.now(ET_TZ).date()

    if _today_calendar is None or _today_calendar_date != today:
        cal = NewsCalendar()
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        cal.load(today.strftime("%Y-%m-%d"), tomorrow)
        _today_calendar       = cal
        _today_calendar_date  = today

    return _today_calendar


def should_block_trade_now(buffer_min: int = 30) -> tuple[bool, str]:
    """
    Quick check สำหรับ live bot
    Returns: (should_block, reason)
    """
    cal = get_today_calendar()
    return cal.is_blocked(datetime.now(), buffer_min)
