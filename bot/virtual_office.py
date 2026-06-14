"""
SmartAgentTrade — Virtual Office
แต่ละ AI Agent มีโต๊ะ/ห้องทำงานของตัวเอง
แสดง pipeline ส่งข้อมูลระหว่าง agent แบบ real-time
"""

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.rule import Rule
from rich import box
from rich.padding import Padding
from rich.columns import Columns

console = Console()

# ── Theme colors ─────────────────────────────────────────────────────────────
C = {
    "bg":       "#0d1117",
    "border":   "#30363d",
    "blue":     "#58a6ff",
    "green":    "#3fb950",
    "yellow":   "#d29922",
    "red":      "#f85149",
    "purple":   "#bc8cff",
    "orange":   "#e3b341",
    "cyan":     "#56d364",
    "dim":      "#8b949e",
    "white":    "#e6edf3",
}

# ── Mock agent states ─────────────────────────────────────────────────────────

PIPELINE_STEP = 4   # 0=scan, 1=chart, 2=bias, 3=news, 4=supervisor, 5=risk, 6=notify

AGENTS_STATE = {
    "chart": {
        "name":    "Chart Analyst",
        "avatar":  "📊",
        "role":    "M5 Entry Specialist",
        "status":  "DONE",
        "mood":    "confident",
        "desk":    "Desk A-1",
        "thought": [
            "Scanning M5 candles...",
            "Bullish OB found @ 3338–3340",
            "Liquidity sweep 3334.20 ✓",
            "M5+M15 confluence ✓",
            "→ VOTE: YES  score 82  conf 78%",
        ],
        "output": {
            "signal":     "BUY",
            "setup":      "Bullish OB + Sweep",
            "score":      82,
            "confidence": 78,
            "entry":      "3338.50 – 3340.00",
            "sl":         "3332.10",
            "tp":         "3355.80",
        },
        "vote": True,
        "color": "green",
    },
    "bias": {
        "name":    "Bias Analyst",
        "avatar":  "📈",
        "role":    "HTF Direction Expert",
        "status":  "CACHED",
        "mood":    "relaxed",
        "desk":    "Desk B-1",
        "thought": [
            "Checking H4 structure...",
            "H4: Higher High confirmed ✓",
            "Daily: Uptrend intact ✓",
            "Using cache (48 min old)",
            "→ VOTE: YES  bias BULLISH",
        ],
        "output": {
            "h4_bias":    "BULLISH",
            "h1_bias":    "BULLISH",
            "daily_bias": "UPTREND",
            "cached":     "48 min",
            "aligned":    True,
        },
        "vote": True,
        "color": "cyan",
    },
    "news": {
        "name":    "News Scout",
        "avatar":  "🗞️",
        "role":    "Event Risk Guard",
        "status":  "CLEAR",
        "mood":    "vigilant",
        "desk":    "Desk C-1",
        "thought": [
            "Checking economic calendar...",
            "Next high-impact: 22:00 ICT",
            "1h 13min away — SAFE ✓",
            "No USD/XAU news now",
            "→ VOTE: YES  risk CLEAR",
        ],
        "output": {
            "next_event": "FOMC (22:00 ICT)",
            "time_left":  "1h 13m",
            "risk_level": "CLEAR",
            "block":      False,
        },
        "vote": True,
        "color": "yellow",
    },
    "risk": {
        "name":    "Risk Manager",
        "avatar":  "⚖️",
        "role":    "Position & Risk Guard",
        "status":  "STANDBY",
        "mood":    "cautious",
        "desk":    "Desk D-1",
        "thought": [
            "Checking portfolio state...",
            "Loss streak: 1/3 — OK ✓",
            "Daily P&L: -0.4% — OK ✓",
            "Calculating lot size...",
            "→ 0.04 lot  risk 1.2%  NO VETO",
        ],
        "output": {
            "lot":        "0.04",
            "risk_pct":   "1.2%",
            "streak":     "1 / 3",
            "daily_loss": "-0.4%",
            "veto":       False,
        },
        "vote": None,   # risk doesn't vote, just VETOs
        "color": "#e3b341",
    },
    "supervisor": {
        "name":    "Supervisor",
        "avatar":  "🧠",
        "role":    "Decision Commander",
        "status":  "APPROVED",
        "mood":    "decisive",
        "desk":    "Board Room",
        "thought": [
            "Collecting agent votes...",
            "Chart Analyst: YES ✓",
            "Bias Analyst:  YES ✓",
            "News Scout:    YES ✓",
            "Risk Manager:  NO VETO ✓",
            "→ APPROVED 3/3  RR 2.38",
        ],
        "output": {
            "decision":   "APPROVED",
            "votes":      "3 / 3",
            "rr":         "2.38",
            "lot":        "0.04",
            "signal":     "BUY XAUUSD",
        },
        "vote": True,
        "color": "purple",
    },
    "notifier": {
        "name":    "Notifier",
        "avatar":  "📱",
        "role":    "Telegram Gateway",
        "status":  "WAITING",
        "mood":    "alert",
        "desk":    "Reception",
        "thought": [
            "Received approved signal...",
            "Formatting Telegram alert...",
            "Sent to @YourBot ✓",
            "⚡ Awaiting user reply...",
            "[CONFIRM] or [SKIP]",
        ],
        "output": {
            "sent_at":  "20:47:03",
            "chat_id":  "***hidden***",
            "status":   "WAITING_CONFIRM",
            "timeout":  "10 min",
        },
        "vote": None,
        "color": "blue",
    },
}

