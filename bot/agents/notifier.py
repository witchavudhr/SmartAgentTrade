import asyncio
import anthropic
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from config.settings import (
    ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    MODEL_SMART, TRADING_PAIR, BALANCE
)
from agents import chart_analyst, bias_analyst, news_scout
from agents import supervisor, risk_manager
from agents.trade_log import (
    log_trade, update_outcome, format_report,
    get_all_trades, format_trade_list, export_csv, get_trade,
    format_today_summary, get_summary,
    log_scan,
)
from agents import paper_trader
from agents import state_manager
from agents import mt5_executor
from agents import pos_guard
from agents.json_utils import fmt_pts

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

DASHBOARD_URL = "http://localhost:8000"

def _push_to_dashboard(result: dict):
    """Push supervisor result to War Room dashboard (best-effort)."""
    try:
        import urllib.request
        data = json.dumps({"result": result}).encode()
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/push",
            data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


def _md(text: str) -> str:
    """Escape Telegram Markdown v1 special chars ใน AI-generated text"""
    for ch in ('_', '*', '`', '['):
        text = text.replace(ch, f'\\{ch}')
    return text


async def _safe_send(send_fn, text: str, **kwargs):
    """ส่ง Telegram message — ถ้า Markdown parse fail → retry เป็น plain text"""
    try:
        await send_fn(text, **kwargs)
    except Exception as e:
        if "parse" in str(e).lower() or "entity" in str(e).lower() or "BadRequest" in str(type(e).__name__):
            # Strip markdown และส่งใหม่เป็น plain text
            plain = text.replace("*", "").replace("_", "").replace("`", "").replace("\\", "")
            kw = {k: v for k, v in kwargs.items() if k != "parse_mode"}
            await send_fn(plain, **kw)
        else:
            raise


# โหลด state จาก disk (รองรับ restart / ย้าย session)
bot_state = state_manager.load()

