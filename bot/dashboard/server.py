import sys, os, asyncio, json
from datetime import datetime
from typing import List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))  # Forex/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))        # Forex/bot/

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="SmartAgentTrade War Room")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def start_stats_refresh():
    asyncio.create_task(_stats_refresh_loop())
    asyncio.create_task(_price_loop())

async def _price_loop():
    """Fetch XAUUSD price every 60s via yfinance and broadcast."""
    global current_price, nearest_ob
    while True:
        await asyncio.sleep(60)
        try:
            import yfinance as yf
            price = round(float(yf.Ticker("GC=F").fast_info.last_price), 2)
            current_price = price
            ob_lo = nearest_ob.get("lo", 0)
            ob_hi = nearest_ob.get("hi", 0)
            ob_mid = (ob_lo + ob_hi) / 2 if ob_lo else 0
            dist = round(abs(price - ob_mid) * 10, 1) if ob_mid else 0
            nearest_ob = {**nearest_ob, "dist": dist}
            await manager.broadcast({"type": "price_update", "price": price, "ob": nearest_ob})
        except Exception:
            pass

async def _stats_refresh_loop():
    """Broadcast stats every 15s — always read from DB directly."""
    global stats
    while True:
        await asyncio.sleep(15)
        try:
            from agents.trade_log import get_dashboard_stats
            new_stats = get_dashboard_stats()
            if new_stats != stats:
                stats = new_stats
                await manager.broadcast({"type": "stats", "data": stats})
        except Exception:
            pass

# ── WebSocket manager ────────────────────────────────────────────────────────
class WsManager:
    def __init__(self):
        self.clients: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, data: dict):
        msg = json.dumps(data, default=str)
        dead = []
        for ws in self.clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = WsManager()

# ── App state ────────────────────────────────────────────────────────────────
scan_phase = "idle"  # idle | gathering | scanning | voting | approved | rejected

_EMPTY_SIGNAL = {
    "direction": "—", "setup_type": "—", "stars": "",
    "entry": 0.0, "sl": 0.0, "tp": 0.0, "lot": 0.0, "rr": 0.0,
    "approved": False, "time": "—", "pnl": 0.0,
    "votes": {"chart": False, "bias": False, "news": False, "risk": False},
    "reason": "Waiting for first scan…",
}

def _load_initial_state():
    """Load real data from trade_log DB on startup."""
    try:
        from agents.trade_log import get_recent_scans, get_dashboard_stats, get_latest_dashboard_signal
        sig = get_latest_dashboard_signal() or _EMPTY_SIGNAL
        return get_recent_scans(9), get_dashboard_stats(), sig
    except Exception as e:
        print(f"[server] trade_log load failed: {e}")
        empty_stats = {"today_pnl": 0, "open_pnl": 0, "win_rate": 0,
                       "wins": 0, "losses": 0, "best_trade": 0, "trades": []}
        return [], empty_stats, _EMPTY_SIGNAL

scan_log, stats, latest_signal = _load_initial_state()
scan_running = False
scan_requested = False  # set by /api/scan, cleared by /api/poll-scan

# ── Extra live state ──────────────────────────────────────────────────────────
current_price: float = 0.0
htf_bias: dict = {"overall": "?", "h4": "?", "h1": "?", "daily": "?"}
nearest_ob: dict = {"type": "?", "lo": 0.0, "hi": 0.0, "dist": 0}
# Stats cache per period — populated by bot push (Windows DB), fallback to local DB
stats_cache: dict = {}  # {"today": {...}, "week": {...}, ...}
# Transaction cache per period — populated by bot push
transactions_cache: dict = {}  # {"today": [...], "week": [...], ...}

GATHER_MSGS = {
    "supervisor":    "Knights, to the table!",
    "chart_analyst": "Fetching M5 data...",
    "bias_analyst":  "Reading HTF bias...",
    "news_scout":    "Checking calendar...",
    "risk_manager":  "Loading balance...",
}
ANALYZE_MSGS = {
    "supervisor":    "Weighing all votes...",
    "chart_analyst": "Scanning OB + BOS...",
    "bias_analyst":  "H1 / H4 / Daily...",
    "news_scout":    "Calendar + impact...",
    "risk_manager":  "Lot size + VETO...",
}

