# SmartAgentTrade — Project Tracker
> Last updated: 2026-06-17

---

## Overall Progress

```
Phase 1 — Foundation         [100% ] ██████████ ✅
Phase 2 — Full Voting        [100% ] ██████████ ✅
Phase 3 — MT5 Live Execution [85%  ] ████████░░ 🔄
Phase 4 — Self-Improvement   [ 0%  ] ░░░░░░░░░░

TOTAL                        [ 71% ] ███████░░░
```

---

## System Design Summary

### 6 Agents — 3 Layers

```
LAYER 1: ANALYSTS (โหวต + confidence score)
  📊 Chart Analyst   — OB, BOS, CHoCH, Sweep (M5)
  🌍 Bias Analyst    — HTF direction H1/H4/Daily
  📰 News Scout      — Economic calendar, high impact news

LAYER 2: JUDGE
  🎯 Supervisor      — LLM as a Judge, รวบรวม vote 2/3 ผ่าน
  ⚠️ Risk Manager    — VETO power, lot size, max risk 2%

LAYER 3: OUTPUT
  📱 Notifier        — Telegram alert + Virtual Office
```

### Voting Logic
```
ต้องผ่าน 2/3 Analyst + Risk Manager ไม่ VETO
→ ถึงจะแจ้งเตือนคุณ
```

### Self-Improvement Loop
```
จบแต่ละ trade → บันทึก log
ทุกสัปดาห์ → Supervisor อ่าน log ปรับ weight agent
Agent vote ถูกบ่อย → weight สูงขึ้น
Agent vote ผิดบ่อย → weight ต่ำลง
```

---

## Phase 1 — Foundation
> เป้าหมาย: bot คุยได้ + วิเคราะห์ได้ + บันทึกได้
> Estimate: 1-2 สัปดาห์ | Budget: ~$5-10
> Last updated: 2026-06-08

### Tasks
- [x] ติดตั้ง Python 3.11+
- [x] สมัคร Claude API + เติม credit
- [x] สร้าง Telegram Bot (ผ่าน @BotFather)
- [x] สร้าง project structure
- [x] เขียน Chart Analyst agent (yfinance + Claude Sonnet)
- [x] เขียน SMC Engine (Python port จาก LuxAlgo + Sweep detection)
- [x] เขียน Telegram Handler (โต้ตอบได้)
- [ ] เพิ่ม indicator จริง (RSI, EMA, pandas-ta)
- [x] เขียน Trade Log (SQLite — confirmed/skipped/win/loss/win rate)
- [ ] ทดสอบ /scan, /status, /ask ครบทุกคำสั่ง

### คำสั่ง Telegram ที่ทำงานได้แล้ว
```
/start     ✅ เมนูหลัก
/scan      ✅ สแกน Gold ตอนนี้
/status    ✅ ดูสถานะ bot
/pause     ✅ หยุดชั่วคราว
/resume    ✅ เริ่มใหม่
/report    ✅ สรุป trade
/scalein   ✅ คำนวณ Scale-in plan (OB top/bottom + direction + balance)
/ask       ✅ ถามอะไรก็ได้ + พิมข้อความตรงได้เลย
```

### Tech ที่ใช้ตอนนี้
```
ดึงราคา:  yfinance (GC=F Gold Futures)
SMC:      smc_engine.py (Python port จาก LuxAlgo)
          - Swing Points, BOS, CHoCH
          - Order Blocks + mitigation
          - Fair Value Gaps
          - Liquidity Sweep ✅ (เพิ่มเติมจาก LuxAlgo)
          - Equal Highs/Lows
วิเคราะห์: Claude Sonnet (context + entry/exit จาก SMC data)
Telegram:  python-telegram-bot + job-queue
Auto scan: ทุก 15 นาที
```

