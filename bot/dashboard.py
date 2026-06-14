"""
SmartAgentTrade — WAR ROOM Dashboard
Rich terminal dashboard แบบ mock data เพื่อแสดง UI design
"""

from datetime import datetime
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.align import Align
from rich.rule import Rule
from rich import box
from rich.padding import Padding
from rich.progress import BarColumn, Progress, TextColumn

console = Console()

# ── Mock data ────────────────────────────────────────────────────────────────

MOCK_PRICE  = 3341.85
MOCK_CHANGE = +4.20
MOCK_SESSION = "🇬🇧 LONDON"
MOCK_TIME    = "20:47 ICT"
MOCK_DATE    = "Sat 14 Jun 2026"

AGENTS = [
    ("Chart Analyst",  "READY",   "OB+BOS detect ✓  |  M5/M15 confluence ✓",  "green"),
    ("Bias Analyst",   "CACHED",  "H4 bias: BULLISH  |  Cache 48 min",         "cyan"),
    ("News Scout",     "CLEAR",   "No high-impact within 30 min",              "green"),
    ("Risk Manager",   "STANDBY", "Streak 1/3  |  Daily -0.4%  |  No VETO",   "yellow"),
    ("Supervisor",     "IDLE",    "Waiting for 2/3 votes  |  Last: 14:30",     "dim white"),
]

PENDING_SIGNAL = {
    "signal":     "BUY",
    "setup":      "Bullish OB + Liquidity Sweep",
    "stars":      "★★★★☆",
    "score":      82,
    "confidence": 78,
    "entry":      "3338.50 – 3340.00",
    "sl":         "3332.10",
    "tp":         "3355.80",
    "rr":         "2.38",
    "lot":        "0.04",
    "session":    "LONDON",
    "votes":      {"chart": True, "bias": True, "news": True},
}

TRADES = [
    ("#12", "14:30", "BUY",  "Bullish OB",   "★★★★☆", "✅ WIN",  "+18.5"),
    ("#11", "10:15", "SELL", "Bearish OB",   "★★★☆☆", "❌ LOSS", "-9.2"),
    ("#10", "08:00", "BUY",  "CHoCH Sweep",  "★★★★★", "✅ WIN",  "+31.0"),
    ("#9",  "YEST",  "SELL", "BOS Retest",   "★★★☆☆", "↔️ BE",   "0.0"),
    ("#8",  "YEST",  "BUY",  "Bullish OB",   "★★★★☆", "✅ WIN",  "+22.4"),
]

STATS = {
    "total_signals": 47,
    "confirmed":     18,
    "skipped":       29,
    "wins":          12,
    "losses":        5,
    "be":            1,
    "win_rate":      70.6,
    "total_pips":    +213.4,
    "profit_factor": 2.31,
    "best_setup":    "LONDON BUY Bullish OB",
}

EVENT_FEED = [
    ("20:47", "INFO",  "Scanner triggered — London session"),
    ("20:47", "SMC",   "M5 Bullish OB @ 3338-3340 detected"),
    ("20:47", "SMC",   "Liquidity sweep below 3334.20 confirmed"),
    ("20:46", "AGENT", "Chart Analyst → VOTE YES  (score 82, conf 78%)"),
    ("20:46", "AGENT", "Bias Analyst  → VOTE YES  (H4 bullish, cached)"),
    ("20:46", "AGENT", "News Scout    → VOTE YES  (no news risk)"),
    ("20:46", "SUPER", "Supervisor → APPROVED 3/3  |  RR 2.38  |  Lot 0.04"),
    ("20:45", "INFO",  "Signal sent to Telegram — waiting user confirm"),
    ("14:33", "TRADE", "Trade #12 confirmed by user — BUY 3341.20"),
    ("14:55", "TRADE", "Trade #12 closed TP — +18.5 pips  ✅"),
]


# ── Panels ────────────────────────────────────────────────────────────────────

def make_header() -> Panel:
    change_color = "bright_green" if MOCK_CHANGE >= 0 else "bright_red"
    change_arrow = "▲" if MOCK_CHANGE >= 0 else "▼"

    title = Text()
    title.append("  ⚔️  SMART AGENT TRADE  ", style="bold white on #1a1a2e")
    title.append("WAR ROOM", style="bold yellow on #1a1a2e")

    price_text = Text()
    price_text.append(f"  XAUUSD  ", style="bold white")
    price_text.append(f"{MOCK_PRICE:.2f}", style=f"bold {change_color}")
    price_text.append(f"  {change_arrow} {MOCK_CHANGE:+.2f}", style=change_color)
    price_text.append(f"   |   {MOCK_SESSION}   |   {MOCK_TIME}  {MOCK_DATE}  ",
                      style="dim white")

    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    grid.add_row(title, price_text)

    return Panel(grid, style="on #0d1117", box=box.HEAVY, padding=(0, 1))