# ── Commands ──────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏢 *SmartAgentTrade* พร้อมแล้ว!\n\n"
        "📡 *Analysis*\n"
        "/scan — สแกนหา setup (auto-execute ถ้าผ่าน) · 7 windows/day\n"
        "/testscan — ดู vote ทุก agent โดยไม่เปิด trade\n"
        "/ob — ดู Bull OB / Bear OB ปัจจุบัน (M5 + M15)\n"
        "/bias — ดู HTF direction H1/H4/Daily\n"
        "/news — เช็คข่าว Economic Calendar\n\n"
        "⚙️ *Control*\n"
        "/status — ดูสถานะ bot\n"
        "/mt5 — ดู MT5 account + open positions\n"
        "/posguard — ดู POS Guard config + force check\n"
        "/pause — หยุดสแกนชั่วคราว\n"
        "/resume — เริ่มสแกนใหม่\n\n"
        "📊 *Report & Tools*\n"
        "/report — สรุป trade จริง + P&L รายวัน\n"
        "/txreport [today|week|month|year] — ดู MT5 transactions + P&L แยกช่วงเวลา\n"
        "/trades — ดู trade history ทั้งหมด\n"
        "/outcome [id] [win/loss/be] [จุด] [exit] — บันทึกผลหลังเทรด\n"
        "/export — ดาวน์โหลด CSV ประวัติ trade\n"
        "/scalein [top] [bot] [bull/bear] [balance] — คำนวณ entry แบบ scale-in\n"
        "/closetrade [exit_price] — ปิด trailing monitor\n\n"
        "📝 *Paper Trade (ลงกระดาษ)*\n"
        "/paper buy 4290 sl 4250 tp 4360 — เปิด Long\n"
        "/paper sell 4350 sl 4380 tp 4290 — เปิด Short\n"
        "/paper status — ดู open trades\n"
        "/paper close [id] [price] — ปิด trade\n"
        "/pnl — สรุป P&L + win rate\n\n"
        "/ask [คำถาม] — ถามอะไรก็ได้\n\n"
        "หรือพิมข้อความถามได้เลยครับ 🤖",
        parse_mode="Markdown"
    )

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 กำลังรัน Supervisor scan...")
    result = supervisor.run(force_session=True)  # manual scan ข้าม session filter
    log_scan(result)
    _push_to_dashboard(result)
    state_manager.set_field(bot_state, "last_scan", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    await _handle_scan_result(result, update.message.reply_text)

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from agents.trade_log import get_summary
    msg    = state_manager.describe(bot_state)
    s      = get_summary()
    msg   += (
        f"\n━━━━━━━━━━━━━━━━━\n"
        f"📈 Trade Log: W`{s['wins']}` / L`{s['losses']}` / ⏳`{s['pending']}`\n"
        f"Pair: `{TRADING_PAIR}`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state_manager.set_field(bot_state, "is_running", False)
    await update.message.reply_text("⏸ หยุดสแกนแล้ว — state บันทึกแล้ว\nพิม /resume เพื่อเริ่มใหม่")

async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state_manager.set_field(bot_state, "is_running", True)
    await update.message.reply_text("▶️ เริ่มสแกนใหม่แล้ว — state บันทึกแล้ว")

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    report = format_report()
    await update.message.reply_text(report, parse_mode="Markdown")

async def cmd_scalein(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /scalein [ob_top] [ob_bottom] [direction] [balance]
    เช่น: /scalein 4313 4280 bullish 10000
    """
    args = ctx.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "📐 *Scale-in Calculator*\n"
            "ใช้แบบนี้:\n"
            "`/scalein [OB_top] [OB_bottom] [bullish/bearish] [balance]`\n\n"
            "ตัวอย่าง:\n"
            "`/scalein 4313 4280 bullish 10000`",
            parse_mode="Markdown"
        )
        return

    try:
        ob_top = float(args[0])
        ob_bottom = float(args[1])
        direction = args[2].lower()
        balance = float(args[3]) if len(args) > 3 else 10000.0

        scale = risk_manager.calculate_scale_in(
            ob_top=ob_top,
            ob_bottom=ob_bottom,
            sl_price=None,
            balance=balance,
            direction=direction
        )
        message = risk_manager.format_scale_in_message(scale)
        await update.message.reply_text(message, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_paper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /paper buy 4290 sl 4250 tp 4360 [lot 0.01] [stars ★★★] [type C_LONG]
    /paper sell 4350 sl 4380 tp 4290
    /paper close [trade_id] [price]
    /paper status
    """
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "📝 *Paper Trade*\n"
            "━━━━━━━━━━━━━━━━━\n"
            "เปิด trade:\n"
            "`/paper buy 4290 sl 4250 tp 4360`\n"
            "`/paper sell 4350 sl 4380 tp 4290`\n\n"
            "เพิ่ม options:\n"
            "`/paper buy 4290 sl 4250 tp 4360 lot 0.05 stars ★★★`\n\n"
            "ปิด trade:\n"
            "`/paper close 1 4340` — ปิด trade #1 ที่ราคา 4340\n"
            "`/paper close` — ปิดทุก open trade ที่ราคาปัจจุบัน\n\n"
            "ดูสถานะ:\n"
            "`/paper status` — open trades\n"
            "`/pnl` — สรุป P&L ทั้งหมด",
            parse_mode="Markdown"
        )
        return

    sub = args[0].lower()

    # ── /paper status ──────────────────────────────────────────
    if sub == "status":
        trades = paper_trader.get_open_trades()
        if not trades:
            await update.message.reply_text("📋 ไม่มี open paper trades ตอนนี้")
            return
        lines = ["📋 *Open Paper Trades*\n━━━━━━━━━━━━━━━━━"]
        for t in trades:
            lines.append(paper_trader.format_open_trade(t))
        await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
        return

    # ── /paper close [id] [price] ──────────────────────────────
    if sub == "close":
        # ดึงราคาปัจจุบัน
        price_data, _ = chart_analyst.get_price_data()
        current = round(price_data['close'].iloc[-1], 2) if price_data is not None else None

        trade_id   = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        close_price = float(args[2]) if len(args) > 2 else (
                      float(args[1]) if len(args) > 1 and not args[1].isdigit() else current)

        if close_price is None:
            await update.message.reply_text("❌ ดึงราคาไม่ได้ — ระบุราคาเองเลยครับ\n`/paper close [id] [price]`", parse_mode="Markdown")
            return

        if trade_id:
            result = paper_trader.close_trade(trade_id, close_price)
            await update.message.reply_text(paper_trader.format_close_result(result), parse_mode="Markdown")
        else:
            # ปิดทุก open trade
            trades = paper_trader.get_open_trades()
            if not trades:
                await update.message.reply_text("📋 ไม่มี open trade")
                return
            for t in trades:
                r = paper_trader.close_trade(t["id"], close_price)
                await update.message.reply_text(paper_trader.format_close_result(r), parse_mode="Markdown")
        return

    # ── /paper buy / sell ─────────────────────────────────────
    if sub in ("buy", "sell"):
        direction = sub.upper()
        # parse: buy 4290 sl 4250 tp 4360 [lot 0.01] [stars ★★] [type C_LONG]
        params = {}
        i = 1
        try:
            params["entry"] = float(args[i]); i += 1
            while i < len(args):
                key = args[i].lower(); i += 1
                if key in ("sl", "stop", "stoploss"):
                    params["sl"] = float(args[i]); i += 1
                elif key in ("tp", "target"):
                    params["tp"] = float(args[i]); i += 1
                elif key == "lot":
                    params["lot"] = float(args[i]); i += 1
                elif key == "stars":
                    params["stars"] = args[i]; i += 1
                elif key == "type":
                    params["setup_type"] = args[i]; i += 1
        except (IndexError, ValueError):
            pass

        if "entry" not in params or "sl" not in params or "tp" not in params:
            await update.message.reply_text(
                "❌ รูปแบบไม่ถูกต้อง\n"
                "ตัวอย่าง: `/paper buy 4290 sl 4250 tp 4360`",
                parse_mode="Markdown"
            )
            return

        from agents.smc_engine import get_session
        sess = get_session()

        result = paper_trader.open_trade(
            direction   = direction,
            entry_price = params["entry"],
            sl_price    = params["sl"],
            tp_price    = params["tp"],
            lot         = params.get("lot", 0.01),
            setup_type  = params.get("setup_type"),
            stars       = params.get("stars"),
            session     = sess.get("session"),
        )

        if "error" in result:
            await update.message.reply_text(f"❌ {result['error']}")
            return

        d = direction
        emoji = "🟢" if d == "BUY" else "🔴"
        await update.message.reply_text(
            f"{emoji} *Paper Trade #{result['id']} เปิดแล้ว*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Direction: *{d}*\n"
            f"Entry: `{result['entry']}`\n"
            f"SL: `{result['sl']}` ({fmt_pts(result['sl_pips'])} จุด)\n"
            f"TP: `{result['tp']}` ({fmt_pts(result['tp_pips'])} จุด)\n"
            f"RR: `1:{result['rr']}`\n"
            f"Lot: `{result['lot']}`\n"
            f"Session: {sess.get('emoji','')} {sess.get('session','')}\n\n"
            f"ปิด trade: `/paper close {result['id']} [ราคา]`",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text("❓ ไม่เข้าใจคำสั่ง — พิม `/paper` เพื่อดูวิธีใช้", parse_mode="Markdown")


async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """สรุป P&L paper trades ทั้งหมด"""
    summary = paper_trader.get_pnl_summary()
    msg = paper_trader.format_pnl_summary(summary)
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """แสดง trade history ล่าสุด 20 รายการ"""
    trades = get_all_trades(limit=20)
    msg    = format_trade_list(trades)
    # แบ่งข้อความถ้ายาวเกิน 4096 chars (Telegram limit)
    if len(msg) <= 4096:
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
            await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_outcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /outcome [id] [win/loss/be] [จุด] [exit_price] [notes...]
    ตัวอย่าง:
      /outcome 5 win 150 3310
      /outcome 5 loss -80
      /outcome 5 be 0 3285 SL moved to entry
    """
    args = ctx.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "📝 *บันทึกผล Trade*\n"
            "━━━━━━━━━━━━━━━━━\n"
            "รูปแบบ:\n"
            "`/outcome [id] [win/loss/be] [จุด] [exit_price]`\n\n"
            "ตัวอย่าง:\n"
            "`/outcome 5 win 150 3310`\n"
            "`/outcome 5 loss -80 3270`\n"
            "`/outcome 5 be 0`\n\n"
            "ดู trade id ได้จาก /trades หรือ /report",
            parse_mode="Markdown"
        )
        return

    try:
        trade_id = int(args[0])
        outcome  = args[1].lower()
        if outcome not in ("win", "loss", "be"):
            await update.message.reply_text("❌ outcome ต้องเป็น `win`, `loss`, หรือ `be`", parse_mode="Markdown")
            return

        pnl_pips    = float(args[2]) if len(args) > 2 else None
        actual_exit = float(args[3]) if len(args) > 3 else None
        notes       = " ".join(args[4:]) if len(args) > 4 else None

        # ดึง trade เพื่อคำนวณ duration
        t = get_trade(trade_id)
        if not t:
            await update.message.reply_text(f"❌ ไม่พบ Trade #{trade_id}")
            return

        duration_min = None
        if t.get("timestamp"):
            try:
                opened = datetime.strptime(t["timestamp"], "%Y-%m-%d %H:%M:%S")
                duration_min = int((datetime.now() - opened).total_seconds() / 60)
            except Exception:
                pass

        update_outcome(
            trade_id     = trade_id,
            outcome      = outcome,
            pnl_pips     = pnl_pips,
            actual_entry = t.get("entry_low"),
            actual_exit  = actual_exit,
            duration_min = duration_min,
            notes        = notes,
        )

        icon   = "✅" if outcome == "win" else "❌" if outcome == "loss" else "↔️"
        pips_s = f"{fmt_pts(pnl_pips, sign=True)} จุด" if pnl_pips is not None else "-"
        dur_s  = f"{duration_min} นาที" if duration_min else "-"
        exit_s = f"`{actual_exit}`" if actual_exit else "-"

        # running P&L วันนี้
        from agents.trade_log import get_today_summary
        today = get_today_summary()
        today_str = (
            f"วันนี้: W`{today['wins']}` L`{today['losses']}` "
            f"| `{fmt_pts(today['total_pips'], sign=True)} จุด`"
        )

        await update.message.reply_text(
            f"{icon} *Trade #{trade_id} — {outcome.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Signal: `{t.get('signal')}`  {t.get('stars') or ''}\n"
            f"P&L: `{pips_s}`  |  Exit: {exit_s}\n"
            f"Duration: {dur_s}\n"
            + (f"Notes: _{notes}_\n" if notes else "")
            + f"━━━━━━━━━━━━━━━━━\n"
            f"📊 {today_str}",
            parse_mode="Markdown"
        )

    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ รูปแบบไม่ถูกต้อง: {e}\nดูวิธีใช้: `/outcome`", parse_mode="Markdown")


async def cmd_backfill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/backfill — ดึง MT5 deal history แล้ว auto-match กับ pending trades"""
    from agents.trade_log import get_pending_trades, update_outcome
    from datetime import datetime, timedelta

    await update.message.reply_text("🔍 กำลังดึง MT5 deal history...")

    pending = get_pending_trades()
    if not pending:
        await update.message.reply_text("✅ ไม่มี pending trade")
        return

    if not mt5_executor.is_available():
        await update.message.reply_text("⚠️ MT5 ไม่ได้เชื่อม — ใช้ `/outcome` แทน", parse_mode="Markdown")
        return

    ok, _ = mt5_executor._connect()
    if not ok:
        await update.message.reply_text("⚠️ เชื่อม MT5 ไม่ได้")
        return

    try:
        import MetaTrader5 as mt5
        date_from = datetime.now() - timedelta(days=30)
        date_to   = datetime.now() + timedelta(hours=1)
        all_deals = mt5.history_deals_get(date_from, date_to) or []
    finally:
        mt5_executor.disconnect()

    if not all_deals:
        await update.message.reply_text(
            "⚠️ MT5 ไม่มี deal history\n"
            "ลองเปิด MT5 → History tab แล้วดูว่ามี trades จริงไหม\n"
            "หรือ deals อาจอยู่นอกช่วง 30 วันที่ query"
        )
        return

    # debug: แสดงตัวอย่าง deals ที่ดึงมา
    close_deals = [d for d in all_deals if d.entry == 1]
    open_deals  = [d for d in all_deals if d.entry == 0]
    debug_lines = [
        f"🔍 *MT5 deals พบ {len(all_deals)} รายการ*\n"
        f"Open deals: {len(open_deals)} | Close deals: {len(close_deals)}\n"
        f"ช่วงเวลา: {date_from.strftime('%m-%d')} ถึง {date_to.strftime('%m-%d')}\n"
    ]
    # แสดง 5 closing deals ล่าสุดเป็น reference
    for d in sorted(close_deals, key=lambda x: x.time, reverse=True)[:5]:
        dt = datetime.fromtimestamp(d.time).strftime("%m-%d %H:%M")
        dir_txt = "BUY" if d.type == 1 else "SELL"
        debug_lines.append(f"  `[{dt}] {dir_txt} pos#{d.position_id} @{d.price} profit={d.profit:.2f}`")
    await update.message.reply_text("\n".join(debug_lines), parse_mode="Markdown")

    # จัด index ตาม position_id → closing deal
    pos_close = {}
    for d in close_deals:
        pid = d.position_id
        if pid not in pos_close or d.time > pos_close[pid].time:
            pos_close[pid] = d

    pos_open = {}
    for d in open_deals:
        pid = d.position_id
        if pid not in pos_open:
            pos_open[pid] = d

    # match: สำหรับแต่ละ pending trade ดูว่ามี position ที่ open time ใกล้เคียงกันไหม
    updated = []
    unmatched = []
    WINDOW_SEC = 3600  # match ±1 ชั่วโมง (กว้างขึ้นเพราะ user อาจเปิดเองใน MT5)

    for trade in pending:
        try:
            trade_ts = datetime.strptime(trade["timestamp"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            unmatched.append(trade)
            continue

        trade_dir = trade["signal"]  # BUY or SELL
        # MT5 deal type: 0=BUY, 1=SELL
        expected_type = 0 if trade_dir == "BUY" else 1

        best_pid = None
        best_diff = float("inf")
        for pid, od in pos_open.items():
            if od.type != expected_type:
                continue
            if pid not in pos_close:
                continue  # ยังไม่ปิด
            open_dt = datetime.fromtimestamp(od.time)
            diff = abs((open_dt - trade_ts).total_seconds())
            if diff < WINDOW_SEC and diff < best_diff:
                best_diff = diff
                best_pid = pid

        if best_pid is None:
            unmatched.append(trade)
            continue

        cd = pos_close[best_pid]
        od = pos_open[best_pid]
        close_px = cd.price
        open_px  = od.price
        pnl_raw  = (close_px - open_px) if trade_dir == "BUY" else (open_px - close_px)
        pnl_pips = round(pnl_raw * 10, 1)
        outcome  = "win" if pnl_pips > 0 else "loss" if pnl_pips < 0 else "be"
        close_dt = datetime.fromtimestamp(cd.time)
        dur_min  = int((close_dt - trade_ts).total_seconds() / 60)

        update_outcome(trade["id"], outcome, pnl_pips,
                       actual_entry=open_px, actual_exit=close_px, duration_min=dur_min)
        updated.append({
            "id": trade["id"], "dir": trade_dir, "setup": trade["setup_type"] or "",
            "outcome": outcome, "pips": pnl_pips,
            "open_px": open_px, "close_px": close_px,
            "matched_pid": best_pid, "diff_sec": int(best_diff),
        })

    # รายงาน
    lines = [f"📊 *Backfill เสร็จแล้ว*\n━━━━━━━━━━━━━━━━━"]
    if updated:
        wins   = sum(1 for u in updated if u["outcome"] == "win")
        losses = sum(1 for u in updated if u["outcome"] == "loss")
        be     = sum(1 for u in updated if u["outcome"] == "be")
        lines.append(f"✅ Match แล้ว {len(updated)} trade — W:{wins} L:{losses} BE:{be}\n")
        for u in updated:
            icon = "✅" if u["outcome"] == "win" else "❌" if u["outcome"] == "loss" else "➖"
            lines.append(
                f"{icon} `#{u['id']} {u['dir']} {u['setup']} {u['pips']:+.1f}p`"
                f" ({u['open_px']}→{u['close_px']}, ±{u['diff_sec']}s)"
            )
    if unmatched:
        lines.append(f"\n⚠️ Match ไม่ได้ {len(unmatched)} trade:")
        for u in unmatched:
            t = u["timestamp"][11:16] if u["timestamp"] else "?"
            lines.append(f"  `#{u['id']} {u['signal']} {u['setup_type'] or ''} {t}` → /outcome {u['id']} win/loss pips")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_mt5import(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/mt5import [days] — import MT5 closed trades โดยตรงเป็น DB records"""
    from agents.trade_log import init_db, DB_PATH
    from datetime import datetime, timedelta
    import sqlite3

    days = 3
    if ctx.args:
        try: days = int(ctx.args[0])
        except ValueError: pass

    await update.message.reply_text(f"📥 กำลัง import MT5 history ย้อนหลัง {days} วัน...")

    if not mt5_executor.is_available():
        await update.message.reply_text("⚠️ MT5 ไม่ได้เชื่อม")
        return

    ok, _ = mt5_executor._connect()
    if not ok:
        await update.message.reply_text("⚠️ เชื่อม MT5 ไม่ได้")
        return

    try:
        import MetaTrader5 as mt5
        date_from = datetime.now() - timedelta(days=days)
        date_to   = datetime.now() + timedelta(hours=1)
        all_deals = mt5.history_deals_get(date_from, date_to) or []
    finally:
        mt5_executor.disconnect()

    if not all_deals:
        await update.message.reply_text("⚠️ MT5 ไม่มี deal history ในช่วงนี้")
        return

    # จับ open/close deals ตาม position_id
    pos_open  = {}
    pos_close = {}
    for d in all_deals:
        pid = d.position_id
        if d.entry == 0 and pid not in pos_open:
            pos_open[pid] = d
        elif d.entry in (1, 2, 3):  # OUT / INOUT / OUT_BY — ครอบ close ทุกแบบ
            if pid not in pos_close or d.time > pos_close[pid].time:
                pos_close[pid] = d

    # ดู tickets ที่ import แล้ว — แยกที่มี outcome แล้ว (skip) จากที่ยัง NULL (update)
    init_db()
    conn = sqlite3.connect(DB_PATH)
    # ticket → (id, outcome) — outcome=NULL แปลว่าเปิดไว้แต่ยังไม่ได้บันทึกผล
    existing_map = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT mt5_ticket, id, outcome FROM trades WHERE mt5_ticket IS NOT NULL"
        ).fetchall()
    }

    imported = []
    updated_existing = 0
    skipped  = 0

    for pid, cd in pos_close.items():
        existing = existing_map.get(pid)
        if existing and existing[1] is not None:
            skipped += 1          # มี outcome แล้ว — ข้าม
            continue
        od = pos_open.get(pid)
        if not od:
            continue

        open_dt   = datetime.fromtimestamp(od.time)
        close_dt  = datetime.fromtimestamp(cd.time)
        # type: 0=BUY open, 1=SELL open
        direction = "BUY" if od.type == 0 else "SELL"
        open_px   = od.price
        close_px  = cd.price
        pnl_raw   = (close_px - open_px) if direction == "BUY" else (open_px - close_px)
        pnl_pips  = round(pnl_raw * 10, 1)
        outcome   = "win" if pnl_pips > 0 else "loss" if pnl_pips < 0 else "be"
        dur_min   = int((close_dt - open_dt).total_seconds() / 60)
        lot       = od.volume

        # ── trade มีอยู่แล้วแต่ outcome ยัง NULL → UPDATE แทน INSERT ──
        if existing:
            conn.execute("""
                UPDATE trades SET outcome=?, actual_entry=?, actual_exit=?,
                       pnl_pips=?, duration_min=?
                WHERE id=?
            """, (outcome, open_px, close_px, pnl_pips, dur_min, existing[0]))
            updated_existing += 1
            imported.append({
                "pid": pid, "dir": direction, "outcome": outcome,
                "pips": pnl_pips, "open_px": open_px, "close_px": close_px,
                "open_dt": open_dt.strftime("%m-%d %H:%M"), "lot": lot,
            })
            continue

        conn.execute("""
            INSERT INTO trades (
                timestamp, pair, session, signal, setup_type, lot,
                entry_low, entry_high, stop_loss,
                action, outcome, actual_entry, actual_exit,
                pnl_pips, duration_min, mt5_ticket, notes
            ) VALUES (?, 'XAUUSD', ?, ?, 'MT5_IMPORT', ?,
                      ?, ?, NULL,
                      'mt5_import', ?, ?, ?,
                      ?, ?, ?, ?)
        """, (
            open_dt.strftime("%Y-%m-%d %H:%M:%S"),
            _get_session_label(open_dt),
            direction,
            lot,
            open_px, open_px,
            outcome,
            open_px, close_px,
            pnl_pips, dur_min,
            pid,
            f"MT5 import — ticket {pid}",
        ))
        imported.append({
            "pid": pid, "dir": direction, "outcome": outcome,
            "pips": pnl_pips, "open_px": open_px, "close_px": close_px,
            "open_dt": open_dt.strftime("%m-%d %H:%M"), "lot": lot,
        })

    conn.commit()
    conn.close()

    # Force dashboard stats refresh
    try:
        import urllib.request
        req = urllib.request.Request(f"{DASHBOARD_URL}/api/refresh-stats", method="POST")
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass

    if not imported:
        await update.message.reply_text(
            f"ℹ️ ไม่มี trade ใหม่ให้ import\n"
            f"(ข้ามไปแล้ว {skipped} รายการที่บันทึกผลแล้ว)"
        )
        return

    wins   = sum(1 for u in imported if u["outcome"] == "win")
    losses = sum(1 for u in imported if u["outcome"] == "loss")
    be     = sum(1 for u in imported if u["outcome"] == "be")
    total_pips = sum(u["pips"] for u in imported)

    lines = [
        f"✅ *Import สำเร็จ {len(imported)} trades*\n"
        f"W:{wins} L:{losses} BE:{be} | รวม `{total_pips:+.1f} จุด`\n"
        f"━━━━━━━━━━━━━━━━━"
    ]
    for u in imported[:15]:
        icon = "✅" if u["outcome"] == "win" else "❌" if u["outcome"] == "loss" else "➖"
        lines.append(f"{icon} `{u['open_dt']} {u['dir']} {u['lot']}L {u['pips']:+.1f}p ({u['open_px']}→{u['close_px']})`")
    if len(imported) > 15:
        lines.append(f"_...และอีก {len(imported)-15} trades_")
    if skipped:
        lines.append(f"\n_ข้ามไป {skipped} trades ที่ import แล้ว_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _get_session_label(dt: datetime) -> str:
    """แปล datetime เป็น session label (Asian/London/NY)"""
    h = dt.hour
    if 1 <= h < 8:   return "Asian"
    if 8 <= h < 16:  return "London"
    if 16 <= h < 23: return "NY"
    return "Off-hours"


async def cmd_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/pending — แสดง trades ที่ยังรอ outcome"""
    from agents.trade_log import get_pending_trades
    rows = get_pending_trades()
    if not rows:
        await update.message.reply_text("✅ ไม่มี trade ค้าง — ทุกตัว update outcome แล้ว")
        return
    lines = [f"📋 *Pending Trades ({len(rows)} รายการ)*\n━━━━━━━━━━━━━━━━━"]
    for r in rows:
        t = r["timestamp"][11:16] if r["timestamp"] else "?"
        entry = f"{r['entry_low']}–{r['entry_high']}" if r["entry_low"] else "?"
        ticket = f" [T#{r['mt5_ticket']}]" if r["mt5_ticket"] else ""
        lines.append(
            f"*#{r['id']}* `{r['signal']} {r['setup_type'] or ''}` {t}{ticket}\n"
            f"  Entry: `{entry}` SL: `{r['stop_loss'] or '?'}`\n"
            f"  → `/outcome {r['id']} win/loss [pips]`"
        )
    lines.append("\n━━━━━━━━━━━━━━━━━\nใช้ `/outcome [id] win 150` หรือ `/outcome [id] loss -80`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Export trade history เป็น CSV แล้วส่งไฟล์ใน Telegram"""
    await update.message.reply_text("📤 กำลัง export CSV...")
    try:
        csv_path = export_csv()
        await update.message.reply_document(
            document=open(csv_path, "rb"),
            filename="trades_export.csv",
            caption=(
                f"📊 Trade Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"เปิดด้วย Excel หรือ Google Sheets ได้เลย"
            )
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Export ไม่ได้: {e}")


async def cmd_bias(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌍 กำลังวิเคราะห์ HTF Bias (H1/H4/Daily)...")
    bias = bias_analyst.analyze()
    message = bias_analyst.format_bias_message(bias)
    await update.message.reply_text(message, parse_mode="Markdown")

async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📰 กำลังเช็ค Economic Calendar...")
    news = news_scout.analyze()
    message = news_scout.format_news_message(news)
    await update.message.reply_text(message, parse_mode="Markdown")

async def cmd_ob(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/ob — แสดง Bull OB และ Bear OB ปัจจุบัน (M5 + M15)"""
    await update.message.reply_text("📦 กำลังดึง OB zones...")
    try:
        from agents.chart_analyst import get_price_data
        df5, smc = get_price_data()
        if smc is None:
            await update.message.reply_text("❌ ดึงข้อมูลราคาไม่ได้")
            return

        price = smc.get("current_price") or smc.get("price")
        source = smc.get("price_source", "yfinance")
        m15 = smc.get("m15") or {}

        m5_bull  = smc.get("active_bull_ob")
        m5_bear  = smc.get("active_bear_ob")
        m15_bull = m15.get("active_bull_ob")
        m15_bear = m15.get("active_bear_ob")

        def _fmt(ob, label):
            if not ob: return f"{label}: ไม่มี"
            in_tag = " ← IN OB ✅" if ob.get("in_ob") else ""
            return f"{label}: `{ob.get('bottom')} – {ob.get('top')}`{in_tag}"

        src_icon = "🔴 yfinance (delay ~15m)" if source == "yfinance" else "🟢 MT5 (real-time)"
        msg = (
            f"📦 *Order Blocks*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📡 Source: {src_icon}\n"
            f"💰 ราคา: `{price}`\n\n"
            f"*M5:*\n"
            f"  🟢 {_fmt(m5_bull, 'Bull OB')}\n"
            f"  🔴 {_fmt(m5_bear, 'Bear OB')}\n\n"
            f"*M15:*\n"
            f"  🟢 {_fmt(m15_bull, 'Bull OB')}\n"
            f"  🔴 {_fmt(m15_bear, 'Bear OB')}\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    question = " ".join(ctx.args) if ctx.args else ""
    if not question:
        await update.message.reply_text("❓ ใช้แบบนี้: /ask [คำถาม]\nเช่น: /ask วันนี้มองทองยังไง")
        return

    await handle_question(update, question)

# ── Free text handler ──────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await handle_question(update, text)

async def handle_question(update: Update, question: str):
    await update.message.reply_text("🤔 กำลังวิเคราะห์...")

    # ดึงราคาปัจจุบัน
    _, price_data = chart_analyst.get_price_data()
    price_info = f"ราคา Gold ปัจจุบัน: {price_data.get('current_price')}" if price_data else ""

    response = claude.messages.create(
        model=MODEL_SMART,
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""คุณคือ AI Trading Assistant ผู้เชี่ยวชาญ Gold (XAUUSD)
ใช้หลัก Smart Money Concepts ในการวิเคราะห์
ตอบเป็นภาษาไทย กระชับ ชัดเจน

{price_info}

คำถาม: {question}"""
        }]
    )

    await update.message.reply_text(response.content[0].text)

# ── Auto-execute helper ────────────────────────────────

def _pyramid_lot(existing_lot: float) -> float:
    """ไม้ pyramid ต้องใหญ่กว่าไม้แรก: ×1.5 (ปัดเป็น 0.01)"""
    lot2 = round(max(0.01, (existing_lot or 0.01) * 1.5), 2)
    from config.settings import MAX_LOT
    return min(lot2, MAX_LOT)


def _price_is_better(new_price: float, ex_price: float, direction: str) -> bool:
    """BUY: ราคาใหม่ต้องต่ำกว่า (cheaper) | SELL: ราคาใหม่ต้องสูงกว่า (higher)"""
    if direction == "BUY":
        return new_price < ex_price
    return new_price > ex_price


async def _execute_pyramid_auto(result: dict, existing_trade: dict, send_fn):
    """
    Pyramid auto-execute — ราคาดีกว่าไม้แรก = เปิดทันที ไม่รอ approve
    ราคาไม้ 2 ต้องดีกว่าไม้ 1 | ล็อตใหญ่กว่าไม้ 1 (×1.5)
    """
    signal    = result.get("analysis", {})
    direction = signal.get("signal", "?")
    entry_raw = signal.get("entry_zone") or signal.get("entry")
    tp_price  = signal.get("take_profit") or signal.get("tp")
    # SL ของ pyramid ต้องใช้ SL เดิมของไม้แรก — ทุกไม้ใน setup เดียวกันต้อง SL เดียวกัน
    # ห้ามใช้ SL จาก scan ใหม่ (Sonnet คิดค่าต่างกันทุกรอบ = ไม้ล่างอาจโดนก่อน)
    sl_price = (
        existing_trade.get("original_sl")
        or existing_trade.get("current_sl")
        or signal.get("stop_loss")
        or signal.get("sl")
    )
    confidence = int(signal.get("confidence") or 0)

    # ใช้ราคาตลาดจริงจาก MT5 (ask/bid) แทน midpoint ของ entry_zone
    # เพราะ MT5 เปิดที่ราคาตลาด ไม่ใช่ราคาที่ analyst แนะนำ
    mkt_price = None
    if mt5_executor.is_available() and direction in ("BUY", "SELL"):
        try:
            mkt_price = mt5_executor.get_current_price(direction)
        except Exception:
            pass

    entry_price = mkt_price or (
        (entry_raw[0] + entry_raw[1]) / 2 if isinstance(entry_raw, list)
        else float(entry_raw) if entry_raw else None
    )

    ex_tid   = existing_trade.get("trade_id", "?")
    ex_dir   = existing_trade.get("direction", "?")
    ex_entry = float(existing_trade.get("entry") or 0)
    ex_lot   = float(existing_trade.get("lot") or 0.01)

    # ── เช็คจำนวนไม้ที่เปิดอยู่ (MT5 เป็น source of truth) ──────
    MAX_PYRAMID = 3
    open_count = 0
    if mt5_executor.is_available():
        try:
            open_count = len(mt5_executor.get_open_positions())
        except Exception:
            pass
    if open_count >= MAX_PYRAMID:
        await send_fn(
            f"🚫 *Pyramid หยุด — ครบ {MAX_PYRAMID} ไม้แล้ว*\n"
            f"ตอนนี้มี `{open_count}` positions เปิดอยู่ใน MT5\n"
            f"_ปิดไม้ก่อนแล้วค่อย pyramid ใหม่_",
            parse_mode="Markdown"
        )
        return

    # ตรวจราคา — ราคาตลาดปัจจุบันต้องดีกว่าไม้แรก
    if entry_price and ex_entry and not _price_is_better(entry_price, ex_entry, direction):
        diff = abs(entry_price - ex_entry)
        price_src = "ราคาตลาด" if mkt_price else "entry zone midpoint"
        await send_fn(
            f"⏭ *Pyramid ข้าม — ราคาไม่ดีกว่าไม้แรก*\n"
            f"ไม้ 1: `{ex_entry}` | {price_src}: `{entry_price}` (ต่างกัน `{diff:.2f}`)\n"
            f"_{'BUY ต้องเข้าต่ำกว่าไม้แรก' if direction == 'BUY' else 'SELL ต้องเข้าสูงกว่าไม้แรก'}_",
            parse_mode="Markdown"
        )
        return

    # ล็อตไม้ 2 ใหญ่กว่าไม้ 1
    lot_val = _pyramid_lot(ex_lot)

    trade_id = log_trade(signal, "confirmed")
    state_manager.set_field(bot_state, "last_scan", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if entry_price and sl_price and direction in ("BUY", "SELL"):
        open_trade = {
            "entry":            entry_price,
            "original_sl":      float(sl_price),
            "current_sl":       float(sl_price),
            "tp":               float(tp_price) if tp_price else None,
            "direction":        direction,
            "lot":              lot_val,
            "trade_id":         trade_id,
            "peak_price":       entry_price,
            "opened_at":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reentry_analyzed": False,
        }
        state_manager.set_field(bot_state, "open_trade", open_trade)

    mt5_tag = ""
    if mt5_executor.is_available() and entry_price and sl_price and direction in ("BUY", "SELL"):
        ex = mt5_executor.open_trade(
            direction=direction,
            lot=lot_val,
            sl=float(sl_price),
            tp=0.0,
            comment=f"SAT-{trade_id}-PYR",
        )
        if "ticket" in ex:
            mt5_tag = f"\n✅ *MT5:* Ticket `{ex['ticket']}` @ `{ex['price']}`"
            ot_upd = bot_state.get("open_trade", {})
            ot_upd["mt5_ticket"] = ex["ticket"]
            state_manager.set_field(bot_state, "open_trade", ot_upd)
            try:
                from agents.trade_log import update_mt5_ticket
                update_mt5_ticket(trade_id, ex["ticket"])
            except Exception:
                pass
        else:
            mt5_tag = f"\n⚠️ *MT5 Error:* `{ex.get('error','unknown')}`"
    else:
        mt5_tag = "\n📋 _MT5 ไม่ได้เชื่อม — เปิด trade เองใน MT5_"

    dir_icon = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    await send_fn(
        f"🔺 *Pyramid Auto-Confirmed — Trade #{trade_id}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚡ confidence {confidence}% > 70% → execute อัตโนมัติ\n"
        f"เพิ่มไม้ {dir_icon} เข้าไม้เดิม #{ex_tid}\n\n"
        f"Entry: `{entry_raw}`\n"
        f"SL: `{sl_price}`  TP: `{tp_price}`\n"
        f"Lot: `{lot_val}`"
        + mt5_tag +
        f"\n\n`/closetrade` เมื่อปิด position",
        parse_mode="Markdown",
    )


async def _send_advisory_alert(result: dict, existing_trade: dict, send_fn):
    """
    Advisory mode — มี open trade ค้างอยู่
    แสดง signal ใหม่ + confidence score + ปุ่มให้กดเองถ้าอยากเปิดเพิ่ม
    ไม้ 2 ต้องราคาดีกว่า + lot ใหญ่กว่าไม้ 1
    """
    signal    = result.get("analysis", {})
    direction = signal.get("signal", "?")
    entry_raw = signal.get("entry_zone") or signal.get("entry")
    sl        = signal.get("stop_loss") or signal.get("sl")
    tp        = signal.get("take_profit") or signal.get("tp")
    rr        = signal.get("rr_ratio", "?")

    entry_price = (
        (entry_raw[0] + entry_raw[1]) / 2 if isinstance(entry_raw, list)
        else float(entry_raw) if entry_raw else None
    )

    # Confidence score
    sup_conf  = result.get("supervisor_confidence") or (
        signal.get("confidence") or round((result.get("vote_score", 0) / 3) * 100)
    )
    if isinstance(sup_conf, str) and sup_conf.endswith("%"):
        sup_conf = int(sup_conf.rstrip("%"))
    conf_pct  = int(sup_conf) if sup_conf else 0
    conf_bar  = _confidence_bar(conf_pct)

    # ไม้ที่ค้างอยู่
    ex_dir    = existing_trade.get("direction", "?")
    ex_entry  = float(existing_trade.get("entry") or 0)
    ex_lot    = float(existing_trade.get("lot") or 0.01)
    ex_tid    = existing_trade.get("trade_id", "?")

    # คำนวณ lot ไม้ 2 (ใหญ่กว่าไม้ 1)
    lot2 = _pyramid_lot(ex_lot)
    signal["lot"] = lot2  # override lot เป็น lot2

    # ตรวจราคา
    price_ok = True
    price_warn = ""
    if entry_price and ex_entry and direction in ("BUY", "SELL"):
        price_ok = _price_is_better(entry_price, ex_entry, direction)
        if not price_ok:
            price_warn = f"\n⚠️ _ราคาไม่ดีกว่าไม้แรก — ไม้ 1: `{ex_entry}` ไม้ 2: `{entry_price}`_"

    dir_icon  = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    same_dir  = direction == ex_dir
    dir_note  = "↗ Pyramid ทิศเดียวกัน" if same_dir else "↔ ทิศตรงข้าม (hedge)"

    # เก็บ signal ไว้สำหรับ callback
    bot_state["pending_signal"] = signal
    state_manager.save(bot_state)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ เปิดเพิ่ม", callback_data="confirm"),
        InlineKeyboardButton("⏭ ข้าม",       callback_data="skip"),
    ]])

    await send_fn(
        f"📡 *Setup ใหม่ — Pyramid Alert*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"ไม้ 1: `{ex_dir}` #{ex_tid} @ `{ex_entry}` (lot `{ex_lot}`)\n"
        f"_({dir_note})_\n\n"
        f"*ไม้ 2:* {dir_icon}\n"
        f"Entry: `{entry_raw}`\n"
        f"SL: `{sl}` | TP: `{tp}` | RR: `{rr}`\n"
        f"Lot ไม้ 2: `{lot2}` (×1.5 จากไม้ 1)"
        + price_warn +
        f"\n\n🎯 *Confidence: {conf_bar}*\n\n"
        f"กดเปิดเพิ่ม หรือข้าม",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


def _confidence_bar(pct: int) -> str:
    """แสดง confidence เป็น bar เช่น ████░░ 67%"""
    filled = round(pct / 10)
    return "█" * filled + "░" * (10 - filled) + f" {pct}%"


async def _handle_scan_result(result: dict, send_fn):
    """
    ถ้ามี open trade ค้างอยู่ → Advisory mode (แจ้ง + confidence + ปุ่มให้กดเอง)
    ถ้าไม่มี open trade → Auto-execute ปกติ
    Rejected → แจ้งสั้นๆ
    """
    from agents.smc_engine import get_session

    if result.get("approved"):
        # ── เช็ค MT5 โดยตรง — source of truth ──────────────────
        mt5_positions = []
        if mt5_executor.is_available():
            try:
                mt5_positions = mt5_executor.get_open_positions()
            except Exception:
                pass

        # sync state ให้ตรงกับ MT5 เสมอ
        existing_trade = bot_state.get("open_trade")
        if existing_trade and mt5_executor.is_available():
            ticket = existing_trade.get("mt5_ticket")
            open_tickets = {p["ticket"] for p in mt5_positions}
            if ticket and ticket not in open_tickets:
                # ปิดจาก MT5 โดยตรง — ดึง close price แล้ว auto-record outcome
                try:
                    deal = mt5_executor.get_last_deal_for_ticket(int(ticket))
                    if deal:
                        close_px = deal["close_price"]
                        entry_px = existing_trade.get("entry", 0)
                        direction = existing_trade.get("direction", "BUY")
                        pnl_raw  = (close_px - entry_px) if direction == "BUY" else (entry_px - close_px)
                        pnl_pips = round(pnl_raw * 10, 1)
                        outcome  = "win" if pnl_pips > 0 else "loss" if pnl_pips < 0 else "be"
                        trade_id = existing_trade.get("trade_id")
                        if trade_id and str(trade_id).isdigit():
                            from agents.trade_log import update_outcome
                            update_outcome(int(trade_id), outcome, pnl_pips, actual_exit=close_px)
                        icon = "✅" if outcome == "win" else "❌" if outcome == "loss" else "➖"
                        print(f"[notifier] {icon} MT5 closed — {outcome} {pnl_pips:+.1f}p trade_id={trade_id}")
                except Exception as e:
                    print(f"[notifier] ⚠️ auto-outcome failed: {e}")
                state_manager.set_field(bot_state, "open_trade", None)
                existing_trade = None
                print(f"[notifier] 🧹 open_trade state cleared — MT5 position closed")
            elif not ticket and not mt5_positions:
                state_manager.set_field(bot_state, "open_trade", None)
                existing_trade = None

        # ถ้า MT5 มีไม้แต่ state ไม่รู้ → restore
        if mt5_positions and not existing_trade:
            pos = mt5_positions[0]
            existing_trade = {
                "direction":   pos["direction"],
                "trade_id":    f"MT5-{pos['ticket']}",
                "mt5_ticket":  pos["ticket"],
                "entry":       pos["entry"],
                "lot":         sum(p["lot"] for p in mt5_positions),
            }
            state_manager.set_field(bot_state, "open_trade", existing_trade)
            print(f"[notifier] 🔄 Restored open_trade from MT5: {pos['direction']} ticket={pos['ticket']}")

        if existing_trade:
            ex_dir = existing_trade.get("direction", "?")
            ex_tid = existing_trade.get("trade_id", "?")
            new_dir = result.get("analysis", {}).get("signal", "")

            if new_dir == ex_dir:
                # inject lot + session ก่อนทุก path
                _sig = result.get("analysis", {})
                _sig["lot"]      = result.get("lot")
                _sig["risk_pct"] = result.get("risk_pct")
                _sig["session"]  = get_session().get("session")
                result["analysis"] = _sig

                # ราคาดีกว่าไม้แรก = auto-execute ทันที ไม่รอ approve
                # (ราคาไม่ดี = _execute_pyramid_auto จะ skip + แจ้ง Telegram เอง)
                confidence = int(_sig.get("confidence") or 0)
                print(f"[notifier] 🔺 Auto-pyramid — confidence={confidence}% (no approval needed)")
                await _execute_pyramid_auto(result, existing_trade, send_fn)
                return
            else:
                await _safe_send(
                    send_fn,
                    f"🔒 *มีไม้ {ex_dir} ค้างอยู่ — ห้ามเปิด {new_dir} สวน*\n"
                    f"Trade #{ex_tid} ยังเปิดอยู่\n"
                    f"_ปิดก่อนด้วย `/closetrade` แล้วค่อย scan ใหม่_",
                    parse_mode="Markdown"
                )
                return
        signal = result.get("analysis", {})
        signal["lot"]      = result.get("lot")
        signal["risk_pct"] = result.get("risk_pct")
        sess = get_session()
        signal["session"] = sess.get("session")

        trade_id = log_trade(signal, "confirmed")
        state_manager.set_field(bot_state, "last_scan", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # ตั้ง open_trade สำหรับ trailing monitor
        entry_raw = signal.get("entry_zone") or signal.get("entry")
        entry_price = (
            (entry_raw[0] + entry_raw[1]) / 2 if isinstance(entry_raw, list)
            else float(entry_raw) if entry_raw else None
        )
        sl_price = signal.get("stop_loss") or signal.get("sl")
        tp_price = signal.get("take_profit") or signal.get("tp")
        direction = signal.get("signal")

        if entry_price and sl_price and direction in ("BUY", "SELL"):
            # pyramid mode: ถ้า TREND_BOS_BREAK → ใช้ lot1 เล็ก + รอ lot2 ที่ OB
            is_pyramid = signal.get("pyramid_mode", False) or signal.get("setup_type") == "TREND_BOS_BREAK"
            pyramid_lot2 = None
            if is_pyramid:
                sl_pips = abs(entry_price - float(sl_price)) * 10
                plots = risk_manager.pyramid_bos_lots(BALANCE, sl_pips)
                pyramid_lot2 = plots["lot2"]

            # หา OB zone สำหรับรอ pyramid ไม้ 2
            pyramid_ob = None
            if is_pyramid:
                _, _smc = chart_analyst.get_price_data()
                if _smc:
                    ob_key = "active_bull_ob" if direction == "BUY" else "active_bear_ob"
                    pyramid_ob = _smc.get(ob_key)

            open_trade = {
                "entry":            entry_price,
                "original_sl":      float(sl_price),
                "current_sl":       float(sl_price),
                "tp":               float(tp_price) if tp_price else None,
                "direction":        direction,
                "lot":              signal.get("lot"),
                "trade_id":         trade_id,
                "peak_price":       entry_price,
                "opened_at":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "reentry_analyzed": False,
                "pyramid_waiting":  is_pyramid,
                "pyramid_lot2":     pyramid_lot2,
                "pyramid_ob":       pyramid_ob,
                "pyramid_alerted":  False,
            }
            state_manager.set_field(bot_state, "open_trade", open_trade)

        message = supervisor.format_alert(result)

        # ── MT5 Auto-Execute ────────────────────────────────
        mt5_tag = ""
        current_mkt = result.get("current_price") or 0

        # ตรวจว่า entry zone valid สำหรับ market order
        # ใช้ OB top/bottom (ไม่ใช่ midpoint) เปรียบกับราคาตลาด
        # BUY: ราคาต้องไม่สูงกว่า OB_top เกิน $25 (มิฉะนั้น = ยังไม่ pullback ถึง OB)
        # SELL: ราคาต้องไม่ต่ำกว่า OB_bottom เกิน $25
        entry_far = False
        entry_far_msg = ""
        if entry_price and current_mkt:
            entry_raw2 = signal.get("entry_zone")
            ob_top    = entry_raw2[1] if isinstance(entry_raw2, list) else entry_price
            ob_bottom = entry_raw2[0] if isinstance(entry_raw2, list) else entry_price
            setup_type_now = signal.get("setup_type", "")

            # BULL/BEAR_OB_ENTRY = รอ pullback ถึง OB จริงๆ → threshold แคบ (5 pts)
            # setup อื่น เช่น SWEEP_REJECT = เข้าได้ทันที → threshold กว้าง (25 pts)
            FAR_THRESHOLD = 5 if "OB_ENTRY" in setup_type_now else 25

            if direction == "BUY" and (current_mkt - ob_top) > FAR_THRESHOLD:
                dist_usd = round(current_mkt - ob_top, 2)
                entry_far = True
                entry_far_msg = (
                    f"⏳ *รอ Pullback*\n"
                    f"ราคาตลาด `{current_mkt}` สูงกว่า OB top `{ob_top}` อยู่ `${dist_usd}`\n"
                    f"_ตั้ง BUY LIMIT ที่ OB zone `{ob_bottom}–{ob_top}` หรือรอราคาลงมาถึงก่อน_"
                )
            elif direction == "SELL" and (ob_bottom - current_mkt) > FAR_THRESHOLD:
                dist_usd = round(ob_bottom - current_mkt, 2)
                entry_far = True
                entry_far_msg = (
                    f"⏳ *รอ Rally*\n"
                    f"ราคาตลาด `{current_mkt}` ต่ำกว่า OB bottom `{ob_bottom}` อยู่ `${dist_usd}`\n"
                    f"_ตั้ง SELL LIMIT ที่ OB zone `{ob_bottom}–{ob_top}` หรือรอราคาขึ้นมาถึงก่อน_"
                )

        if mt5_executor.is_available() and entry_price and sl_price and direction in ("BUY","SELL") and not entry_far:
            lot_val = signal.get("lot") or 0.01
            ex = mt5_executor.open_trade(
                direction = direction,
                lot       = float(lot_val),
                sl        = float(sl_price),
                tp        = 0.0,   # ไม่ตั้ง TP — ให้ EA POS Guard จัดการ exit
                comment   = f"SAT-{trade_id}",
            )
            if "ticket" in ex:
                mt5_tag = (
                    f"\n\n✅ *MT5 Executed!*\n"
                    f"Ticket: `{ex['ticket']}` | Price: `{ex['price']}` | Lot: `{ex['volume']}`\n"
                    f"_EA POS Guard จัดการ exit — ไม่มี fixed TP_"
                )
                # เก็บ ticket ใน open_trade สำหรับ auto-close ในอนาคต
                if bot_state.get("open_trade"):
                    ot_upd = bot_state["open_trade"]
                    ot_upd["mt5_ticket"] = ex["ticket"]
                    state_manager.set_field(bot_state, "open_trade", ot_upd)
                # บันทึก ticket ใน DB ด้วย
                try:
                    from agents.trade_log import update_mt5_ticket
                    update_mt5_ticket(trade_id, ex["ticket"])
                except Exception:
                    pass
            else:
                mt5_tag = f"\n\n⚠️ *MT5 Error:* `{ex.get('error','unknown')}`\n→ กรุณาเปิด trade เองใน MT5"
        elif entry_far:
            mt5_tag = f"\n\n{entry_far_msg}"
        elif not mt5_executor.is_available():
            mt5_tag = "\n\n📋 _MT5_ENABLED=false — กรุณาเปิด trade เองใน MT5_"

        message += mt5_tag
        message += f"\n🤖 *Trade #{trade_id}* | `/closetrade` เมื่อปิด position"
        await _safe_send(send_fn, message, parse_mode="Markdown")
    else:
        message = supervisor.format_alert(result)
        await _safe_send(send_fn, message, parse_mode="Markdown")


# ── Callback (ปุ่ม Confirm/Skip) ──────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data

    # ── txreport period switch ─────────────────────────────
    if action.startswith("txr:"):
        from agents.trade_log import get_transactions_by_period
        period = action.split(":", 1)[1]
        data   = get_transactions_by_period(period)
        text, kb = _fmt_txreport(data)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            plain = text.replace("*", "").replace("_", "").replace("`", "").replace("\\", "")
            await query.edit_message_text(plain, reply_markup=kb)
        return

    # ── Pyramid ไม้ 2 callback ────────────────────────────
    if action in ("pyramid_confirm", "pyramid_skip"):
        p = bot_state.get("pending_pyramid")
        if not p:
            await query.edit_message_text("⚠️ ไม่มี pyramid signal")
            return
        bot_state.pop("pending_pyramid", None)
        state_manager.save(bot_state)

        if action == "pyramid_confirm":
            direction = p["direction"]
            lot2      = p["lot"]
            ob        = p["ob_zone"] or {}
            orig_tid  = p["original_trade_id"]
            cur       = p["current_price"]

            # MT5 execute ไม้ 2
            mt5_tag = ""
            if mt5_executor.is_available():
                sl_price = ob.get("bottom", cur - 5) if direction == "BUY" else ob.get("top", cur + 5)
                ot = bot_state.get("open_trade", {})
                tp_price = ot.get("tp", 0) or 0
                ex = mt5_executor.open_trade(
                    direction=direction, lot=float(lot2),
                    sl=float(sl_price), tp=float(tp_price),
                    comment=f"SAT-{orig_tid}-P2",
                )
                mt5_tag = (
                    f"\n✅ MT5: Ticket `{ex['ticket']}` @ `{ex['price']}`"
                    if "ticket" in ex
                    else f"\n⚠️ MT5 Error: `{ex.get('error','?')}`"
                )

            await query.edit_message_text(
                f"🔺 *Pyramid ไม้ 2 เปิดแล้ว!*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{direction} | Lot: `{lot2}` | ราคา: `{cur}`\n"
                f"OB Zone: `{ob.get('bottom','?')}–{ob.get('top','?')}`\n"
                f"_รวมกับไม้ 1 แล้ว average entry ดีขึ้น_"
                + mt5_tag,
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                f"⏭ ข้าม pyramid ไม้ 2\n_ไม้ 1 ยังเปิดอยู่ตามปกติ_",
                parse_mode="Markdown",
            )
        return

    signal = bot_state.get("pending_signal")

    if not signal:
        await query.edit_message_text("⚠️ ไม่มี signal ที่รอ confirm")
        return

    # เพิ่ม session ก่อนบันทึก
    from agents.smc_engine import get_session
    sess = get_session()
    signal["session"] = sess.get("session")

    trade_action = "confirmed" if action == "confirm" else "skipped"
    trade_id = log_trade(signal, trade_action)

    # clear pending และ save state ทันที
    state_manager.clear_pending(bot_state)

    entry_raw = signal.get("entry_zone") or signal.get("entry")
    if action == "confirm":
        # ตั้ง open_trade เพื่อ monitor trailing + reentry
        entry_price = (
            (entry_raw[0] + entry_raw[1]) / 2 if isinstance(entry_raw, list)
            else float(entry_raw) if entry_raw else None
        )
        tp_price = signal.get("take_profit") or signal.get("tp")
        direction = signal.get("signal")
        # ถ้ามี open trade ค้างอยู่ (pyramid) → ใช้ SL ของไม้แรก ไม่ใช่ SL จาก signal ใหม่
        _existing = bot_state.get("open_trade") or {}
        sl_price = (
            _existing.get("original_sl")
            or _existing.get("current_sl")
            or signal.get("stop_loss")
            or signal.get("sl")
        )
        mt5_tag = ""
        if entry_price and sl_price and direction in ("BUY", "SELL"):
            open_trade = {
                "entry":           entry_price,
                "original_sl":     float(sl_price),
                "current_sl":      float(sl_price),
                "tp":              float(tp_price) if tp_price else None,
                "direction":       direction,
                "lot":             signal.get("lot"),
                "trade_id":        trade_id,
                "peak_price":      entry_price,
                "opened_at":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "reentry_analyzed": False,
            }
            state_manager.set_field(bot_state, "open_trade", open_trade)

            # ── MT5 Execute ──────────────────────────────────────
            if mt5_executor.is_available():
                lot_val = float(signal.get("lot") or 0.01)
                ex = mt5_executor.open_trade(
                    direction=direction,
                    lot=lot_val,
                    sl=float(sl_price),
                    tp=0.0,
                    comment=f"SAT-{trade_id}",
                )
                if "ticket" in ex:
                    mt5_tag = f"\n✅ *MT5:* Ticket `{ex['ticket']}` @ `{ex['price']}`"
                    ot_upd = bot_state.get("open_trade", {})
                    ot_upd["mt5_ticket"] = ex["ticket"]
                    state_manager.set_field(bot_state, "open_trade", ot_upd)
                else:
                    mt5_tag = f"\n⚠️ *MT5 Error:* `{ex.get('error','unknown')}`\n→ กรุณาเปิด trade เองใน MT5"
            else:
                mt5_tag = "\n\n📋 _MT5_ENABLED=false — กรุณาเปิด trade เองใน MT5_"

        await query.edit_message_text(
            f"✅ *Confirmed — Trade #{trade_id}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Signal: *{signal.get('signal')}*  {signal.get('reversal_stars') or ''}\n"
            f"Entry: `{entry_raw}`\n"
            f"SL: `{signal.get('stop_loss') or signal.get('sl')}`  "
            f"TP: `{signal.get('take_profit') or signal.get('tp')}`\n"
            f"Lot: `{signal.get('lot', '-')}`"
            + mt5_tag +
            f"\n\n📡 Trailing monitor เริ่มทำงานแล้ว (trail 1000p)\n"
            f"🍀 โชคดี! หลังเทรดเสร็จ:\n"
            f"`/outcome {trade_id} win 150 3310`\n"
            f"`/closetrade {entry_price}` — ปิด monitor",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            f"⏭ *Skipped — Trade #{trade_id}*\n"
            f"บันทึกว่า Skip {signal.get('signal')} setup แล้ว",
            parse_mode="Markdown"
        )

async def cmd_mt5(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /mt5 — ดูสถานะ MT5 connection + account info + open positions
    """
    if not mt5_executor.is_available():
        await update.message.reply_text(
            "⚠️ *MT5 ยังไม่ได้เปิดใช้งาน*\n"
            "ตั้งค่าใน `.env`:\n"
            "`MT5_ENABLED=true`\n"
            "`MT5_LOGIN=...`\n"
            "`MT5_PASSWORD=...`\n"
            "`MT5_SERVER=...`",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text("🔌 กำลังเชื่อม MT5...")

    # Account info
    info = mt5_executor.get_account_info()
    if "error" in info:
        await update.message.reply_text(f"❌ เชื่อม MT5 ไม่ได้: `{info['error']}`", parse_mode="Markdown")
        return

    # Open positions
    positions = mt5_executor.get_open_positions()
    pos_lines = ""
    if positions:
        pos_lines = "\n\n*Open Positions:*\n"
        for p in positions:
            icon = "🟢" if p["direction"] == "BUY" else "🔴"
            pnl_icon = "+" if p["profit"] >= 0 else ""
            pos_lines += (
                f"{icon} #{p['ticket']} {p['direction']} {p['lot']}L\n"
                f"   Entry: `{p['entry']}` SL: `{p['sl']}` TP: `{p['tp']}`\n"
                f"   P&L: `{pnl_icon}{p['profit']:.2f}` USD\n"
            )
    else:
        pos_lines = "\n\n_ไม่มี open positions_"

    await update.message.reply_text(
        f"🔌 *MT5 Connected*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Account: `{info['login']}` | {info['server']}\n"
        f"Balance: `{info['balance']:.2f}` {info['currency']}\n"
        f"Equity:  `{info['equity']:.2f}` {info['currency']}\n"
        f"Free Margin: `{info['free_margin']:.2f}`\n"
        f"Leverage: 1:{info['leverage']}"
        + pos_lines,
        parse_mode="Markdown"
    )


async def cmd_posguard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /posguard — ดูสถานะ POS Guard config + รัน 1 รอบทันที
    /posguard check — force check ทันที
    """
    args = ctx.args or []
    cfg_msg = pos_guard.describe()

    if not pos_guard.POSGUARD_ENABLED:
        await update.message.reply_text(
            f"{cfg_msg}\n\n"
            f"เปิดใช้ใน `.env`:\n"
            f"`POSGUARD_ENABLED=true`\n"
            f"`POSGUARD_TRIGGER_USD=5.0`   # profit $ ก่อน lock\n"
            f"`POSGUARD_LOCK_TICKS=10.0`   # ticks เหนือ open\n"
            f"`POSGUARD_STEP_TICKS=50.0`   # trail step (0 = lock only)`,",
            parse_mode="Markdown"
        )
        return

    if args and args[0] == "check":
        await update.message.reply_text("🛡 กำลังเช็ค POS Guard...")
        actions = await pos_guard.check_once()
        if not actions:
            await update.message.reply_text(
                f"{cfg_msg}\n\n✅ ไม่มีไม้ที่ต้องเลื่อน SL ตอนนี้",
                parse_mode="Markdown"
            )
        else:
            lines = [f"{cfg_msg}\n\n*ผลการเช็ค {len(actions)} ไม้:*"]
            for a in actions:
                icon = "✅" if a.get("ok") else "❌"
                lines.append(
                    f"{icon} #{a['ticket']} | SL: `{a['old_sl']}` → `{a['new_sl']}` "
                    f"| P&L: `${a['profit']:.2f}`"
                )
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        await update.message.reply_text(
            f"{cfg_msg}\n\n"
            f"ใช้ `/posguard check` เพื่อ force-check ทันที\n"
            f"Guard รันอัตโนมัติทุก `{pos_guard.POSGUARD_CHECK_INTERVAL}s`",
            parse_mode="Markdown"
        )


async def _pos_guard_job(ctx: ContextTypes.DEFAULT_TYPE):
    """PTB repeating job — เรียก POS Guard ทุก POSGUARD_CHECK_INTERVAL วินาที"""
    await pos_guard.check_once()


async def cmd_testscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /testscan — รัน pipeline ครบทุก agent แต่ไม่เปิด trade
    แสดงผล vote ของแต่ละ agent + reasoning เพื่อ debug
    """
    await update.message.reply_text("🔬 *Test Scan* — รัน pipeline (ไม่เปิด trade จริง)...", parse_mode="Markdown")

    result = supervisor.run()
    _push_to_dashboard(result)

    stages  = result.get("stages", {})
    votes   = result.get("votes", {})
    details = result.get("vote_details", {})

    # ── Stage results ────────────────────────────────────────
    smc_stage = stages.get("smc", "?")
    chart_s   = stages.get("chart", {})
    bias_s    = stages.get("bias",  {})
    news_s    = stages.get("news",  {})
    sup_s     = stages.get("supervisor", {})
    risk_s    = stages.get("risk", {})

    # votes dict ว่างถ้า chart reject early — fallback ดึงจาก stages โดยตรง
    chart_vote = votes.get("chart") if votes else (chart_s.get("vote") == "YES" if chart_s else None)
    bias_vote  = votes.get("bias")
    news_vote  = votes.get("news")
    vote_score = result.get("vote_score", 0)

    def vi(v):  # vote icon
        if v is True:  return "✅ YES"
        if v is False: return "❌ NO"
        return "⬜ —"

    # ── Format ───────────────────────────────────────────────
    price = result.get("current_price", "?")

    def _fmt_ob_zone(ob: dict | None, label: str) -> str:
        if not ob or not ob.get("top"):
            return f"{label}: ไม่มี"
        top = ob["top"]; bot = ob["bottom"]; tf = ob.get("tf", "")
        in_ob = " ← IN OB ✅" if ob.get("in_ob") else ""
        return f"{label} [{tf}]: `{bot} – {top}`{in_ob}"

    bull_ob_line = _fmt_ob_zone(chart_s.get("bull_ob_zone"), "🟢 Bull OB")
    bear_ob_line = _fmt_ob_zone(chart_s.get("bear_ob_zone"), "🔴 Bear OB")
    ob_block = f"{bull_ob_line}\n{bear_ob_line}\n"

    if smc_stage == "NO_SIGNAL":
        msg = (
            f"🔬 *Test Scan Result*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💰 ราคา: `{price}`\n"
            f"{ob_block}"
            f"⬜ SMC Engine: ไม่มี setup\n"
            f"→ ไม่เรียก Claude เลย (ประหยัด cost)\n"
        )
    else:
        # Chart
        chart_conf = chart_s.get("confidence", "?")
        chart_sig  = chart_s.get("signal", "?")
        chart_r    = details.get("chart", chart_s.get("reasoning", ""))[:200]

        # Bias
        chart_rejected = (chart_vote is False or chart_s.get("signal") == "NO_TRADE")
        bias_td    = bias_s.get("trade_direction", "ไม่ได้รัน" if chart_rejected else "?")
        bias_r     = details.get("bias", "ข้ามเพราะ Chart rejected" if chart_rejected else "")[:150]
        bias_cache = "📦 cached" if bias_s.get("from_cache") else ""

        # News
        news_risk  = news_s.get("risk_level", "ไม่ได้รัน" if chart_rejected else "?")
        news_key   = news_s.get("key_event") or ("—" if chart_rejected else "ไม่มีข่าว")
        news_r     = details.get("news", "ข้ามเพราะ Chart rejected" if chart_rejected else "")[:150]

        # Supervisor
        sup_conf   = sup_s.get("confidence", "?") if sup_s else "—"
        sup_r      = sup_s.get("reasoning", "—")[:200] if sup_s else "—"

        # Reject reason
        reject     = result.get("reject_reason", "")

        # Final
        final_icon = "🟢 APPROVED" if result.get("approved") else "🔴 REJECTED"
        final_sig  = result.get("final_signal", "NO_TRADE")

        msg = (
            f"🔬 *Test Scan — {final_icon}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💰 ราคา: `{price}`\n"
            f"{ob_block}\n"
            f"*[1] SMC Engine:* ✅ พบ setup\n"
            f"*[2] Chart Analyst:* {vi(chart_vote)}\n"
            f"   Signal: `{chart_sig}` Conf: `{chart_conf}%`\n"
            f"   _{_md(chart_r)}_\n\n"
            f"*[3] Bias Analyst:* {vi(bias_vote)} {bias_cache}\n"
            f"   Direction: `{bias_td}`\n"
            f"   _{_md(bias_r)}_\n\n"
            f"*[4] News Scout:* {vi(news_vote)}\n"
            f"   Risk: `{news_risk}` | Key: `{_md(str(news_key))}`\n"
            f"   _{_md(news_r)}_\n\n"
            f"*[5] Vote:* `{vote_score}/3`\n"
        )

        if risk_s:
            veto = "⛔ VETO" if risk_s.get("veto") else "✅ OK"
            msg += f"*[6] Risk Manager:* {veto} | Lot: `{risk_s.get('lot')}` ({risk_s.get('risk_pct')}%)\n"

        if sup_s:
            msg += (
                f"*[7] Supervisor:* Conf: `{sup_conf}%`\n"
                f"   _{_md(sup_r)}_\n\n"
            )

        if reject:
            msg += f"⛔ Reject: _{_md(reject[:250])}_\n"
        elif result.get("approved"):
            msg += (
                f"\n✅ *ผ่านทุก stage — จะเปิด {final_sig}*\n"
                f"SL: `{result.get('stop_loss')}` | TP: `{result.get('take_profit')}`\n"
                f"_(testscan ไม่เปิด trade จริง)_"
            )

    await update.message.reply_text(msg, parse_mode="Markdown")


# ── Trade monitor constants ────────────────────────────
TRAIL_PIPS   = 1000   # trailing distance (pips)
REENTRY_MIN_PROFIT = 200   # ต้องเคย profit อย่างน้อย 200p ถึงจะ check reentry
REENTRY_NEAR_ENTRY = 50    # ราคาอยู่ห่างจาก entry ≤ 50p ถือว่า "กลับมาแล้ว"


async def _refresh_loss_digest():
    """เรียก Sonnet สรุป pattern จาก loss ล่าสุด 10 เทรด → save digest cache."""
    try:
        import anthropic
        from config.settings import CLAUDE_API_KEY
        from agents.trade_log import get_raw_losses_for_digest, save_loss_digest

        losses = get_raw_losses_for_digest(limit=10)
        if not losses:
            return

        lines = []
        for t in losses:
            pips = f"{t['pnl_pips']:+.1f}p" if t["pnl_pips"] else "?"
            why  = f" | Why: {t['notes']}" if t.get("notes") else ""
            lines.append(
                f"#{t['id']} {t['signal']} {t['setup_type'] or ''} "
                f"conf={t['confidence']}% sess={t['session'] or '?'} "
                f"→ {pips}{why}"
            )

        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    f"Loss trades ล่าสุดของ XAUUSD bot:\n" + "\n".join(lines) + "\n\n"
                    "สรุปเป็น 3 bullet points (ภาษาไทย) ว่า Supervisor ควรระวังอะไร "
                    "เพื่อหลีกเลี่ยง loss pattern แบบนี้ในครั้งต่อไป "
                    "ให้กระชับ actionable เช่น setup ไหนควร reject, session ไหนอันตราย, "
                    "confidence threshold ที่ควรยก ขึ้นต้นแต่ละ bullet ด้วย •"
                )
            }]
        )
        digest = "⚠️ *บทเรียนจาก loss ล่าสุด — Supervisor ระวัง:*\n" + resp.content[0].text.strip()
        save_loss_digest(digest)
        print(f"[loss_digest] refreshed — {len(losses)} trades analyzed")
    except Exception as e:
        print(f"[loss_digest] refresh error: {e}")


async def _analyze_loss_reason(ot: dict, entry: float, exit_price: float,
                               pnl_pips: float, duration_min) -> str | None:
    """Call Haiku to analyze why this trade lost — returns 1-sentence reason."""
    try:
        import anthropic, sqlite3
        from config.settings import CLAUDE_API_KEY
        from agents.trade_log import DB_PATH, init_db

        direction  = ot.get("direction", "?")
        dur_str    = f"{duration_min} นาที" if duration_min else "ไม่ทราบ"

        # pull extra context from DB
        setup_type = session = sl = tp = reasoning = confidence = None
        trade_id = ot.get("trade_id")
        if trade_id:
            init_db()
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT setup_type, session, sl, tp, reasoning, confidence "
                "FROM trades WHERE id=?", (int(trade_id),)
            ).fetchone()
            conn.close()
            if row:
                setup_type, session, sl, tp, reasoning, confidence = row

        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        prompt = (
            f"Trade นี้ขาดทุน {abs(pnl_pips):.1f} pips\n"
            f"Direction: {direction} | Setup: {setup_type or '?'} | Session: {session or '?'}\n"
            f"Entry: {entry} | Exit: {exit_price} | SL: {sl or '?'} | TP: {tp or '?'}\n"
            f"Confidence เดิม: {confidence or '?'}% | Duration: {dur_str}\n"
            f"Reasoning เดิม: {(reasoning or '')[:300] or 'ไม่มี'}\n\n"
            "วิเคราะห์ใน 1 ประโยค (ภาษาไทย) ว่าทำไมเทรดนี้ถึงขาดทุน "
            "เช่น entry ผิดจุด, bias ผิด, ข่าว, SL ใกล้เกินไป, หรือ market structure เปลี่ยน"
        )

        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        reason = resp.content[0].text.strip()
        print(f"[loss_analysis] trade#{trade_id}: {reason}")
        return reason
    except Exception as e:
        print(f"[loss_analysis] error: {e}")
        return None


async def cmd_closetrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /closetrade [exit_price] — ปิด MT5 position + monitor
    ตัวอย่าง: /closetrade 4350
    """
    from agents.json_utils import fmt_pts

    ot = bot_state.get("open_trade")
    if not ot:
        await update.message.reply_text("ℹ️ ไม่มี trade ที่กำลัง monitor อยู่")
        return

    exit_price_arg = None
    if ctx.args:
        try:
            exit_price_arg = float(ctx.args[0])
        except ValueError:
            pass

    direction  = ot.get("direction")
    entry      = ot.get("entry", 0)
    peak       = ot.get("peak_price", entry)
    trade_id   = ot.get("trade_id")
    ticket     = ot.get("mt5_ticket")

    # ── ปิด MT5 position จริง ──────────────────────────────────────
    mt5_tag = ""
    actual_exit = exit_price_arg
    if mt5_executor.is_available() and ticket:
        ex = mt5_executor.close_trade(int(ticket))
        if "close_price" in ex:
            actual_exit = ex["close_price"]
            mt5_tag = f"\n✅ *MT5 ปิดแล้ว* — Ticket `{ticket}` @ `{actual_exit}`"
        else:
            mt5_tag = f"\n⚠️ MT5 ปิดไม่สำเร็จ: `{ex.get('error','?')}`\n_ปิดเองใน MT5 ด้วย_"
    elif mt5_executor.is_available() and not ticket:
        # ไม่มี ticket — ลอง close ทุก position
        positions = mt5_executor.get_open_positions()
        for p in positions:
            mt5_executor.close_trade(p["ticket"])
        mt5_tag = f"\n✅ ปิดทุก MT5 position ({len(positions)} ไม้)"
        if positions:
            actual_exit = positions[0].get("entry")

    # ── คำนวณ P&L ──────────────────────────────────────────────────
    pnl_str = ""
    outcome = None
    if actual_exit and entry:
        pnl_raw = (actual_exit - entry) if direction == "BUY" else (entry - actual_exit)
        pnl_pips = round(pnl_raw * 10, 1)
        outcome = "win" if pnl_pips > 0 else "loss" if pnl_pips < 0 else "be"
        icon = "✅" if outcome == "win" else "❌" if outcome == "loss" else "➖"
        pnl_str = f"\n{icon} *P&L: `{fmt_pts(pnl_pips, sign=True)} จุด`* (`{pnl_pips:+.1f} pips`)"

        # auto-update outcome ใน DB
        if trade_id:
            from agents.trade_log import update_outcome
            from datetime import datetime
            opened_at = ot.get("opened_at")
            duration = None
            if opened_at:
                try:
                    dt_open = datetime.strptime(opened_at, "%Y-%m-%d %H:%M:%S")
                    duration = int((datetime.now() - dt_open).total_seconds() / 60)
                except Exception:
                    pass

            loss_notes = None
            if outcome == "loss":
                loss_notes = await _analyze_loss_reason(ot, entry, actual_exit, pnl_pips, duration)

            update_outcome(int(trade_id), outcome, pnl_pips,
                           actual_entry=entry, actual_exit=actual_exit,
                           duration_min=duration, notes=loss_notes)

            if outcome == "loss":
                await _refresh_loss_digest()

    state_manager.set_field(bot_state, "open_trade", None)

    await update.message.reply_text(
        f"🔴 *Trade ปิดแล้ว — #{trade_id}*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{direction} | Entry: `{entry}` → Exit: `{actual_exit or '?'}`\n"
        f"Peak ที่เคยถึง: `{peak}`"
        + pnl_str + mt5_tag,
        parse_mode="Markdown"
    )

    # ── Re-scan ทันทีหลังปิดไม้ด้วยมือ ──────────────────────
    if bot_state.get("is_running"):
        await _rescan_after_close(ctx)


async def _rescan_after_close(ctx: ContextTypes.DEFAULT_TYPE):
    """Re-scan ทันทีหลังไม้ปิด — หา setup ใหม่ทันที ไม่ต้องรอ window ถัดไป"""
    try:
        # ไม่ scan ถ้ามีข่าว High Impact
        blocked, block_reason = news_scout.should_block_trade()
        if blocked:
            print(f"[rescan] 📰 News block — {block_reason}")
            await ctx.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"📰 *Skip re-scan — ข่าว High Impact*\n_{block_reason}_",
                parse_mode="Markdown",
            )
            return

        await ctx.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="🔄 *Re-scan หลังไม้ปิด* — กำลังหา setup ใหม่...",
            parse_mode="Markdown",
        )
        result = await asyncio.get_event_loop().run_in_executor(None, supervisor.run)
        log_scan(result)
        _push_to_dashboard(result)

        async def send(text, **kw):
            await ctx.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, **kw)

        await _handle_scan_result(result, send)
    except Exception as e:
        print(f"[rescan] error: {e}")


def _capture_one_position(pos: dict) -> dict | None:
    """เก็บ transaction ของ position 1 ตัว — คืน dict newly หรือ None ถ้าข้าม"""
    from agents.trade_log import (
        transaction_exists, find_trade_by_ticket,
        log_mt5_transaction, update_outcome,
    )
    from datetime import datetime

    ticket = pos["position_id"]
    if transaction_exists(ticket):
        return None
    direction = pos["direction"]
    open_px   = pos["open_price"]
    close_px  = pos["close_price"]

    trade = find_trade_by_ticket(ticket)
    trade_id = trade["id"] if trade else None

    # fallback: ถ้า MT5 open deal หาย → ใช้ราคา/ทิศจาก trades table
    if trade:
        if open_px is None:
            open_px = trade.get("actual_entry") or trade.get("entry_low")
        if direction is None:
            direction = trade.get("signal")
    direction = direction or "BUY"

    if open_px is None or close_px is None:
        print(f"[mt5_sync] skip ticket={ticket} — open_px/close_px หาย (open={open_px} close={close_px})")
        return None
    pnl_raw  = (close_px - open_px) if direction == "BUY" else (open_px - close_px)
    pnl_pips = round(pnl_raw * 10, 1)
    outcome  = "win" if pnl_pips > 0 else "loss" if pnl_pips < 0 else "be"
    if trade and trade.get("outcome") is None:
        dur = None
        if pos["open_time"] and pos["close_time"]:
            dur = int((pos["close_time"] - pos["open_time"]) / 60)
        update_outcome(trade_id, outcome, pnl_pips,
                       actual_entry=open_px, actual_exit=close_px,
                       duration_min=dur)

    deal = {
        "close_price": close_px, "ticket": ticket,
        "profit": pos["profit"], "commission": pos["commission"],
        "swap": pos["swap"], "time": pos["close_time"],
    }
    ot = {
        "entry": open_px, "lot": pos["volume"], "mt5_ticket": ticket,
        "pair": pos["symbol"],
        "opened_at": datetime.fromtimestamp(pos["open_time"]).strftime("%Y-%m-%d %H:%M:%S") if pos["open_time"] else None,
    }
    net = log_mt5_transaction(trade_id or 0, direction, deal, ot)
    return {"ticket": ticket, "dir": direction, "trade_id": trade_id,
            "pips": pnl_pips, "outcome": outcome, "net": net}


def _run_mt5_sync(hours: int = 24) -> dict:
    """Core MT5 sync — 2 pass:
      1. date-range: ดึง closed positions ทั้งหมดใน X ชม.
      2. direct: ไม้ที่บอทเปิด (มี ticket) แต่ยังไม่ captured → query position filter ตรง
         (กัน MT5 date-range query ไม่ครบ deal ล่าสุด)
    """
    from agents.trade_log import transaction_exists, get_uncaptured_tickets

    newly = []
    # ── Pass 1: date-range ────────────────────────────────────
    closed = mt5_executor.get_closed_positions(hours)
    found_tickets = [p["position_id"] for p in closed]
    for pos in closed:
        n = _capture_one_position(pos)
        if n:
            newly.append(n)

    # ── Pass 2: direct ticket query (ไม้ที่ date-range พลาด) ───
    direct_found = []
    for ticket in get_uncaptured_tickets():
        if transaction_exists(ticket):
            continue
        pos = mt5_executor.get_position_deals(int(ticket))
        if pos:
            direct_found.append(ticket)
            n = _capture_one_position(pos)
            if n:
                newly.append(n)

    return {"newly": newly, "total_closed": len(closed),
            "found_tickets": found_tickets, "direct_found": direct_found}


def _fmt_sync_result(newly: list) -> str:
    lines = ["📥 *MT5 Sync — เก็บ transaction ใหม่*", "━━━━━━━━━━━━━━━━━"]
    for n in newly:
        icon = "✅" if n["outcome"] == "win" else "❌" if n["outcome"] == "loss" else "➖"
        tid = f"#{n['trade_id']}" if n["trade_id"] else f"T#{n['ticket']}"
        net_s = f" | net `${n['net']:+.2f}`" if n["net"] is not None else ""
        lines.append(f"{icon} {tid} {n['dir']} `{n['pips']:+.1f}p`{net_s}")
    return "\n".join(lines)


async def mt5_sync_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Background sync ทุก 3 นาที — เก็บ transaction เงียบๆ (ไม่โพสต์ Telegram)"""
    if not mt5_executor.is_available():
        return
    try:
        res = await asyncio.get_event_loop().run_in_executor(None, _run_mt5_sync, 24)
        newly = res["newly"]
        if not newly:
            return
        # state cleanup ถ้าไม้ที่เปิดอยู่เพิ่งถูก sync ว่าปิดแล้ว
        ot_state = bot_state.get("open_trade")
        if ot_state:
            st_ticket = ot_state.get("mt5_ticket")
            if st_ticket and any(n["ticket"] == int(st_ticket) for n in newly):
                state_manager.set_field(bot_state, "open_trade", None)
        # ทำงานเงียบ — เก็บลง DB อย่างเดียว ไม่แจ้ง Telegram (กันสแปม)
        print(f"[mt5_sync] captured {len(newly)} new transactions (silent)")
    except Exception as e:
        print(f"[mt5_sync] error: {e}")


async def cmd_sync(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/sync — ดึง closed positions จาก MT5 มาเก็บ transaction ทันที (manual trigger)"""
    if not mt5_executor.is_available():
        await update.message.reply_text("⚠️ MT5 ไม่ได้เชื่อม")
        return
    await update.message.reply_text("🔄 กำลัง sync MT5 transactions...")
    try:
        res = await asyncio.get_event_loop().run_in_executor(None, _run_mt5_sync, 48)
        newly = res["newly"]
        # debug: แสดง position_id ที่ MT5 ดึงมาได้ (เทียบกับ MT5 History)
        found = res.get("found_tickets", [])
        direct = res.get("direct_found", [])
        dbg = (f"\n\n🔍 date-range = {len(found)} ไม้ | direct query = {len(direct)} ไม้")
        if direct:
            dbg += f"\ndirect tickets: `{', '.join(str(t) for t in direct)}`"
        if not newly:
            await update.message.reply_text(
                f"✅ ไม่มี transaction ใหม่ (เก็บครบแล้ว {res['total_closed']} ไม้ใน 48 ชม.)"
                f"{dbg}",
                parse_mode="Markdown",
            )
            return
        await update.message.reply_text(_fmt_sync_result(newly) + dbg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Sync error: `{e}`", parse_mode="Markdown")


async def cmd_tx(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/tx — ดู MT5 transactions ล่าสุดที่เก็บใน DB"""
    from agents.trade_log import get_recent_transactions
    txs = get_recent_transactions(15)
    if not txs:
        await update.message.reply_text("ℹ️ ยังไม่มี transaction ใน DB — ลอง /sync")
        return
    lines = ["💼 *MT5 Transactions ล่าสุด*", "━━━━━━━━━━━━━━━━━"]
    for t in txs:
        icon = "✅" if (t["pnl_pips"] or 0) > 0 else "❌" if (t["pnl_pips"] or 0) < 0 else "➖"
        tid = f"#{t['trade_id']}" if t["trade_id"] else f"T#{t['ticket']}"
        ct  = (t["close_time"] or "")[5:16]
        net = f"${t['net_usd']:+.2f}" if t["net_usd"] is not None else "?"
        lines.append(f"{icon} {tid} {t['direction']} `{t['pnl_pips']:+.1f}p` net `{net}` _{ct}_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _fmt_txreport(data: dict) -> tuple[str, object]:
    """Format /txreport message + inline keyboard สำหรับ switch period"""
    txs   = data['txs']
    label = data['label']
    cnt   = data['count']
    wins  = data['wins']
    losses = data['losses']
    be_   = data['be']
    wr    = data['win_rate']
    total_net  = data['total_net_usd']
    total_pips = data['total_pips']
    total_gross = data['total_gross_usd']
    total_comm  = data['total_commission']
    total_swap  = data['total_swap']

    net_icon = "✅" if total_net > 0 else "❌" if total_net < 0 else "➖"
    lines = [
        f"💼 *MT5 Transactions — {_md(label)}*",
        "━━━━━━━━━━━━━━━━━",
        f"จำนวน: `{cnt}` ไม้  |  W`{wins}` / L`{losses}` / BE`{be_}`  |  WR `{wr}%`",
        f"Gross: `${total_gross:+.2f}`  Comm: `${total_comm:.2f}`  Swap: `${total_swap:.2f}`",
        f"Net P&L: {net_icon} `${total_net:+.2f}`  (`{total_pips:+.1f} pips`)",
        "━━━━━━━━━━━━━━━━━",
    ]

    if not txs:
        lines.append("_ไม่มี transaction ในช่วงนี้_")
    else:
        # แสดงสูงสุด 20 รายการ (Telegram 4096 char limit)
        shown = txs[-20:] if len(txs) > 20 else txs
        if len(txs) > 20:
            lines.append(f"_...แสดง 20 ล่าสุดจาก {len(txs)} ทั้งหมด_")
        for t in shown:
            icon  = "✅" if (t.get('pnl_pips') or 0) > 0 else "❌" if (t.get('pnl_pips') or 0) < 0 else "➖"
            tid   = f"#{t['trade_id']}" if t.get('trade_id') else f"T#{t.get('ticket','?')}"
            ct    = (t.get('close_time') or t.get('timestamp') or "")
            # แสดง dd/mm HH:MM (TH time บนเครื่อง bot)
            try:
                ct_dt = datetime.strptime(ct[:19], "%Y-%m-%d %H:%M:%S")
                ct_str = ct_dt.strftime("%d/%m %H:%M")
            except Exception:
                ct_str = ct[5:16] if ct else "?"
            pips_str = f"{t['pnl_pips']:+.1f}p" if t.get('pnl_pips') is not None else "?"
            net_str  = f"${t['net_usd']:+.2f}" if t.get('net_usd') is not None else "?"
            dr   = t.get('direction', '?')
            op   = t.get('open_price') or 0
            cp   = t.get('close_price') or 0
            lot  = t.get('lot') or 0
            lines.append(
                f"{icon} `{ct_str}` {tid} *{dr}* `{op:.2f}`→`{cp:.2f}` "
                f"`{lot:.2f}L` `{pips_str}` net`{net_str}`"
            )

    # Inline keyboard — switch period
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("วันนี้", callback_data="txr:today"),
        InlineKeyboardButton("7 วัน",  callback_data="txr:week"),
        InlineKeyboardButton("เดือน",  callback_data="txr:month"),
        InlineKeyboardButton("ปีนี้",   callback_data="txr:year"),
    ]])
    return "\n".join(lines), keyboard


async def cmd_txreport(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/txreport [today|week|month|year] — ดู MT5 transactions แยกช่วงเวลา พร้อมสรุป P&L"""
    from agents.trade_log import get_transactions_by_period
    args   = ctx.args
    period = args[0].lower() if args else 'today'
    data   = get_transactions_by_period(period)
    text, kb = _fmt_txreport(data)
    await _safe_send(
        lambda t, **kw: update.message.reply_text(t, **kw),
        text, parse_mode="Markdown", reply_markup=kb
    )


async def trade_monitor(ctx: ContextTypes.DEFAULT_TYPE):
    """
    ทำงานทุก 5 นาที — เช็ค trailing stop และ re-entry opportunity
    """
    ot = bot_state.get("open_trade")
    if not ot:
        return

    # ── Auto-detect MT5 close (ปิดจาก MT5 โดยตรง) ────────────────
    if mt5_executor.is_available():
        try:
            ticket = ot.get("mt5_ticket")
            positions = mt5_executor.get_open_positions()
            open_tickets = {p["ticket"] for p in positions}
            is_closed = ticket and int(ticket) not in open_tickets
            if not is_closed and not ticket and not positions:
                is_closed = True

            if is_closed:
                trade_id  = ot.get("trade_id")
                entry_px  = ot.get("entry", 0)
                direction = ot.get("direction", "BUY")
                pnl_pips  = None
                outcome   = None
                close_px  = None

                deal = None
                if ticket:
                    deal = mt5_executor.get_last_deal_for_ticket(int(ticket))
                # fallback: ticket lookup ล้มเหลว หรือไม่มี ticket → หา deal ล่าสุดของ XAUUSD
                if not deal:
                    deal = mt5_executor.get_latest_closed_deal(hours=4)
                    if deal and ticket:
                        print(f"[trade_monitor] ticket={ticket} lookup ล้มเหลว → ใช้ latest deal fallback")
                if deal:
                    close_px = deal["close_price"]
                    pnl_raw  = (close_px - entry_px) if direction == "BUY" else (entry_px - close_px)
                    pnl_pips = round(pnl_raw * 10, 1)
                    outcome  = "win" if pnl_pips > 0 else "loss" if pnl_pips < 0 else "be"

                net_usd = None
                if outcome and trade_id and str(trade_id).isdigit():
                    from agents.trade_log import update_outcome, log_mt5_transaction
                    update_outcome(int(trade_id), outcome, pnl_pips, actual_exit=close_px)
                    if deal:
                        try:
                            net_usd = log_mt5_transaction(int(trade_id), direction, deal, ot)
                        except Exception as e:
                            print(f"[trade_monitor] log_mt5_transaction error: {e}")

                icon = "✅" if outcome == "win" else "❌" if outcome == "loss" else "➖"
                net_line = f"\nNet (หลัง commission+swap): `${net_usd:+.2f}`" if net_usd is not None else ""
                state_manager.set_field(bot_state, "open_trade", None)
                await ctx.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=(
                        f"{icon} *MT5 ปิด Trade อัตโนมัติ — #{trade_id}*\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"{direction} | Entry: `{entry_px}` → Exit: `{close_px or '?'}`\n"
                        f"P&L: `{pnl_pips:+.1f} จุด`{net_line}\n"
                        f"บันทึก outcome: `{outcome or 'unknown'}` ใน DB แล้ว"
                    ) if outcome else (
                        f"⚠️ *Trade ปิดจาก MT5 แต่ดึง history ไม่ได้*\n"
                        f"Trade #{trade_id} — ใช้ `/outcome {trade_id} win/loss [จุด]` บันทึกเอง"
                    ),
                    parse_mode="Markdown",
                )

                # ── Re-scan ทันทีหลังไม้ปิด — หา setup ใหม่ ──────────
                if bot_state.get("is_running"):
                    await _rescan_after_close(ctx)

                return  # ไม่ต้อง monitor ต่อ
        except Exception as e:
            print(f"[trade_monitor] auto-close detect error: {e}")

    direction = ot.get("direction")
    entry     = ot.get("entry", 0)
    current_sl = ot.get("current_sl", 0)
    peak      = ot.get("peak_price", entry)
    trade_id  = ot.get("trade_id")

    # ดึงราคาปัจจุบัน
    try:
        _, smc_summary = chart_analyst.get_price_data()
        if not smc_summary:
            return
        current = smc_summary.get("current_price", 0)
        if not current:
            return
    except Exception:
        return

    is_buy  = direction == "BUY"

    # ── อัพเดต peak ─────────────────────────────────────
    new_peak = max(peak, current) if is_buy else min(peak, current)
    if new_peak != peak:
        ot["peak_price"] = new_peak
        state_manager.set_field(bot_state, "open_trade", ot)
        peak = new_peak

    # ── Trailing Stop Alert ──────────────────────────────
    # suggested SL = peak ± TRAIL_PIPS
    if is_buy:
        suggested_sl = round(peak - TRAIL_PIPS, 2)
        should_trail = suggested_sl > current_sl + 50
    else:
        suggested_sl = round(peak + TRAIL_PIPS, 2)
        should_trail = suggested_sl < current_sl - 50

    if should_trail:
        profit_locked = abs(suggested_sl - ot.get("original_sl", current_sl))
        ot["current_sl"] = suggested_sl
        state_manager.set_field(bot_state, "open_trade", ot)

        await ctx.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                f"📈 *Trail SL Alert — Trade #{trade_id}*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{direction} | ราคาปัจจุบัน: `{current}`\n"
                f"Peak: `{peak}`\n"
                f"🛑 เลื่อน SL ไปที่: `{suggested_sl}` (+{profit_locked:.0f}p locked)\n"
                f"_(trail distance {TRAIL_PIPS}p จาก peak)_"
            ),
            parse_mode="Markdown"
        )

    # ── Pyramid ไม้ 2 Check ───────────────────────────────
    # trigger: มีไม้ 1 เปิด (BOS breakout) ยังไม่กำไร → รอ pull back มา OB
    pyramid_waiting  = ot.get("pyramid_waiting", False)
    pyramid_alerted  = ot.get("pyramid_alerted", False)
    pyramid_lot2     = ot.get("pyramid_lot2")
    pyramid_ob       = ot.get("pyramid_ob")

    if pyramid_waiting and not pyramid_alerted and pyramid_lot2:
        # ยัง "ไม่กำไร" = ราคายังไม่ห่างจาก entry เกิน 80 pips
        profit_now = (current - entry) if is_buy else (entry - current)
        still_near = profit_now < 80

        if still_near:
            # อัพเดต OB zone จาก SMC ปัจจุบันถ้าไม่มี
            if not pyramid_ob:
                ob_key = "active_bull_ob" if is_buy else "active_bear_ob"
                pyramid_ob = smc_summary.get(ob_key)
                ot["pyramid_ob"] = pyramid_ob
                state_manager.set_field(bot_state, "open_trade", ot)

            if pyramid_ob:
                ob_top = pyramid_ob.get("top", 0)
                ob_bot = pyramid_ob.get("bottom", 0)
                # ราคาถึง OB หรืออยู่ใน OB แล้ว (±15 pip buffer)
                if is_buy:
                    at_ob = ob_bot - 1.5 <= current <= ob_top + 1.5
                else:
                    at_ob = ob_bot - 1.5 <= current <= ob_top + 1.5

                if at_ob:
                    ot["pyramid_alerted"] = True
                    state_manager.set_field(bot_state, "open_trade", ot)

                    bot_state["pending_pyramid"] = {
                        "direction":          direction,
                        "lot":                pyramid_lot2,
                        "ob_zone":            pyramid_ob,
                        "original_trade_id":  trade_id,
                        "current_price":      current,
                    }
                    state_manager.save(bot_state)

                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ เปิดไม้ 2", callback_data="pyramid_confirm"),
                        InlineKeyboardButton("⏭ ข้าม",      callback_data="pyramid_skip"),
                    ]])
                    await ctx.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=(
                            f"🔺 *Pyramid ไม้ 2 — ราคาถึง OB แล้ว!*\n"
                            f"━━━━━━━━━━━━━━━━━\n"
                            f"ไม้ 1: `{direction}` @ `{entry}` (เปิดอยู่)\n"
                            f"ราคาปัจจุบัน: `{current}`\n"
                            f"🎯 OB Zone: `{ob_bot}–{ob_top}`\n"
                            f"📦 ไม้ 2 Lot: `{pyramid_lot2}` (60% ของ full size)\n"
                            f"💡 Average entry จะดีขึ้น ค่า risk เพิ่มนิดเดียว\n\n"
                            f"เปิดไม้ 2 ที่ OB เลยมั้ย?"
                        ),
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )

    # ── Re-entry Analysis ────────────────────────────────
    # trigger: เคย profit ≥200p แล้วราคากลับมาใกล้ entry
    profit_had = abs(peak - entry)
    near_entry = abs(current - entry) <= REENTRY_NEAR_ENTRY
    already_analyzed = ot.get("reentry_analyzed", False)

    if profit_had >= REENTRY_MIN_PROFIT and near_entry and not already_analyzed:
        # mark ก่อนเพื่อกัน double-fire
        ot["reentry_analyzed"] = True
        state_manager.set_field(bot_state, "open_trade", ot)

        await ctx.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"🔄 ราคากลับมาที่ entry `{entry}` (เคย peak `{peak}`) — กำลังให้ Sonnet วิเคราะห์...",
            parse_mode="Markdown"
        )

        try:
            verdict = supervisor.analyze_reentry(ot, current, smc_summary)
            reenter  = verdict.get("reenter", False)
            conf     = verdict.get("confidence", 0)
            reason   = verdict.get("reasoning", "")
            new_sl   = verdict.get("new_sl")
            caution  = verdict.get("caution", "")
            icon     = "✅" if reenter else "❌"

            msg = (
                f"🔄 *Re-entry Analysis — Trade #{trade_id}*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"{icon} Re-enter: *{'YES' if reenter else 'NO'}* ({conf}%)\n"
                f"📝 {reason}\n"
            )
            if new_sl:
                msg += f"🛑 SL ใหม่แนะนำ: `{new_sl}`\n"
            if caution:
                msg += f"⚠️ {caution}\n"
            msg += f"\nราคาปัจจุบัน: `{current}` | Entry เดิม: `{entry}`"

            await ctx.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode="Markdown"
            )
        except Exception as e:
            await ctx.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"⚠️ Re-entry analysis error: {e}"
            )


