"""
Supervisor — Multi-Agent Voting System
แต่ละ agent โหวต YES/NO พร้อม reasoning แล้ว Supervisor Sonnet ตัดสินใจสุดท้าย

Pipeline:
  1. SMC scan (rule-based, free)
  2. Chart Analyst (Sonnet) → VOTE YES/NO + signal + reasoning
  3. Bias Analyst  (Sonnet) → VOTE YES/NO รู้ signal direction แล้ว
  4. News Scout    (Sonnet) → VOTE YES/NO รู้ signal direction แล้ว
  5. Risk Manager  (rule)   → VETO power
  6. Supervisor   (Sonnet) → อ่าน reasoning ทั้ง 3 agent → APPROVE/REJECT
"""

import anthropic
import json
import time


def _md(text: str) -> str:
    """Escape Telegram Markdown v1 special chars ใน AI-generated text"""
    for ch in ('_', '*', '`', '['):
        text = text.replace(ch, f'\\{ch}')
    return text
from datetime import datetime

# ── Bias cache (15 นาที) — H1/H4 ไม่เปลี่ยนทุก 5 นาที ──────────────────
_BIAS_CACHE_TTL = 15 * 60  # 900 วินาที
_bias_cache: dict | None = None
_bias_cache_ts: float = 0.0


def _get_bias(signal_direction: str) -> dict:
    global _bias_cache, _bias_cache_ts
    age = time.time() - _bias_cache_ts
    if _bias_cache is not None and age < _BIAS_CACHE_TTL:
        print(f"[Supervisor] ♻️ bias cache hit ({int(age)}s old, TTL={_BIAS_CACHE_TTL}s)")
        return _bias_cache
    result = bias_analyst.analyze(signal_direction=signal_direction)
    _bias_cache = result
    _bias_cache_ts = time.time()
    print(f"[Supervisor] 🔄 bias refreshed (cache miss, age was {int(age)}s)")
    return result


# ── Reject cooldown — กัน AI call ซ้ำสำหรับ setup เดิมที่เพิ่งโดน NO_TRADE ──
# (has_signal() เป็น rule-based ฟรี เจอ setup เดิมซ้ำทุก scan รอบใหม่ๆ ถ้ายังไม่มี
# อะไรเปลี่ยน (ราคาไม่ขยับพ้น tolerance) การเรียก Chart Analyst ซ้ำจะได้ผลเหมือนเดิม
# แน่ๆ (เช่น รอ CHoCH bullish ที่ยังไม่เกิด) เสียเงินฟรีทุก 5 นาที)
_REJECT_COOLDOWN_SEC = 15 * 60   # 15 นาที
# user feedback: $8 หลวมไปสำหรับ setup ที่ไม่มี level ชัดเจน (SWING_/EQL/
# POST_SWEEP) ลดเหลือ $4 — ส่วน SWEEP_HIGH/LOW ย้ายไปใช้ dist-based cooldown
# (เทียบกับ level ตรงๆ) แล้ว ไม่ผ่าน branch นี้อีกต่อไป
_REJECT_PRICE_TOLERANCE = 4.0    # ราคาต้องขยับเกินนี้ถึงจะถือว่า "เปลี่ยนสถานการณ์" แล้ว
_reject_cache: dict = {}
# smc_setup label มาจากหลาย data source ผสมกัน (sweep/rejection/approach/swing)
# ที่แต่ละอันมี threshold คาบเกี่ยวกันได้ (เช่น เพิ่งเข้า/หลุดเกณฑ์ $5 ไปมา) ทำให้
# label สลับไปมาได้ทุก scan แม้ราคาแทบไม่ขยับเลย — cooldown เดิม key ด้วย label
# string ตรงๆ เลยโดน reset ทุกครั้งที่ label เปลี่ยน ทั้งที่ราคาเหมือนเดิมเป๊ะ
# เก็บ "ครั้งล่าสุดที่โดน reject ไม่ว่า label ไหน" แยกไว้ต่างหาก ใช้เป็น fallback
# cooldown เพิ่มอีกชั้น — ราคานิ่งจริงก็ข้าม AI call ได้แม้ label จะเปลี่ยนไป
_last_reject_any: dict | None = None

# user feedback: สำหรับ setup ที่ผูกกับระยะห่างถึง OB/SSL/BSL โดยตรง (NEAR_BULL_OB,
# NEAR_BEAR_OB, APPROACHING_SSL, APPROACHING_BSL) ไม่ต้องสนเวลาเลย ("เอาเป็นจุดที่
# เปลี่ยนดีกว่า") — เช็คแค่ว่าราคาตอนนี้ "ใกล้ zone นั้นกว่าตอนที่โดน reject ครั้ง
# ก่อนหรือเปล่า" ถ้าไม่ได้ใกล้กว่าเดิม (dist เท่าเดิมหรือไกลกว่า) ก็ไม่ต้องเรียก AI
# ซ้ำ ไม่ว่าจะผ่านไปนานแค่ไหน — ถ้าใกล้กว่าเดิมจริง (เกิน epsilon กันราคา noise
# เล็กๆน้อยๆ) ถึงจะปล่อยให้เรียกใหม่ setup อื่นที่ไม่มี dist (EQL/SWEEP/SWING) ยังใช้
# เกณฑ์เดิม (time + price tolerance) เพราะไม่มีแนวคิด "ระยะห่างถึง level" ที่ชัดเจน
# user feedback: 0.5 หลวมเกินไป — เจอเคสห่างขึ้น/ลงแค่ 0.3-0.9 (noise ธรรมดา) แต่ยัง
# ผ่าน epsilon ทำให้เรียก AI ซ้ำถี่เกิน ("ราคายังไม่เข้าใกล้มากกว่าเดิมเกิน 1$ ไม่ต้อง
# เรียก AI และไม่ต้องมี signal") ยกเป็น 1.0
_DIST_IMPROVE_EPS = 1.0


def _check_reject_cooldown(setup_key: str, price: float | None, dist: float | None = None) -> dict | None:
    if price is None:
        return None
    if setup_key:
        cached = _reject_cache.get(setup_key)
        if cached:
            cached_dist = cached.get("dist")
            if dist is not None and cached_dist is not None:
                if dist >= cached_dist - _DIST_IMPROVE_EPS:
                    age = time.time() - cached["time"]
                    return {"age": int(age), "price": cached["price"], "reason": cached.get("reason", ""), "dist": cached_dist}
            else:
                age = time.time() - cached["time"]
                if age < _REJECT_COOLDOWN_SEC and abs(price - cached["price"]) <= _REJECT_PRICE_TOLERANCE:
                    return {"age": int(age), "price": cached["price"], "reason": cached.get("reason", "")}
    if _last_reject_any:
        age = time.time() - _last_reject_any["time"]
        if age < _REJECT_COOLDOWN_SEC and abs(price - _last_reject_any["price"]) <= _REJECT_PRICE_TOLERANCE:
            return {"age": int(age), "price": _last_reject_any["price"], "reason": _last_reject_any.get("reason", "")}
    return None


def _record_reject(setup_key: str, price: float | None, reason: str, dist: float | None = None) -> None:
    global _last_reject_any
    if price is None:
        return
    if setup_key:
        _reject_cache[setup_key] = {"time": time.time(), "price": price, "reason": reason, "dist": dist}
    _last_reject_any = {"time": time.time(), "price": price, "reason": reason}