STATUS_BADGE = {
    "DONE":     ("[bold green] ✓ DONE [/]",     "#1a3a1a"),
    "CACHED":   ("[bold cyan] ◈ CACHED [/]",    "#0a2a2a"),
    "CLEAR":    ("[bold green] ◉ CLEAR [/]",    "#1a3a1a"),
    "STANDBY":  ("[bold yellow] ◌ STANDBY [/]", "#2a2a0a"),
    "APPROVED": ("[bold purple] ★ APPROVED [/]","#1a0a2a"),
    "WAITING":  ("[bold blue] ⚡ WAITING [/]",  "#0a1a2a"),
    "WORKING":  ("[bold white] ◎ WORKING [/]",  "#1a1a1a"),
    "VETO":     ("[bold red] ✗ VETO [/]",       "#2a0a0a"),
    "IDLE":     ("[dim] ○ IDLE [/]",            "#111111"),
}

VOTE_BADGE = {
    True:  Text(" YES ", style="bold white on green"),
    False: Text(" NO  ", style="bold white on red"),
    None:  Text(" —   ", style="dim white"),
}

MOOD_ICON = {
    "confident": "😎",
    "relaxed":   "😌",
    "vigilant":  "👀",
    "cautious":  "🤔",
    "decisive":  "💪",
    "alert":     "🔔",
}


# ── Agent desk panel ──────────────────────────────────────────────────────────