# ── Scan window builder (DST-aware) ───────────────────

def _build_scan_windows() -> list[tuple]:
    """
    สร้าง scan windows ทุก 15 นาที ตามช่วงเวลาที่กำหนด
    ปรับ DST อัตโนมัติ:
      London windows  → shift +1h เมื่อ GMT (winter), 0 เมื่อ BST (summer)
      NY windows      → shift +1h เมื่อ EST (winter), 0 เมื่อ EDT (summer)
    """
    import pytz
    from datetime import datetime, time as dtime

    thai_tz   = pytz.timezone("Asia/Bangkok")
    london_tz = pytz.timezone("Europe/London")
    ny_tz     = pytz.timezone("America/New_York")
    now = datetime.now()

    london_h = int(london_tz.localize(now).utcoffset().total_seconds() / 3600)
    ny_h     = int(ny_tz.localize(now).utcoffset().total_seconds() / 3600)

    # Base windows ตาม BST(+1) + EDT(-4) = summer hours
    # GMT(+0) → shift +1h | EST(-5) → shift +1h
    ld = max(0, 1 - london_h)   # 0=BST, 1=GMT
    nd = max(0, -4 - ny_h)      # 0=EDT, 1=EST

    def _range(sh, sm, eh, em, shift, label):
        """สร้าง list ของ (time, label) ทุก 15 นาที"""
        result = []
        h, m = sh + shift, sm
        end_mins = (eh + shift) * 60 + em
        while h * 60 + m <= end_mins:
            result.append((dtime(h % 24, m, tzinfo=thai_tz), label))
            m += 15
            if m >= 60:
                m = 0
                h += 1
        return result

    windows = []
    windows += _range(6,  30, 8,  30, 0,  "🌅 Pre-Event")        # Asia/pre-8AM
    windows += _range(10, 30, 12,  0, 0,  "🌏 Late Asia")         # late Tokyo
    windows += _range(13, 45, 14, 30, ld, "🇬🇧 London Open")      # London open
    windows += _range(15, 45, 16, 15, 0,  "🇯🇵 Japan Close")      # Tokyo close 09:00 UTC = 16:00 Thai
    windows += _range(16, 30, 17, 30, ld, "🇬🇧 London Mid")       # London mid
    windows += _range(19,  0, 21, 45, nd, "🇺🇸 NY Session")       # NY open/mid
    windows += _range(22,  0, 23,  0, nd, "🇺🇸 NY Peak")          # NY peak
    windows += _range(23, 15, 23, 45, nd, "🌙 NY Close")          # NY close 23:15-23:45
    windows += _range(1,   0,  2,  0, nd, "🌙 Late NY")           # late NY

    dst_info = f"London DST+{ld}h / NY DST+{nd}h"
    print(f"📅 Scan windows built: {len(windows)} slots/day ({dst_info})")
    return windows


