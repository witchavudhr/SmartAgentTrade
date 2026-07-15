"""
chart_analyst_agent.py — เรียก Claude ตรงผ่าน Anthropic API (Sonnet)

Flow:
  has_signal() ผ่าน → api_query() → parse JSON
"""

import time

from config.settings import MODEL_SMART
from agents.sdk_utils import api_query
from agents.chart_analyst import has_signal
from agents.json_utils import safe_json_parse

# ── Instructions + Pattern definitions (ใส่ใน prompt เพราะ SDK ไม่มี system param) ──
_INSTRUCTIONS = """\
You are an expert XAUUSD (Gold) SMC trading analyst.

⚠️ OUTPUT FORMAT — STRICTLY ENFORCED:
Your entire response must be ONE JSON object and NOTHING else. The very first character of your
response must be "{". Do NOT write any analysis, headers, bullet points, markdown (no "**bold**"),
or explanation before or after the JSON. Do not narrate your reasoning in prose — put reasoning only
inside the "vote_reasoning" JSON field. A response that starts with anything other than "{" is wrong.

PATTERN PRIORITY (highest → lowest):
0. CASE B ★★★: Price INSIDE an OB right now (in_ob=True), OR price was in the OB and a rejection
   candle just pushed it back OUT of the zone within the last 1-2 bars (RECENT BEAR/BULL OB
   REJECTION data) → BULL_OB_ENTRY / BEAR_OB_ENTRY (conf 55-70)
   Bull OB: bull_ob.in_ob=True (or recent bull OB rejection ≤2 bars ago) + a bullish rejection candle
             at/in the zone → BUY
   Bear OB: bear_ob.in_ob=True (or recent bear OB rejection ≤2 bars ago) + a bearish rejection candle
             at/in the zone → SELL
   This is the simplest, most common setup — no sweep needed, just "price sitting in (or just rejected
   from) a valid OB with confirmation right now". Treat it as a small first-leg / pyramid entry, not a
   max-conviction signal. A genuine rejection candle often WICKS through the OB and CLOSES just outside
   it — that is still a valid CASE B, do not require in_ob=True at this exact instant if the rejection
   candle itself is what pushed price back out.
   entry_zone = [ob.bottom, ob.top] (the OB boundaries themselves, NOT current price alone)
   ⚠️ DO NOT require bias to agree — entering directly at an OB is counter-trend by definition (that is
   the whole point of an OB entry). Bias conflicting is the expected, normal case here, not a red flag.
   ⚠️ Only fire this for price in/just-rejected-from the OB — do NOT use it for price merely "nearby" an OB
   (that is CASE G/I below, which have their own displacement rules).
1. CASE F ★★★★: BSL/SSL Sweep + Rejection → BSL_SWEEP_SELL / SSL_SWEEP_BUY (conf 75-90)
   NOTE: EQL (Equal Lows) = SSL pool; EQH (Equal Highs) = BSL pool — treat identically as CASE F
   ⚠️ There are TWO separate sweep tracks in the data, shown as two "PULLBACK STATUS" lines —
   use PULLBACK STATUS (BUY side) for SSL_SWEEP_BUY and PULLBACK STATUS (SELL side) for
   BSL_SWEEP_SELL. Do NOT judge one direction using the other direction's pullback status —
   a fresh low-sweep does not invalidate an earlier still-relevant high-sweep and vice versa.
2. CASE L ★★★: Post-BOS CHoCH Retest → POST_BOS_CHOCH_RETEST_SELL/BUY (conf 65-80)
   BOS bearish → minor bullish CHoCH (bounce) → price returns near CHoCH swing high → SELL
   BOS bullish → minor bearish CHoCH (dip) → price returns near CHoCH swing low → BUY
   Entry: at/near CHoCH swing high (SELL) or swing low (BUY) — the retracement extreme
   SL: post_bos_choch_retest.sl_ref (3 pts beyond swing_extreme)
   Conf bonus: +10 if rejection_confirmed=True | reduce -10 if retrace_pct > 60 (deep = risky)
   ⚡ This is the "pullback to CHoCH level after BOS" — trend continuation, HIGH probability
3. CASE I ★★★:  Stored OB Pullback (ob_rejection_zones) → STORED_OB_PULLBACK_SELL/BUY (conf 60-75)
4. CASE G ★★★:  OB Rejection (recent, no sweep needed) → OB_REJECTION_SELL/BUY (conf 65-80)
5. CASE J ★★:   Strong Rejection at EQL/EQH → STRONG_REJECTION_SELL/BUY (conf 50-65)
6. CASE H ★★:   Post-Sweep Pullback → POST_SWEEP_PULLBACK_SELL/BUY (conf 60-75)
   Two ways this fires (see POST-SWEEP PULLBACK line in the data):
   (a) FAST — sweep_age_bars ≤8 and pullback ≥15% of the initial move: enter promptly, this is the
       normal case. Do not wait for a deeper pullback once this fires — price is already moving away
       from the sweep zone fast, waiting longer means a worse entry.
   (b) RETEST AFTER BIG MOVE — retest_after_big_move=True: price ran ≥500pts away from the sweep level
       then came back within ~20pts of it — this is a legitimate late retest of the original level even
       though many bars have passed, treat it with the same conviction as (a), not as "stale".
   level_held=True means price never broke back through the sweep extreme — required for both paths
   deeper pullback (>65%) = 2nd touch zone → conf+10 bonus
7. CASE K ★★:   CHoCH + Sweep → Reversal → CHOCH_SWEEP_SELL/BUY (conf 65-80)

CRITICAL RULES:
- CASE B BIAS/STRUCTURE IMMUNITY (strictly enforced, repeated here because it keeps getting missed):
  for BULL_OB_ENTRY / BEAR_OB_ENTRY specifically, NEVER vote NO because of bearish/bullish bias,
  BOS direction, or CHoCH structure direction. An OB entry is counter-trend BY DEFINITION — bias and
  structure will ALWAYS look like they disagree with it, every single time, with no exception. That is
  not new information, it is not a red flag, and it must never appear in vote_reasoning as a reason to
  reject CASE B. If price is in_ob=True (or a rejection just pushed it out ≤2 bars ago) with a genuine
  rejection candle, that is sufficient — the ONLY valid reasons to vote NO on CASE B are: no rejection
  candle actually present, OB already mitigated, or SL/TP math fails RR≥1.5.
- NO_TRADE FOCUS: when voting NO_TRADE, do not just repeat that sweep/pullback statuses are EXPIRED —
  that only explains why the LAST setup died, it does not tell the user what to actually watch for next.
  ⚠️ FIRST check RECENT BEAR/BULL OB REJECTION in the data — if present, price has ALREADY dipped into
  that OB and bounced back out. Do NOT describe this as "approaching" or state a current distance from
  the OB as if it hasn't been touched yet — say explicitly that price already tested the zone (bars_ago)
  and bounced, and explain why that specific test wasn't good enough to trigger a signal (e.g. rejection
  candle too weak, momentum still against it, RR from that level too small) — that is a materially
  different, more accurate statement than "still approaching, X pts away".
  Only if there is NO recent OB rejection: check the ORDER BLOCKS data — if price is approaching
  (within ~100pts, not yet inside) an unmitigated Bull OB or Bear OB in the direction it is currently
  moving, vote_reasoning MUST name that OB's level and the remaining distance to it — that is the more
  useful, forward-looking thing to watch than restating why an old, already-dead sweep expired.
- PULLBACK VALIDITY: SELL pullback close must stay BELOW rejection high; BUY must stay ABOVE rejection low → if violated = INVALIDATED → NO_TRADE
- CHASING CHECK (for OB_REJECTION and STORED_OB_PULLBACK setups): these setups require price to be
  RETURNING toward the OB for a genuine retest, not continuing to run away from it. If momentum_bear=True
  while considering OB_REJECTION_SELL / STORED_OB_PULLBACK_SELL, or momentum_bull=True while considering
  the BUY versions, price is in a strong impulsive move AWAY from the OB right now (chasing an ever-moving
  target, entry keeps sliding further from the OB with each scan while SL stays fixed at the OB edge —
  degrading risk with no fresh confirmation) → NO_TRADE, do not chase. Only valid once momentum has cooled
  and price shows signs of actually turning back toward the OB (a pause, consolidation, or reversal candle).
- ob_quality=LOW (<50p gap) → reduce confidence 15-20
- When unsure → NO_TRADE (never force)
- BIAS CONTEXT: post_sweep_continuation and sweep ages are informational — use to assess context but do NOT hard-block signals. A fresh BUY sweep followed by a bounce into Bear OB is a valid SELL setup (distribution). Trust price action over bias lock.
- OB MIN DISTANCE (strictly enforced for OB_REJECTION and STORED_OB_PULLBACK setups):
  BUY OB: current_price must be >= ob_top + 25  (price must be ≥25 pts ABOVE OB top before pulling back in)
           If current_price < ob_top + 25 → OB too close, no displacement → NO_TRADE
  SELL OB: current_price must be <= ob_bottom - 25  (price must be ≥25 pts BELOW OB bottom)
            If current_price > ob_bottom - 25 → OB too close → NO_TRADE
  Rationale: reversal needs distance (displacement) to build momentum before hitting the OB. In sideways/
             ranging conditions, bull and bear OBs sit close together with no real separation — a bounce
             off an OB that close to opposing structure is easily faked out. The farther apart genuine
             structure sits (e.g. a BSL target far from the OB it is meant to reject from), the stronger
             and more reliable the reaction tends to be.
             An OB within 25 pts will be consumed without a meaningful reaction — no stops collected.
             Only flag OBs where price must travel ≥25 pts to reach the zone.

PULLBACK ENTRY RULE (strictly enforced):
- FIRST pullback (pullback_status=FIRST): VALID — price just bounced from sweep, first retracement candle near sweep level = entry. OB is the TP target, NOT the entry zone.
- SECOND pullback (pullback_status=SECOND): VALID — price moved away then returned within ±$10 of sweep level; reduce confidence by 10
- EXPIRED (pullback_status=EXPIRED): NO_TRADE — setup stale, price too far from sweep zone
- NONE: follow normal OB rules
⚠️ For sweep setups: set entry = current price (near sweep level), NOT at active OB. The OB zone is TP1/TP2.
⚠️ REJECTION TOLERANCE around the target SSL/BSL level (applies before checking FIRST/SECOND/EXPIRED):
  - A rejection candle within $3 of the level BEFORE price fully reaches/sweeps it still counts as valid —
    the wick does not need to touch the exact number, price flinching $3 short of a real liquidity pool
    and reversing is the same signal as sweeping it.
  - If price overshoots PAST the level, keep watching up to $15 beyond it for a rejection candle (this is
    what the FIRST/SECOND window distance already encodes). If nothing rejects within that $15, treat the
    level as missed for this cycle — signal=NO_TRADE and mention in vote_reasoning that the next SSL/BSL
    pool further out is now the one to watch instead of this one.

SWEEP DEPTH — MINIMUM REQUIRED (hard gate, not just a bonus):
- depth < 5 pts → NO_TRADE for CASE F. A wick that barely pokes past the level is noise in a tight/
  ranging market, not a real liquidity grab — there was no meaningful pool of stops to sweep, and no
  real displacement to build a reversal from. This matters even more now that CASE F fires within 1-2
  bars of the sweep (fast entry rule) — do not let that speed rule apply to depth-less noise sweeps.
  Sideways/ranging price action packs bull and bear structure right next to each other; only trade a
  sweep that actually traveled a meaningful distance beyond the level.

SWEEP DEPTH BONUS (once the ≥5pt minimum above is met):
- depth ≥ 5 pts:  conf +5  (meaningful sweep)
- depth ≥ 10 pts: conf +10 (significant liquidity grab)
- depth ≥ 20 pts: conf +20 (major sweep — highest priority, stops heavily collected)
The deeper the sweep, the more stops were collected → stronger reversal → higher conviction

SL CALCULATION (mandatory — never output stop_loss=null when vote=YES):
- BULL_OB_ENTRY: SL = bull_ob.bottom - 3.0 | entry_zone = [bull_ob.bottom, bull_ob.top]
- BEAR_OB_ENTRY: SL = bear_ob.top + 3.0    | entry_zone = [bear_ob.bottom, bear_ob.top]
- BSL_SWEEP_SELL: SL = last_sweep.level + 10.0 (base off the actual BSL pool level, NOT the wick —
  wick depth varies per sweep and can leave too little room; pyramid legs 2/3 need the extra buffer
  to avoid getting stopped out before they can even trigger)
- SSL_SWEEP_BUY:  SL = last_sweep.level - 10.0 (base off the actual SSL pool level, NOT the wick, same reason)
  ⚠️ Use last_sweep.level (the SSL/BSL pool price), not last_sweep.wick_extreme, as the SL anchor
- OB_REJECTION_SELL / STORED_OB_PULLBACK_SELL: SL = bear_ob.top + 3.0
- OB_REJECTION_BUY  / STORED_OB_PULLBACK_BUY:  SL = bull_ob.bottom - 3.0
- POST_SWEEP_PULLBACK_SELL: SL = last_sweep.level + 10.0
- POST_SWEEP_PULLBACK_BUY:  SL = last_sweep.level - 10.0
- CHOCH_SWEEP_SELL: SL = last_choch.level + 3.0
- CHOCH_SWEEP_BUY:  SL = last_choch.level - 3.0
- STRONG_REJECTION: SL = 3 pts beyond the rejection wick extreme
If none of the above applies and SL cannot be calculated → NO_TRADE (do NOT vote YES with null SL)

TP SELECTION (must achieve RR ≥ 1:1.5 — verified mathematically before outputting):
Step 1: risk_distance = abs(entry - stop_loss)
Step 2: required_tp [BUY]  = entry + risk_distance × 1.5   (must be ABOVE entry)
        required_tp [SELL] = entry - risk_distance × 1.5   (must be BELOW entry)
Step 3a [BUY]:  scan WEEKLY BSL pools ordered nearest→farthest (include ✓sw swept ones)
                pick the FIRST pool where pool_level >= required_tp
                if none → try active_bear_ob.top (must also be >= required_tp)
                if still none → output signal=NO_TRADE (do NOT guess a TP)
Step 3b [SELL]: scan WEEKLY SSL pools ordered nearest→farthest (include ✓sw swept ones)
                pick the FIRST pool where pool_level <= required_tp
                if none → try active_bull_ob.bottom (must also be <= required_tp)
                if still none → output signal=NO_TRADE (do NOT guess a TP)
Step 4: VERIFY before output — compute actual_rr = abs(take_profit - entry) / risk_distance
        if actual_rr < 1.5 → go back to Step 3 and scan for a farther pool
        NEVER output take_profit that results in actual_rr < 1.5
⚠️ NEVER pick the nearest pool without verifying actual_rr ≥ 1.5 — scan farther until one qualifies
⚠️ A pool that is closer than required_tp does NOT qualify even if it is the only one available → NO_TRADE
✓sw (swept) BSL/SSL pools are VALID TP targets — price often revisits them for a second liquidity grab

OUTPUT — respond with ONLY this JSON object, first character "{", no text before or after it:
{"signal":"BUY"|"SELL"|"NO_TRADE","setup_type":"BULL_OB_ENTRY"|"BEAR_OB_ENTRY"|"BSL_SWEEP_SELL"|"SSL_SWEEP_BUY"|"OB_REJECTION_SELL"|"OB_REJECTION_BUY"|"STORED_OB_PULLBACK_SELL"|"STORED_OB_PULLBACK_BUY"|"STRONG_REJECTION_SELL"|"STRONG_REJECTION_BUY"|"POST_SWEEP_PULLBACK_SELL"|"POST_SWEEP_PULLBACK_BUY"|"CHOCH_SWEEP_SELL"|"CHOCH_SWEEP_BUY"|"POST_BOS_CHOCH_RETEST_SELL"|"POST_BOS_CHOCH_RETEST_BUY"|"NO_TRADE","confidence":0-100,"entry":price|null,"entry_zone":[low,high]|null,"bull_ob_zone":{"top":price,"bottom":price}|null,"bear_ob_zone":{"top":price,"bottom":price}|null,"stop_loss":price|null,"take_profit":price|null,"rr_ratio":number|null,"vote":"YES"|"NO","vote_reasoning":"1-2 sentences","liquidity_target":price|null}
NOTE: "take_profit" = primary TP (replaces tp1). "rr_ratio" = abs(take_profit-entry)/abs(entry-stop_loss) — compute and include it. If rr_ratio < 1.5, set signal=NO_TRADE instead.
NOTE: "entry_zone" is REQUIRED (non-null) for BULL_OB_ENTRY/BEAR_OB_ENTRY (= the OB's own [bottom, top]).
Always fill "bull_ob_zone"/"bear_ob_zone" with the OB you referenced (top/bottom) whenever your setup_type
used an OB at all (BULL_OB_ENTRY, BEAR_OB_ENTRY, OB_REJECTION_*, STORED_OB_PULLBACK_*) — the supervisor
uses these to verify your entry actually sits at the OB you claimed, not floating in mid-air.
"""

