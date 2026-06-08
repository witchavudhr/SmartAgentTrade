# SmartAgentTrade — Project Tracker
> Last updated: 2026-06-08

---

## Overall Progress

```
Phase 1 — Foundation         [ 0%  ] ░░░░░░░░░░
Phase 2 — Full Voting        [ 0%  ] ░░░░░░░░░░
Phase 3 — Virtual Office     [ 0%  ] ░░░░░░░░░░
Phase 4 — Self-Improvement   [ 0%  ] ░░░░░░░░░░

TOTAL                        [ 0%  ] ░░░░░░░░░░
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

### Tasks
- [ ] ติดตั้ง Python 3.11+
- [ ] สมัคร Claude API + เติม credit ($5)
- [ ] สร้าง Telegram Bot (ผ่าน @BotFather)
- [ ] สร้าง project structure
- [ ] เขียน Chart Analyst agent
- [ ] เขียน Telegram Handler (โต้ตอบได้)
- [ ] เขียน Trade Log (บันทึก CSV/SQLite)
- [ ] ทดสอบ /scan, /status, /ask

### คำสั่ง Telegram ที่ต้องทำงานได้
```
/scan    → สแกนหา setup ตอนนี้
/status  → ดูสถานะ bot
/pause   → หยุดชั่วคราว
/resume  → เริ่มใหม่
/ask     → ถามอะไรก็ได้
```

### Definition of Done
- [ ] พิมคำสั่งแล้ว bot ตอบได้ใน 5 วินาที
- [ ] bot ส่ง setup พร้อมปุ่ม Confirm/Skip
- [ ] กด Confirm แล้วบันทึกลง trade log
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
> ✅ สิ่งที่ต้องทำก่อนเริ่ม Phase 1

1. ติดตั้ง Python → https://python.org
2. สมัคร Claude API → https://console.anthropic.com
3. สร้าง Telegram Bot → คุยกับ @BotFather ใน Telegram
4. แจ้ง Claude Code เพื่อเริ่ม code Phase 1

---

## Decisions Log
| วันที่ | Decision |
|--------|---------|
| 2026-06-08 | ใช้ Telegram (interactive) แทน Line |
| 2026-06-08 | เริ่มจาก 0 ไม่ใช้ indicator เดิม |
| 2026-06-08 | ใช้ Python เป็น main language |
| 2026-06-08 | Virtual Office ทำ Phase 3 ไม่ใช่ตอนเริ่ม |
| 2026-06-08 | Voting ต้องผ่าน 2/3 analyst + Risk ไม่ veto |