# ── Daily close summary ────────────────────────────────

async def daily_close_summary(ctx: ContextTypes.DEFAULT_TYPE):
    """ส่งสรุปผลเทรดประจำวัน ตอนตลาดปิด (23:30 Thai)"""
    msg = format_today_summary()
    await ctx.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=msg,
        parse_mode="Markdown"
    )


# ── Dashboard scan request poller ──────────────────────

_dashboard_scan_running = False  # ป้องกัน concurrent scan จาก dashboard

async def poll_dashboard_scan(ctx: ContextTypes.DEFAULT_TYPE):
    """Poll dashboard every 5s — if 'Scan Now' was clicked, run real supervisor.run()."""
    global _dashboard_scan_running
    if _dashboard_scan_running:
        return  # scan กำลังรันอยู่ ข้าม

    try:
        import urllib.request
        req = urllib.request.Request(f"{DASHBOARD_URL}/api/poll-scan")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
        if not data.get("requested"):
            return
    except Exception:
        return

    # Dashboard requested a scan — run supervisor with real MT5 data
    _dashboard_scan_running = True
    try:
        result = await asyncio.get_event_loop().run_in_executor(None, supervisor.run)
        log_scan(result)
        _push_to_dashboard(result)

        async def send(text, **kw):
            await ctx.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, **kw)

        await ctx.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="🖥️ *Dashboard Scan*",
            parse_mode="Markdown"
        )
        await _handle_scan_result(result, send)
    finally:
        _dashboard_scan_running = False


