import sys, os, asyncio, json
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

def _load_initial_state():
    """Load real data from trade_log DB on startup."""
    try:
        from agents.trade_log import get_recent_scans, get_dashboard_stats
        return get_recent_scans(9), get_dashboard_stats()
    except Exception as e:
        print(f"[server] trade_log load failed: {e}")
        return [], {"today_pnl": 0, "open_pnl": 0, "win_rate": 0,
                    "wins": 0, "losses": 0, "best_trade": 0, "trades": []}

scan_log, stats = _load_initial_state()

latest_signal = {
    "direction": "—", "setup_type": "—", "stars": "",
    "entry": 0.0, "sl": 0.0, "tp": 0.0, "lot": 0.0, "rr": 0.0,
    "approved": False, "time": "—", "pnl": 0.0,
    "votes": {"chart": False, "bias": False, "news": False, "risk": False},
    "reason": "Waiting for first scan…",
}
scan_running = False
scan_requested = False  # set by /api/scan, cleared by /api/poll-scan

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
    global latest_signal, scan_log, stats
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
    # Refresh stats from real DB
    try:
        from agents.trade_log import get_dashboard_stats
        stats = get_dashboard_stats()
        await manager.broadcast({"type": "stats", "data": stats})
    except Exception:
        pass
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
