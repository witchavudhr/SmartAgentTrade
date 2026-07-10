# SmartAgentTrade — Setup Pattern Reference

แหล่งอ้างอิงเดียวสำหรับ pattern ที่ `chart_analyst_agent.py` ตรวจจับ + pipeline การเรียก agent ทั้งหมด

## Pattern Priority (สูง → ต่ำ)

| # | Case | ชื่อ | setup_type | Conf | เงื่อนไข |
|---|------|------|-----------|------|----------|
| 1 | **F** ★★★★ | BSL/SSL Sweep + Rejection | `BSL_SWEEP_SELL` / `SSL_SWEEP_BUY` | 75–90 | BSL swept → bearish rejection → SELL / SSL swept → bullish rejection → BUY. EQH=BSL pool, EQL=SSL pool (นับเหมือนกัน) |
| 2 | **L** ★★★ | Post-BOS CHoCH Retest | `POST_BOS_CHOCH_RETEST_SELL` / `_BUY` | 65–80 | BOS ทิศหนึ่ง → CHoCH สวนทางเล็กน้อย (bounce/dip) → ราคากลับมาแตะ CHoCH swing extreme → เข้าตามเทรนด์เดิม (continuation) |
| 3 | **I** ★★★ | Stored OB Pullback | `STORED_OB_PULLBACK_SELL` / `_BUY` | 60–75 | ราคากลับมาที่ OB ที่เคยมี rejection มาก่อน (จาก bot state) |
| 4 | **G** ★★★ | OB Rejection (สด) | `OB_REJECTION_SELL` / `_BUY` | 65–80 | ราคาแตะ Bear/Bull OB แล้วมี rejection candle — ไม่ต้องมี sweep มาก่อน |
| 5 | **J** ★★ | Strong Rejection ที่ EQL/EQH | `STRONG_REJECTION_SELL` / `_BUY` | 50–65 | EQH/EQL sweep แล้วมีแท่งปฏิเสธแรง |
| 6 | **H** ★★ | Post-Sweep Pullback | `POST_SWEEP_PULLBACK_SELL` / `_BUY` | 60–75 | pullback ≥15% ภายใน ≤30 bar หลัง sweep; `level_held=True` = valid แม้ pullback ตื้น; pullback ลึก >65% = 2nd touch (+10 conf) |
| 7 | **K** ★★ | CHoCH + Sweep → Reversal | `CHOCH_SWEEP_SELL` / `_BUY` | 65–80 | CHoCH เกิดพร้อม/หลัง sweep = สัญญาณกลับตัว |

## กฎบังคับ (Critical Rules)

- **Pullback validity:** SELL ต้องปิดต่ำกว่า rejection high เสมอ / BUY ต้องปิดสูงกว่า rejection low เสมอ — ผิดกฎ = INVALIDATED → NO_TRADE
- **OB quality LOW** (gap <50p) → ลด confidence 15–20
- **ไม่มั่นใจ → NO_TRADE เสมอ** (ห้าม force)
- **OB MIN DISTANCE** (บังคับสำหรับ OB_REJECTION / STORED_OB_PULLBACK):
  - BUY: `current_price >= ob_top + 15` เท่านั้น
  - SELL: `current_price <= ob_bottom - 15` เท่านั้น
  - เหตุผล: reversal ต้องมี displacement (ระยะ+โมเมนตัม) ก่อนชน OB ไม่งั้นราคาจะทะลุผ่านไปเฉยๆ ไม่มี rejection แรง
- **Pullback entry (สำหรับ sweep setup):**
  - `FIRST` = valid, entry ที่ราคาปัจจุบันใกล้ sweep level (ไม่ใช่ที่ OB — OB คือ TP)
  - `SECOND` = valid แต่ลด conf 10, ราคาต้องกลับมาใน ±$10 ของ sweep level
  - `EXPIRED` = NO_TRADE (stale)
- **Sweep depth bonus:** ≥5pt → +5 conf, ≥10pt → +10, ≥20pt → +20 (สะสมสต็อปมาก = กลับตัวแรง)

## SL Calculation (ต้องคำนวณเสมอเมื่อ vote=YES)