# ── Auto scan job ──────────────────────────────────────

async def auto_scan(ctx: ContextTypes.DEFAULT_TYPE):
    if not bot_state["is_running"]:
        return

    session_label = (ctx.job.data or {}).get("session_label", "🔍 Auto Scan")

    # ── ข้ามถ้าตลาดปิด (วันหยุด / weekend) ──────────────
    from agents.smc_engine import is_market_holiday
    mkt_closed, holiday_name = is_market_holiday()
    if mkt_closed:
        print(f"[auto_scan] 🚫 ตลาดปิด — {holiday_name}")
        return

    # ── ข้ามถ้ามีข่าว High Impact ──────────────────────
    blocked, block_reason = news_scout.should_block_trade()
    if blocked:
        print(f"[auto_scan] 📰 News block — {block_reason}")
        await ctx.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"📰 *Skip Scan — ข่าว High Impact*\n_{block_reason}_",
            parse_mode="Markdown"
        )
        return

    # แจ้งว่าเริ่ม scan session ไหน
    await ctx.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"{session_label} — กำลัง scan...",
        parse_mode="Markdown"
    )

    result = supervisor.run()
    log_scan(result)
    _push_to_dashboard(result)

    async def send(text, **kw):
        await ctx.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, **kw)

    await _handle_scan_result(result, send)

