# SmartAgentTrade — Project Tracker
> Last updated: 2026-06-08

---

## Overall Progress

```
Phase 1 — Foundation         [ 95% ] █████████░
Phase 2 — Full Voting        [ 60% ] ██████░░░░
Phase 3 — Virtual Office     [ 0%  ] ░░░░░░░░░░
Phase 4 — Self-Improvement   [ 0%  ] ░░░░░░░░░░

TOTAL                        [ 15% ] █░░░░░░░░░
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
/start   ✅ เมนูหลัก
/scan    ✅ สแกน Gold ตอนนี้
/status  ✅ ดูสถานะ bot
/pause   ✅ หยุดชั่วคราว
/resume  ✅ เริ่มใหม่
/report  ✅ สรุป trade
/ask     ✅ ถามอะไรก็ได้ + พิมข้อความตรงได้เลย
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
- [ ] เขียน Bias Analyst agent
- [ ] เขียน News Scout agent (ดึง economic calendar)
- [ ] เขียน Supervisor (voting logic)
- [ ] เขียน Risk Manager (veto + lot size)
- [ ] เชื่อม agent ทั้งหมดเข้าด้วยกัน
- [ ] ทดสอบ voting 2/3
- [ ] ทดสอบ veto cases

### คำสั่ง Telegram เพิ่ม
```
/bias    → ดู H1/H4 direction
/news    → ข่าววันนี้
/vote    → ดูผล vote รอบล่าสุด
/risk    → คำนวณ lot size
/report  → สรุป trade ทั้งหมด
```

### Definition of Done
- [ ] setup ผ่าน 2/3 agent ถึงแจ้ง
- [ ] Risk Manager veto ได้จริง
- [ ] bot หยุดแจ้งก่อนข่าว 30 นาที
- [ ] daily report ส่งอัตโนมัติทุกเช้า

---

## Phase 3 — Virtual Office UI
> เป้าหมาย: Web dashboard สวยงาม, report ครบ
> Estimate: 3-4 สัปดาห์ | Budget: ~$0-10/เดือน (hosting)

### Tasks
- [ ] ออกแบบ UI (Figma หรือ sketch คร่าวๆ)
- [ ] เขียน Web App (React + Tailwind)
- [ ] Agent Cards แสดงสถานะ real-time
- [ ] Live Feed แสดง activity
- [ ] Report page (Daily/Weekly/Monthly)
- [ ] สร้าง Agent Avatar (Midjourney)
- [ ] Deploy บน Vercel (ฟรี)

### หน้าที่ต้องมี
```
Dashboard   → ภาพรวม agent ทั้งหมด
Live Feed   → activity real-time
Reports     → Daily / Weekly / Monthly
Trade Log   → ประวัติทุก trade
Settings    → ปรับ mode, risk level
```

### Definition of Done
- [ ] เปิดบนมือถือได้สวยงาม
- [ ] ข้อมูล update real-time
- [ ] report export เป็น PDF ได้
- [ ] agent status แสดงถูกต้อง

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
> 🔄 กำลังทำ Phase 1 — ขั้นตอนถัดไป

1. ทดสอบ /start และ /scan ใน Telegram ให้ครบ
2. เพิ่ม indicator จริง (RSI, EMA) เข้า Chart Analyst
3. เพิ่ม Trade Log บันทึกลง CSV/SQLite
4. รัน bot ทิ้งไว้ 1 ชั่วโมง ดูว่า crash มั้ย
5. เมื่อ Phase 1 ครบ → เริ่ม Phase 2 (Bias + News Agent)

---

## Backlog (ทำทีหลัง)
| รายการ | หมายเหตุ |
|--------|---------|
| UI สวยงาม (Virtual Office) | Phase 3 — React + dark theme + agent cards |
| Agent Avatar | Midjourney generate ทีหลัง |
| MT5 Integration | ส่งคำสั่งเทรดจริง — ทำหลังจาก bot stable แล้ว (มี EA + VPS พร้อมแล้ว) |
| Cloud Server | มี VPS อยู่แล้ว — ย้าย bot ขึ้น VPS เมื่อพร้อม live |
| Daily Auto Report | ส่งสรุปทุกเช้าอัตโนมัติ |

---

## Decisions Log
| วันที่ | Decision |
|--------|---------|
| 2026-06-08 | ใช้ Telegram (interactive) แทน Line |
| 2026-06-08 | เริ่มจาก 0 ไม่ใช้ indicator เดิม |
| 2026-06-08 | ใช้ Python เป็น main language |
| 2026-06-08 | Virtual Office ทำ Phase 3 ไม่ใช่ตอนเริ่ม |
| 2026-06-08 | Voting ต้องผ่าน 2/3 analyst + Risk ไม่ veto |
| 2026-06-08 | ใช้ yfinance ดึงราคา + Claude Sonnet วิเคราะห์ |
| 2026-06-08 | Bot รันครั้งแรกสำเร็จ — Telegram เชื่อมต่อได้แล้ว |
| 2026-06-08 | ปรับ cost: Haiku + cache + signal filter → $22 → $2-3/เดือน |
| 2026-06-08 | มี EA + VPS พร้อมแล้ว — MT5 integration ทำทีหลัง |