| Setup | SL |
|---|---|
| BSL_SWEEP_SELL / SSL_SWEEP_BUY | `last_sweep.wick_extreme` (ไม่ต้อง offset เพิ่ม) |
| OB_REJECTION / STORED_OB_PULLBACK SELL | `bear_ob.top + 3.0` |
| OB_REJECTION / STORED_OB_PULLBACK BUY | `bull_ob.bottom - 3.0` |
| POST_SWEEP_PULLBACK | `last_sweep.wick_extreme` |
| CHOCH_SWEEP SELL/BUY | `last_choch.level ± 3.0` |
| STRONG_REJECTION | 3pt เลย rejection wick extreme |

ถ้าคำนวณ SL ไม่ได้ → NO_TRADE (ห้าม vote YES แล้ว SL=null)

## TP Selection (ต้อง RR ≥ 1:1.5 verified ก่อนส่งออก)

```
1. risk_distance = |entry - stop_loss|
2. required_tp:
     BUY  = entry + risk_distance × 1.5
     SELL = entry - risk_distance × 1.5
3. สแกน weekly BSL/SSL pool ใกล้→ไกล (รวม pool ที่ swept แล้ว ✓sw)
   เลือก pool แรกที่ >= required_tp (BUY) หรือ <= required_tp (SELL)
   ไม่มี → ลอง active OB ฝั่งตรงข้าม → ไม่มีอีก → NO_TRADE
4. verify: actual_rr = |take_profit - entry| / risk_distance
   ถ้า < 1.5 → กลับไป step 3 หา pool ไกลกว่า (ห้ามเดา TP)
```

---

## Pipeline การเรียก Agent (production path)

```
scan ทุก 5 นาที (06:30–22:00)
        │
        ▼
┌─────────────────────────┐
│ 1. SMC Engine (ฟรี)      │  smc_engine.py — คำนวณ OB/BOS/CHoCH/sweep/pool ทั้งหมด
│    rule-based, no LLM   │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 2. has_signal() (ฟรี)    │  chart_analyst.py — เช็คว่ามี setup อย่างน้อย 1 case เข้าเงื่อนไข
│    rule-based gate       │  ❌ ไม่ผ่าน → จบ scan ทันที (ไม่เรียก Claude เลย)
└───────────┬─────────────┘
            │ ✅ ผ่าน
            ▼
┌─────────────────────────┐
│ 3. chart_analyst_agent   │  Sonnet — เลือก 1 ใน 7 CASE, คำนวณ entry/SL/TP/RR
│    (Claude Agent SDK)    │  ❌ vote=NO หรือ NO_TRADE → จบ scan
└───────────┬─────────────┘
            │ ✅ vote=YES
            ▼
┌─────────────────────────┐
│ 4a. bias_analyst          │  cache 15 นาที — เช็ค H1/H4 trend ตรงทาง signal มั้ย
│ 4b. news_scout             │  เช็ค high-impact news ใน 30 นาที
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 5. Vote count (chart+     │  ต้อง ≥1/3 vote ถึงไปต่อ
│    bias+news)             │  ❌ 0/3 → จบ scan
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 6. risk_manager (ฟรี)     │  rule-based VETO (loss streak / daily limit)
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ 7. OB proximity guard     │  ราคาต้องห่าง OB ≥15pt ตอน rule-based check (safety net)
│    (ฟรี, rule-based)      │  ❌ ใกล้เกินไป → reject
└───────────┬─────────────┘
            ▼
      ┌─────┴─────┐
      ▼           ▼
 Fast-approve   ต้องผ่าน
 (rule-based,   supervisor
  ฟรี)          Sonnet judge
  BULL_OB_ENTRY  (อ่าน reasoning
  TREND_OB       ทุก agent
  TREND_BOS      ตัดสินสุดท้าย)
  BSL/SSL sweep
  + RR≥1.5
      │           │
      └─────┬─────┘
            ▼
      APPROVE / REJECT
            ▼
   Telegram alert + MT5 execute
   (entry_zone ต้องเป็น [bottom, top]
    ไม่งั้น block execute)
```

**จุดตัดต้นทุน:** chart_analyst_agent (step 3) เป็น Sonnet call ที่แพงสุดและถูกเรียก**ทุกครั้ง**ที่ has_signal ผ่าน (rule-based เท่านั้น ไม่มี pre-filter ราคาถูกก่อน) — คิดเป็น ~84% ของต้นทุนถ้าเปลี่ยนไปใช้ metered API
