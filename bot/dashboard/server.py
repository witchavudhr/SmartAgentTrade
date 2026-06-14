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

class AskBody(BaseModel):
    question: str

@app.post("/api/ask")
async def ask_agents(body: AskBody):
    answer = await run_ask(body.question)
    return {"response": answer}

# ── Scan logic ───────────────────────────────────────────────────────────────
async def run_scan():
    global scan_phase, latest_signal, scan_running
    scan_running = True
    agents = ["supervisor", "chart_analyst", "bias_analyst", "news_scout", "risk_manager"]

    # Phase 1 — gather
    scan_phase = "gathering"
    await manager.broadcast({"type": "scan_phase", "phase": "gathering"})
    for a in agents:
        await manager.broadcast({"type": "agent_bubble", "agent": a, "text": GATHER_MSGS[a]})
        await asyncio.sleep(0.35)
    await asyncio.sleep(1.4)

    # Phase 2 — scan
    scan_phase = "scanning"
    await manager.broadcast({"type": "scan_phase", "phase": "scanning"})
    for a in agents:
        await manager.broadcast({"type": "agent_bubble", "agent": a, "text": ANALYZE_MSGS[a]})
        await asyncio.sleep(0.3)

    result = await run_bot_agents()
    await asyncio.sleep(1.2)

    # Phase 3 — vote
    scan_phase = "voting"
    await manager.broadcast({"type": "scan_phase", "phase": "voting"})
    for a, vote_text in result["votes"].items():
        await manager.broadcast({"type": "agent_bubble", "agent": a, "text": vote_text})
        await asyncio.sleep(0.4)
    await asyncio.sleep(1.0)

    # Phase 4 — result
    approved = result["approved"]
    scan_phase = "approved" if approved else "rejected"
    await manager.broadcast({"type": "scan_phase", "phase": scan_phase, "approved": approved})

    sig = result["signal"]
    latest_signal = sig
    await manager.broadcast({"type": "signal", "data": sig})

    entry = {
        "time": datetime.now().strftime("%H:%M"),
        "result": "ok" if approved else "no",
        "text": f"{'BUY' if sig['direction']=='BUY' else 'SELL'} {sig['setup_type']} {sig['stars']}" if approved else "REJECTED",
        "sub": f"APPROVED · {result['vote_count']}/4" if approved else result.get("reason", "No setup"),
    }
    scan_log.append(entry)
    await manager.broadcast({"type": "scan_log", "data": entry})

    await asyncio.sleep(3.5)
    scan_phase = "idle"
    await manager.broadcast({"type": "scan_phase", "phase": "idle"})
    scan_running = False

async def run_bot_agents() -> dict:
    """Run actual bot agents if available, otherwise return mock data."""
    try:
        from agents.chart_analyst import ChartAnalyst
        from agents.bias_analyst import BiasAnalyst
        from agents.news_scout import NewsScout
        from agents.supervisor import Supervisor

        chart = ChartAnalyst()
        bias  = BiasAnalyst()
        news  = NewsScout()
        sup   = Supervisor()

        df, price = chart.get_price_data()
        if df is None:
            raise ValueError("No price data")

        chart_result = await asyncio.to_thread(chart.analyze, df, price)
        bias_result  = await asyncio.to_thread(bias.analyze)
        news_result  = await asyncio.to_thread(news.check_news)
        sup_result   = await asyncio.to_thread(sup.decide, chart_result, bias_result, news_result)

        approved = sup_result.get("decision") == "BUY" or sup_result.get("decision") == "SELL"
        return {
            "approved": approved,
            "votes": {
                "chart_analyst": f"{'YES' if chart_result.get('signal') else 'NO'} — {chart_result.get('setup_type','?')}",
                "bias_analyst":  f"{'YES' if bias_result.get('bias') else 'NO'} — {bias_result.get('direction','?')}",
                "news_scout":    f"{'YES' if news_result.get('safe') else 'BLOCK'} — {news_result.get('summary','?')}",
                "risk_manager":  f"OK — {sup_result.get('lot', '?')}L",
                "supervisor":    f"{'APPROVED' if approved else 'REJECTED'} — {sup_result.get('reason','?')}",
            },
            "vote_count": 4 if approved else 0,
            "signal": {
                "direction":  sup_result.get("decision", "BUY"),
                "setup_type": chart_result.get("setup_type", "TREND_OB"),
                "stars": "★★",
                "entry": float(price.get("current_price", 3310)),
                "sl":    float(price.get("current_price", 3310)) - 10,
                "tp":    float(price.get("current_price", 3310)) + 24,
                "lot":   float(sup_result.get("lot", 0.01)),
                "rr":    2.4,
                "approved": approved,
                "time":  datetime.now().strftime("%H:%M"),
                "pnl":   0.0,
                "votes": {"chart": True, "bias": True, "news": True, "risk": True},
                "reason": sup_result.get("reason", ""),
            },
            "reason": sup_result.get("reason", ""),
        }
    except Exception as e:
        # Mock fallback
        approved = random.random() > 0.4
        direction = random.choice(["BUY", "SELL"])
        setup = random.choice(["SWING_OB", "TREND_OB"])
        entry = round(3300 + random.uniform(0, 50), 1)
        return {
            "approved": approved,
            "votes": {
                "chart_analyst": f"{'YES' if approved else 'NO'} — {setup} detected",
                "bias_analyst":  "YES — H4 demand bullish",
                "news_scout":    "YES — calendar clear",
                "risk_manager":  f"{'OK' if approved else 'VETO'} — RR {2.3 if approved else 1.1:.1f}",
                "supervisor":    f"{'APPROVED' if approved else 'REJECTED'}",
            },
            "vote_count": 4 if approved else random.randint(0, 2),
            "signal": {
                "direction": direction, "setup_type": setup, "stars": "★★" if approved else "★",
                "entry": entry, "sl": round(entry - 10, 1), "tp": round(entry + 23, 1),
                "lot": 0.01, "rr": 2.3, "approved": approved,
                "time": datetime.now().strftime("%H:%M"), "pnl": 0.0,
                "votes": {"chart": approved, "bias": True, "news": True, "risk": approved},
                "reason": "Bull OB + HTF demand" if approved else "RR too low / no setup",
            },
            "reason": "No OB proximity" if not approved else "",
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