# ── Main ───────────────────────────────────────────────

def _startup_clear_stale_trade():
    """ตอน startup:
    1. ถ้า bot มี open_trade แต่ MT5 ไม่มีแล้ว → clear state
    2. ถ้า MT5 มีไม้อยู่แต่ bot ไม่รู้ → restore state จาก MT5
    """
    try:
        positions = mt5_executor.get_open_positions()
    except Exception as e:
        print(f"[startup] ⚠️ Could not connect to MT5: {e}")
        return

    existing = bot_state.get("open_trade")

    # ── Case 1: bot มี state แต่ MT5 ปิดไปแล้ว ──────────────
    if existing:
        ticket = existing.get("mt5_ticket")
        open_tickets = {p["ticket"] for p in positions}
        stale = (ticket and ticket not in open_tickets) or (not ticket and not positions)
        if stale:
            state_manager.set_field(bot_state, "open_trade", None)
            tid = existing.get("trade_id", "?")
            print(f"[startup] 🧹 Trade #{tid} state cleared — no matching MT5 position")
            existing = None
        else:
            tid = existing.get("trade_id", "?")
            print(f"[startup] ✅ Trade #{tid} still open in MT5 — keeping state")

    # ── Case 2: MT5 มีไม้แต่ bot ไม่รู้ → restore ──────────
    if not existing and positions:
        # รวม positions ทุกไม้ที่เปิดอยู่
        pos = positions[0]  # ใช้ไม้แรก (oldest) เป็น reference
        all_tickets = [p["ticket"] for p in positions]
        restored = {
            "entry":        pos["entry"],
            "direction":    pos["direction"],
            "original_sl":  pos["sl"],
            "current_sl":   pos["sl"],
            "tp":           pos["tp"] or None,
            "lot":          sum(p["lot"] for p in positions),
            "mt5_ticket":   pos["ticket"],
            "mt5_tickets":  all_tickets,
            "trade_id":     f"MT5-{pos['ticket']}",
            "peak_price":   pos["entry"],
            "opened_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "restored":     True,
            "pyramid_waiting": len(positions) > 1,
        }
        state_manager.set_field(bot_state, "open_trade", restored)
        dirs = set(p["direction"] for p in positions)
        print(f"[startup] 🔄 Restored {len(positions)} MT5 position(s) → Trade MT5-{pos['ticket']} {dirs} lot={restored['lot']}")