_API_TIMEOUT = 60  # วินาที — ต่อ 1 request


def _fmt(val):
    return str(val) if val is not None else "N/A"


def _build_prompt(smc: dict) -> str:
    price    = smc.get("current_price", 0)
    bias     = smc.get("bias", "neutral")
    sweep    = smc.get("last_sweep") or {}
    choch    = smc.get("last_choch") or {}
    bos      = smc.get("last_bos") or {}
    bull_ob  = smc.get("active_bull_ob") or {}
    bear_ob  = smc.get("active_bear_ob") or {}
    adv      = smc.get("advanced") or {}
    liq      = smc.get("liquidity") or {}
    sess     = smc.get("session") or {}
    ob_q     = smc.get("ob_quality") or {}
    post_sw  = smc.get("post_sweep_continuation") or {}
    bear_rej = smc.get("recent_bear_ob_rejection")
    bull_rej = smc.get("recent_bull_ob_rejection")
    choch_k  = smc.get("choch_sweep_setup")
    stored   = smc.get("stored_ob_rejections") or []
    srw      = smc.get("sweep_rejection_watch")
    rev      = smc.get("reversal") or {}

    ssl_raw = liq.get("nearest_ssl")
    bsl_raw = liq.get("nearest_bsl")
    ssl_lvl = ssl_raw.get("level") if isinstance(ssl_raw, dict) else ssl_raw
    bsl_lvl = bsl_raw.get("level") if isinstance(bsl_raw, dict) else bsl_raw

    # compute sweep_depth ก่อน lines = [...] เพราะถูกใช้ใน LAST SWEEP line
    _sweep_level_early = sweep.get("level") or 0
    _wick_ext_early    = sweep.get("wick_extreme") or _sweep_level_early
    _sweep_depth       = round(abs(_sweep_level_early - _wick_ext_early), 2) if _sweep_level_early else 0

    lines = [
        "=== MARKET DATA — XAUUSD M5 ===",
        f"Price: {price} | Bias: {bias} | Session: {sess.get('session','?')}",
        "",
        "STRUCTURE:",
        f"  CHoCH: {choch.get('direction','none')} @ {choch.get('level','?')}",
        f"  BOS:   {bos.get('direction','none')} @ {bos.get('level','?')}",
        "",
        f"LAST SWEEP: {sweep.get('kind','none')} @ {sweep.get('level','?')} recovered={sweep.get('recovered','?')}"
        + (f" | wick_extreme={sweep.get('wick_extreme','?')} depth={_sweep_depth}pts → SL_ref=level+10 ({round((sweep.get('level') or 0)+10, 2)})" if sweep.get('kind')=='high' else "")
        + (f" | wick_extreme={sweep.get('wick_extreme','?')} depth={_sweep_depth}pts → SL_ref=level-10 ({round((sweep.get('level') or 0)-10, 2)})" if sweep.get('kind')=='low' else ""),
        "",
        "ORDER BLOCKS:",
        f"  Bear OB: {bear_ob.get('bottom','?')} – {bear_ob.get('top','?')} (in_ob={bear_ob.get('in_ob',False)})",
        f"  Bull OB: {bull_ob.get('bottom','?')} – {bull_ob.get('top','?')} (in_ob={bull_ob.get('in_ob',False)})",
        "",
        f"OB QUALITY: {ob_q}",
        "",
        f"LIQUIDITY: SSL={_fmt(ssl_lvl)} BSL={_fmt(bsl_lvl)}",
        f"  Sweep ages: sweep_h={adv.get('sweep_h_age_bars','?')}bars sweep_l={adv.get('sweep_l_age_bars','?')}bars",
    ]

    weekly_bsl = liq.get("weekly_bsl_pools") or []
    weekly_ssl = liq.get("weekly_ssl_pools") or []
    if weekly_bsl:
        _bsl_str = " | ".join(
            f"{p['level']}({'M15' if p['timeframe']=='M15' else 'M5'}{'★' if p['size']=='major' else ''}{'✓sw' if p['swept'] else ''})"
            for p in weekly_bsl[:8]
        )
        lines += [f"  WEEKLY BSL (7d): {_bsl_str}"]
    if weekly_ssl:
        _ssl_str = " | ".join(
            f"{p['level']}({'M15' if p['timeframe']=='M15' else 'M5'}{'★' if p['size']=='major' else ''}{'✓sw' if p['swept'] else ''})"
            for p in weekly_ssl[:8]
        )
        lines += [f"  WEEKLY SSL (7d): {_ssl_str}"]

    pb_bos_choch = smc.get("post_bos_choch_retest")
    if pb_bos_choch:
        lines += [
            "",
            f"CASE L — POST-BOS CHoCH RETEST: dir={pb_bos_choch['direction']} retrace={pb_bos_choch['retrace_pct']}%",
            f"  BOS @ {pb_bos_choch['bos_level']} ({pb_bos_choch['bos_age_bars']}bars ago)",
            f"  CHoCH @ {pb_bos_choch['choch_level']} ({pb_bos_choch['choch_age_bars']}bars ago) | swing_extreme={pb_bos_choch['swing_extreme']}",
            f"  dist_to_choch={pb_bos_choch['dist_pts']}pts | rejection={pb_bos_choch['rejection_confirmed']} | SL_ref={pb_bos_choch['sl_ref']}",
        ]

    if choch_k:
        lines += [
            "",
            f"CASE K — CHoCH+SWEEP: dir={choch_k['direction']} conf={choch_k['confidence']}",
            f"  CHoCH @ {choch_k['choch_level']} ({choch_k['choch_age_bars']}bars) | Sweep @ {choch_k['sweep_level']} ({choch_k['sweep_age_bars']}bars)",
            f"  rejection={choch_k['rejection_confirmed']}",
        ]

    if post_sw:
        lines += [
            "",
            f"POST-SWEEP PULLBACK (CASE H): dir={post_sw.get('direction')} pb={post_sw.get('pullback_pct')}% "
            f"sweep_age_bars={post_sw.get('sweep_age_bars')} level_held={post_sw.get('level_held', False)} "
            f"retest_after_big_move={post_sw.get('retest_after_big_move', False)}",
        ]

    if bear_rej:
        lines += [f"RECENT BEAR OB REJECTION: zone={bear_rej['ob_zone']} {bear_rej['bars_ago']}bars ago"]
    if bull_rej:
        lines += [f"RECENT BULL OB REJECTION: zone={bull_rej['ob_zone']} {bull_rej['bars_ago']}bars ago"]

    if stored:
        lines += ["STORED OB ZONES (CASE I):"]
        for z in stored[:3]:
            lines += [f"  {z['direction']} zone={z['zone']}"]

    if srw:
        lines += [f"SWEEP WATCH ACTIVE: dir={srw['direction']} since={srw['watched_since']}"]

    # ── Pullback status ──────────────────────────────────────────────
    # sweep-based pullback status
    # FIRST  = sweep ≤48 bars + price อยู่ใน OB zone (กำลัง pullback เข้า OB ครั้งแรก)
    # SECOND = sweep ≤48 bars + price ออกจาก OB ไปแล้ว แต่กลับมาใน ±$10 ของ sweep level
    # EXPIRED = sweep >48 bars + price ห่าง level, หรือ setup หมดอายุ
    # BUG FIX: เดิมดึง age จาก adv.sweep_l/h_age_bars (20-bar rolling mechanism,
    # คนละตัวกับ "sweep" ที่มาจาก last_sweep — full-history scan) ทำให้ age มัก
    # อ่านได้ 999 (sentinel "ไม่เจอ") แม้ sweep จะสดจริง → EXPIRED ผิดๆ บ่อยมาก
    # ตอนนี้ใช้ sweep["age_bars"] ที่คำนวณจาก object เดียวกันโดยตรงแทน
    _in_any_ob   = bull_ob.get("in_ob", False) or bear_ob.get("in_ob", False)

    # ── PULLBACK STATUS แยกทิศ — ห้ามใช้ "last_sweep" (sweep ล่าสุดไม่ว่าทิศไหน)
    # เพียงตัวเดียวมาประเมินทั้ง BUY และ SELL เพราะถ้า SSL sweep เกิดทีหลัง BSL sweep
    # last_sweep จะทับเป็น SSL ไปเลย ทำให้ประเมิน SELL setup (BSL_SWEEP_SELL) ผิดตัว
    # (เห็น last_sweep.kind='low' แล้วสรุปว่า "ไม่มี BSL sweep" ทั้งที่จริงมีอยู่ก่อนหน้า)
    # — ใช้ last_sweep_high สำหรับฝั่ง SELL และ last_sweep_low สำหรับฝั่ง BUY เสมอ
    # user feedback: rejection ต้องเข้าใน 1-2 แท่งแรก แล้วตามเข้าเลย — ไม่ชอบรอ
    # 3-4 แท่งค่อยเข้า เพราะราคาวิ่งไปไกลจากจุด sweep แล้ว ไม่ใช่ entry ที่ดีอีกต่อไป
    # (เดิม FIRST ใช้ age<=12, SECOND ใช้ age<=48 แบบไม่จำกัด — หลวมเกินไปมาก)
    # user feedback: sideways/ranging price packs bull/bear structure right next to
    # each other — a wick that barely pokes past a level (shallow depth) is noise,
    # not a real liquidity grab. Especially now that FIRST fires within 1-2 bars,
    # a depth-less sweep must never qualify — require depth >= MIN_SWEEP_DEPTH.
    _MIN_SWEEP_DEPTH = 5.0
    def _pullback(sw: dict) -> tuple[str, float, float, int]:
        _lvl   = sw.get("level") or 0
        _wick  = sw.get("wick_extreme") or _lvl
        _depth = round(abs(_lvl - _wick), 2) if _lvl else 0
        _age   = sw.get("age_bars")
        _age   = _age if _age is not None else 999
        _dist  = round(abs(price - _lvl), 1) if _lvl else 999
        _window = max(15.0, _depth * 2.5)
        if not sw:
            _status = "NONE"
        elif _depth < _MIN_SWEEP_DEPTH:
            _status = "EXPIRED"  # depth ตื้นเกินไป — noise ในโซนแคบ ไม่ใช่ sweep จริง
        elif _age > 48:
            _status = "EXPIRED"
        elif _age <= 2 and _dist <= _window:
            _status = "FIRST"
        elif _age <= 4 and _dist <= 10.0:
            _status = "SECOND"
        else:
            _status = "EXPIRED"
        return _status, _dist, _depth, _age

    _sweep_low  = smc.get("last_sweep_low") or {}
    _sweep_high = smc.get("last_sweep_high") or {}
    _pb_buy,  _dist_buy,  _depth_buy,  _age_buy  = _pullback(_sweep_low)
    _pb_sell, _dist_sell, _depth_sell, _age_sell = _pullback(_sweep_high)

    # ── backward-compat: _pb/_dist/_sweep_depth/_sweep_age = ทิศเดียวกับ "sweep"
    # (last_sweep เดี่ยว) ยังใช้อ้างอิงจุดอื่นๆ ในไฟล์นี้ต่อไปตามเดิม
    if sweep.get("kind") == "high":
        _pb, _dist, _sweep_depth, _sweep_age = _pb_sell, _dist_sell, _depth_sell, _age_sell
    elif sweep.get("kind") == "low":
        _pb, _dist, _sweep_depth, _sweep_age = _pb_buy, _dist_buy, _depth_buy, _age_buy
    else:
        _pb, _dist, _sweep_age = "NONE", 999, 999

    # OB rejection-based pullback status (CASE G)
    _ob_pb = "NONE"
    _ob_rej = bear_rej or bull_rej
    if _ob_rej:
        _ob_age  = _ob_rej.get("bars_ago", 999)
        _ob_zone = _ob_rej.get("ob_zone", [0, 0])
        _ob_mid  = (_ob_zone[0] + _ob_zone[1]) / 2 if isinstance(_ob_zone, list) and len(_ob_zone) == 2 else 0
        _ob_dist = round(abs(price - _ob_mid), 1) if _ob_mid else 999
        if _ob_age > 48:
            _ob_pb = "EXPIRED"
        elif _ob_age <= 12 and _ob_dist <= 15.0:
            _ob_pb = "FIRST"
        elif _ob_dist <= 10.0:
            _ob_pb = "SECOND"
        else:
            _ob_pb = "EXPIRED"

    lines += [
        "",
        f"PULLBACK STATUS (BUY side — SSL/last_sweep_low): {_pb_buy} (age={_age_buy}bars dist={_dist_buy}pts depth={_depth_buy}pts)",
        f"PULLBACK STATUS (SELL side — BSL/last_sweep_high): {_pb_sell} (age={_age_sell}bars dist={_dist_sell}pts depth={_depth_sell}pts)",
        f"  ⚠️ ใช้ BUY side ตัดสิน SSL_SWEEP_BUY เท่านั้น, ใช้ SELL side ตัดสิน BSL_SWEEP_SELL เท่านั้น — คนละ sweep กัน ห้ามสลับ",
        f"ob_rejection={_ob_pb}",
        "",
        f"ADVANCED: signal_type={adv.get('signal_type','?')} "
        f"bull_grab={adv.get('bull_grab',False)} bear_grab={adv.get('bear_grab',False)}",
        f"  momentum: bull={adv.get('momentum_bull',False)} bear={adv.get('momentum_bear',False)}",
        "",
        # SWING REVERSAL ENGINE (detect_swing_entry — rule-based, ตัวที่ has_signal()
        # ใช้ trigger เรียก AI รอบนี้บ่อยครั้ง) — ⚠️ sweep ที่ engine นี้ใช้อาจเป็นคนละ
        # ตัวกับ "LAST SWEEP" ด้านบน (ซึ่งเป็น full-history scan อาจเก่ากว่ามาก) —
        # ถ้า bull_score/bear_score สูงแต่ LAST SWEEP ข้างบนดู EXPIRED อย่าตัดสินใจ
        # จาก LAST SWEEP อย่างเดียว ให้พิจารณา reasons ของ SWING REVERSAL นี้ด้วย
        # ว่ามี fresh trigger (เช่น zone-sweep ≤5 bars) ที่ LAST SWEEP มองไม่เห็น
        f"SWING REVERSAL ENGINE: signal={rev.get('swing_signal') or 'none'} "
        f"bull_score={rev.get('bull_score', 0)} bear_score={rev.get('bear_score', 0)}",
        f"  reasons: {', '.join(rev.get('swing_reasons') or []) or 'none'}",
        f"  entry_zone={rev.get('entry_zone')} sl={rev.get('stop_loss')} tp={rev.get('take_profit')}",
        "",
        "Analyze the data above and return JSON decision only.",
    ]

    return "\n".join(lines)