def make_agent_panel() -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold #58a6ff",
        style="on #0d1117",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("AGENT",  style="bold white",  no_wrap=True, width=16)
    table.add_column("STATUS", justify="center",     width=10)
    table.add_column("INFO",   style="dim white")

    STATUS_STYLES = {
        "READY":   ("◉ READY",   "bold bright_green"),
        "CACHED":  ("◈ CACHED",  "bold cyan"),
        "CLEAR":   ("◉ CLEAR",   "bold bright_green"),
        "STANDBY": ("◌ STANDBY", "bold yellow"),
        "IDLE":    ("○ IDLE",    "dim white"),
        "VOTING":  ("◎ VOTING",  "bold magenta"),
        "ERROR":   ("✗ ERROR",   "bold red"),
    }

    for name, status, info, _ in AGENTS:
        label, style = STATUS_STYLES.get(status, (status, "white"))
        table.add_row(name, Text(label, style=style), info)

    return Panel(
        table,
        title="[bold #58a6ff]◈ AGENT STATUS[/]",
        border_style="#30363d",
        box=box.ROUNDED,
        style="on #0d1117",
    )


def make_signal_panel() -> Panel:
    p = PENDING_SIGNAL

    vote_icons = {k: "[bold green]YES[/]" if v else "[bold red]NO[/]"
                  for k, v in p["votes"].items()}

    grid = Table.grid(padding=(0, 2))
    grid.add_column(min_width=22, style="dim white")
    grid.add_column(style="bold white")

    signal_color = "bright_green" if p["signal"] == "BUY" else "bright_red"

    grid.add_row(
        Text("SIGNAL", style="dim white"),
        Text(f"▶  {p['signal']}", style=f"bold {signal_color}") ,
    )
    grid.add_row("SETUP",       p["setup"])
    grid.add_row("STARS",       Text(p["stars"], style="yellow"))
    grid.add_row("SCORE / CONF", f"{p['score']}  /  {p['confidence']}%")
    grid.add_row("ENTRY ZONE",  Text(p["entry"], style="bold cyan"))
    grid.add_row("STOP LOSS",   Text(p["sl"],    style="bold red"))
    grid.add_row("TAKE PROFIT", Text(p["tp"],    style="bold green"))
    grid.add_row("R:R",         Text(p["rr"],    style="bold yellow"))
    grid.add_row("LOT",         f"{p['lot']}  (risk ~1.2%)")
    grid.add_row("SESSION",     p["session"])
    grid.add_row(
        "VOTES",
        Text.from_markup(
            f"Chart {vote_icons['chart']}  Bias {vote_icons['bias']}  News {vote_icons['news']}"
        ),
    )

    footer = Text("\n  ⚡ Telegram alert sent — awaiting [CONFIRM] / [SKIP]",
                  style="bold yellow")

    content = Table.grid()
    content.add_row(grid)
    content.add_row(footer)

    return Panel(
        content,
        title="[bold yellow]⚡ PENDING SIGNAL — AWAITING CONFIRMATION[/]",
        border_style="yellow",
        box=box.ROUNDED,
        style="on #0d1117",
    )


def make_stats_panel() -> Panel:
    s = STATS

    wr_color   = "bright_green" if s["win_rate"] >= 60 else "yellow" if s["win_rate"] >= 50 else "red"
    pnl_color  = "bright_green" if s["total_pips"] >= 0 else "bright_red"
    pnl_arrow  = "▲" if s["total_pips"] >= 0 else "▼"
    pf_color   = "bright_green" if (s["profit_factor"] or 0) >= 1.5 else "yellow"

    # Win rate progress bar
    progress = Progress(
        TextColumn("[dim white]WR"),
        BarColumn(bar_width=18, style="green", complete_style="bright_green"),
        TextColumn(f"[bold {wr_color}]{s['win_rate']}%"),
        expand=False,
    )
    task = progress.add_task("", total=100, completed=s["win_rate"])

    grid = Table.grid(padding=(0, 2), expand=True)
    grid.add_column(justify="left",  style="dim white", min_width=16)
    grid.add_column(justify="right", style="bold white")

    grid.add_row("Total Signals",  f"{s['total_signals']}")
    grid.add_row("Confirmed",       f"[white]{s['confirmed']}[/]  (skip {s['skipped']})")
    grid.add_row("W / L / BE",
                 f"[green]{s['wins']}[/] / [red]{s['losses']}[/] / [dim]{s['be']}[/]")
    grid.add_row("", progress)
    grid.add_row("Total P&L",
                 Text(f"{pnl_arrow} {s['total_pips']:+.1f} pips", style=f"bold {pnl_color}"))
    grid.add_row("Profit Factor",
                 Text(str(s["profit_factor"]), style=f"bold {pf_color}"))
    grid.add_row("Best Setup",     f"[cyan]{s['best_setup']}[/]")

    return Panel(
        grid,
        title="[bold #58a6ff]📊 PERFORMANCE[/]",
        border_style="#30363d",
        box=box.ROUNDED,
        style="on #0d1117",
    )