from config.settings import ANTHROPIC_API_KEY, MODEL_SMART
from agents import chart_analyst, bias_analyst, news_scout, risk_manager
from agents.trade_log import get_performance_summary, get_loss_lesson_digest
from agents.json_utils import safe_json_parse

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def run(balance: float = 10000.0, force_session: bool = False, context: dict = None) -> dict:
    """
    รัน pipeline ทั้งหมด:
    1. SMC scan (ฟรี)
    2. ถ้ามี signal → เช็ค news block
    3. เช็ค bias
    4. Risk Manager
    5. Supervisor vote
    6. คืน final decision
    """

    result = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "approved": False,
        "final_signal": "NO_TRADE",
        "vote_score": 0,
        "stages": {}
    }

    # ── Stage 1: SMC Engine (ฟรี) ─────────────────────────────
    _, smc_summary = chart_analyst.get_price_data()
    if not smc_summary:
        result["reject_reason"] = "ดึงข้อมูลราคาไม่ได้"
        return result

    result["current_price"]      = smc_summary.get("current_price")
    result["choch_sweep_setup"]  = smc_summary.get("choch_sweep_setup")

    # เพิ่ม liquidity snapshot ทุก scan (แม้ NO_SIGNAL) เพื่อให้ notifier ตรวจ sweep ได้
    _liq = smc_summary.get("liquidity", {})
    result["liq_snapshot"] = {
        "nearest_ssl": _liq.get("nearest_ssl"),
        "nearest_bsl": _liq.get("nearest_bsl"),
        "ssl_pools":   _liq.get("ssl_pools", []),
        "bsl_pools":   _liq.get("bsl_pools", []),
    }

    # ── Inject context จาก bot_state (Pattern 1 & 3) ──────────────
    if context:
        _orz = [z for z in (context.get("ob_rejection_zones") or [])
                if not z.get("used") and z.get("expire_at", "") > datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        if _orz:
            smc_summary["stored_ob_rejections"] = _orz
        _srw = context.get("sweep_rejection_watch")
        if _srw and _srw.get("expire_at", "") > datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
            smc_summary["sweep_rejection_watch"] = _srw
        _ot = context.get("open_trade")
        if _ot:
            smc_summary["open_trade"] = _ot

    # Pre-filter: ถ้าไม่มี signal ไม่เรียก Claude เลย
    if not chart_analyst.has_signal(smc_summary, force_session=force_session):
        result["reject_reason"] = "SMC Engine: ไม่มี setup ครบเงื่อนไข"
        result["stages"]["smc"] = "NO_SIGNAL"
        # off-hours แต่ signal กำลังก่อตัว — ส่งต่อให้ notifier.py แจ้ง Telegram
        # เบาๆ (ไม่เรียก AI) เพื่อให้เห็นข้อมูลล่าสุดตอนบอทเริ่มรันใหม่
        _off_note = smc_summary.get("off_hours_signal_note")
        if _off_note:
            result["off_hours_note"] = _off_note
            result["current_price"]  = smc_summary.get("current_price")
        return result

    result["stages"]["smc"] = "SIGNAL_FOUND"
    # เก็บ smc setup ไว้เพื่อ notifier แสดงใน lightweight alert แม้ chart AI บอก NO_TRADE
    _eql = smc_summary.get("eql_eqh_sweep") or {}
    _pc  = smc_summary.get("post_sweep_continuation") or {}
    _rev = smc_summary.get("reversal") or {}
    # last_sweep (เดี่ยว, ไม่แยกทิศ) เก่าเป็นร้อยแท่งได้ง่ายๆ ถ้า sweep อีกทิศ
    # เกิดทีหลัง — ใช้ last_sweep_high/low แยกทิศแทน และเช็ค freshness (≤48 bars)
    # ก่อนเอามาตั้งชื่อ label ไม่งั้น label ใน Telegram (เช่น "Setup: SWEEP_LOW")
    # จะโชว์ sweep ที่หมดอายุไปนานแล้วและไม่เกี่ยวกับ trigger จริงเลย (เช่น 79-379
    # bars) ทำให้ดูสับสนว่า "sweep ยังใหม่อยู่" ทั้งที่จริงไม่มี sweep ที่ valid เลย
    # user feedback: sweep ที่ 4047.78 (SSL) โดนไปแล้ว 52 bars ก่อน (เกิน 48 bars
    # นิดเดียว) แต่ราคาเพิ่งไหลกลับมาใกล้ระดับนี้อีกครั้ง (retest จริง) — เดิมเช็ค
    # แค่ age<=48 ไม่สนระยะห่างจากราคาปัจจุบันเลย พอ age เกินนิดเดียว label เลย
    # หลุดไปเป็น SWING_SELL (จาก detect_swing_entry's generic score) ทั้งที่ราคา
    # กำลัง retest sweep zone ที่ควรจับตาดูจริงๆ — เปลี่ยนมาเช็คระยะห่างเป็นหลัก
    # (สำคัญกว่า age ตรงๆ) ยังกัน sweep เก่ามากๆ (>100 bars) ไว้ไม่ให้หลุดมาได้
    _sw_high = smc_summary.get("last_sweep_high") or {}
    _sw_low  = smc_summary.get("last_sweep_low") or {}
    _price_lbl = smc_summary.get("current_price")

    # user feedback: sweep depth 0.93pts (< MIN_SWEEP_DEPTH=5 ที่ใช้ทุกที่อื่นใน
    # ระบบ) ยังโผล่เป็น label "SWEEP_LOW" ได้ เพราะ _sweep_label_ok เดิมเช็คแค่
    # age+distance ไม่เคยเช็ค depth เลย — sweep ตื้นแบบนี้ไม่ควรถือว่าเป็น signal
    # เดียวกับ CASE F ตั้งแต่แรก ต้องเช็ค depth ให้ตรงกับเกณฑ์เดียวกันทั้งระบบ
    _MIN_SWEEP_DEPTH_LBL = 5.0

    # user feedback: sweep ที่ rejection ยืนยันแล้ว (recovered=True) แต่ราคาเปลี่ยน
    # ทางไปแล้วเกิน 3 แท่ง ควรถือว่า "หมดเวลาแล้ว" ไม่ต้องมาโผล่เป็น SWEEP_HIGH/LOW
    # อีก — ถ้า rejection ยืนยันจริง ต้องเข้าไม้ไปนานแล้วตั้งแต่ 1-3 แท่งแรก ปล่อยไว้
    # นานกว่านั้นแปลว่าไม่ใช่ setup ที่ actionable แล้ว (เจอเคส SWEEP_HIGH ค้าง label
    # อยู่ 20+ นาที ถูก AI reject ซ้ำๆ ทั้งที่ rejection confirmed ตั้งแต่ age=1bar)
    _SWEEP_REJECTED_MAX_AGE = 3

    # user feedback: เดิม label เช็คแค่ age<=100 + dist<=20 (หลวมกว่ามาก) ทำให้ label
    # ขึ้นเรียก AI ทั้งที่ chart_analyst_agent.py's _pullback() (FIRST/SECOND window
    # age<=4, dist<=10) จะตอบ EXPIRED แน่ๆ อยู่แล้ว — เสียเงินเรียก AI ไปฟรีๆ ซ้ำๆ
    # ("BSL pullback status is EXPIRED (dist=1.2pts, age=6bars)" ฯลฯ) ตอนนี้ใช้
    # เกณฑ์เดียวกันกับ _pullback() เป๊ะ (age<=4, dist<=10) — label จะไม่ขึ้นเลยถ้ารู้
    # อยู่แล้วว่า AI จะตอบ EXPIRED
    _SWEEP_ENTRY_WINDOW = 10.0

    # user feedback: MAJOR POOL CHECK เช็คแค่ "ราคาเคยไปถึง level นี้หรือยัง" (ไม่
    # สนใจ depth) แต่ label เดิมต้อง depth>=$5 ก่อนถึงจะขึ้นเลย ทำให้พอ major pool
    # (EQH/EQL) โดนแตะแบบตื้นๆ (เช่น $1.75) label ไม่ขึ้น เลยไม่มีการเรียก AI ไป
    # เช็ค MAJOR POOL CHECK ใหม่เลยทั้งที่ pool ที่รอมาตลอดเพิ่งโดนแตะจริง — แยก
    # เกณฑ์: major pool ไม่ต้องผ่าน depth check (ถือว่า "แตะแล้ว" คือพอ ปล่อยให้
    # AI/chart_analyst_agent's _pullback() (ยัง depth>=$5 เหมือนเดิม) เป็นคน
    # ตัดสินสุดท้ายว่าจะเทรดจริงไหม) — minor pool ยังต้อง depth>=$5 เหมือนเดิม
    # กันไม่ให้ wick เล็กๆ ทั่วไปเรียก AI ฟรีๆ
    def _is_major_pool_level(lvl: float) -> bool:
        if lvl is None:
            return False
        _liq = smc_summary.get("liquidity") or {}
        _pools = (_liq.get("weekly_bsl_pools") or []) + (_liq.get("weekly_ssl_pools") or [])
        return any(p.get("size") == "major" and abs(p.get("level", 0) - lvl) < 1.5 for p in _pools)

    def _sweep_label_ok(sw: dict) -> bool:
        if not sw or _price_lbl is None:
            return False
        _age = sw.get("age_bars")
        if _age is None or _age > 4:
            return False
        if sw.get("recovered") and _age > _SWEEP_REJECTED_MAX_AGE:
            return False
        _lvl = sw.get("level")
        _wick = sw.get("wick_extreme")
        if (_lvl is not None and _wick is not None
                and abs(_lvl - _wick) < _MIN_SWEEP_DEPTH_LBL
                and not _is_major_pool_level(_lvl)):
            return False
        if _lvl is None:
            return False
        return abs(_price_lbl - _lvl) <= _SWEEP_ENTRY_WINDOW

    # user feedback: sweep บน M15/M30 ไม่เคยถูกเช็คเป็น label เลย (เจอ sweep BSL
    # ของ M15 จริงบนกราฟ แต่ label ไม่เคยขึ้นเพราะเช็คแค่ M5) — รวม M15/M30 เข้ามา
    # เช็คด้วยเกณฑ์เดียวกัน (_sweep_label_ok ใช้ age_bars ของ timeframe นั้นๆ เอง
    # ตรงๆ ไม่ได้เทียบข้าม timeframe ดังนั้นเกณฑ์ยังคง fair สำหรับทุก timeframe)
    _m15_smc_lbl = smc_summary.get("m15") or {}
    _m30_smc_lbl = smc_summary.get("m30") or {}
    _sw_high_all = [_sw_high, _m15_smc_lbl.get("last_sweep_high"), _m30_smc_lbl.get("last_sweep_high")]
    _sw_low_all  = [_sw_low,  _m15_smc_lbl.get("last_sweep_low"),  _m30_smc_lbl.get("last_sweep_low")]

    _fresh_sweep_kind = None
    if any(_sweep_label_ok(sw) for sw in _sw_high_all if sw):
        _fresh_sweep_kind = "HIGH"
    elif any(_sweep_label_ok(sw) for sw in _sw_low_all if sw):
        _fresh_sweep_kind = "LOW"
    # ไม่มี layer ไหนใน has_signal()/smc_setup เดิมรู้จัก "ราคากำลังเข้าใกล้เงื่อนไข
    # ไหนอยู่" เลย — มีแค่เช็คแบบ all-or-nothing รายตัว (CASE B ต้อง in_ob=True,
    # sweep ต้อง valid แล้ว ฯลฯ) ทำให้เคสที่ยังไม่เข้าเงื่อนไขไหนเป๊ะๆ แต่กำลังไหล
    # เข้าใกล้อะไรบางอย่างอยู่ ตกไปอยู่ label "SIGNAL" เฉยๆ ไม่บอกอะไรเลย
    #
    # แก้แบบทั่วไป: คำนวณ "ระยะห่างจนถึงเงื่อนไข" ของทุก pattern ที่ยัง watch อยู่
    # (OB, BSL/SSL liquidity pool) แล้วเลือก label ตามตัวที่ใกล้สุด — ไม่ใช่ patch
    # ทีละ pattern แบบเดิมอีกต่อไป เพิ่ม pattern ใหม่ในอนาคตแค่เติมใน candidates list
    def _watchlist_candidates(smc: dict) -> list[tuple[str, float, float]]:
        price = smc.get("current_price")
        if price is None:
            return []
        cands: list[tuple[str, float, float]] = []

        # เช็คทั้ง M5 และ M15 OB แล้วเอาตัวที่ระยะห่างน้อยสุดจริง — เดิม preferred
        # M15 ก่อนเสมอ (ถ้ามี) ทำให้พลาดเคสที่ M5 OB ใกล้กว่า M15 OB จริงๆ (เช่น
        # M15 Bull OB top=4024.38 ห่างกว่า M5 Bull OB top=4028.97 แต่ยังไปเลือก
        # M15 มาเทียบกับ Bear OB ทำให้ผลออกมาว่า Bear OB ใกล้กว่าทั้งที่ไม่จริง)
        # user feedback: ราคาอยู่ "ใน" OB แล้ว (in_ob=True, กรณีดีที่สุด) แต่เดิม
        # เช็คแค่ price >= top (เฉพาะตอนยังไม่ถึง OB) พอราคาลงมาอยู่ในโซนแล้ว
        # (ระหว่าง bottom-top) จะหลุด filter ไปเลยเพราะ price < top ทำให้ OB หายไป
        # จาก watchlist ทั้งที่ price อยู่ในโซนพอดี (ดีกว่า "ใกล้" อีก) ต้องนับ OB
        # ที่ price ยังไม่หลุดออกไปทั้งโซน (price >= bottom สำหรับ Bull, price <=
        # top สำหรับ Bear) แล้ว clamp ระยะห่างที่ 0 ถ้าราคาอยู่ในโซนแล้ว
        # user feedback: เอา M30 เข้ามาด้วยเลย ("เอา bsl/ssl ของ m15/m30 ด้วยเลยงั้น")
        _m15w = smc.get("m15") or {}
        _m30w = smc.get("m30") or {}
        _bull_obs = [ob for ob in (smc.get("active_bull_ob"), _m15w.get("active_bull_ob"), _m30w.get("active_bull_ob"))
                     if ob and ob.get("top") is not None and ob.get("bottom") is not None]
        _bear_obs = [ob for ob in (smc.get("active_bear_ob"), _m15w.get("active_bear_ob"), _m30w.get("active_bear_ob"))
                     if ob and ob.get("top") is not None and ob.get("bottom") is not None]
        _valid_bull_obs = [ob for ob in _bull_obs if price >= ob["bottom"]]
        _valid_bear_obs = [ob for ob in _bear_obs if price <= ob["top"]]
        _near_bull_ob = max(_valid_bull_obs, key=lambda ob: ob["top"]) if _valid_bull_obs else None
        _near_bear_ob = min(_valid_bear_obs, key=lambda ob: ob["bottom"]) if _valid_bear_obs else None
        _near_bull_top = _near_bull_ob["top"] if _near_bull_ob else None
        _near_bear_bot = _near_bear_ob["bottom"] if _near_bear_ob else None
        _bull_dist = max(0.0, price - _near_bull_top) if _near_bull_top is not None else None
        _bear_dist = max(0.0, _near_bear_bot - price) if _near_bear_bot is not None else None

        # user feedback: nearest_ssl/nearest_bsl เดิมมาจาก M5 ล้วนๆ ("เอา bsl/ssl
        # ของ m15/m30 ด้วยเลยงั้น") — ใช้ weekly_ssl/bsl_pools แทน (รวม M5+M15+M30
        # แล้ว dedup เรียงตามระยะห่างอยู่แล้วจาก get_price_data()) เลือกตัวที่ยัง
        # ไม่ swept ที่ใกล้ที่สุด
        _liqw = smc.get("liquidity") or {}
        _weekly_ssl = [p for p in (_liqw.get("weekly_ssl_pools") or []) if not p.get("swept")]
        _weekly_bsl = [p for p in (_liqw.get("weekly_bsl_pools") or []) if not p.get("swept")]
        if _weekly_ssl:
            _ssl_lvl = _weekly_ssl[0]["level"]
        else:
            _ssl_raw = _liqw.get("nearest_ssl")
            _ssl_lvl = _ssl_raw.get("level") if isinstance(_ssl_raw, dict) else _ssl_raw
        if _weekly_bsl:
            _bsl_lvl = _weekly_bsl[0]["level"]
        else:
            _bsl_raw = _liqw.get("nearest_bsl")
            _bsl_lvl = _bsl_raw.get("level") if isinstance(_bsl_raw, dict) else _bsl_raw

        # user feedback: เกณฑ์ $20 เดิมเช็คผิดจุด (ห่างระหว่าง Bull OB กับ Bear OB
        # คนละฝั่ง) ที่จริงต้องเช็คว่า OB นั้นห่างจาก SSL/BSL (จุดสูงสุด/ต่ำสุดที่ราคา
        #วิ่งมาจาก) อย่างน้อย $20 ถึงจะมี "room สะสมแล้วกลับตัว" จริง ไม่ใช่แค่ noise
        # ติดขอบ range (เช่น Bear OB 3991 แต่ SSL ที่มันวิ่งขึ้นมาจากอยู่แค่ 3973 ห่าง
        # แค่ $18 → sideway โดนหลอกง่าย ข้ามไปเลย) — Bear OB เทียบกับ SSL (จุดต่ำที่
        # ราคาเด้งขึ้นมา), Bull OB เทียบกับ BSL (จุดสูงที่ราคาร่วงลงมา)
        _MIN_OB_ROOM = 20.0
        _bull_has_room = (_bsl_lvl is None or abs(_near_bull_top - _bsl_lvl) >= _MIN_OB_ROOM) if _near_bull_top is not None else False
        _bear_has_room = (_ssl_lvl is None or abs(_near_bear_bot - _ssl_lvl) >= _MIN_OB_ROOM) if _near_bear_bot is not None else False

        if _near_bull_top is not None and _bull_has_room:
            cands.append(("NEAR_BULL_OB", _bull_dist, _near_bull_top))
        if _near_bear_bot is not None and _bear_has_room:
            cands.append(("NEAR_BEAR_OB", _bear_dist, _near_bear_bot))

        if _ssl_lvl is not None and price >= _ssl_lvl:
            cands.append(("APPROACHING_SSL", price - _ssl_lvl, _ssl_lvl))
        if _bsl_lvl is not None and price <= _bsl_lvl:
            cands.append(("APPROACHING_BSL", _bsl_lvl - price, _bsl_lvl))

        return cands

    # user feedback: 100pts (=$100) กว้างเกินไป — เจอเคส label โชว์ "approaching"
    # ทั้งที่จริงยังห่างอยู่มาก ไม่ค่อยมีประโยชน์ ให้แจ้งเตือนเฉพาะตอนใกล้จริงๆ
    # (≤$5) เท่านั้น ไม่กระทบเกณฑ์การเข้าเทรดจริง (OB_MIN_DISPLACEMENT ฯลฯ แยกกัน)
    _watch = [(lbl, d, lvl) for lbl, d, lvl in _watchlist_candidates(smc_summary) if 0 <= d <= 5]
    _approach_lbl = min(_watch, key=lambda x: x[1])[0] if _watch else None
    _watch_dist_map = {lbl: d for lbl, d, _lvl in _watch}
    _watch_lvl_map = {lbl: lvl for lbl, _d, lvl in _watch}

    # user feedback: SWEEP_HIGH/SWEEP_LOW ก็มี level ชัดเจนอยู่แล้ว (last_sweep_high/
    # low) เอามาคำนวณระยะห่างแบบเดียวกับ NEAR_BULL_OB/APPROACHING_SSL ได้เลย แทนที่
    # จะใช้ time+price tolerance แบบหลวมๆ — จะได้ cooldown ตาม "ใกล้กว่าเดิม" แบบ
    # เดียวกันทั้งระบบ
    if _price_lbl is not None and _sw_high.get("level") is not None:
        _watch_dist_map["SWEEP_HIGH"] = abs(_price_lbl - _sw_high["level"])
        _watch_lvl_map["SWEEP_HIGH"] = _sw_high["level"]
    if _price_lbl is not None and _sw_low.get("level") is not None:
        _watch_dist_map["SWEEP_LOW"] = abs(_price_lbl - _sw_low["level"])
        _watch_lvl_map["SWEEP_LOW"] = _sw_low["level"]

    # user feedback: label "BEAR_OB_REJECTED" (จาก recent_bear/bull_ob_rejection
    # event) กับ "APPROACHING_BEAR_OB" (จาก _watchlist_candidates ระยะห่างล้วนๆ)
    # เคยเป็นคนละเกณฑ์กัน ทำให้ scan ห่างกันไม่กี่นาที ราคาแทบไม่ขยับ (~$0.6) แต่
    # label สลับไปมาระหว่างสองอันนี้ตลอด (ทำให้ reject-cooldown ที่ผูกกับ label
    # รีเซ็ตใหม่ทุกครั้ง เรียก AI ซ้ำเปลืองเงิน) — user ยืนยันว่าที่จริงควรมีเกณฑ์
    # เดียวคือ "ระยะห่างจาก OB เท่าไหร่" ไม่สนใจว่ามี rejection event เกิดขึ้นหรือยัง
    # จึงรวมเป็น label เดียว (NEAR_BULL_OB/NEAR_BEAR_OB จาก _watchlist_candidates)
    # ไม่มี _rejected_lbl แยกต่างหากอีกต่อไป — recent_bear/bull_ob_rejection ยังใช้
    # อยู่ในที่อื่น (has_signal() layer 7, chart_analyst prompt) ไม่ได้ตัดทิ้ง แค่ไม่
    # เอามาแข่ง priority กับ label ระยะห่างตรงนี้แล้ว

    # user feedback (หลักการทั่วไป): OB/SSL/BSL สำคัญที่สุดเสมอ เพราะคือจุดกลับตัว
    # จริง — ทุกอย่างที่ผูกกับ OB/SSL/BSL level เจาะจง (EQL/EQH sweep, sweep-retest,
    # OB rejection, post-sweep continuation) ต้องมาก่อน swing_signal (คะแนน
    # bull/bear แบบกว้างๆ จาก detect_swing_entry ที่ไม่ผูกกับ level ไหนเป๊ะๆ) เสมอ
    # — swing_signal เป็นแค่ fallback ตอนไม่มีอะไรที่ผูกกับ OB/SSL/BSL จริงเท่านั้น
    # user feedback (ทดลอง — ปิดเฉพาะ SWING): SWING_BUY/SWING_SELL แทบไม่เคยได้
    # เข้าเทรดจริงเลย เพราะ chart_analyst_agent.py ไม่มี CASE ไหนรองรับ "SWING"
    # โดยตรง (CASE taxonomy มีแค่ B/F/L/I/G/J/H/K) — SWING เป็นแค่คะแนนกว้างๆ จาก
    # detect_swing_entry ไม่ผูกกับ level ไหนเป๊ะๆ พอไม่มี sweep/OB event ที่ตรง
    # กับ 8 case จริงมาสนับสนุน AI แทบจะ NO_TRADE ทุกครั้ง — เสีย AI call ไปฟรีๆ
    # ส่วน NEAR_BULL_OB/NEAR_BEAR_OB/APPROACHING_SSL/APPROACHING_BSL (_approach_lbl)
    # เปิดกลับมาใช้ตามที่ขอ — ราคาใกล้ SSL/BSL/OB ≤$5 ถือว่า "ใกล้จะ sweep" แล้ว
    # นับเป็นกลุ่ม sweep-related ด้วย ไม่ใช่แค่ SWING ที่ปิดไว้
    result["smc_setup"] = (
        _eql.get("signal")
        or (_fresh_sweep_kind and f"SWEEP_{_fresh_sweep_kind}")
        or (_pc.get("direction") and f"POST_SWEEP_{_pc['direction']}")
        # or (_rev.get("swing_signal") and f"SWING_{_rev['swing_signal']}")
        or _approach_lbl
        or "SIGNAL"
    )
    result["current_price"] = smc_summary.get("current_price")

    # ── ไม่เรียก AI ถ้า label ตกไปที่ "SIGNAL" (fallback ทั่วไป) ──────────
    # แปลว่าไม่มี pattern ที่มีความหมายจริงเลยสักอัน (ไม่มี EQL/SWING/SWEEP สด,
    # ไม่มี rejection, ไม่มีอะไรใกล้ ≤$5) — เจอซ้ำๆ ตลอด session นี้ว่าเคสแบบนี้
    # เรียก Chart Analyst แล้วได้ NO_TRADE แน่ๆ ทุกครั้ง (has_signal() ยัง True
    # จาก TREND branch แบบคะแนนกว้างๆ เช่น structure+liquidity score≥3/4 ซึ่งไม่ผูก
    # กับอะไรที่ actionable จริง) ข้ามไปเลยประหยัดเงิน ไม่ต้องรอ reject cooldown
    # — แต่ยังบอกระยะห่างจริงของทุกอย่าง (ไม่ filter ≤$5) ให้เห็นว่าห่างจากอะไร
    # เท่าไร ไม่ใช่แค่บอกเฉยๆ ว่า "ไม่มี signal"
    if result["smc_setup"] == "SIGNAL":
        # user feedback: บอกแค่ label+ระยะห่างไม่พอ ("NEAR_BEAR_OB 11.7p" ไม่รู้ว่า
        # รอราคาไหนอยู่) ต้องบอกราคาจริงของ level ที่กำลังรอด้วย ("รอ Bear OB ที่
        # 3991.71 ห่าง 11.7$") ถึงจะเทียบกับ /ob หรือชาร์ตได้ตรงๆ
        _all_watch = sorted(_watchlist_candidates(smc_summary), key=lambda x: x[1])
        _dist_str = (
            ", ".join(f"{lbl} @{lvl} ห่าง{round(d,1)}$" for lbl, d, lvl in _all_watch[:4])
            or "ไม่มีข้อมูล OB/SSL/BSL"
        )
        result["reject_reason"] = (
            f"ไม่มี pattern ที่มีความหมายจริง (ไม่มี EQL/SWING/SWEEP สด, ไม่มี OB rejection) — "
            f"รอ: {_dist_str} — ข้าม AI call เพื่อประหยัด"
        )
        result["stages"]["smc"] = "NO_MEANINGFUL_SIGNAL"
        return result

    # ── Reject cooldown gate — setup เดิม + ราคาใกล้เดิม เพิ่งโดน NO_TRADE ไปเมื่อกี้ ──
    _cd_dist = _watch_dist_map.get(result["smc_setup"])
    _cd_hit = _check_reject_cooldown(result["smc_setup"], result["current_price"], _cd_dist)
    if _cd_hit:
        _cd_setup_disp = result["smc_setup"].replace("_", " ")
        _cd_lvl = _watch_lvl_map.get(result["smc_setup"])
        _cd_lvl_str = f" (level {_cd_lvl})" if _cd_lvl is not None else ""
        if _cd_dist is not None:
            _cd_why = f"ราคายังไม่ใกล้ zone นี้{_cd_lvl_str}กว่าตอนโดน reject ครั้งก่อน ({_cd_dist:.1f} vs {_cd_hit.get('dist', '?')})"
        else:
            _cd_why = f"ราคาใกล้เดิม ({_cd_hit['price']} vs {result['current_price']})"
        result["reject_reason"] = (
            f"AI Call Cooldown — setup {_cd_setup_disp} เพิ่งโดนปฏิเสธเมื่อ {_cd_hit['age']}s "
            f"ที่แล้ว {_cd_why} — ข้าม AI call รอบนี้เพื่อประหยัด "
            f"(เหตุผลเดิม: {_cd_hit['reason'][:150]})"
        )
        result["stages"]["smc"] = "AI_CALL_SKIPPED_COOLDOWN"
        return result

    # ── Stage 2: Chart Analyst — SDK (subscription) ──
    try:
        from agents import chart_analyst_agent
        analysis = chart_analyst_agent.analyze(smc_summary, setup_hint=result.get("smc_setup"))
    except Exception as _sdk_err:
        print(f"[Supervisor] ⚠️ Agent SDK failed ({_sdk_err}) — skip scan (no API fallback)")
        result["reject_reason"] = f"SDK timeout/error — scan skipped"
        return result
    signal     = analysis.get("signal", "NO_TRADE")
    confidence = analysis.get("confidence", 0)
    chart_vote = analysis.get("vote", "NO")

    result["stages"]["chart"] = {
        "signal":        signal,
        "confidence":    confidence,
        "vote":          chart_vote,
        "vote_reasoning": analysis.get("vote_reasoning", ""),
        "reasoning":     analysis.get("reasoning"),
        "bull_ob_zone":  analysis.get("bull_ob_zone"),
        "bear_ob_zone":  analysis.get("bear_ob_zone"),
        # Pattern 3 (notifier.py _update_pattern_state) เก็บ zone ที่โดน rejection ไว้
        # จากตรงนี้ (chart_s.get("recent_bear/bull_ob_rejection")) — ไม่เคยถูก copy
        # เข้ามาใน stages["chart"] มาก่อนเลย ทำให้ CASE I (STORED_OB_PULLBACK_SELL/BUY —
        # รอราคากลับมา retest โซน rejection เดิมแล้ว sell/buy ซ้ำ) ไม่เคยทำงานได้จริง
        # ตั้งแต่แรก เพราะ Pattern 3 อ่านค่า None ตลอด ไม่เคยมี zone ถูกบันทึกเลย
        "recent_bear_ob_rejection": analysis.get("recent_bear_ob_rejection"),
        "recent_bull_ob_rejection": analysis.get("recent_bull_ob_rejection"),
    }

    result["analysis"] = analysis  # เก็บไว้เสมอ เพื่อให้ format_alert แสดง signal/conf/setup จริง

    # ── Zone cooldown gate — กันเทรดซ้ำโซนราคาใกล้เคียงกันในเวลาไม่ห่างกัน ──
    # (เช่น sweep-based signal ที่ swing point ใหม่เกิดใกล้ๆ level ที่เพิ่ง confirmed
    # ไปหมาดๆ — บอทไม่มี memory ข้าม scan จึงมองเป็นสัญญาณสดได้ทั้งที่เป็นโซนเดิม)
    if signal in ("BUY", "SELL") and chart_vote == "YES":
        _entry_chk = analysis.get("entry")
        if _entry_chk is not None:
            from agents.trade_log import check_zone_cooldown
            _cd = check_zone_cooldown(signal, _entry_chk)
            if _cd:
                result["reject_reason"] = (
                    f"Zone Cooldown — เพิ่งเทรด {signal} ใกล้ระดับนี้ไปแล้วเมื่อ {_cd['timestamp']} "
                    f"(entry เดิม {_cd['entry_low']}, ใหม่ {_entry_chk}) ภายใน 4 ชม. — ข้ามรอบนี้กันเทรดซ้ำโซน"
                )
                result["stages"]["zone_cooldown"] = _cd
                return result

    if signal == "NO_TRADE" or chart_vote == "NO":
        _record_reject(result["smc_setup"], result["current_price"], analysis.get("vote_reasoning", ""), _cd_dist)
        result["reject_reason"] = f"Chart Analyst voted NO — {analysis.get('vote_reasoning', 'NO_TRADE')}"
        # ตรวจว่า Claude ปฏิเสธเพราะ Liquidity Gate (รอ SSL/BSL sweep) หรือเปล่า
        _vote_reason = (analysis.get("vote_reasoning") or "").lower()
        _trade_plan  = (analysis.get("trade_plan") or "").lower()
        _reasoning   = (analysis.get("reasoning") or "").lower()
        _all_text = _vote_reason + _trade_plan + _reasoning

        # ── Bypass keywords: ถ้า Claude บอกว่า sweep เพิ่งเกิดแล้ว + rejection → ไม่ gate ──
        _bypass_keywords = (
            "bsl ถูก sweep", "ssl ถูก sweep", "sweep แล้ว", "swept already",
            "rejection ที่ ob", "ob rejection", "wick rejection", "bearish rejection",
            "bullish rejection", "rejection candle", "หลัง sweep", "after sweep",
            "sweep เกิดแล้ว", "bsl swept", "ssl swept",
        )
        _is_post_sweep_rejection = any(kw in _all_text for kw in _bypass_keywords)

        _liq_keywords = ("รอ ssl", "รอ bsl", "liquidity gate", "ssl ยัง", "bsl ยัง",
                         "ยังไม่ถูก sweep", "wait for ssl", "wait for bsl", "ssl intact", "bsl intact")
        if any(kw in _all_text for kw in _liq_keywords) and not _is_post_sweep_rejection:
            result["liq_gate_blocked"]  = True
            result["liq_gate_level"]    = analysis.get("liquidity_target")
            result["liq_gate_map_read"] = analysis.get("liquidity_map_read", "")
            result["liq_gate_signal"]   = analysis.get("signal")
        return result

    # ── Stage 3: Bias Analyst (cached 15 นาที) — รู้ signal แล้ว ──
    bias = _get_bias(signal_direction=signal)
    bias_vote = bias.get("vote", "NO")

    result["stages"]["bias"] = {
        "overall":         bias.get("overall_bias"),
        "weekly_bias":     bias.get("weekly_bias"),
        "trade_direction": bias.get("trade_direction"),
        "vote":            bias_vote,
        "vote_reasoning":  bias.get("vote_reasoning", ""),
        "case":            bias.get("case", "?"),
        "at_htf_level":    bias.get("at_htf_level", False),
        "htf_level_detail":bias.get("htf_level_detail"),
        "from_cache":      bias.get("from_cache", False),
    }

    # ── Stage 4: News Scout (Sonnet, cached) — รู้ signal แล้ว ──
    news = news_scout.analyze(signal_direction=signal)
    news_vote = news.get("vote", "NO")
    blocked   = news.get("is_blocked", False)

    result["stages"]["news"] = {
        "risk_level":    news.get("risk_level"),
        "vote":          news_vote,
        "vote_reasoning": news.get("vote_reasoning", ""),
        "key_event":     news.get("key_event"),
        "next_event":    news.get("next_event"),
    }

    # ── Stage 5: Agent Voting ──────────────────────────────────
    votes = {
        "chart": chart_vote == "YES",
        "bias":  bias_vote  == "YES",
        "news":  news_vote  == "YES",
    }

    vote_score = sum(votes.values())
    result["vote_score"] = vote_score
    result["votes"]      = votes
    result["vote_details"] = {
        "chart": analysis.get("vote_reasoning", ""),
        "bias":  bias.get("vote_reasoning", ""),
        "news":  news.get("vote_reasoning", ""),
    }

    if vote_score < 1:
        result["reject_reason"] = "Vote 0/3 — ทุก agent reject"
        return result

    # ── Stage 6: Risk Manager (VETO) ──────────────────────────
    risk = risk_manager.evaluate(analysis, bias, balance)
    result["stages"]["risk"] = risk

    if risk.get("veto"):
        result["reject_reason"] = risk.get("veto_reason")
        return result

    setup_type = analysis.get("setup_type", "")
    # rr_ratio: Claude ส่งมาโดยตรง (ถ้าคำนวณเอง) หรือคำนวณจาก entry/sl/tp
    rr         = float(analysis.get("rr_ratio") or 0)
    if rr == 0:
        _tp_sv  = analysis.get("take_profit") or analysis.get("tp1")
        _sl_sv  = analysis.get("stop_loss")
        _ez_sv  = analysis.get("entry_zone") or analysis.get("entry")
        _em_sv  = ((_ez_sv[0] + _ez_sv[1]) / 2 if isinstance(_ez_sv, list) and len(_ez_sv) == 2
                   else float(_ez_sv) if _ez_sv else None)
        if _em_sv and _sl_sv and _tp_sv:
            try:
                rr = round(abs(float(_tp_sv) - _em_sv) / abs(_em_sv - float(_sl_sv)), 2)
            except (TypeError, ValueError, ZeroDivisionError):
                rr = 0

    # ── CASE L PROXIMITY GUARD — retest ต้องอยู่ใกล้ CHoCH level จริง ──────
    # user feedback: เจอเคสจริง — POST_BOS_CHOCH_RETEST_SELL อนุมัติที่ราคาปัจจุบัน
    # (4043) ทั้งที่ CHoCH swing high (retest zone จริง) อยู่ที่ ~4062 (~$19 ห่าง)
    # นี่คือ pattern "รอราคากลับไป retest ที่ระดับ CHoCH" ไม่ใช่ trend-following —
    # เชื่อ prompt instruction อย่างเดียวไม่พอ (เจอ AI ข้ามเงื่อนไขนี้มาแล้ว) เพิ่ม
    # code-level gate บังคับเลย
    if setup_type in ("POST_BOS_CHOCH_RETEST_SELL", "POST_BOS_CHOCH_RETEST_BUY"):
        _pbcr = smc_summary.get("post_bos_choch_retest") or {}
        _dist_choch = _pbcr.get("dist_pts")
        if _dist_choch is not None and _dist_choch > 15:
            result["reject_reason"] = (
                f"{setup_type} rejected: ราคาปัจจุบันห่างจาก CHoCH retest zone อยู่ {_dist_choch}pts "
                f"(ต้องการ ≤15pts) — นี่คือ pattern รอราคากลับไป retest ที่ CHoCH level ไม่ใช่ chase "
                f"ตามเทรนด์ที่ราคาปัจจุบัน"
            )
            return result

    # ── OB PROXIMITY GUARD — ก่อน fast-approve ──────────────────
    # OB_REJECTION / STORED_OB_PULLBACK: OB ต้องห่างจากราคาปัจจุบัน ≥10 pts
    # ถ้า OB ใกล้เกินไป → ราคาจะผ่านทะลุทันที ไม่มี significance
    _ob_prox_setups = {
        "OB_REJECTION_BUY", "OB_REJECTION_SELL",
        "STORED_OB_PULLBACK_BUY", "STORED_OB_PULLBACK_SELL",
    }
    _cur_px = result.get("current_price") or 0
    if setup_type in _ob_prox_setups and _cur_px:
        _ez_prox  = analysis.get("entry_zone") or analysis.get("entry")
        _ob_t     = (_ez_prox[1] if isinstance(_ez_prox, list) and len(_ez_prox) == 2
                     else float(_ez_prox) if _ez_prox else None)
        _ob_b     = (_ez_prox[0] if isinstance(_ez_prox, list) and len(_ez_prox) == 2
                     else float(_ez_prox) if _ez_prox else None)
        if _ob_t and _ob_b:
            _too_close = False
            _prox_pts  = 0.0  # distance in pts (1 pt = $1 for XAUUSD)
            if signal == "BUY":
                # _prox_pts > 0  = ราคายังอยู่เหนือ ob_top (กำลังเข้าใกล้ แต่ยังไม่ถึง)
                # _prox_pts <= 0 = ราคาอยู่ใน/ต่ำกว่า OB แล้ว (valid rejection) → ไม่ filter
                _prox_pts = round(_cur_px - _ob_t, 1)
                if 0 < _prox_pts < 25:
                    _too_close = True
            else:
                # _prox_pts > 0  = ราคายังอยู่ต่ำกว่า ob_bottom (กำลังเข้าใกล้ แต่ยังไม่ถึง)
                # _prox_pts <= 0 = ราคาอยู่ใน/เหนือ OB แล้ว (valid rejection) → ไม่ filter
                _prox_pts = round(_ob_b - _cur_px, 1)
                if 0 < _prox_pts < 25:
                    _too_close = True
            if _too_close:
                result["reject_reason"] = (
                    f"OB ใกล้เกินไป — ราคา {_cur_px} ห่าง OB {_ob_b}–{_ob_t} แค่ {_prox_pts} pts "
                    f"(ต้องการ ≥25 pts เพื่อให้มี displacement พอก่อน rejection)\n"
                    f"รอ OB ที่ไกลกว่าหรือรอ setup ใหม่"
                )
                return result

    # ── SSL/BSL CONFLUENCE — CASE B: ถ้า OB ใกล้ SSL/BSL มาก (≤$10) ให้เข้าที่
    # ระดับ SSL/BSL แทน OB zone ตรงๆ (ระดับ liquidity คือจุด stop-hunt จริงที่
    # แม่นกว่า OB) — ไม่พึ่งแค่ prompt instruction เพราะเจอ AI ข้ามเงื่อนไขแบบนี้
    # มาหลายรอบแล้วในเซสชันนี้ บังคับ override entry_zone ที่ code เลย
    if setup_type in ("BULL_OB_ENTRY", "BEAR_OB_ENTRY"):
        _liq_conf = smc_summary.get("liquidity") or {}
        _ssl_conf_raw = _liq_conf.get("nearest_ssl")
        _bsl_conf_raw = _liq_conf.get("nearest_bsl")
        _ssl_conf = _ssl_conf_raw.get("level") if isinstance(_ssl_conf_raw, dict) else _ssl_conf_raw
        _bsl_conf = _bsl_conf_raw.get("level") if isinstance(_bsl_conf_raw, dict) else _bsl_conf_raw
        _ob_zone_conf = (analysis.get("bull_ob_zone") if setup_type == "BULL_OB_ENTRY"
                         else analysis.get("bear_ob_zone")) or {}
        _ob_ref = _ob_zone_conf.get("top") if setup_type == "BULL_OB_ENTRY" else _ob_zone_conf.get("bottom")
        _pool_conf = _ssl_conf if setup_type == "BULL_OB_ENTRY" else _bsl_conf
        if _ob_ref is not None and _pool_conf is not None and abs(_pool_conf - _ob_ref) <= 10:
            analysis["entry_zone"] = [round(_pool_conf - 2, 2), round(_pool_conf + 2, 2)]
            analysis["entry"] = _pool_conf

    # ── Fast APPROVE: OB setups — rule-based ไม่ต้องให้ Claude ตัดสิน ──
    # เพิ่มเงื่อนไข: TREND_OB ต้องมี entry zone ใกล้ OB จริงๆ
    # ป้องกัน Claude return TREND_OB แต่ entry zone ไม่ใช่ OB (กลางอากาศ)
    ob_setups = {"BULL_OB_ENTRY", "BEAR_OB_ENTRY", "BULL_OB_SWEEP_REJECT", "TREND_OB", "TREND_BOS_BREAK"}

    # ตรวจ entry zone ตรงกับ OB จริงมั้ย (เฉพาะ TREND_OB)
    _entry_zone = analysis.get("entry_zone") or []
    _entry_mid  = ((_entry_zone[0] + _entry_zone[1]) / 2) if len(_entry_zone) == 2 else None
    _bear_ob    = analysis.get("bear_ob_zone") or {}
    _bull_ob    = analysis.get("bull_ob_zone") or {}
    _ob_zone_ok = True
    if setup_type == "TREND_OB" and _entry_mid:
        if signal == "SELL" and _bear_ob:
            # entry ต้องอยู่ใกล้ Bear OB ≤300p
            bear_bottom = _bear_ob.get("bottom", _entry_mid)
            dist_entry_ob = abs(_entry_mid - bear_bottom) * 10
            if dist_entry_ob > 300:
                _ob_zone_ok = False
                result["reject_reason"] = (
                    f"TREND_OB SELL rejected: entry zone {_entry_zone} ห่าง Bear OB "
                    f"{_bear_ob.get('bottom')}–{_bear_ob.get('top')} อยู่ {dist_entry_ob:.0f}p — กลางอากาศ"
                )
        elif signal == "BUY" and _bull_ob:
            # entry ต้องอยู่ใกล้ Bull OB ≤300p
            bull_top = _bull_ob.get("top", _entry_mid)
            dist_entry_ob = abs(_entry_mid - bull_top) * 10
            if dist_entry_ob > 300:
                _ob_zone_ok = False
                result["reject_reason"] = (
                    f"TREND_OB BUY rejected: entry zone {_entry_zone} ห่าง Bull OB "
                    f"{_bull_ob.get('bottom')}–{_bull_ob.get('top')} อยู่ {dist_entry_ob:.0f}p — กลางอากาศ"
                )

    # ── TREND_OB bias conflict guard ──────────────────────────────
    # TREND_OB หมายถึง "trend-aligned" — ถ้า bias ขัดแย้งกับ signal ให้ reject ทันที
    # (ป้องกัน SELL ขณะ BULL bias หรือ BUY ขณะ BEAR bias)
    if setup_type == "TREND_OB" and _ob_zone_ok:
        _bias_dir = (bias.get("trade_direction") or "").upper()
        if signal == "SELL" and _bias_dir == "BUY":
            result["reject_reason"] = (
                f"TREND_OB SELL rejected: Bias = BUY (BULL) — ไม่ใช่ trend-aligned, ห้าม SELL สวนทาง"
            )
            return result
        if signal == "BUY" and _bias_dir == "SELL":
            result["reject_reason"] = (
                f"TREND_OB BUY rejected: Bias = SELL (BEAR) — ไม่ใช่ trend-aligned, ห้าม BUY สวนทาง"
            )
            return result

    if setup_type in ob_setups and chart_vote == "YES" and rr >= 1.5 and not blocked and _ob_zone_ok:
        auto_reason = {
            "BULL_OB_SWEEP_REJECT": "Sweep + rejection ที่ Bull OB — สัญญาณแข็งที่สุด auto-approve",
            "BULL_OB_ENTRY":        f"ราคาอยู่ที่ Bull OB (pyramid ไม้ 1) — RR {rr} auto-approve",
            "BEAR_OB_ENTRY":        f"ราคาอยู่ที่ Bear OB (pyramid ไม้ 1) — RR {rr} auto-approve",
            "TREND_OB":             f"Trend-aligned OB entry — RR {rr} auto-approve",
            "TREND_BOS_BREAK":      f"BOS break pyramid — RR {rr} auto-approve",
        }.get(setup_type, f"{setup_type} auto-approve")

        liq_target_a = analysis.get("liquidity_target")
        idm_level_a  = analysis.get("inducement_level")
        liq_read_a   = analysis.get("liquidity_map_read", "")
        auto_liq = (
            f"BSL/SSL target: {liq_target_a}" if liq_target_a else ""
        ) + (f" | Inducement: {idm_level_a}" if idm_level_a else "")

        result["approved"]         = True
        result["final_signal"]     = signal
        result["lot"]              = risk.get("lot")
        result["risk_pct"]         = risk.get("risk_pct")
        result["caution_mode"]     = risk.get("caution_mode", False)
        result["entry_zone"]       = analysis.get("entry_zone")
        result["stop_loss"]        = analysis.get("stop_loss")
        result["take_profit"]      = analysis.get("take_profit") or analysis.get("tp1")
        result["rr_ratio"]         = rr
        result["reasoning"]        = auto_reason
        result["entry_condition"]  = analysis.get("vote_reasoning", auto_reason)
        result["liquidity_summary"]= liq_read_a or auto_liq or "–"
        result["analysis"]         = analysis
        result["stages"]["supervisor"] = {
            "approve": True, "reasoning": auto_reason,
            "entry_condition": result["entry_condition"],
            "liquidity_summary": result["liquidity_summary"],
            "auto": True,
        }
        return result

    # user feedback: MAJOR POOL CHECK คำนวณล้วนๆ จาก pool list ไม่ต้องพึ่ง AI เลย
    # แต่เดิมคำนวณอยู่ "ข้างใน" _supervisor_judge() (เรียก AI ไปแล้ว) แค่ยัด note
    # บังคับใน prompt ให้ AI reject ตาม — เสียเงินเรียก AI ทั้งที่รู้คำตอบล่วงหน้า
    # อยู่แล้วว่าต้อง reject แน่ๆ ("แล้วจะเรียก AI ทำไม") ย้ายมาเช็คก่อนเรียกเลย
    # ข้ามการเรียก AI ไปเลยถ้ารู้แล้วว่าจะ reject
    _mp_ok, _mp_note = _major_pool_check(setup_type, smc_summary)
    if not _mp_ok:
        result["reject_reason"] = _mp_note
        result["stages"]["supervisor"] = {"approve": False, "reasoning": _mp_note}
        return result

    # ── Stage 7: Supervisor Final Decision — สำหรับ setup ที่ไม่ชัด ──
    verdict = _supervisor_judge(analysis, bias, news, risk, vote_score, result["vote_details"], smc_summary)
    result["stages"]["supervisor"] = verdict

    if verdict.get("approve"):
        result["approved"]          = True
        result["final_signal"]      = signal
        result["lot"]               = risk.get("lot")
        result["risk_pct"]          = risk.get("risk_pct")
        result["caution_mode"]      = risk.get("caution_mode", False)
        result["entry_zone"]        = analysis.get("entry_zone")
        result["stop_loss"]         = analysis.get("stop_loss")
        result["take_profit"]       = analysis.get("take_profit") or analysis.get("tp1")
        result["rr_ratio"]          = rr
        result["reasoning"]         = verdict.get("reasoning")
        result["entry_condition"]   = verdict.get("entry_condition", "")
        result["liquidity_summary"] = verdict.get("liquidity_summary", "")
        result["analysis"]          = analysis
    else:
        _record_reject(result["smc_setup"], result["current_price"], verdict.get("reasoning", ""), _cd_dist)
        result["reject_reason"] = verdict.get("reasoning", "Supervisor rejected")

    return result


def _major_pool_check(setup_type_s: str, smc_summary: dict | None) -> tuple[bool, str]:
    """
    เช็คว่า sweep ที่ setup_type_s (BSL_SWEEP_SELL/SSL_SWEEP_BUY) อ้างถึง เป็นการ
    sweep pool ที่แท้จริง (ไม่มี pool อื่นไกลกว่าในทิศเดียวกันที่ยังไม่โดน sweep) —
    คำนวณล้วนๆ จาก pool list ไม่ต้องพึ่ง AI เลย ใช้เป็น pre-check ก่อนเรียก
    Supervisor Judge (เสียเงิน) กันเรียก AI ไปเปล่าๆ ทั้งที่รู้คำตอบล่วงหน้าแล้วว่า
    ต้อง reject แน่ๆ คืน (ok, note) — ok=False แปลว่าต้อง reject ทันที ไม่ต้องเรียก AI
    """
    if setup_type_s not in ("BSL_SWEEP_SELL", "SSL_SWEEP_BUY") or not smc_summary:
        return True, ""
    _liq_chk = smc_summary.get("liquidity") or {}
    if setup_type_s == "BSL_SWEEP_SELL":
        _swept_lvl = (smc_summary.get("last_sweep_high") or {}).get("level")
        _pool_list = _liq_chk.get("weekly_bsl_pools") or []
    else:
        _swept_lvl = (smc_summary.get("last_sweep_low") or {}).get("level")
        _pool_list = _liq_chk.get("weekly_ssl_pools") or []
    if _swept_lvl is None:
        return True, ""
    if setup_type_s == "BSL_SWEEP_SELL":
        _bigger_unswept = [p for p in _pool_list if not p.get("swept") and p.get("level", -9e9) > _swept_lvl + 1.0]
    else:
        _bigger_unswept = [p for p in _pool_list if not p.get("swept") and p.get("level", 9e9) < _swept_lvl - 1.0]
    _bigger_unswept.sort(key=lambda p: abs(p.get("level", 0) - _swept_lvl))
    if _bigger_unswept:
        _bp = _bigger_unswept[0]
        return False, (
            f"🚫 MAJOR POOL CHECK: sweep ที่ {_swept_lvl} ยังมี pool ที่ {_bp.get('level')} "
            f"อยู่ไกลกว่า (ไปทิศเดียวกัน) และยังไม่โดน sweep เลย — liquidity ที่แท้จริงกว่า "
            f"ยังไม่ถูกกวาด premise ของ {setup_type_s} ยังไม่สมเหตุสมผล รอให้ pool ที่ "
            f"{_bp.get('level')} โดน sweep จริงก่อน — ข้าม AI call เพื่อประหยัด"
        )
    return True, ""


def _supervisor_judge(analysis, bias, news, risk, vote_score, vote_details: dict, smc_summary: dict = None) -> dict:
    """
    Supervisor ตัดสินใจสุดท้าย — Claude Sonnet อ่าน reasoning ทุก agent
    ไม่ได้แค่นับ vote score แต่เข้าใจ WHY แต่ละ agent โหวต
    """
    chart_r = vote_details.get("chart", analysis.get("reasoning", ""))
    bias_r  = vote_details.get("bias",  bias.get("reasoning", ""))
    news_r  = vote_details.get("news",  news.get("reasoning", ""))

    # สร้าง vote summary สำหรับ prompt
    chart_vote_str = analysis.get('vote', '?')
    bias_vote_str  = bias.get('vote', '?')
    news_vote_str  = news.get('vote', '?')
    bias_case      = bias.get('case', '?')
    at_htf         = bias.get('at_htf_level', False)
    htf_detail     = bias.get('htf_level_detail', '')

    perf = get_performance_summary(days=30)
    loss_ctx = get_loss_lesson_digest()

    # Late NY session context
    from agents.smc_engine import get_session
    sess_now = get_session()
    is_late_ny = sess_now.get("is_late_ny", False)
    late_ny_warning = (
        "\n⚠️ *LATE NY SESSION (00:00–04:00 Thai)* — liquidity ต่ำ spread กว้าง\n"
        "   → ต้องการ confidence ≥ 75% + setup ชัดมาก (SWEEP_REJECT เท่านั้น)\n"
        "   → ถ้า setup เป็น BULL_OB_ENTRY หรือ TREND_OB ให้ REJECT ก่อน รอ session ปกติ\n"
    ) if is_late_ny else ""

    # Liquidity map info จาก chart analyst
    liq_target   = analysis.get("liquidity_target")
    idm_level    = analysis.get("inducement_level")
    liq_map_read = analysis.get("liquidity_map_read", "")
    setup_type_s = analysis.get("setup_type", "")

    # ── Build M15 context ──────────────────────────────────────────
    m15_ctx = ""
    if smc_summary:
        m15 = smc_summary.get("m15") or {}
        liq  = smc_summary.get("liquidity") or {}
        m15_bias   = m15.get("bias", "–")
        m15_bos    = m15.get("last_bos") or {}
        m15_choch  = m15.get("last_choch") or {}
        m15_ob_b   = m15.get("active_bull_ob") or {}
        m15_ob_bear= m15.get("active_bear_ob") or {}
        w_bsl = liq.get("weekly_bsl_pools") or []
        w_ssl = liq.get("weekly_ssl_pools") or []

        def _fmt_pool(pools, n=5):
            intact = [p for p in pools if not p.get("swept")][:n]
            swept  = [p for p in pools if p.get("swept")][:3]
            parts  = [f"{p['level']}({'M15' if p.get('timeframe')=='M15' else 'M5'}{'★' if p.get('size')=='major' else ''})" for p in intact]
            parts += [f"{p['level']}(✓swept)" for p in swept]
            return " | ".join(parts) if parts else "–"

        m15_ctx = f"""
🕐 M15 Context (ใช้เป็นเหตุผลประกอบ ไม่ใช่ vote):
   Bias: {m15_bias}
   BOS: {m15_bos.get('direction','–')} @ {m15_bos.get('level','–')}
   CHoCH: {m15_choch.get('direction','–')} @ {m15_choch.get('level','–')}
   Bull OB: {m15_ob_b.get('bottom','–')}–{m15_ob_b.get('top','–')} (in_ob={m15_ob_b.get('in_ob',False)})
   Bear OB: {m15_ob_bear.get('bottom','–')}–{m15_ob_bear.get('top','–')} (in_ob={m15_ob_bear.get('in_ob',False)})
   Weekly BSL (7d intact→swept): {_fmt_pool(w_bsl)}
   Weekly SSL (7d intact→swept): {_fmt_pool(w_ssl)}
   ★=EQH/EQL major level, M15=stronger level, ✓swept=โดน sweep ไปแล้ว
   → ถ้า signal ตรงกับ M15 bias = confluence สูง
   → ถ้า M15 Bear OB อยู่ใกล้ + BUY signal = OB นั้นอาจเป็น target หรือ resistance — ระบุในเหตุผล
   → Weekly SSL ที่ยัง intact + ราคาใกล้ = แนวที่รอ sweep อยู่
"""

    # ── MAJOR POOL CHECK — คำนวณเอง (authoritative) ว่า sweep นี้เป็นการ sweep
    # BSL/SSL "ตัวจริง" หรือแค่ swing high/low รองที่บังเอิญมี depth พอผ่านเกณฑ์
    # (เจอเคสจริง: BSL_SWEEP_SELL sweep แค่ 4018.7 [minor] แต่ major BSL ที่ 4034
    # ยังไม่โดนแตะเลย — premise ของ BSL_SWEEP_SELL คือ "liquidity หลักโดนกวาดแล้ว
    # smart money จะกลับตัว" ถ้า pool ใหญ่กว่ายังไม่โดน sweep แปลว่า liquidity หลัก
    # ยังไม่ถูกกวาด สัญญาณกลับตัวยังไม่สมเหตุสมผล ไม่ปล่อยให้ LLM เดาว่า sweep นี้
    # "ใหญ่พอ" หรือเปล่าจากความรู้สึก — เช็ค weekly pool list ตรงๆ)
    _major_pool_note = ""
    if setup_type_s in ("BSL_SWEEP_SELL", "SSL_SWEEP_BUY") and smc_summary:
        _liq_chk = smc_summary.get("liquidity") or {}
        if setup_type_s == "BSL_SWEEP_SELL":
            _swept_lvl = (smc_summary.get("last_sweep_high") or {}).get("level")
            _pool_list = _liq_chk.get("weekly_bsl_pools") or []
        else:
            _swept_lvl = (smc_summary.get("last_sweep_low") or {}).get("level")
            _pool_list = _liq_chk.get("weekly_ssl_pools") or []
        if _swept_lvl is not None:
            # ห้ามอิง size=="major" (ต้องตรง EQH/EQL cluster เท่านั้นถึงจะติดแท็กนี้) —
            # pool ธรรมดาที่ไม่ใช่ EQH ก็เป็น "ของจริงที่ยังไม่โดน sweep" ได้เหมือนกัน
            # (เคสจริง: 4034 ไม่ติดแท็ก major แต่ก็ยังเป็น BSL ที่ยังไม่ถูกแตะอยู่ดี)
            # เช็คแค่ "อยู่ไกลกว่าจุดที่ sweep ไปในทิศเดียวกัน" — นั่นแปลว่ายังมี
            # liquidity ที่แท้จริงกว่าเหลืออยู่ ไม่ว่าจะติดแท็ก major หรือไม่ก็ตาม
            if setup_type_s == "BSL_SWEEP_SELL":
                _bigger_unswept = [
                    p for p in _pool_list
                    if not p.get("swept") and p.get("level", -9e9) > _swept_lvl + 1.0
                ]
            else:
                _bigger_unswept = [
                    p for p in _pool_list
                    if not p.get("swept") and p.get("level", 9e9) < _swept_lvl - 1.0
                ]
            _bigger_unswept.sort(key=lambda p: abs(p.get("level", 0) - _swept_lvl))
            if _bigger_unswept:
                _bp = _bigger_unswept[0]
                _major_pool_note = (
                    f"\n🚫 MAJOR POOL CHECK (คำนวณแล้ว ยึดตามนี้): sweep ที่ {_swept_lvl} ยังมี pool "
                    f"ที่ {_bp.get('level')} อยู่ไกลกว่า (ไปทิศเดียวกัน) และยังไม่โดน sweep เลย → "
                    f"liquidity ที่แท้จริงกว่ายังไม่ถูกกวาด premise ของ {setup_type_s} ยังไม่สมเหตุสมผล "
                    f"ต้อง REJECT รอให้ pool ที่ {_bp.get('level')} โดน sweep จริงก่อนค่อย approve\n"
                )
            else:
                _major_pool_note = f"\n✅ MAJOR POOL CHECK: sweep ที่ {_swept_lvl} ไม่มี pool ที่ไกลกว่ายังไม่ sweep ค้างอยู่ — ใช้ approve ได้ตามปกติ\n"

    # NOTE: เคยลองเพิ่ม TREND STRUCTURE CHECK (บังคับต้องมี CHoCH ยืนยันก่อน sweep-reversal)
    # แต่ user feedback: CHoCH เองก็ "หลอก" ได้ (fake CHoCH ที่จริงคือ sweep เฉยๆ แล้วเด้งกลับ
    # ทันที ไม่ใช่ structure เปลี่ยนจริง) — บังคับ CHoCH เป็นเงื่อนไขจึงผิด ถอดออกแล้ว
    # เกณฑ์ที่ถูกต้องคือ: BSL/SSL level คำนวณถูกตัว (MAJOR POOL CHECK) + sweep depth พอ
    # ($5-10 เกินระดับจริง — MIN_SWEEP_DEPTH) + มี rejection candle ยืนยัน เท่านั้นพอ
    _trend_struct_note = ""

    # ── OB PATH CHECK — คำนวณเอง (authoritative) ว่า OB ขวางเส้นทาง entry→TP จริงมั้ย ──
    # (เจอเคสจริง: SELL entry ต่ำกว่า Bear OB อยู่แล้ว [Bear OB อยู่ฝั่งตรงข้ามกับ TP,
    # ไม่ได้อยู่ระหว่างทาง] แต่ Supervisor LLM ยังหยิบ Bear OB มาอ้างเป็นเหตุผลรอ
    # "pullback ขึ้นไปดู rejection ที่ OB ก่อน" ทั้งที่การรอแบบนั้นคือรอให้ราคา breakout
    # ขึ้นสวนทาง SELL ไปเลย ไม่ใช่ confirmation ของ SELL — ต้องคำนวณ geometry จริง
    # ไม่ปล่อยให้ LLM เดาว่า OB "ขวางทาง" หรือเปล่าจากความรู้สึก)
    _ob_path_note = "OB PATH CHECK: entry/TP ไม่ครบ — ข้ามการเช็ค"
    _entry_raw = analysis.get("entry") or analysis.get("entry_zone")
    _entry_mid = (
        (_entry_raw[0] + _entry_raw[1]) / 2 if isinstance(_entry_raw, list) and len(_entry_raw) == 2
        else float(_entry_raw) if _entry_raw is not None else None
    )
    _tp_raw = analysis.get("take_profit") or analysis.get("tp1")
    _tp_val = float(_tp_raw) if _tp_raw is not None else None
    if _entry_mid is not None and _tp_val is not None and smc_summary:
        _lo, _hi = min(_entry_mid, _tp_val), max(_entry_mid, _tp_val)
        _m15c = smc_summary.get("m15") or {}
        _bull_ob_chk = _m15c.get("active_bull_ob") or smc_summary.get("active_bull_ob") or {}
        _bear_ob_chk = _m15c.get("active_bear_ob") or smc_summary.get("active_bear_ob") or {}
        _in_path_notes = []
        _out_path_notes = []
        for _label, _ob in (("Bull OB", _bull_ob_chk), ("Bear OB", _bear_ob_chk)):
            _bot, _top = _ob.get("bottom"), _ob.get("top")
            if _bot is None or _top is None:
                continue
            _overlaps = not (_top < _lo or _bot > _hi)
            if _overlaps:
                _in_path_notes.append(f"{_label} {_bot}-{_top} อยู่ระหว่าง entry({_entry_mid}) กับ TP({_tp_val}) จริง — ใช้เป็นแนวต้าน/รับที่ขวางทางได้")
            else:
                _out_path_notes.append(f"{_label} {_bot}-{_top} อยู่นอกช่วง entry-TP ({_lo}-{_hi}) — ไม่ได้ขวางทาง ห้ามอ้างเป็นเหตุผล reject/รอ")
        _ob_path_note = "OB PATH CHECK (คำนวณแล้ว ยึดตามนี้ ห้ามเดาเอง):\n" + (
            "\n".join(f"   ✅ {n}" for n in _in_path_notes) if _in_path_notes else ""
        ) + ("\n" if _in_path_notes and _out_path_notes else "") + (
            "\n".join(f"   🚫 {n}" for n in _out_path_notes) if _out_path_notes else ""
        )
        if not _in_path_notes and not _out_path_notes:
            _ob_path_note = "OB PATH CHECK: ไม่มี OB ที่มีข้อมูลพอให้เช็ค"

    prompt = f"""คุณคือ Supervisor Agent — ตัดสินใจสุดท้าย APPROVE หรือ REJECT trade นี้
Vote รวม {vote_score}/3 — อ่านเหตุผลของทุก agent แล้วชั่งน้ำหนักเอง (ไม่ต้องนับเสียงข้างมาก)

{perf}
⚠️ Performance ข้างบนเป็นแค่ context — ห้ามนำ WR% หรือ sample size มาตั้งเกณฑ์ confidence ขั้นต่ำ
   การ APPROVE/REJECT ดูจากเงื่อนไข OB/setup/RR เท่านั้น ไม่ใช่จาก WR ประวัติ
{late_ny_warning}
{loss_ctx}

═══ Agent Votes & Reasoning ═══
🔍 Chart Analyst [{chart_vote_str}]
   {chart_r}
   → Signal: {analysis.get('signal')} | Confidence: {analysis.get('confidence')}% | Setup: {setup_type_s} | RR: 1:{analysis.get('rr_ratio')}
   → Entry: {analysis.get('entry_zone')} | SL: {analysis.get('stop_loss')} | TP: {analysis.get('take_profit')}

🌊 Liquidity Map (จาก Chart Analyst):
   {liq_map_read or '–'}
   BSL/SSL Target: {liq_target or '–'} | Inducement: {idm_level or '–'}

🌍 Bias Analyst [{bias_vote_str}] (Case {bias_case}{' — ถึง HTF level แล้ว' if at_htf else ''})
   {bias_r}
   → Weekly={bias.get('weekly_bias')} Daily={bias.get('daily_bias')} H4={bias.get('h4_bias')} H1={bias.get('h1_bias')}
   → Direction: {bias.get('trade_direction')} | HTF Level: {htf_detail or '–'}

📰 News Scout [{news_vote_str}]
   {news_r}
   → Risk: {news.get('risk_level')} | Key Event: {news.get('key_event')} | Gold Impact: {news.get('gold_impact')}

⚖️ Risk Manager: Lot={risk.get('lot')} | Risk={risk.get('risk_pct')}% | Caution={risk.get('caution_mode')} | {risk.get('notes','')}
{m15_ctx}
{_major_pool_note}
{_ob_path_note}

═══ วิธีตัดสิน ═══

── กฎเหล็ก (ห้ามฝ่าฝืน) ──
✅ APPROVE ทันทีถ้า:
   • setup_type = BSL_SWEEP_SELL / SSL_SWEEP_BUY → liquidity sweep เกิดแล้ว + rejection = highest conviction
     (rejection ต้องเกิดภายใน 1-2 แท่งหลัง sweep — ดู PULLBACK STATUS ที่ Chart Analyst ระบุ ถ้า
     Chart บอกว่า pullback_status=SECOND/EXPIRED เพราะรอเกิน 2-4 แท่งไปแล้ว ราคาวิ่งไปไกลจากจุด
     sweep แล้ว ไม่ใช่ entry ที่ดีอีกต่อไป — ให้ REJECT ตามที่ Chart Analyst ประเมิน ไม่ใช่ APPROVE
     ทันทีเพราะเห็นแค่ชื่อ setup_type — และต้องเป็น ✅ ใน "MAJOR POOL CHECK" ด้วย ถ้าขึ้น 🚫 ต้อง
     REJECT เท่านั้น ไม่ว่า timing/rejection จะดูดีแค่ไหนก็ตาม — ไม่ต้องรอ CHoCH ยืนยัน sweep
     ล้วนๆ + depth พอ + rejection candle ก็เพียงพอแล้ว, CHoCH เองก็ fake ได้ ไม่ใช่เกณฑ์ที่เชื่อถือได้)
   • setup_type = BULL_OB_SWEEP_REJECT → sweep+reject ที่ OB เกิดแล้ว = confirmation ชัดที่สุด
   • setup_type = BULL_OB_ENTRY / BEAR_OB_ENTRY + Chart YES + ราคาอยู่ใน OB (หรือเพิ่งโดน rejection
     ดันออกจาก OB ภายใน ≤2 แท่ง) + RR ≥ 1.5
     → นี่คือ pyramid ไม้ 1 เล็กๆ ก่อน ไม่ต้องรอ confirmation เพิ่ม
     → "counter-trend" ไม่ใช่เหตุผล reject สำหรับ BULL_OB/BEAR_OB_ENTRY เด็ดขาด — การเข้าที่ OB
       คือการสวน trend โดยนิยามอยู่แล้ว (นั่นคือเหตุผลที่มันเป็น OB) ห้ามรอ bias เห็นด้วยก่อน
   • setup_type = TREND_OB + Chart YES → trend-aligned entry approve ได้เลย

❌ REJECT ได้แค่ถ้า:
   • มีข่าว High Impact ใน 30 นาที (ฟัง News Scout)
   • Risk Manager VETO (loss streak / daily limit)
   • Chart Analyst NO หรือ confidence < 30%
   • OB ถูก mitigated แล้ว (Chart ระบุ)
   • RR < 1.5
   • OB ขวางเส้นทาง entry→TP จริง (ดู "OB PATH CHECK" ด้านบน — ต้องเป็น ✅ เท่านั้น)
   • Chart Analyst ระบุว่า sweep/pullback หมดอายุแล้ว (รอเกิน 2-4 แท่งหลัง sweep ราคาไปไกลแล้ว)
   • MAJOR POOL CHECK ขึ้น 🚫 (sweep แค่ pool รอง, major pool ยังไม่โดนแตะ — ดูด้านล่าง)

── กฎเหล็ก: BSL/SSL sweep ต้อง sweep pool ตัวจริง ห้าม sweep แค่ pool รอง ──
"MAJOR POOL CHECK" ด้านบนคำนวณแล้วว่า sweep ที่ Chart Analyst อ้างถึงเป็น pool ใหญ่/major
จริงหรือแค่ swing high/low รองที่บังเอิญมี depth พอผ่านเกณฑ์ — ถ้าขึ้น 🚫 (ยังมี major pool
ที่ใหญ่กว่ายังไม่โดน sweep) ต้อง REJECT เสมอ ไม่ว่า rejection candle หรือ timing จะดูดีแค่ไหน
เพราะ premise ทั้งหมดของ BSL_SWEEP_SELL/SSL_SWEEP_BUY คือ "liquidity หลักโดนกวาดแล้ว smart
money จะกลับตัว" — ถ้า liquidity หลัก (major pool) ยังไม่โดนแตะเลย แปลว่ายังไม่มีเหตุผลให้
กลับตัวจริงๆ การ sweep pool รองไม่ได้แปลว่าตลาดจะกลับตัว ราคายังมีโอกาสวิ่งต่อไปหา major
pool ก่อนได้เสมอ

── กฎเหล็ก: ห้ามอ้าง OB ที่ไม่ได้ขวางทางจริง ──
"OB PATH CHECK" ด้านบนคำนวณ geometry จริงแล้วว่า OB แต่ละอันอยู่ระหว่าง entry กับ TP
หรือไม่ (🚫 = ไม่ได้ขวางทาง, ✅ = ขวางทางจริง) ห้ามใช้ OB ที่ขึ้น 🚫 มาเป็นเหตุผล
REJECT หรือ "รอ pullback ไปดู rejection ที่ OB นั้นก่อน" เด็ดขาด — ถ้า OB อยู่คนละฝั่ง
กับ TP (เช่น SELL setup แต่ OB อยู่เหนือทั้ง entry และ TP) แปลว่าราคาผ่านมาแล้ว/ไม่ได้
อยู่ในเส้นทางเลย การรอให้ราคากลับไปที่ OB นั้นคือรอให้ราคา breakout สวนทาง signal
ไปเลย ไม่ใช่ confirmation ของ setup นี้แต่อย่างใด — ใช้ผลจาก OB PATH CHECK เป็นหลัก
ห้ามประเมิน "OB ขวางทางมั้ย" ด้วยความรู้สึกเอง

── กฎเหล็ก: Bias ห้าม block sweep-based / OB-entry setup ──
setup_type ที่เป็น liquidity-sweep/reversal หรือ OB-entry โดยธรรมชาติ (SSL_SWEEP_BUY,
BSL_SWEEP_SELL, SWING_BUY, SWING_SELL, BULL_OB_SWEEP_REJECT, OB_REJECTION_BUY,
OB_REJECTION_SELL, STORED_OB_PULLBACK_*, BULL_OB_ENTRY, BEAR_OB_ENTRY) คือการ "สวน
trend HTF โดยตั้งใจ" อยู่แล้ว (เทรด liquidity grab หรือเข้าที่ OB ก่อนกลับตัว) —
ดังนั้น Bias NO / Bias conflict กับ HTF trend **ห้ามใช้เป็นเหตุผล REJECT เด็ดขาด**
ไม่ว่า Bias จะ conflict แรงแค่ไหน (แม้ Weekly/Daily/H4/H1 bearish 100% ก็ตาม) —
Bias ขัดแย้งคือเรื่องปกติ ไม่ใช่สัญญาณเตือนสำหรับ setup กลุ่มนี้
ถ้าจะ REJECT setup กลุ่มนี้ ต้องใช้เหตุผลอื่นเท่านั้น: OB/BOS ขวางเส้นทางจริงจนทำให้ effective
RR < 1.5, sweep/pullback หมดอายุแล้ว (รอเกิน 2-4 แท่งหลัง sweep — ฟัง Chart Analyst), ข่าว,
หรือ Risk veto — ห้ามเขียนในเหตุผล REJECT ว่า "Bias น้ำหนักสูงกว่าปกติ" หรือทำนองนั้นสำหรับ
setup กลุ่มนี้

── ชั่งน้ำหนัก (สำหรับ setup อื่นที่ไม่ใช่ sweep-based/OB-entry ข้างบน — TREND_OB, TREND_BOS_BREAK ฯลฯ) ──
1. Chart Analyst = agent หลัก น้ำหนักสูงสุด
2. Bias NO เพราะข่าว/HTF structure พัง → น้ำหนักสูง
3. News NO เพราะข่าว High Impact → น้ำหนักสูงที่สุด ต้องฟัง

ตอบ JSON เท่านั้น — ห้ามมีข้อความ/markdown อธิบายก่อนหรือหลัง JSON เด็ดขาด ตัวอักษรแรกของคำตอบต้องเป็น "{{":
{{
  "approve": true/false,
  "confidence": 0-100,
  "entry_condition": "ระบุให้ชัด: เข้าเงื่อนไข Case ไหน (A/B/C/D/E/F) และทำไม เช่น 'Case F1 — BSL ที่ 3350 swept แล้ว + bearish wick ยาว → BSL_SWEEP_SELL' หรือ 'Case B1 — EQL swept + rejection ที่ Bull OB 3180 → BULL_OB_SWEEP_REJECT'",
  "liquidity_summary": "สรุปสถานะ liquidity: BSL อยู่ที่ไหน / SSL อยู่ที่ไหน / ราคากำลังมุ่งหาอะไร / inducement มีมั้ย เช่น 'SSL (EQL) ที่ 3165 swept แล้ว, BSL ที่ 3330 คือ next target TP, ไม่มี inducement ระหว่างทาง'",
  "key_agent": "chart/bias/news — agent ที่มีน้ำหนักมากสุด",
  "reject_reason_detail": {{
    "chart": "เหตุผลจาก Chart vote",
    "bias": "เหตุผลจาก Bias vote",
    "news": "เหตุผลจาก News vote",
    "supervisor": "เหตุผลสุดท้ายที่ supervisor ตัดสิน"
  }},
  "what_to_watch": "⚠️ REQUIRED ถ้า approve=false — ระบุให้ชัดว่าต้องรออะไรและที่ราคาเท่าไร เช่น 'รอ pullback ถึง Bull OB 3180–3182 แล้วดู rejection candle' หรือ 'รอ sweep SSL ที่ 3165 ก่อน แล้วค่อย BUY' — ต้องมีราคาอ้างอิงเสมอ ห้าม null เมื่อ reject",
  "reasoning": "2-3 ประโยค ภาษาไทย — ระบุ: ① เข้าเงื่อนไขไหน ② liquidity อยู่ตรงไหน ③ ทำไมถึง approve/reject"
}}"""

    from agents.sdk_utils import api_query
    raw = api_query(prompt, model=MODEL_SMART, label="Supervisor", max_tokens=1500)
    result = safe_json_parse(
        raw,
        fallback={"approve": vote_score >= 2, "confidence": 0, "reasoning": "JSON parse error — auto by vote score"}
    )
    result["confidence"] = int(result.get("confidence", 0))
    return result


def analyze_reentry(open_trade: dict, current_price: float, smc_summary: dict) -> dict:
    """
    Sonnet วิเคราะห์ว่าควร re-enter หลังราคากลับมาที่ entry zone หรือไม่
    เรียกเมื่อราคาดิ่งกลับมาใกล้ entry หลังจากเคยกำไร ≥200p
    """
    direction = open_trade.get("direction", "?")
    entry     = open_trade.get("entry", 0)
    peak      = open_trade.get("peak_price", entry)
    original_sl = open_trade.get("original_sl", 0)
    tp        = open_trade.get("tp", 0)
    profit_had = abs(peak - entry)

    active_ob    = smc_summary.get("active_ob")
    last_choch   = smc_summary.get("last_choch")
    last_sweep   = smc_summary.get("last_sweep")
    equal_highs  = smc_summary.get("equal_highs")
    equal_lows   = smc_summary.get("equal_lows")
    nearest_fvg  = smc_summary.get("nearest_fvg")

    prompt = f"""คุณคือ Chart Analyst ผู้เชี่ยวชาญ SMC
trade นี้เปิดไปแล้ว แต่ราคากลับมาใกล้ entry zone — วิเคราะห์ว่าควร re-enter หรือยกเลิก

═══ สถานะ Trade เดิม ═══
Direction:  {direction}
Entry:      {entry}
Peak Price: {peak} (เคย profit {profit_had:.1f}p)
ราคาปัจจุบัน: {current_price} (กลับมาจาก peak แล้ว {abs(current_price - peak):.1f}p)
Original SL: {original_sl}  |  TP: {tp}

═══ SMC Context ตอนนี้ ═══
Active OB:   {active_ob}
CHoCH ล่าสุด: {last_choch}
Sweep ล่าสุด: {last_sweep}
EQH: {equal_highs}  |  EQL: {equal_lows}
FVG ใกล้สุด: {nearest_fvg}

วิเคราะห์:
1. ราคาอยู่ที่ OB หรือ Key Level จริงมั้ย?
2. Structure ยัง intact อยู่มั้ย (CHoCH ยังสด/ไม่ถูก invalidate)?
3. ถ้า re-enter SL ใหม่ควรอยู่ที่ไหน?
4. หรือ pullback นี้เป็นสัญญาณว่า thesis เสียแล้ว?

ตอบ JSON เท่านั้น — ห้ามมีข้อความ/markdown อธิบายก่อนหรือหลัง JSON เด็ดขาด ตัวอักษรแรกของคำตอบต้องเป็น "{{":
{{
  "reenter": true/false,
  "confidence": 0-100,
  "reasoning": "2-3 ประโยค ภาษาไทย — ระบุว่า OB/Level ที่กลับมาถึงคืออะไร",
  "new_sl": ราคา SL ใหม่ หรือ null,
  "caution": "ข้อควรระวัง 1 ประโยค" หรือ null
}}"""

    from agents.sdk_utils import api_query
    raw = api_query(prompt, model=MODEL_SMART, label="ReEntry", max_tokens=1000)
    return safe_json_parse(
        raw,
        fallback={"reenter": False, "confidence": 0, "reasoning": "Parse error", "new_sl": None, "caution": None}
    )


def _fmt_next_event(news_s: dict) -> str:
    """บรรทัดข่าวถัดไป: 'ข่าวต่อไป: NFP — พรุ่งนี้ 19:30 (อีก 21.5 ชม.)'"""
    ne = news_s.get("next_event") if isinstance(news_s, dict) else None
    if not ne:
        return ""
    name = _md(str(ne.get("event", ""))[:40])
    hrs  = ne.get("hours_until", 0)
    when = f"{ne.get('day_label','')} {ne.get('time_label','')}".strip()
    impact = ne.get("impact", "")
    icon = "🔴" if impact == "High" else "🟡" if impact == "Medium" else "⚪"
    if hrs < 1:
        left = f"อีก {ne.get('minutes_until', 0)} นาที"
    else:
        left = f"อีก {hrs:g} ชม."
    return f"\n   📅 ข่าวต่อไป: {icon} {name} — {when} ({left})"


def format_alert(result: dict) -> str:
    """แปลง supervisor result เป็น Telegram alert"""

    if not result.get("approved"):
        vote_score  = result.get("vote_score", 0)
        votes_map   = result.get("votes", {})
        vote_detail = result.get("vote_details", {})
        stages      = result.get("stages", {})
        chart_s     = stages.get("chart", {})
        bias_s      = stages.get("bias", {})
        news_s      = stages.get("news", {})
        risk_s      = stages.get("risk", {})
        sup_stage   = stages.get("supervisor", {}) or {}
        watch       = sup_stage.get("what_to_watch", "") if isinstance(sup_stage, dict) else ""
        sup_r       = result.get("reject_reason", "ไม่ผ่านเงื่อนไข")
        analysis    = result.get("analysis", {}) or {}

        def vi(v): return "✅" if v == "YES" or v is True else "❌"

        chart_vote = chart_s.get("vote", votes_map.get("chart") and "YES" or "NO")
        bias_vote  = bias_s.get("vote",  votes_map.get("bias")  and "YES" or "NO")
        news_vote  = news_s.get("vote",  votes_map.get("news")  and "YES" or "NO")

        chart_r = _md((vote_detail.get("chart") or chart_s.get("vote_reasoning") or "")[:120])
        bias_r  = _md((vote_detail.get("bias")  or bias_s.get("vote_reasoning")  or "")[:120])
        news_r  = _md((vote_detail.get("news")  or news_s.get("vote_reasoning")  or "")[:100])

        setup_type = analysis.get("setup_type", "–")
        conf       = analysis.get("confidence", "–")
        signal     = analysis.get("signal", "–")
        watch_line = f"\n👁 *รอดู:* _{_md(watch)}_" if watch else ""

        risk_line = ""
        if risk_s:
            veto = "⛔ VETO" if risk_s.get("veto") else "✅ OK"
            risk_line = f"*[6] Risk:* {veto} | Lot: `{risk_s.get('lot')}` ({risk_s.get('risk_pct')}%)\n"

        return (
            f"🔍 *Scan — 🔴 REJECTED* `({vote_score}/3)`\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💰 ราคา: `{result.get('current_price')}`\n\n"
            f"*[2] Chart:* {vi(chart_vote)} Signal: `{signal}` Conf: `{conf}%` Setup: `{setup_type}`\n"
            f"   _{chart_r}_\n\n"
            f"*[3] Bias:* {vi(bias_vote)} `{bias_s.get('trade_direction','–')}`\n"
            f"   _{bias_r}_\n\n"
            f"*[4] News:* {vi(news_vote)} Risk: `{news_s.get('risk_level','–')}`\n"
            f"   _{news_r}_"
            f"{_fmt_next_event(news_s)}\n\n"
            f"*[5] Vote:* `{vote_score}/3`\n"
            f"{risk_line}"
            f"*[7] Supervisor:* ❌\n"
            f"   _{_md(sup_r[:200])}_"
            f"{watch_line}\n"
            f"⏰ {result.get('timestamp')}"
        )

    signal = result.get("final_signal")
    emoji = "🟢" if signal == "BUY" else "🔴"
    caution = "🟡 *CAUTION MODE*\n" if result.get("caution_mode") else ""

    vote        = result.get("vote_score", 0)
    vote_bar    = "●" * vote + "○" * (3 - vote)
    vote_detail = result.get("vote_details", {})
    votes_map   = result.get("votes", {})

    vote_lines = ""
    bias_stage  = result.get("stages", {}).get("bias", {})
    news_stage  = result.get("stages", {}).get("news", {})
    for agent, passed in votes_map.items():
        icon    = "✅" if passed else "❌"
        reason  = vote_detail.get(agent, "")
        label   = {"chart": "Chart", "bias": "Bias ", "news": "News "}[agent]
        extra   = ""
        if agent == "bias" and bias_stage.get("at_htf_level"):
            extra = f" 📍_{_md(bias_stage.get('htf_level_detail','HTF level'))}_"
        vote_lines += f"\n  {icon} {label}: _{_md(reason[:60])}_{extra}"
        if agent == "news":
            vote_lines += _fmt_next_event(news_stage)

    entry = result.get("entry_zone")
    entry_str = f"`{entry[0]} - {entry[1]}`" if entry else "N/A"

    analysis   = result.get("analysis", {})
    setup_type = analysis.get("setup_type", "")
    rev_stars  = analysis.get("reversal_stars") or ""
    rev_score  = analysis.get("reversal_score", 0)

    if setup_type == "BULL_OB_SWEEP_REJECT":
        setup_line = f"🔥 Setup: *BULL OB SWEEP+REJECT* {rev_stars} — สัญญาณแข็งที่สุด\n"
    elif setup_type == "BULL_OB_ENTRY":
        setup_line = f"📍 Setup: *BULL OB ENTRY* (pyramid) {rev_stars}\n"
    elif "SWING_OB" in str(setup_type):
        setup_line = f"🔀 Setup: *SWING OB* {rev_stars} (score {rev_score}/10)\n"
    elif setup_type:
        setup_line = f"📐 Setup: `{setup_type}`\n"
    else:
        setup_line = ""

    # ── Entry zone with buffer ────────────────────────────────
    sl    = result.get("stop_loss")
    tp    = result.get("take_profit")
    rr    = result.get("rr_ratio")
    price = result.get("current_price")
    lot   = result.get("lot")

    # แสดง entry zone พร้อม buffer ±2 pips
    if entry and len(entry) == 2:
        buf = 2.0
        ez_lo = round(entry[0] - buf, 2)
        ez_hi = round(entry[1] + buf, 2)
        entry_str = f"`{ez_lo} – {ez_hi}`"
    else:
        entry_str = "N/A"

    # คำนวณ entry mid สำหรับหาระยะ SL/TP
    entry_mid = (entry[0] + entry[1]) / 2 if entry and len(entry) == 2 else (price or 0)

    def _pts(price_a, price_b):
        """ระยะห่างระหว่างสองราคา → จุด (×10)"""
        if price_a and price_b:
            return round(abs(float(price_a) - float(price_b)) * 10, 0)
        return None

    sl_pts  = _pts(entry_mid, sl)
    tp_pts  = _pts(entry_mid, tp)
    sl_line = f"  🛑 SL: `{sl}`" + (f"  _({int(sl_pts):,} จุด)_" if sl_pts else "") + "\n"

    # แสดง TP เป็น reference target (EA POS Guard จัดการ exit จริง)
    tp_ext = analysis.get("tp_extended")
    tp1_line = f"  🎯 TP: `{tp}`" + (f"  _({int(tp_pts):,} จุด)_" if tp_pts else "") + "  _(EA จัดการ)_\n"
    tp_lines = tp1_line
    if tp_ext:
        tp_ext_pts = _pts(entry_mid, tp_ext)
        tp_lines += f"  🎯 TP2: `{tp_ext}`" + (f"  _({int(tp_ext_pts):,} จุด)_" if tp_ext_pts else "") + "\n"

    pyramid_plan = analysis.get("pyramid_plan")
    pyramid_line = f"\n📐 *Pyramid:* _{_md(str(pyramid_plan))}_\n" if pyramid_plan else ""

    # Entry condition & liquidity summary
    entry_cond   = result.get("entry_condition", "")
    liq_summary  = result.get("liquidity_summary", "")
    liq_target_r = analysis.get("liquidity_target")
    idm_level_r  = analysis.get("inducement_level")

    cond_line = f"📌 *เงื่อนไข:* _{_md(entry_cond[:180])}_\n" if entry_cond else ""

    liq_line = ""
    if liq_summary and liq_summary != "–":
        liq_line = f"🌊 *Liquidity:* _{_md(liq_summary[:180])}_\n"
    if liq_target_r:
        liq_line += f"   🎯 Target: `{liq_target_r}`"
        if idm_level_r:
            liq_line += f" | IDM: `{idm_level_r}`"
        liq_line += "\n"

    return (
        f"🔔 *SETUP APPROVED — {signal}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{caution}"
        f"{emoji} *{signal}* | {setup_line.strip()}\n"
        f"🗳 Vote: `{vote_bar}` {vote}/3{vote_lines}\n\n"
        f"━━━ เหตุผล ━━━\n"
        f"{cond_line}"
        f"{liq_line}"
        f"━━━ จุดเข้า ━━━\n"
        f"{'🟢 BUY' if signal=='BUY' else '🔴 SELL'} zone: {entry_str}\n"
        f"{tp_lines}"
        f"{sl_line}"
        f"  ⚖️ RR: `1:{rr}` | Lot: `{lot}` ({result.get('risk_pct')}%)\n"
        f"{pyramid_line}"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 ราคาปัจจุบัน: `{price}`\n"
        f"📝 _{_md(result.get('reasoning', ''))}_\n"
        f"⏰ {result.get('timestamp')}"
    )