### Definition of Done
- [ ] พิมคำสั่งแล้ว bot ตอบได้ใน 5 วินาที
- [ ] bot ส่ง setup พร้อมปุ่ม Confirm/Skip
- [ ] กด Confirm แล้วบันทึกลง trade log
- [ ] เพิ่ม indicator (RSI/EMA) เพื่อความแม่นยำ
- [ ] รันบน Mac ได้ไม่ crash 1 ชั่วโมง

---

## Phase 2 — Full Voting System
> เป้าหมาย: voting ครบ, veto ทำงาน, filter ดีขึ้น
> Estimate: 2-3 สัปดาห์ | Budget: ~$5-15

### Tasks
- [x] เขียน Bias Analyst agent (H1/H4/Daily, Haiku, 60min cache)
- [x] เขียน News Scout agent (economic calendar, Haiku, 30min cache)
- [x] เขียน Supervisor (voting logic, LLM as a Judge)
- [x] เขียน Risk Manager (veto + lot size + VETO conditions)
- [x] เพิ่ม Scale-in OB strategy (30%/30%/40%, sweep zone = entry 3)
- [x] เชื่อม agent ทั้งหมดเข้าด้วยกัน (pipeline: SMC→News→Chart→Bias→Vote→Risk→Supervisor)
- [ ] ทดสอบ voting 2/3 ในสภาพตลาดจริง
- [ ] ทดสอบ veto cases (streak, daily loss)
- [ ] daily report ส่งอัตโนมัติทุกเช้า

### Scale-in Strategy (เพิ่มใหม่ ✅)
```
OB Zone เข้า 3 entries:
  Entry 1 (OB top):     30% lot — เริ่มเล็ก ยังไม่มั่นใจ
  Entry 2 (OB middle):  30% lot — ยืนยันโซน
  Entry 3 (Sweep zone): 40% lot — ใหญ่สุด high probability
  SL: ใต้ sweep zone เสมอ — ไม่มี hard SL ไม่เทรด

ใช้ /scalein [top] [bot] [bull/bear] [balance] ใน Telegram
```

### คำสั่ง Telegram เพิ่ม
```
/bias      ✅ ดู H1/H4 direction
/news      ✅ ข่าววันนี้
/scalein   ✅ คำนวณ Scale-in OB plan
/ob        ✅ แสดง Bull OB / Bear OB (M5+M15) ปัจจุบัน
/outcome   ✅ บันทึก win/loss เอง (ticket + pips)
/backfill  ✅ ดึง MT5 deal history เติม trade ที่ pending
/closetrade ✅ ปิด MT5 position + re-scan ทันที
```

### Definition of Done
- [x] setup ผ่าน 2/3 agent ถึงแจ้ง
- [x] Risk Manager veto ได้จริง (streak ≥3, daily loss >3%, RR <1.5)
- [x] Scale-in OB plan คำนวณได้จาก Telegram
- [ ] bot หยุดแจ้งก่อนข่าว 30 นาที (logic อยู่แล้ว — ต้องทดสอบ)
- [ ] daily report ส่งอัตโนมัติทุกเช้า

---

## Phase 3 — MT5 Live Execution
> เป้าหมาย: เปิด/ปิด trade จริงผ่าน MT5 + EA จัดการ trailing
> Status: 🔄 Live บน demo — กำลัง fine-tune