def make_agent_desk(key: str, compact: bool = False) -> Panel:
    ag = AGENTS_STATE[key]
    color = ag["color"]
    badge_markup, _ = STATUS_BADGE.get(ag["status"], (" ? ", "#111"))

    # Thought process
    thoughts = Table.grid(padding=(0, 0))
    thoughts.add_column()
    is_last = len(ag["thought"]) - 1
    for i, line in enumerate(ag["thought"]):
        if i == is_last:
            thoughts.add_row(Text(f"  {line}", style=f"bold {color}"))
        else:
            thoughts.add_row(Text(f"  {line}", style="dim white"))

    # Output summary
    out = ag["output"]
    out_table = Table.grid(padding=(0, 1))
    out_table.add_column(style="dim white", min_width=10)
    out_table.add_column(style="bold white")

    for k, v in list(out.items())[:4]:
        val_text = str(v)
        if v is True:
            val_text = "✓"
        elif v is False:
            val_text = "✗"
        out_table.add_row(k.upper(), val_text)

    # Vote indicator
    vote_text = VOTE_BADGE.get(ag["vote"], Text("  —  "))
    mood = MOOD_ICON.get(ag["mood"], "")

    # Header row
    header = Table.grid(expand=True, padding=(0, 1))
    header.add_column(justify="left")
    header.add_column(justify="right")
    header.add_row(
        Text(f"{ag['avatar']}  {ag['name']}  {mood}", style=f"bold {color}"),
        Text.from_markup(badge_markup),
    )

    sub = Text(f"  {ag['role']}  ·  {ag['desk']}", style="dim white")

    content = Table.grid()
    content.add_row(header)
    content.add_row(sub)
    content.add_row(Text(""))
    content.add_row(Text("  💭 Thought Process", style=f"bold {color}"))
    content.add_row(Rule(style=f"dim {color}"))
    content.add_row(thoughts)
    if not compact:
        content.add_row(Text(""))
        content.add_row(Text("  📤 Output", style=f"bold {color}"))
        content.add_row(Rule(style=f"dim {color}"))
        content.add_row(out_table)

    return Panel(
        content,
        border_style=color,
        box=box.ROUNDED,
        style="on #0d1117",
        padding=(0, 1),
    )


# ── Pipeline arrow ────────────────────────────────────────────────────────────

def make_pipeline() -> Panel:
    """แสดง pipeline การไหลของข้อมูลระหว่าง agent"""

    steps = [
        ("SMC Engine",    "🔍", "Scan M5+M15",          True,  "white"),
        ("Chart Analyst", "📊", "VOTE YES  score 82",    True,  "green"),
        ("Bias Analyst",  "📈", "VOTE YES  H4 BULLISH",  True,  "cyan"),
        ("News Scout",    "🗞️", "VOTE YES  risk CLEAR",  True,  "yellow"),
        ("Supervisor",    "🧠", "APPROVED 3/3  RR 2.38", True,  "purple"),
        ("Risk Manager",  "⚖️", "Lot 0.04  NO VETO",     True,  "orange"),
        ("Notifier",      "📱", "Sent Telegram  WAIT",   False, "blue"),
    ]

    t = Table.grid(expand=True, padding=(0, 1))
    t.add_column(justify="center", min_width=14)
    t.add_column(justify="center", min_width=3)
    t.add_column(justify="left")
    t.add_column(justify="center", min_width=8)

    for i, (name, icon, detail, done, color) in enumerate(steps):
        done_icon = Text("✓", style="bold green") if done else Text("◌", style="dim white")
        arrow = Text("   ↓   ", style=f"dim {color}") if i < len(steps) - 1 else Text("")

        t.add_row(
            Text(f"{icon}  {name}", style=f"bold {color}"),
            done_icon,
            Text(detail, style=f"dim white" if not done else "white"),
            Text(""),
        )
        if arrow.plain.strip():
            t.add_row(Text(""), Text(arrow.plain, style=f"dim {color}"), Text(""), Text(""))

    return Panel(
        t,
        title="[bold #58a6ff]🔄 SIGNAL PIPELINE[/]",
        border_style="#30363d",
        box=box.ROUNDED,
        style="on #0d1117",
        padding=(0, 2),
    )


# ── Floor plan header ─────────────────────────────────────────────────────────