_MAX_RETRIES = 2   # retry กี่ครั้งก่อน give up
_RETRY_DELAY = 8   # วินาทีที่รอระหว่าง retry


def analyze(smc_summary: dict) -> dict:
    """
    เรียก Sonnet ผ่าน Anthropic API ตรงๆ
    คืน dict format เดียวกันเป๊ะ — supervisor.py ใช้ต่อได้ทันที
    """
    t0 = time.time()

    prompt = _build_prompt(smc_summary)

    raw = None
    last_err = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            raw = api_query(prompt, model=MODEL_SMART, label="ChartAnalyst",
                             max_tokens=1500, timeout=_API_TIMEOUT, system=_INSTRUCTIONS)
            break  # สำเร็จ — ออกจาก retry loop
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            err_str = str(e).lower()
            if any(k in err_str for k in ("rate limit", "429", "usage limit", "quota", "overloaded", "529")):
                print(f"[ChartAnalyst] ⚠️ Rate limit ({elapsed}s): {e}")
                raise RuntimeError(f"ChartAnalyst rate limit — skip: {e}")
            last_err = e

        if attempt < _MAX_RETRIES:
            print(f"[ChartAnalyst] ⚠️ attempt {attempt+1} failed — retry in {_RETRY_DELAY}s ({last_err})")
            time.sleep(_RETRY_DELAY)

    if raw is None:
        raise last_err or RuntimeError("ChartAnalyst API failed after retries")

    elapsed = round(time.time() - t0, 1)
    print(f"[ChartAnalyst] response in {elapsed}s: {raw[:120]}")

    result = safe_json_parse(
        raw,
        fallback={"signal": "NO_TRADE", "vote": "NO", "vote_reasoning": "API parse error", "confidence": 0},
    )
    result["analyzed_at"]   = smc_summary.get("analyzed_at")
    result["current_price"] = smc_summary.get("current_price")
    result["smc_bias"]      = smc_summary.get("bias")
    result["had_sweep"]     = smc_summary.get("last_sweep") is not None
    result["claude_called"] = True
    result["recent_bear_ob_rejection"] = smc_summary.get("recent_bear_ob_rejection")
    result["recent_bull_ob_rejection"] = smc_summary.get("recent_bull_ob_rejection")

    return result
