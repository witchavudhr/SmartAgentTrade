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
from agents import chart_analyst
from agents.trade_log import log_trade, format_report

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# State ของ bot
bot_state = {
    "is_running": True,
    "last_scan": None,
    "pending_signal": None,
    "trade_log": []
}

# ── Commands ──────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏢 *SmartAgentTrade* พร้อมแล้ว!\n\n"
        "คำสั่งที่ใช้ได้:\n"
        "/scan — สแกนหา setup ตอนนี้\n"
        "/status — ดูสถานะ bot\n"
        "/pause — หยุดสแกนชั่วคราว\n"
        "/resume — เริ่มสแกนใหม่\n"
        "/report — สรุป trade ทั้งหมด\n"
        "/ask [คำถาม] — ถามอะไรก็ได้\n\n"
        "หรือพิมข้อความถามได้เลยครับ 🤖",
        parse_mode="Markdown"
    )

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 กำลังสแกน XAUUSD M5...")

    analysis = chart_analyst.analyze()
    message = chart_analyst.format_signal_message(analysis)

    bot_state["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if analysis.get("signal") in ["BUY", "SELL"]:
        bot_state["pending_signal"] = analysis

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
    status = "🟢 กำลังทำงาน" if bot_state["is_running"] else "🔴 หยุดอยู่"
    last = bot_state["last_scan"] or "ยังไม่ได้สแกน"
    trades = len(bot_state["trade_log"])

    await update.message.reply_text(
        f"📊 *Status Report*\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Bot: {status}\n"
        f"สแกนล่าสุด: `{last}`\n"
        f"Trade ที่บันทึก: `{trades}` รายการ\n"
        f"Pair: `{TRADING_PAIR}`",
        parse_mode="Markdown"
    )

async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot_state["is_running"] = False
    await update.message.reply_text("⏸ หยุดสแกนแล้ว พิม /resume เพื่อเริ่มใหม่")

async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot_state["is_running"] = True
    await update.message.reply_text("▶️ เริ่มสแกนใหม่แล้ว")

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    report = format_report()
    await update.message.reply_text(report, parse_mode="Markdown")

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

    # บันทึกลง SQLite trade log
    trade_action = "confirmed" if action == "confirm" else "skipped"
    trade_id = log_trade(signal, trade_action)

    log_entry = {"trade_id": trade_id, "action": trade_action}
    bot_state["trade_log"].append(log_entry)
    bot_state["pending_signal"] = None

    if action == "confirm":
        await query.edit_message_text(
            f"✅ *Confirmed!*\n"
            f"บันทึก {signal.get('signal')} trade แล้ว\n"
            f"Entry: `{signal.get('entry_zone')}`\n"
            f"SL: `{signal.get('stop_loss')}` | TP: `{signal.get('take_profit')}`\n\n"
            f"โชคดีนะครับ! 🍀",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text(
            f"❌ *Skipped*\n"
            f"บันทึกว่า Skip {signal.get('signal')} setup แล้ว",
            parse_mode="Markdown"
        )

# ── Auto scan job ──────────────────────────────────────

async def auto_scan(ctx: ContextTypes.DEFAULT_TYPE):
    if not bot_state["is_running"]:
        return

    analysis = chart_analyst.analyze()
    bot_state["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if analysis.get("signal") in ["BUY", "SELL"] and analysis.get("confidence", 0) >= 65:
        bot_state["pending_signal"] = analysis
        message = chart_analyst.format_signal_message(analysis)

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
    app.add_handler(CommandHandler("ask", cmd_ask))

    # Callback (ปุ่ม)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Free text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Auto scan ทุก X นาที
    app.job_queue.run_repeating(
        auto_scan,
        interval=SCAN_INTERVAL_MINUTES * 60,
        first=10
    )

    print("🏢 SmartAgentTrade Bot เริ่มทำงานแล้ว...")
    app.run_polling()