# ── WebSocket endpoint ───────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    global stats
    await manager.connect(websocket)
    # Always read fresh stats from DB on connect (default period: today)
    try:
        from agents.trade_log import get_dashboard_stats
        stats = get_dashboard_stats("today")
    except Exception:
        pass
    await websocket.send_text(json.dumps({
        "type": "init",
        "scan_phase": scan_phase,
        "signal": latest_signal,
        "scan_log": scan_log[-9:],
        "stats": stats,
        "price": current_price,
        "htf_bias": htf_bias,
        "nearest_ob": nearest_ob,
    }, default=str))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ── REST endpoints ───────────────────────────────────────────────────────────
@app.get("/api/debug")
def get_debug():
    """Debug: ดูว่า server อ่าน DB ได้ไหม และมี trades/scans กี่รายการ"""
    import sqlite3, os
    result = {"sys_path": sys.path[:4], "error": None}
    try:
        from agents.trade_log import DB_PATH, get_dashboard_stats
        result["db_path"] = str(DB_PATH)
        result["db_exists"] = os.path.exists(DB_PATH)
        if result["db_exists"]:
            conn = sqlite3.connect(DB_PATH)
            result["trades_total"] = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            result["trades_today"] = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE DATE(timestamp)=DATE('now','localtime')"
            ).fetchone()[0]
            result["wins_today"] = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE outcome='win' AND DATE(timestamp)=DATE('now','localtime')"
            ).fetchone()[0]
            result["scan_log_total"] = conn.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0]
            result["scan_log_today"] = conn.execute(
                "SELECT COUNT(*) FROM scan_log WHERE DATE(timestamp)=DATE('now','localtime')"
            ).fetchone()[0]
            result["recent_trades"] = conn.execute(
                "SELECT id, timestamp, outcome, pnl_pips FROM trades ORDER BY id DESC LIMIT 5"
            ).fetchall()
            conn.close()
        result["stats"] = get_dashboard_stats()
    except Exception as e:
        result["error"] = str(e)
    return result

@app.get("/api/stats")
def get_stats_period(period: str = "today"):
    """Return stats for a specific period: today|week|month|year|all — always read DB directly"""
    try:
        from agents.trade_log import get_dashboard_stats
        return get_dashboard_stats(period)
    except Exception as e:
        return {"error": str(e)}

class StatsSyncBody(BaseModel):
    stats_all: dict           # {period: stats_dict} from bot's DB
    transactions_all: dict = {}  # {period: list of tx dicts}

@app.post("/api/stats-sync")
async def stats_sync(body: StatsSyncBody):
    """Bot pushes all-period stats without a scan (startup, periodic refresh)."""
    global transactions_cache
    if body.transactions_all:
        transactions_cache.update(body.transactions_all)
    return {"ok": True}

@app.get("/api/transactions")
def get_transactions(period: str = "today"):
    """Return MT5 transaction list for a period. Prefers bot-pushed cache."""
    if period in transactions_cache:
        return {"transactions": transactions_cache[period], "period": period}
    try:
        from agents.trade_log import get_transactions_by_period
        data = get_transactions_by_period(period)
        return {"transactions": data["txs"], "period": period}
    except Exception as e:
        return {"transactions": [], "error": str(e)}

@app.post("/api/refresh-stats")
async def refresh_stats():
    """Force reload stats from DB and broadcast to all clients."""
    global stats
    try:
        from agents.trade_log import get_dashboard_stats, get_recent_scans
        stats = get_dashboard_stats()
        await manager.broadcast({"type": "stats", "data": stats})
        return {"ok": True, "stats": stats}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/state")
def get_state():
    try:
        from agents.trade_log import get_dashboard_stats
        live_stats = get_dashboard_stats()
    except Exception:
        live_stats = stats
    return {"scan_phase": scan_phase, "signal": latest_signal, "log": scan_log[-9:], "stats": live_stats}

@app.post("/api/scan")
async def trigger_scan():
    global scan_running
    if scan_running:
        return {"ok": False, "msg": "Scan already running"}
    scan_running = True  # lock ก่อน create task — ป้องกัน double-trigger
    asyncio.create_task(run_scan())
    return {"ok": True}