### Tasks
- [x] mt5_executor.py — เปิด/ปิด trade ผ่าน MT5 Python API
- [x] SmartPartialClose EA — trailing stop + partial close อัตโนมัติ
- [x] trade_monitor — monitor open position, trailing SL, re-entry
- [x] pos_guard — ป้องกัน duplicate position
- [x] state_manager — บันทึก bot state ลงดิสก์ (รอด restart)
- [x] startup scan — จับ scan ที่หายเมื่อ restart
- [x] paper_trader — ทดสอบโดยไม่ใช้เงินจริง
- [x] War Room dashboard — web UI ดู signal real-time (localhost:8000)
- [x] AMD pattern detector — จับท่า Spring/Upthrust (Range→Sweep→CHoCH→BOS)
- [x] Primary OB selector — code เลือก M5/M15 OB ที่ใกล้ที่สุดก่อนส่งให้ Sonnet
- [x] OB_ENTRY threshold $5 — ป้องกัน miss entry
- [x] bot รันบน Windows เครื่องเดียวกับ MT5 — ใช้ MT5 real-time data (ไม่ delay)
- [x] MT5 transactions table — เก็บ profit_usd, commission, swap, net_usd ลง SQLite
- [x] MT5 history fallback — get_latest_closed_deal() + retry 3 ครั้ง + entry type 1/2/3
- [x] News Scout → ForexFactory JSON feed (ฟรี ไม่ต้อง API key) แทน mock
- [x] News fail → vote NO (เดิม vote YES = "ปลอดภัย" ทั้งที่ดึงไม่ได้)
- [x] Scan window gap fix — เพิ่ม 21:00–21:45 (ช่องว่าง NY Session)
- [x] Re-scan ทันทีหลังไม้ปิด — หา setup ใหม่ ไม่ต้องรอ window ถัดไป
- [x] /backfill — auto-match MT5 deal history กับ trade ที่ยัง pending
- [ ] Midnight scan window (00:00–01:00 Thai) — รอ backtest ก่อนเพิ่ม
- [ ] Daily auto report ทุกเช้า
- [ ] M15 OB algorithm alignment — Python SMC ≠ TradingView Pine (ยังคลาดเคลื่อน)
- [ ] Entry direction filter — ราคากำลังเข้า OB (approaching) เข้าได้ / เด้งแล้ว (bounced) ห้ามเข้า

### Definition of Done
- [x] bot เปิด trade จริงได้ (demo)
- [x] EA จัดการ trailing/partial close
- [x] data real-time (MT5 บน Windows — ไม่ใช้ yfinance delay แล้ว)
- [ ] รันต่อเนื่อง 7 วันไม่ crash (router พังทำ timeout — ต้อง resilience)

---

## Phase 4 — Self-Improvement Loop
> เป้าหมาย: AI เรียนรู้จาก trade จริง ปรับตัวได้
> Estimate: 3-4 สัปดาห์ | Budget: ~$10-20/เดือน

### Tasks
- [ ] ออกแบบ trade log schema (เก็บข้อมูลครบ)
- [ ] เขียน weekly review agent
- [ ] ระบบปรับ weight แต่ละ agent
- [ ] Supervisor อ่าน log แล้วปรับ prompt ตัวเอง
- [ ] track ว่า setup ไหน win rate ดีสุด
- [ ] แจ้งเตือนถ้า performance ตกต่ำ

### Definition of Done
- [ ] หลัง 20 trade → ระบบปรับ weight ได้
- [ ] win rate report ถูกต้อง
- [ ] Supervisor prompt เปลี่ยนตาม performance
- [ ] แจ้งเตือนถ้า drawdown เกิน 5%

---

## Budget Summary

| รายการ | ราคา | ความถี่ |
|--------|------|---------|
| Claude API | ~$5-20 | /เดือน |
| Telegram Bot | ฟรี | - |
| Price Data (yfinance) | ฟรี | - |
| Vercel (hosting UI) | ฟรี | - |
| Cloud Server (optional) | $6-8 | /เดือน |
| Midjourney (avatar) | $10 | ครั้งเดียว |
| **รวมเริ่มต้น** | **~$5** | เดือนแรก |
| **รวมเต็มระบบ** | **~$20-30** | /เดือน |

---

## Timeline Overview

```
มิ.ย. 2026   → Phase 1: Foundation
ก.ค. 2026    → Phase 2: Full Voting
ส.ค. 2026    → Phase 3: Virtual Office
ก.ย. 2026    → Phase 4: Self-Improvement
ต.ค. 2026+   → Live trading + fine-tune
```

---