def make_floor_header() -> Panel:
    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(justify="left")
    grid.add_column(justify="center")
    grid.add_column(justify="right")

    left = Text("  🏢  SmartAgentTrade HQ", style="bold white")
    center = Text("⚔️  VIRTUAL OFFICE  —  XAUUSD WAR ROOM", style="bold yellow")
    right = Text("🇬🇧 LONDON  20:47 ICT  Sat 14 Jun 2026  ", style="dim white")

    grid.add_row(left, center, right)

    price_bar = Table.grid(expand=True, padding=(0, 1))
    price_bar.add_column(justify="center")

    status_items = [
        ("XAUUSD", "3341.85", "bright_green"),
        ("▲ +4.20", "", "green"),
        ("|", "", "dim white"),
        ("SESSION", "LONDON  ACTIVE", "yellow"),
        ("|", "", "dim white"),
        ("BOT", "🟢 RUNNING", "green"),
        ("|", "", "dim white"),
        ("SIGNAL", "⚡ BUY PENDING CONFIRM", "yellow"),
    ]

    row_text = Text()
    for label, val, color in status_items:
        if label == "|":
            row_text.append("  │  ", style="dim #30363d")
        else:
            row_text.append(f" {label} ", style="dim white")
            if val:
                row_text.append(f"{val} ", style=f"bold {color}")

    price_bar.add_row(Align.center(row_text))

    content = Table.grid()
    content.add_row(grid)
    content.add_row(Rule(style="#30363d"))
    content.add_row(price_bar)

    return Panel(
        content,
        style="on #0d1117",
        box=box.HEAVY,
        border_style="#58a6ff",
        padding=(0, 1),
    )


# ── Meeting room (Supervisor boardroom) ───────────────────────────────────────

def make_boardroom() -> Panel:
    sup = AGENTS_STATE["supervisor"]

    vote_row = Table.grid(expand=True, padding=(0, 3))
    vote_row.add_column(justify="center")
    vote_row.add_column(justify="center")
    vote_row.add_column(justify="center")
    vote_row.add_column(justify="center")
    vote_row.add_column(justify="center")

    votes_data = [
        ("📊 Chart\nAnalyst",  True,  "green"),
        ("📈 Bias\nAnalyst",   True,  "cyan"),
        ("🗞️  News\nScout",    True,  "yellow"),
        ("⚖️  Risk\nManager",  None,  "orange"),
    ]

    names = [Text(name, justify="center", style=f"bold {c}") for name, _, c in votes_data]
    badges = []
    for _, v, c in votes_data:
        if v is True:
            badges.append(Text("  ✅ YES  ", style=f"bold white on #1a3a1a", justify="center"))
        elif v is False:
            badges.append(Text("  ❌ NO   ", style=f"bold white on #3a0a0a", justify="center"))
        else:
            badges.append(Text("  ⚖️  —   ", style=f"bold {c}", justify="center"))

    decision = Text(
        "\n  🧠  SUPERVISOR VERDICT  →  APPROVED  3/3 VOTES  |  RR: 2.38  |  LOT: 0.04  |  BUY XAUUSD @ 3338–3340  \n",
        style="bold white on #1a0a3a",
        justify="center",
    )

    vote_row.add_row(*names, Text(""))
    vote_row.add_row(*badges, Text(""))

    content = Table.grid()
    content.add_row(vote_row)
    content.add_row(Text(""))
    content.add_row(Align.center(decision))

    return Panel(
        content,
        title="[bold #bc8cff]🏛️  BOARD ROOM — SUPERVISOR DECISION CHAMBER[/]",
        border_style="#bc8cff",
        box=box.DOUBLE,
        style="on #0d1117",
        padding=(1, 2),
    )


# ── Pending signal card ───────────────────────────────────────────────────────

def make_signal_card() -> Panel:
    grid = Table.grid(padding=(0, 3), expand=True)
    grid.add_column(min_width=18, style="dim white")
    grid.add_column(style="bold white")

    rows = [
        ("DIRECTION",   Text("▶  BUY", style="bold bright_green")),
        ("SETUP",       "Bullish OB + Liquidity Sweep"),
        ("CONFIDENCE",  Text("★★★★☆  78%", style="yellow")),
        ("ENTRY ZONE",  Text("3338.50 – 3340.00", style="bold cyan")),
        ("STOP LOSS",   Text("3332.10", style="bold red")),
        ("TAKE PROFIT", Text("3355.80", style="bold green")),
        ("R:R RATIO",   Text("2.38  (min 1.5 ✓)", style="bold yellow")),
        ("LOT SIZE",    "0.04  (risk 1.2%)"),
        ("SESSION",     "🇬🇧 LONDON"),
    ]
    for label, val in rows:
        grid.add_row(label, val)

    footer = Text(
        "\n  ⚡ Telegram sent at 20:47:03 — awaiting [CONFIRM] / [SKIP]",
        style="bold yellow",
    )

    content = Table.grid()
    content.add_row(grid)
    content.add_row(footer)

    return Panel(
        content,
        title="[bold yellow]⚡ PENDING SIGNAL[/]",
        border_style="yellow",
        box=box.ROUNDED,
        style="on #0d1117",
        padding=(0, 1),
    )