@app.get("/api/poll-scan")
def poll_scan_request():
    """Bot polls this to check if dashboard requested a manual scan."""
    global scan_requested
    if scan_requested:
        scan_requested = False
        return {"requested": True}
    return {"requested": False}

class AskBody(BaseModel):
    question: str

class PushBody(BaseModel):
    result: dict            # raw supervisor.run() output
    stats_all: dict = {}   # {period: stats_dict} from bot's Windows DB
    transactions_all: dict = {}  # {period: list of tx dicts}

@app.post("/api/push")
async def push_result(body: PushBody):
    """Bot calls this after every supervisor.run() to update dashboard live."""
    global latest_signal, scan_log, stats, scan_running, stats_cache, transactions_cache
    scan_running = False  # unblock Scan Now button
    # Cache bot-provided stats (from Windows DB) — used for all periods
    if body.stats_all:
        stats_cache.update(body.stats_all)
    if body.transactions_all:
        transactions_cache.update(body.transactions_all)
    mapped = _map_supervisor_result(body.result)
    sig = mapped["signal"]
    latest_signal = sig
    await manager.broadcast({"type": "signal", "data": sig})
    entry = {
        "time": datetime.now().strftime("%H:%M"),
        "result": "ok" if sig["approved"] else "no",
        "text": f"{sig['direction']} {sig['setup_type']} {sig['stars']}" if sig["approved"] else "REJECTED",
        "sub": f"APPROVED · {mapped['vote_count']}/3" if sig["approved"] else mapped.get("reason", "No setup"),
    }
    scan_log.append(entry)
    await manager.broadcast({"type": "scan_log", "data": entry})
    await manager.broadcast({"type": "scan_phase", "phase": "approved" if sig["approved"] else "rejected"})
    # Broadcast updated price/bias/OB
    await manager.broadcast({"type": "market_update", "price": mapped.get("price", 0),
                             "htf_bias": mapped.get("htf_bias", {}),
                             "nearest_ob": mapped.get("nearest_ob", {})})
    try:
        from agents.trade_log import get_dashboard_stats
        stats = get_dashboard_stats()
    except Exception:
        pass
    await manager.broadcast({"type": "stats", "data": stats})
    await asyncio.sleep(3)
    await manager.broadcast({"type": "scan_phase", "phase": "idle"})
    return {"ok": True}

@app.post("/api/ask")
async def ask_agents(body: AskBody):
    answer = await run_ask(body.question)
    return {"response": answer}

# ── Scan logic ───────────────────────────────────────────────────────────────
async def run_scan():
    """Run gather+scanning animation, then signal bot to do the real scan via /api/poll-scan."""
    global scan_phase, scan_running, scan_requested
    scan_running = True
    agents = ["supervisor", "chart_analyst", "bias_analyst", "news_scout", "risk_manager"]

    # Phase 1 — gather animation
    scan_phase = "gathering"
    await manager.broadcast({"type": "scan_phase", "phase": "gathering"})
    for a in agents:
        await manager.broadcast({"type": "agent_bubble", "agent": a, "text": GATHER_MSGS[a]})
        await asyncio.sleep(0.35)
    await asyncio.sleep(1.4)

    # Phase 2 — scanning animation, then signal bot
    scan_phase = "scanning"
    await manager.broadcast({"type": "scan_phase", "phase": "scanning"})
    for a in agents:
        await manager.broadcast({"type": "agent_bubble", "agent": a, "text": ANALYZE_MSGS[a]})
        await asyncio.sleep(0.3)

    # Signal bot to run real supervisor.run() — result comes back via /api/push
    # scan_running stays True until /api/push clears it (blocks duplicate Scan Now clicks)
    scan_requested = True
    # Safety timeout: auto-release lock after 120s in case bot doesn't respond
    asyncio.create_task(_scan_timeout(120))

