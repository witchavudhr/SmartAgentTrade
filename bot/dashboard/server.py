import sys, os, asyncio, json, random
from datetime import datetime
from typing import List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="SmartAgentTrade War Room")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
scan_log: list = [
    {"time": "16:30", "result": "ok",  "text": "BUY SWING_OB ★★",   "sub": "APPROVED · 3/3"},
    {"time": "16:00", "result": "no",  "text": "REJECTED",           "sub": "No OB proximity"},
    {"time": "15:45", "result": "no",  "text": "REJECTED",           "sub": "No swing setup"},
    {"time": "15:00", "result": "ok",  "text": "BUY TREND_OB ★★★",  "sub": "APPROVED · RR1:2.4"},
    {"time": "14:00", "result": "bl",  "text": "BLOCKED",            "sub": "NFP in 28min"},
    {"time": "13:30", "result": "ok",  "text": "SELL TREND_OB ★★",  "sub": "APPROVED · 2/3"},
    {"time": "12:00", "result": "no",  "text": "REJECTED",           "sub": "RR 1.2 < 1.5"},
    {"time": "10:45", "result": "ok",  "text": "BUY TREND_OB ★★",   "sub": "APPROVED · 3/3"},
    {"time": "09:15", "result": "ok",  "text": "SELL SWING_OB ★",   "sub": "APPROVED · CHoCH"},
]
latest_signal = {
    "direction": "BUY", "setup_type": "SWING_OB", "stars": "★★",
    "entry": 3312.0, "sl": 3302.0, "tp": 3344.0, "lot": 0.01, "rr": 2.3,
    "approved": True, "time": "16:30", "pnl": 6.50,
    "votes": {"chart": True, "bias": True, "news": True, "risk": True},
    "reason": "Bull OB + HTF demand + no news window",
}
stats = {
    "today_pnl": 47.20, "open_pnl": 6.50,
    "win_rate": 67, "wins": 3, "losses": 2, "best_trade": 20.00,
    "trades": [
        {"pnl": 15, "r": "w"}, {"pnl": 12, "r": "w"}, {"pnl": -13, "r": "l"},
        {"pnl": 12, "r": "w"}, {"pnl": -5,  "r": "l"}, {"pnl": 20, "r": "w"},
        {"pnl": 6.5, "r": "o"},
    ]
}
scan_running = False
scan_requested = False  # set by /api/scan, cleared by /api/poll-scan

GATHER_MSGS = {
    "supervisor":    "Knights, to the table!",
    "chart_analyst": "Bull OB at 3308!",
    "bias_analyst":  "HTF demand zone!",
    "news_scout":    "No high impact!",
    "risk_manager":  "RR 2.3 passes!",
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
    await manager.connect(websocket)
    # Send current state to new client
    await websocket.send_text(json.dumps({
        "type": "init",
        "scan_phase": scan_phase,
        "signal": latest_signal,
        "scan_log": scan_log[-9:],
        "stats": stats,
    }, default=str))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ── REST endpoints ───────────────────────────────────────────────────────────
@app.get("/api/state")
def get_state():
    return {"scan_phase": scan_phase, "signal": latest_signal, "log": scan_log[-9:], "stats": stats}

@app.post("/api/scan")
async def trigger_scan():
    global scan_running
    if scan_running:
        return {"ok": False, "msg": "Scan already running"}
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
    result: dict  # raw supervisor.run() output

@app.post("/api/push")
async def push_result(body: PushBody):
    """Bot calls this after every supervisor.run() to update dashboard live."""
    global latest_signal, scan_log
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
    scan_requested = True
    scan_running = False
    # scan_phase stays "scanning" until bot pushes result via /api/push

async def run_bot_agents() -> dict:
    try:
        from agents import supervisor as sup_module
        result = await asyncio.to_thread(sup_module.run)
        return _map_supervisor_result(result)
    except Exception as e:
        print(f"[run_bot_agents] error: {e}")
        return _mock_result()


def _map_supervisor_result(result: dict) -> dict:
    approved  = result.get("approved", False)
    signal    = result.get("final_signal", "NO_TRADE")
    direction = signal if signal in ("BUY", "SELL") else "BUY"
    analysis  = result.get("analysis") or {}
    stages    = result.get("stages") or {}
    votes     = result.get("votes") or {}

    entry_zone = result.get("entry_zone") or []
    entry = float(entry_zone[0] if entry_zone else result.get("current_price") or 3310)
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
    }


def _mock_result() -> dict:
    approved  = random.random() > 0.4
    direction = random.choice(["BUY", "SELL"])
    setup     = random.choice(["SWING_OB", "TREND_OB"])
    entry     = round(3300 + random.uniform(0, 50), 1)
    return {
        "approved": approved,
        "votes": {
            "supervisor":    f"{'APPROVED' if approved else 'REJECTED'} [mock]",
            "chart_analyst": f"{'YES' if approved else 'NO'} — {setup}",
            "bias_analyst":  "YES — H4 demand", "news_scout": "YES — clear",
            "risk_manager":  f"{'OK' if approved else 'VETO'} — 0.01L",
        },
        "vote_count": 3 if approved else 0,
        "signal": {
            "direction": direction, "setup_type": setup,
            "stars": "★★" if approved else "★",
            "entry": entry, "sl": round(entry-10,1), "tp": round(entry+23,1),
            "lot": 0.01, "rr": 2.3, "approved": approved,
            "time": datetime.now().strftime("%H:%M"), "pnl": 0.0,
            "votes": {"chart": approved, "bias": True, "news": True, "risk": approved},
            "reason": "Bull OB + HTF demand" if approved else "No setup",
        },
        "reason": "" if approved else "No OB proximity",
    }

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