def make_trade_panel() -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold #58a6ff",
        style="on #0d1117",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#",       width=4,  style="dim white")
    table.add_column("TIME",    width=6,  style="dim white")
    table.add_column("DIR",     width=5,  justify="center")
    table.add_column("SETUP",   style="white")
    table.add_column("STARS",   width=7,  style="yellow")
    table.add_column("RESULT",  width=10, justify="center")
    table.add_column("PIPS",    width=8,  justify="right")

    for tid, t, direction, setup, stars, result, pips in TRADES:
        dir_color = "bright_green" if direction == "BUY" else "bright_red"
        pips_val  = float(pips)
        pips_color = "bright_green" if pips_val > 0 else "bright_red" if pips_val < 0 else "dim white"

        table.add_row(
            tid, t,
            Text(direction, style=f"bold {dir_color}"),
            setup, stars, result,
            Text(f"{pips_val:+.1f}", style=f"bold {pips_color}"),
        )

    return Panel(
        table,
        title="[bold #58a6ff]📋 RECENT TRADES[/]",
        border_style="#30363d",
        box=box.ROUNDED,
        style="on #0d1117",
    )


def make_feed_panel() -> Panel:
    TYPE_STYLE = {
        "INFO":  ("dim white",         "·"),
        "SMC":   ("bold cyan",         "◈"),
        "AGENT": ("bold #a5d6ff",      "▷"),
        "SUPER": ("bold yellow",       "★"),
        "TRADE": ("bold bright_green", "✦"),
        "WARN":  ("bold red",          "⚠"),
    }

    lines = Text()
    for ts, etype, msg in EVENT_FEED:
        style, icon = TYPE_STYLE.get(etype, ("white", "·"))
        lines.append(f"  {ts}  ", style="dim white")
        lines.append(f"{icon} {etype:<6}", style=style)
        lines.append(f"  {msg}\n", style="white" if etype in ("SUPER","TRADE") else "dim white")

    return Panel(
        lines,
        title="[bold #58a6ff]📡 LIVE FEED[/]",
        border_style="#30363d",
        box=box.ROUNDED,
        style="on #0d1117",
    )


def make_session_bar() -> Table:
    sessions = [
        ("SYDNEY",   "00:00–09:00", False),
        ("TOKYO",    "07:00–16:00", False),
        ("LONDON",   "14:00–23:00", True),
        ("NEW YORK", "19:00–04:00", False),
    ]

    grid = Table.grid(expand=True, padding=(0, 2))
    for name, hours, active in sessions:
        if active:
            grid.add_column(justify="center", style="bold yellow on #1a2a0a")
        else:
            grid.add_column(justify="center", style="dim white")

    row_names = []
    row_hours = []
    for name, hours, active in sessions:
        indicator = f"◉ {name}" if active else f"○ {name}"
        row_names.append(Text(indicator, style="bold yellow" if active else "dim white"))
        row_hours.append(Text(hours, style="yellow" if active else "dim white"))

    grid.add_row(*row_names)
    grid.add_row(*row_hours)

    return Panel(
        grid,
        title="[bold #58a6ff]🕐 SESSION CLOCK  (ICT UTC+7)[/]",
        border_style="#30363d",
        box=box.ROUNDED,
        style="on #0d1117",
        padding=(0, 1),
    )


# ── Render ────────────────────────────────────────────────────────────────────

def render():
    console.clear()

    # Header
    console.print(make_header())

    # Session bar
    console.print(make_session_bar())

    # Main 3-column: Agents | Signal | Stats
    cols = Columns(
        [make_agent_panel(), make_signal_panel(), make_stats_panel()],
        equal=False,
        expand=True,
    )
    console.print(cols)

    # Bottom 2-column: Recent Trades | Live Feed
    bot_cols = Columns(
        [make_trade_panel(), make_feed_panel()],
        equal=True,
        expand=True,
    )
    console.print(bot_cols)

    # Footer
    footer = Text(
        "  q quit   s scan   b bias   n news   p pause/resume   ? help  ",
        style="bold black on #58a6ff",
        justify="center",
    )
    console.print(Panel(footer, style="on #0d1117", box=box.SIMPLE, padding=0))


if __name__ == "__main__":
    render()