def _map_supervisor_result(result: dict) -> dict:
    global current_price, htf_bias, nearest_ob

    approved  = result.get("approved", False)
    signal    = result.get("final_signal", "NO_TRADE")
    direction = signal if signal in ("BUY", "SELL") else "BUY"
    analysis  = result.get("analysis") or {}
    stages    = result.get("stages") or {}
    votes     = result.get("votes") or {}

    entry_zone = result.get("entry_zone") or []
    price_now  = float(result.get("current_price") or current_price or 3310)
    entry = float(entry_zone[0] if entry_zone else price_now)
    sl    = float(result.get("stop_loss")  or entry - 10)
    tp    = float(result.get("take_profit") or entry + 23)
    rr    = float(result.get("rr_ratio")   or 0)
    lot   = float(result.get("lot")        or 0.01)

    setup_type = analysis.get("setup_type", "TREND_OB")
    confidence = analysis.get("confidence", 0)
    stars = "★★★" if confidence >= 80 else "★★" if confidence >= 60 else "★"

    bias_stage = stages.get("bias") or {}
    news_stage = stages.get("news") or {}
    risk_stage = stages.get("risk") or {}
    reason = result.get("reasoning") or result.get("reject_reason", "")

    # ── Update live state ──────────────────────────────────────────────────
    if price_now:
        current_price = price_now

    htf_bias = {
        "overall": bias_stage.get("overall_bias") or bias_stage.get("overall") or "?",
        "h4":      bias_stage.get("h4_bias")      or "?",
        "h1":      bias_stage.get("h1_bias")      or "?",
        "daily":   bias_stage.get("daily_bias")   or bias_stage.get("overall_bias") or "?",
    }

    ob_lo = float(entry_zone[0]) if len(entry_zone) > 0 else entry
    ob_hi = float(entry_zone[1]) if len(entry_zone) > 1 else entry
    ob_mid = (ob_lo + ob_hi) / 2
    ob_dist = round(abs(price_now - ob_mid) * 10, 1) if ob_mid else 0
    nearest_ob = {
        "type": "BULL" if direction == "BUY" else "BEAR",
        "lo": ob_lo, "hi": ob_hi, "dist": ob_dist,
    }

    return {
        "approved": approved,
        "votes": {
            "supervisor":    f"{'APPROVED' if approved else 'REJECTED'} — {reason[:60]}",
            "chart_analyst": f"{'YES' if votes.get('chart') else 'NO'} — {setup_type}",
            "bias_analyst":  f"{'YES' if votes.get('bias') else 'NO'} — {bias_stage.get('overall','?')}",
            "news_scout":    f"{'YES' if votes.get('news') else 'NO'} — {news_stage.get('risk_level','?')}",
            "risk_manager":  f"{'VETO' if risk_stage.get('veto') else 'OK'} — {lot}L",
        },
        "vote_count": result.get("vote_score", 0),
        "signal": {
            "direction": direction, "setup_type": setup_type, "stars": stars,
            "entry": entry, "sl": sl, "tp": tp, "lot": lot, "rr": rr,
            "approved": approved, "time": datetime.now().strftime("%H:%M"), "pnl": 0.0,
            "votes": {"chart": bool(votes.get("chart")), "bias": bool(votes.get("bias")),
                      "news": bool(votes.get("news")), "risk": not risk_stage.get("veto")},
            "reason": reason,
        },
        "reason": result.get("reject_reason", ""),
        "htf_bias": htf_bias,
        "nearest_ob": nearest_ob,
        "price": current_price,
    }


async def _scan_timeout(seconds: int):
    """Release scan lock if bot doesn't push result within timeout."""
    global scan_running, scan_phase
    await asyncio.sleep(seconds)
    if scan_running:
        scan_running = False
        scan_phase = "idle"
        await manager.broadcast({"type": "scan_phase", "phase": "idle"})

async def run_ask(question: str) -> str:
    try:
        import anthropic
        from config.settings import CLAUDE_API_KEY
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": f"คุณเป็น AI trading assistant วิเคราะห์ XAUUSD ด้วย SMC strategy ตอบสั้นๆ เป็นภาษาไทย: {question}"
            }]
        )
        return msg.content[0].text
    except Exception:
        return f"[Sonnet] วิเคราะห์: {question} — BUY bias ยังอยู่ใน demand zone M5 3305–3318 TP ที่ next swing high"

# ── Serve frontend build ─────────────────────────────────────────────────────
frontend_build = os.environ.get("FRONTEND_BUILD_PATH") or os.path.join(os.path.dirname(__file__), "frontend", "build")
if os.path.exists(frontend_build):
    app.mount("/", StaticFiles(directory=frontend_build, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