async def _startup_session_scan(ctx: ContextTypes.DEFAULT_TYPE):
    """รัน scan ทันทีตอน startup ถ้ามี slot ที่ missed ใน 14 นาทีที่ผ่านมา และ last_scan เก่ากว่า 10 นาที"""
    now = datetime.now()

    # ถ้า scan ไปแล้วใน 10 นาที ไม่ต้อง scan ซ้ำ
    last = bot_state.get("last_scan")
    if last:
        try:
            last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            if (now - last_dt).total_seconds() < 600:
                print(f"[startup_scan] ⏭ last_scan={last} — ยังไม่ถึง 10 นาที ข้าม")
                return
        except Exception:
            pass

    # หา slot ที่ควรจะ fire แต่ bot ยังไม่ได้ scan (missed ใน 14 นาทีที่ผ่านมา)
    now_mins = now.hour * 60 + now.minute
    missed_label = None
    for scan_time, label in _build_scan_windows():
        slot_mins = scan_time.hour * 60 + scan_time.minute
        # slot ผ่านไปแล้วไม่เกิน 14 นาที (ก่อน slot ถัดไป 15 นาที)
        if 0 < now_mins - slot_mins <= 14:
            missed_label = label
            break

    if not missed_label:
        print(f"[startup_scan] ⏭ {now.strftime('%H:%M')} — ไม่มี missed slot ใน 14 นาทีที่ผ่านมา")
        return

    print(f"[startup_scan] 🚀 missed slot: {missed_label} → scan ทันที")
    await auto_scan(ctx)


