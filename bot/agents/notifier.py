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
    MODEL_SMART, TRADING_PAIR
)
from agents import chart_analyst, bias_analyst, news_scout
from agents import supervisor, risk_manager
from agents.trade_log import (
    log_trade, update_outcome, format_report,
    get_all_trades, format_trade_list, export_csv, get_trade,
)
from agents import paper_trader
from agents import state_manager

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# โหลด state จาก disk (รองรับ restart / ย้าย session)
bot_state = state_manager.load()

# ── Commands ──────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏢 *SmartAgentTrade* พร้อมแล้ว!\n\n"
        "📡 *Analysis*\n"
        "/scan — สแกนหา setup (เช็ค bias + news อัตโนมัติ)\n"
        "/bias — ดู HTF direction H1/H4/Daily\n"
        "/news — เช็คข่าว Economic Calendar\n\n"
        "⚙️ *Control*\n"
        "/status — ดูสถานะ bot\n"
        "/pause — หยุดสแกนชั่วคราว\n"
        "/resume — เริ่มสแกนใหม่\n\n"
        "📊 *Report & Tools*\n"
        "/report — สรุป trade จริง + P&L รายวัน\n"
        "/trades — ดู trade history ทั้งหมด\n"
        "/outcome [id] [win/loss/be] [pips] [exit] — บันทึกผลหลังเทรด\n"
        "/export — ดาวน์โหลด CSV ประวัติ trade\n"
        "/scalein [top] [bot] [bull/bear] [balance] — คำนวณ entry แบบ scale-in\n\n"
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

    result = supervisor.run()
    state_manager.set_field(bot_state, "last_scan", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    message = supervisor.format_alert(result)

    if result.get("approved"):
        pending = result.get("analysis", {})
        pending["lot"]      = result.get("lot")
        pending["risk_pct"] = result.get("risk_pct")
        state_manager.set_field(bot_state, "pending_signal", pending)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
                InlineKeyboardButton("❌ Skip", callback_data="skip")
            ]
        ])
        await update.message.reply_text(message, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(message, parse_mode="Markdown")

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
            f"SL: `{result['sl']}` ({result['sl_pips']} pips)\n"
            f"TP: `{result['tp']}` ({result['tp_pips']} pips)\n"
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
    /outcome [id] [win/loss/be] [pips] [exit_price] [notes...]
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
            "`/outcome [id] [win/loss/be] [pips] [exit_price]`\n\n"
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
        pips_s = f"{pnl_pips:+.1f}p" if pnl_pips is not None else "-"
        dur_s  = f"{duration_min} นาที" if duration_min else "-"
        exit_s = f"`{actual_exit}`" if actual_exit else "-"

        await update.message.reply_text(
            f"{icon} *Trade #{trade_id} อัพเดทแล้ว*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Signal: `{t.get('signal')}`  {t.get('stars') or ''}\n"
            f"Outcome: *{outcome.upper()}*  `{pips_s}`\n"
            f"Exit Price: {exit_s}\n"
            f"Duration: {dur_s}\n"
            + (f"Notes: _{notes}_" if notes else ""),
            parse_mode="Markdown"
        )

    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ รูปแบบไม่ถูกต้อง: {e}\nดูวิธีใช้: `/outcome`", parse_mode="Markdown")


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
    price_data = chart_analyst.get_price_data()
    price_info = f"ราคา Gold ปัจจุบัน: {price_data['current_price']}" if price_data else ""

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

# ── Callback (ปุ่ม Confirm/Skip) ──────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data
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

    entry = signal.get("entry_zone") or signal.get("entry")
    if action == "confirm":
        await query.edit_message_text(
            f"✅ *Confirmed — Trade #{trade_id}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Signal: *{signal.get('signal')}*  {signal.get('reversal_stars') or ''}\n"
            f"Entry: `{entry}`\n"
            f"SL: `{signal.get('stop_loss') or signal.get('sl')}`  "
            f"TP: `{signal.get('take_profit') or signal.get('tp')}`\n"
            f"Lot: `{signal.get('lot', '-')}`\n\n"
            f"🍀 โชคดี! หลังเทรดเสร็จ:\n"
            f"`/outcome {trade_id} win 150 3310`\n"
            f"`/outcome {trade_id} loss -80`",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            f"⏭ *Skipped — Trade #{trade_id}*\n"
            f"บันทึกว่า Skip {signal.get('signal')} setup แล้ว",
            parse_mode="Markdown"
        )

# ── Auto scan job ──────────────────────────────────────

async def auto_scan(ctx: ContextTypes.DEFAULT_TYPE):
    if not bot_state["is_running"]:
        return

    session_label = (ctx.job.data or {}).get("session_label", "🔍 Auto Scan")

    # แจ้งว่าเริ่ม scan session ไหน
    await ctx.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"{session_label} — กำลัง scan...",
        parse_mode="Markdown"
    )

    result = supervisor.run()
    state_manager.set_field(bot_state, "last_scan", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if result.get("approved"):
        pending = result.get("analysis", {})
        pending["lot"]      = result.get("lot")
        pending["risk_pct"] = result.get("risk_pct")
        state_manager.set_field(bot_state, "pending_signal", pending)

        message = supervisor.format_alert(result)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
                InlineKeyboardButton("❌ Skip", callback_data="skip")
            ]
        ])

        await ctx.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        # ถ้าไม่มี setup แจ้งสั้นๆ แค่ reject reason
        reason = result.get("reject_reason", "ไม่มี setup")
        await ctx.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"{session_label} — ❌ {reason}",
            parse_mode="Markdown"
        )

# ── Main ───────────────────────────────────────────────

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
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("bias", cmd_bias))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("scalein", cmd_scalein))
    app.add_handler(CommandHandler("paper", cmd_paper))
    app.add_handler(CommandHandler("pnl", cmd_pnl))

    # Callback (ปุ่ม)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Free text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Session-Based Scanning — scan เฉพาะช่วงสำคัญ 5 ครั้ง/วัน
    import pytz
    from datetime import time as dtime
    thai_tz = pytz.timezone("Asia/Bangkok")

    session_times = [
        (dtime(1,  0,  tzinfo=thai_tz), "🌙 Late NY Afternoon"),   # 18:00 UTC / 14:00 EDT
        (dtime(3,  0,  tzinfo=thai_tz), "🌙 NY Close Momentum"),   # 20:00 UTC / 16:00 EDT
        (dtime(7, 15, tzinfo=thai_tz),  "🇯🇵 Tokyo Open"),
        # Tokyo Mid (10:00-12:00 Thai) ทดสอบแล้วไม่มี edge แม้ score≥7
        (dtime(13, 45, tzinfo=thai_tz), "🇬🇧 London Open"),
        (dtime(15, 45, tzinfo=thai_tz), "🇬🇧 London Mid"),
        # 20:15 Thai = 09:15 ET — ช่วง NY Pre-Open หลัง spread นิ่ง
        (dtime(20, 15, tzinfo=thai_tz), "🇺🇸 NY Pre-Open"),
        (dtime(22, 45, tzinfo=thai_tz), "🇺🇸 NY Peak"),
    ]

    for scan_time, label in session_times:
        app.job_queue.run_daily(
            auto_scan,
            time=scan_time,
            data={"session_label": label}
        )

    print("🏢 SmartAgentTrade Bot เริ่มทำงานแล้ว...")
    app.run_polling()