## Next Action
> 🔄 Phase 3 กำลังรัน live บน demo (Windows + MT5 real-time)

**ทำทันที:**
1. M15 OB algorithm alignment — Python SMC ยังคลาดเคลื่อนจาก TradingView Pine
2. Entry direction filter — approaching OB เข้าได้ / bounced แล้วห้ามเข้า
3. Bot resilience — router พัง/timeout ทำ run_daily job หาย → ต้อง catch-up scan
4. Backtest midnight window (00:00–01:00 Thai) ก่อนเพิ่ม
5. Daily auto report ทุกเช้า

**Phase 4 (ถัดไป):**
1. Weekly review agent — อ่าน trade log ปรับ weight
2. Win rate tracking by setup type
3. Alert ถ้า drawdown เกิน 5%

---

## Backlog (ทำทีหลัง)
| รายการ | หมายเหตุ |
|--------|---------|
| MT5 data bridge (Mac↔Windows) | ย้าย bot ไป Windows หรือ MetaAPI |
| Midnight scan 00:00–01:00 Thai | รอ backtest ก่อน — NY ยังไม่ปิดสมบูรณ์ |
| Virtual Office UI | React + dark theme + agent cards — Phase ถัดไป |
| Daily Auto Report | ส่งสรุปทุกเช้าอัตโนมัติ |
| Live trading (real account) | หลัง demo stable 30+ วัน |
| Agent Avatar | Midjourney generate ทีหลัง |

---

## Decisions Log
| วันที่ | Decision |
|--------|---------|
| 2026-06-08 | ใช้ Telegram (interactive) แทน Line |
| 2026-06-08 | ใช้ Python เป็น main language |
| 2026-06-08 | Voting ต้องผ่าน 2/3 analyst + Risk ไม่ veto |
| 2026-06-08 | ใช้ yfinance ดึงราคา + Claude Sonnet วิเคราะห์ |
| 2026-06-08 | ปรับ cost: Haiku + cache + signal filter → $22 → $2-3/เดือน |
| 2026-06-08 | เพิ่ม Scale-in OB strategy: 30%/30%/40% ratio |
| 2026-06-10 | MT5 executor live บน demo — เปิด/ปิด trade จริงได้แล้ว |
| 2026-06-10 | SmartPartialClose EA จัดการ trailing/partial close แทน bot |
| 2026-06-15 | OB direction check: Bull OB ต้องเป็น retest (ราคาเคยเหนือ OB แล้ว pull back) |
| 2026-06-15 | Primary OB = code เลือก M5/M15 ที่ใกล้ที่สุด ไม่ให้ Sonnet เดาเอง |
| 2026-06-15 | AMD pattern detector เพิ่มเข้า smc_engine — จับท่า Spring/Upthrust |
| 2026-06-16 | OB_ENTRY threshold $3→$5 — ป้องกัน miss entry จาก margin เล็กน้อย |
| 2026-06-16 | bot รันบน Windows เครื่องเดียวกับ MT5 → ใช้ MT5 real-time data |
| 2026-06-17 | OB-first principle: ราคาที่ OB กำหนด signal, macro bias ปรับแค่ confidence ("ซื้อแนวรับ ขายแนวต้าน") |
| 2026-06-17 | Breaker Block: Bear OB ที่โดน BOS up ทะลุ → กลายเป็น support บน pullback = BUY |
| 2026-06-17 | Pyramid SL ใช้ original_sl ของไม้แรกเสมอ — กันไม้ล่าง SL แคบกว่าโดนก่อน |
| 2026-06-17 | MT5 transactions table — แยกเก็บ net USD (commission+swap) จาก pips |
| 2026-06-17 | News Scout → ForexFactory JSON feed; ดึงไม่ได้ → vote NO (ปลอดภัยไว้ก่อน) |
| 2026-06-17 | Re-scan ทันทีหลังไม้ปิด — ไม่ต้องรอ scan window ถัดไป |