# ── Live comms feed ───────────────────────────────────────────────────────────

def make_comms() -> Panel:
    messages = [
        ("SMC Engine",    "🔍", "green",  "Bullish OB @ 3338–3340 identified on M5"),
        ("SMC Engine",    "🔍", "green",  "Liquidity sweep below EQL 3334.20 — confirmed"),
        ("Chart Analyst", "📊", "cyan",   "→ Bias Analyst  can you confirm direction?"),
        ("Bias Analyst",  "📈", "yellow", "→ Chart Analyst  H4 bullish, aligned ✓"),
        ("News Scout",    "🗞️", "orange", "→ All  next event 22:00 ICT, 1h13m away, SAFE"),
        ("Supervisor",    "🧠", "purple", "→ Risk Manager  calculate lot for BUY 3339 SL 3332"),
        ("Risk Manager",  "⚖️", "orange", "→ Supervisor  lot=0.04, risk=1.2%, NO VETO"),
        ("Supervisor",    "🧠", "purple", "→ Notifier  APPROVED — send BUY alert to user"),
        ("Notifier",      "📱", "blue",   "→ All  alert sent to Telegram, awaiting confirm..."),
    ]

    lines = Text()
    for sender, icon, color, msg in messages:
        lines.append(f"  {icon} ", style="white")
        lines.append(f"{sender:<16}", style=f"bold {color}")
        lines.append(f"  {msg}\n", style="dim white")

    return Panel(
        lines,
        title="[bold #58a6ff]💬 AGENT COMMS CHANNEL[/]",
        border_style="#30363d",
        box=box.ROUNDED,
        style="on #0d1117",
        padding=(0, 1),
    )


# ── Footer ────────────────────────────────────────────────────────────────────

def make_footer() -> Panel:
    grid = Table.grid(expand=True, padding=(0, 4))
    grid.add_column(justify="center")
    grid.add_column(justify="center")
    grid.add_column(justify="center")

    grid.add_row(
        Text("📊 W12 / L5 / BE1  WR 70.6%", style="bold green"),
        Text("💰 Total P&L  ▲ +213.4 pips", style="bold bright_green"),
        Text("q quit  s scan  b bias  ? help", style="dim white"),
    )

    return Panel(
        grid,
        border_style="#30363d",
        box=box.SIMPLE,
        style="on #0d1117",
        padding=(0, 1),
    )


# ── Render ────────────────────────────────────────────────────────────────────

def render():
    console.clear()

    # ── Header ────────────────────────────────────────────────────
    console.print(make_floor_header())

    # ── Row 1: 3 analyst desks (compact) ──────────────────────────
    row1 = Columns(
        [
            make_agent_desk("chart", compact=False),
            make_agent_desk("bias",  compact=False),
            make_agent_desk("news",  compact=False),
        ],
        equal=True,
        expand=True,
    )
    console.print(row1)

    # ── Row 2: Boardroom (full width) ─────────────────────────────
    console.print(make_boardroom())

    # ── Row 3: Risk + Signal card + Notifier ──────────────────────
    row3 = Columns(
        [
            make_agent_desk("risk",     compact=False),
            make_signal_card(),
            make_agent_desk("notifier", compact=False),
        ],
        equal=True,
        expand=True,
    )
    console.print(row3)

    # ── Row 4: Comms feed full width ──────────────────────────────
    console.print(make_comms())

    # ── Footer ────────────────────────────────────────────────────
    console.print(make_footer())


if __name__ == "__main__":
    render()