def run():
    from config.settings import SCAN_INTERVAL_MINUTES

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("outcome", cmd_outcome))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("backfill", cmd_backfill))
    app.add_handler(CommandHandler("mt5import", cmd_mt5import))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("tx", cmd_tx))
    app.add_handler(CommandHandler("txreport", cmd_txreport))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("bias", cmd_bias))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("scalein", cmd_scalein))
    app.add_handler(CommandHandler("paper", cmd_paper))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("closetrade", cmd_closetrade))
    app.add_handler(CommandHandler("testscan", cmd_testscan))
    app.add_handler(CommandHandler("mt5", cmd_mt5))
    app.add_handler(CommandHandler("posguard", cmd_posguard))
    app.add_handler(CommandHandler("ob", cmd_ob))

    # Callback (ปุ่ม)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Free text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Session-Based Scanning — 15 นาที ตามช่วง session จริง (DST-aware)
    for scan_time, label in _build_scan_windows():
        app.job_queue.run_daily(
            auto_scan,
            time=scan_time,
            data={"session_label": label}
        )

    # Dashboard scan request poller — ทุก 5 วิ รับ Scan Now จาก UI
    app.job_queue.run_repeating(poll_dashboard_scan, interval=5, first=10)

    # Trade monitor — ทุก 5 นาที ตรวจ trailing + reentry
    app.job_queue.run_repeating(trade_monitor, interval=300, first=60)

    # MT5 transaction sync — ทุก 3 นาที เก็บ transaction ทุกไม้ (กันจับพลาดตอนปิด)
    app.job_queue.run_repeating(mt5_sync_job, interval=180, first=90)

    # POS Guard — เช็ค SL ของทุก open position ทุก N วินาที
    if pos_guard.POSGUARD_ENABLED:
        app.job_queue.run_repeating(
            _pos_guard_job,
            interval=pos_guard.POSGUARD_CHECK_INTERVAL,
            first=30,
        )

    # Daily close summary — 23:30 Thai (NY ปิด)
    import pytz
    from datetime import time as dtime
    thai_tz = pytz.timezone("Asia/Bangkok")
    app.job_queue.run_daily(
        daily_close_summary,
        time=dtime(23, 30, tzinfo=thai_tz),
    )

    # ── Startup: clear stale open_trade state ──────────────────────
    _startup_clear_stale_trade()

    # ── Startup scan: ถ้า restart ระหว่าง session และ scan เก่าเกิน 10 นาที ──
    app.job_queue.run_once(_startup_session_scan, when=5, data={"session_label": "🚀 Startup"})

    print("🏢 SmartAgentTrade Bot เริ่มทำงานแล้ว...")
    app.run_polling()
