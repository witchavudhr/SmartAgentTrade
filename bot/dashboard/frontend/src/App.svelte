<script>
  import { onMount, onDestroy } from 'svelte';

  // ── SVG Knight sprites (exact from original) ──────────────────────────────
  const C = {
    arthur: `<svg width="38" height="60" viewBox="0 0 38 60"><polygon points="5,15 8,8 12,13 16,6 19,11 22,6 26,13 30,8 33,15" fill="#D4A017"/><rect x="10" y="13" width="18" height="4" rx="1" fill="#B8860B"/><ellipse cx="19" cy="25" rx="10" ry="11" fill="#F0C090"/><circle cx="15" cy="24" r="1.5" fill="#2C1A08"/><circle cx="23" cy="24" r="1.5" fill="#2C1A08"/><circle cx="14.5" cy="23.5" r=".5" fill="#fff"/><circle cx="22.5" cy="23.5" r=".5" fill="#fff"/><path d="M15 30 Q19 33 23 30" fill="none" stroke="#9B6040" stroke-width="1.1" stroke-linecap="round"/><rect x="11" y="36" width="16" height="5" rx="2" fill="#D4A017"/><rect x="9" y="39" width="20" height="13" rx="3" fill="#1565C0"/><rect x="17" y="39" width="4" height="13" fill="#D4A017"/><rect x="11" y="45" width="16" height="2.5" fill="#D4A017"/><rect x="5" y="38" width="9" height="5" rx="2" fill="#D4A017"/><rect x="24" y="38" width="9" height="5" rx="2" fill="#D4A017"/><rect x="11" y="52" width="7" height="7" rx="2" fill="#0D47A1"/><rect x="20" y="52" width="7" height="7" rx="2" fill="#0D47A1"/><rect x="9" y="56" width="9" height="4" rx="2" fill="#050A20"/><rect x="20" y="56" width="9" height="4" rx="2" fill="#050A20"/></svg>`,
    dark: `<svg width="38" height="60" viewBox="0 0 38 60"><path d="M9 21 Q11 8 19 8 Q27 8 29 21Z" fill="#1A1A3E"/><ellipse cx="19" cy="25" rx="10" ry="11" fill="#2C2C54"/><rect x="12" y="20" width="14" height="8" rx="2" fill="#050514"/><line x1="13" y1="23" x2="25" y2="23" stroke="#7C4DFF" stroke-width="1.2" opacity=".9"/><polygon points="17,16 19,10 21,16" fill="#3A3A6E"/><polygon points="9,18 6,9 12,16" fill="#3A3A6E"/><polygon points="29,18 32,9 26,16" fill="#3A3A6E"/><rect x="9" y="36" width="20" height="14" rx="3" fill="#1A1A3E"/><rect x="3" y="35" width="10" height="6" rx="3" fill="#7C4DFF"/><rect x="25" y="35" width="10" height="6" rx="3" fill="#7C4DFF"/><rect x="11" y="50" width="7" height="8" rx="2" fill="#111130"/><rect x="20" y="50" width="7" height="8" rx="2" fill="#111130"/><rect x="9" y="55" width="9" height="4" rx="2" fill="#050514"/><rect x="20" y="55" width="9" height="4" rx="2" fill="#050514"/></svg>`,
    purple: `<svg width="38" height="60" viewBox="0 0 38 60"><ellipse cx="19" cy="24" rx="11" ry="12" fill="#6A1B9A"/><rect x="11" y="19" width="16" height="9" rx="2" fill="#0D001A"/><circle cx="14.5" cy="21" r="1.7" fill="#CE93D8" opacity=".9"/><circle cx="23.5" cy="21" r="1.7" fill="#CE93D8" opacity=".9"/><path d="M19 9 Q13 5 14 12" stroke="#E91E63" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M19 9 Q25 4 24 11" stroke="#FF5722" stroke-width="2" fill="none" stroke-linecap="round"/><rect x="9" y="36" width="20" height="14" rx="3" fill="#7B1FA2"/><rect x="3" y="35" width="10" height="7" rx="3" fill="#9C27B0"/><rect x="25" y="35" width="10" height="7" rx="3" fill="#9C27B0"/><rect x="11" y="50" width="7" height="8" rx="2" fill="#4A148C"/><rect x="20" y="50" width="7" height="8" rx="2" fill="#4A148C"/><rect x="9" y="55" width="9" height="4" rx="2" fill="#1A0030"/><rect x="20" y="55" width="9" height="4" rx="2" fill="#1A0030"/></svg>`,
    barb: `<svg width="38" height="60" viewBox="0 0 38 60"><path d="M8 21 Q6 9 10 5 Q14 2 17 8" fill="#C62828"/><path d="M30 21 Q32 9 28 5 Q24 2 21 8" fill="#B71C1C"/><path d="M13 9 Q19 4 25 9" fill="#D32F2F"/><ellipse cx="19" cy="25" rx="10" ry="11" fill="#D4926A"/><circle cx="15" cy="24" r="1.8" fill="#1A1A1A"/><circle cx="23" cy="24" r="1.8" fill="#1A1A1A"/><path d="M8 37 Q19 32 30 37 L30 43 Q19 39 8 43Z" fill="#6D4C41"/><rect x="10" y="41" width="18" height="11" rx="3" fill="#5D4037"/><rect x="4" y="37" width="9" height="6" rx="3" fill="#4E342E"/><rect x="25" y="37" width="9" height="6" rx="3" fill="#4E342E"/><rect x="11" y="52" width="7" height="7" rx="2" fill="#4E342E"/><rect x="20" y="52" width="7" height="7" rx="2" fill="#4E342E"/><rect x="9" y="56" width="9" height="4" rx="2" fill="#2C1A10"/><rect x="20" y="56" width="9" height="4" rx="2" fill="#2C1A10"/></svg>`,
    gold: `<svg width="38" height="60" viewBox="0 0 38 60"><ellipse cx="19" cy="23" rx="11" ry="12" fill="#F9A825"/><rect x="12" y="18" width="14" height="8" rx="2" fill="#0D0800"/><rect x="16" y="9" width="6" height="10" rx="2" fill="#F57F17"/><path d="M19 5 Q14 2 15 9" stroke="#FFB300" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M19 5 Q24 2 23 9" stroke="#FF9800" stroke-width="2" fill="none" stroke-linecap="round"/><rect x="9" y="35" width="20" height="14" rx="3" fill="#F9A825"/><rect x="17" y="35" width="4" height="14" fill="#FFD54F"/><rect x="11" y="42" width="16" height="3" fill="#FFD54F"/><rect x="4" y="35" width="9" height="6" rx="3" fill="#F57F17"/><rect x="25" y="35" width="9" height="6" rx="3" fill="#F57F17"/><rect x="11" y="49" width="7" height="9" rx="2" fill="#F57F17"/><rect x="20" y="49" width="7" height="9" rx="2" fill="#F57F17"/><rect x="9" y="55" width="9" height="4" rx="2" fill="#BF360C"/><rect x="20" y="55" width="9" height="4" rx="2" fill="#BF360C"/></svg>`,
  };

  const AGENTS_DEF = [
    { id:'a0', char:'arthur', name:'King Arthur',   seat:{l:229,t:2},   patrol:[{l:222,t:4},{l:240,t:3},{l:218,t:12}],  idle:['Watching the realm...','Awaiting intel...'] },
    { id:'a1', char:'dark',   name:'Chart Analyst', seat:{l:408,t:130}, patrol:[{l:448,t:58},{l:454,t:122},{l:449,t:65}], idle:['M5 OB forming...','BOS confirmed...'] },
    { id:'a2', char:'purple', name:'Bias Analyst',  seat:{l:347,t:263}, patrol:[{l:438,t:267},{l:453,t:296},{l:440,t:274}],idle:['H4 in demand...','Daily bias bullish...'] },
    { id:'a3', char:'barb',   name:'News Scout',    seat:{l:53,t:263},  patrol:[{l:4,t:267},{l:18,t:296},{l:6,t:274}],   idle:['Calendar quiet...','No impact 4h...'] },
    { id:'a4', char:'gold',   name:'Risk Manager',  seat:{l:30,t:130},  patrol:[{l:3,t:58},{l:6,t:122},{l:4,t:65}],      idle:['2% rule holding...','RR calc ready...'] },
  ];

  // ── Reactive state ────────────────────────────────────────────────────────
  let statusText = 'Knights on patrol...';
  let vbnText = '';
  let vbnVisible = false;
  let connected = false;
  let scanPhase = 'idle'; // idle|gathering|scanning|voting|approved|rejected

  // Knight state: pos, flip, moving, bubble
  let knights = AGENTS_DEF.map((a, i) => ({
    ...a,
    pos: { ...a.patrol[0] },
    pIdx: 0,
    moving: false,
    flip: false,
    bubble: '',
    bubbleOn: false,
  }));

  let scanLog = [];  // populated from server on init (real DB data)

  let signal = {
    direction:'—', setup_type:'—', stars:'',
    entry:0, sl:0, tp:0, lot:0, rr:0,
    pnl:0, time:'—', approved:false,
    votes:{chart:false,bias:false,news:false,risk:false},
    reason:'Waiting for first scan…',
  };

  let stats = { today_pnl:0, open_pnl:0, win_rate:0, wins:0, losses:0, pending:0, best_trade:0, trades:[], period:'today' };
  let activePeriod = 'today';
  const PERIODS = [
    { key:'today', label:'Today' },
    { key:'week',  label:'Week' },
    { key:'month', label:'Month' },
    { key:'year',  label:'Year' },
    { key:'all',   label:'All' },
  ];

  async function switchPeriod(p) {
    activePeriod = p;
    try {
      const res = await fetch(`/api/stats?period=${p}`);
      const data = await res.json();
      if (!data.error) stats = data;
    } catch(e) {}
  }

  // Ask panel
  let askInput = '';
  let askLoading = false;
  let messages = [
    { role:'ai', text:'สวัสดีครับ — War Room พร้อมแล้ว รอผลจาก bot…' },
  ];

  // Countdown
  let csec = 30;
  let countdownTimer;

  // ── Session label ─────────────────────────────────────────────────────────
  function sessionLabel() {
    const h = new Date().getHours();
    if (h >= 8 && h < 17) return 'London';
    if (h >= 13 && h < 22) return 'New York';
    return 'Off-hours';
  }
  function nowStr() {
    const n = new Date();
    return `${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}`;
  }

  // ── Knight helpers ────────────────────────────────────────────────────────
  function showBubble(idx, txt, dur = 2500) {
    knights[idx] = { ...knights[idx], bubble: txt, bubbleOn: true };
    knights = [...knights];
    if (dur) setTimeout(() => {
      knights[idx] = { ...knights[idx], bubbleOn: false };
      knights = [...knights];
    }, dur);
  }

  function moveKnight(idx, l, t, moving = true) {
    const flip = l < (knights[idx].pos.l - 15);
    knights[idx] = { ...knights[idx], pos: {l, t}, moving, flip };
    knights = [...knights];
  }

  // ── Patrol ────────────────────────────────────────────────────────────────
  let patrolTimer;
  function startPatrol() {
    clearInterval(patrolTimer);
    patrolTimer = setInterval(() => {
      if (scanPhase !== 'idle') return;
      knights.forEach((k, i) => {
        if (Math.random() < 0.55) {
          const nextIdx = (k.pIdx + 1) % AGENTS_DEF[i].patrol.length;
          const wp = AGENTS_DEF[i].patrol[nextIdx];
          knights[i] = { ...k, pIdx: nextIdx };
          moveKnight(i, wp.l, wp.t, true);
        }
      });
      if (Math.random() < 0.38) {
        const i = Math.floor(Math.random() * knights.length);
        const idleLines = AGENTS_DEF[i].idle;
        showBubble(i, idleLines[Math.floor(Math.random() * idleLines.length)], 2400);
      }
    }, 3000);
  }

  function startCountdown() {
    // Auto-scan disabled — manual only via Scan Now button
    statusText = 'Watching the market...';
  }

  // ── Scan sequence ─────────────────────────────────────────────────────────
  let scanRunning = false;

  async function triggerScan() {
    if (scanRunning) return;
    // POST to server — server signals bot via poll, bot runs real supervisor.run()
    // Animation and result come back via WebSocket (scan_phase + signal messages)
    try {
      const res = await fetch('/api/scan', { method: 'POST' });
      const d = await res.json();
      if (!d.ok) return; // already running
    } catch(e) { return; }
    scanRunning = true;
    clearInterval(patrolTimer);
    statusText = 'Sending knights to the field...';
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // ── WebSocket ─────────────────────────────────────────────────────────────
  let ws;
  function initWs() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => { connected = true; };
    ws.onclose = () => { connected = false; setTimeout(initWs, 3000); };
    const AGENT_IDX = { supervisor:0, chart_analyst:1, bias_analyst:2, news_scout:3, risk_manager:4 };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'init') {
        if (msg.signal)   signal  = msg.signal;
        if (msg.scan_log) scanLog = msg.scan_log;
        if (msg.stats)    stats   = { ...stats, ...msg.stats };
      } else if (msg.type === 'signal') {
        signal = msg.data;
      } else if (msg.type === 'scan_log') {
        scanLog = [msg.data, ...scanLog].slice(0, 9);
      } else if (msg.type === 'stats') {
        stats = { ...stats, ...msg.data };
      } else if (msg.type === 'scan_phase') {
        const phase = msg.phase;
        scanPhase = phase;
        if (phase === 'gathering') {
          statusText = 'Knights to the round table...';
          clearInterval(patrolTimer);
          scanRunning = true;
          knights.forEach((_, i) => { const s = AGENTS_DEF[i].seat; moveKnight(i, s.l, s.t, true); });
        } else if (phase === 'scanning') {
          statusText = 'Scanning XAUUSD...';
          knights.forEach((_, i) => { knights[i] = { ...knights[i], moving: false }; });
          knights = [...knights];
        } else if (phase === 'approved') {
          statusText = 'APPROVED — sending alert';
          vbnText = `APPROVED — ${signal.direction} XAUUSD  |  Entry ${signal.entry}  |  TP ${signal.tp}`;
          vbnVisible = true;
          setTimeout(() => { vbnVisible = false; }, 3400);
        } else if (phase === 'rejected') {
          statusText = 'No setup found';
        } else if (phase === 'idle') {
          statusText = 'Watching the market...';
          scanRunning = false;
          knights.forEach((_, i) => { const p = AGENTS_DEF[i].patrol[0]; moveKnight(i, p.l, p.t, true); knights[i] = { ...knights[i], pIdx: 0 }; });
          knights = [...knights];
          startPatrol();
        }
      } else if (msg.type === 'agent_bubble') {
        const idx = AGENT_IDX[msg.agent];
        if (idx !== undefined) showBubble(idx, msg.text, 3000);
      }
    };
  }

  // ── Ask agents ────────────────────────────────────────────────────────────
  async function sendAsk() {
    const q = askInput.trim();
    if (!q || askLoading) return;
    askInput = '';
    askLoading = true;
    messages = [...messages, { role:'user', text:q }];
    try {
      const res = await fetch('/api/ask', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:q}) });
      const d = await res.json();
      messages = [...messages, { role:'ai', text:d.response }];
    } catch(e) {
      messages = [...messages, { role:'ai', text:'[ไม่สามารถเชื่อมต่อได้]' }];
    }
    askLoading = false;
  }
  function askKey(e) { if (e.key === 'Enter') sendAsk(); }
  function fillAsk(t) { askInput = t; }

  // ── Equity bars ───────────────────────────────────────────────────────────
  $: tradeMax = Math.max(...(stats.trades || []).map(t => Math.abs(t.p ?? t.pnl ?? 0)), 1);

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  onMount(() => {
    initWs();
    startPatrol();
    startCountdown();
    // show idle bubbles on load
    AGENTS_DEF.forEach((a, i) => setTimeout(() => showBubble(i, a.idle[0], 2400), i * 500 + 600));
  });
  onDestroy(() => {
    clearInterval(patrolTimer);
    clearInterval(countdownTimer);
    if (ws) ws.close();
  });

  function fmtPnl(v) { return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2); }
</script>

<div class="app">
  <!-- ROW 1: room + log -->
  <div class="row1">
    <!-- War Room -->
    <div class="vr" id="vr">
      <div class="bg"></div>
      <div class="wall"></div>
      <!-- Banner -->
      <div class="ban">
        <div class="ban-p"></div>
        <div class="ban-f">
          <svg width="30" height="30" viewBox="0 0 24 24">
            <polygon points="12,2 15,9 22,9 17,13 19,20 12,16 5,20 7,13 2,9 9,9" fill="#D4A017"/>
          </svg>
        </div>
        <div class="ban-p"></div>
      </div>
      <!-- Torches -->
      <div class="tr" style="left:32px;top:22px"><div class="tg"></div><div class="tf"></div><div class="ts"></div></div>
      <div class="tr" style="right:32px;top:22px"><div class="tg"></div><div class="tf"></div><div class="ts"></div></div>
      <div class="tr" style="left:32px;bottom:10px"><div class="tg"></div><div class="tf"></div><div class="ts"></div></div>
      <div class="tr" style="right:32px;bottom:10px"><div class="tg"></div><div class="tf"></div><div class="ts"></div></div>
      <!-- Round Table SVG -->
      <svg style="position:absolute;left:39px;top:26px;z-index:3" width="420" height="306" viewBox="0 0 220 160">
        <ellipse cx="110" cy="92" rx="112" ry="77" fill="rgba(0,0,0,.45)"/>
        <ellipse cx="110" cy="82" rx="110" ry="74" fill="#3A1E08"/>
        <ellipse cx="110" cy="82" rx="110" ry="74" fill="none" stroke="#5A3010" stroke-width="8"/>
        <ellipse cx="110" cy="82" rx="98" ry="62" fill="#472A0C"/>
        <ellipse cx="110" cy="82" rx="84" ry="50" fill="#57361A"/>
        <ellipse cx="110" cy="82" rx="68" ry="38" fill="none" stroke="#4A2D10" stroke-width=".8" opacity=".5"/>
        <ellipse cx="110" cy="82" rx="50" ry="27" fill="none" stroke="#4A2D10" stroke-width=".6" opacity=".4"/>
        <circle cx="110" cy="82" r="30" fill="none" stroke="#B8900A" stroke-width="1.6" opacity=".55"/>
        <polygon points="110,53 117,72 137,72 122,84 128,103 110,91 92,103 98,84 83,72 103,72" fill="none" stroke="#C4980E" stroke-width="1.6" opacity=".65"/>
        <!-- Seat markers -->
        <rect x="94" y="0" width="32" height="11" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
        <rect x="200" y="52" width="11" height="32" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
        <rect x="171" y="140" width="32" height="11" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
        <rect x="17" y="140" width="32" height="11" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
        <rect x="9" y="52" width="11" height="32" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
        <!-- 5 Swords -->
        <g transform="translate(110,82) rotate(-90)"><circle cx="-8" cy="0" r="3" fill="#D4A017"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#7B4920"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#D4A017"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#C8D4E0"/><polygon points="50,-1 50,1 56,0" fill="#D8E4F0"/></g>
        <g transform="translate(110,82) rotate(-18)"><circle cx="-8" cy="0" r="3" fill="#6030C0"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#0D0020"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#6030C0"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#6A6A90"/><polygon points="50,-1 50,1 56,0" fill="#8080B0"/></g>
        <g transform="translate(110,82) rotate(54)"><circle cx="-8" cy="0" r="3" fill="#9C27B0"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#380048"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#9C27B0"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#BCCCD8"/><polygon points="50,-1 50,1 56,0" fill="#CCDDE8"/></g>
        <g transform="translate(110,82) rotate(126)"><circle cx="-8" cy="0" r="3" fill="#7B2A0A"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#3A1500"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#C62020"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#9A9A9A"/><polygon points="50,-1 50,1 56,0" fill="#B0B0B0"/></g>
        <g transform="translate(110,82) rotate(198)"><circle cx="-8" cy="0" r="3" fill="#F57F17"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#6A3600"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#F9A825"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#D4C040"/><polygon points="50,-1 50,1 56,0" fill="#E0D050"/></g>
        <!-- Candles on table -->
        <rect x="128" y="66" width="4" height="10" rx="1" fill="#EDE0C4"/><ellipse cx="130" cy="64.5" rx="3" ry="3.8" fill="#FF8F00" opacity=".8"/>
        <rect x="90" y="68" width="4" height="9" rx="1" fill="#EDE0C4"/><ellipse cx="92" cy="66.5" rx="3" ry="3.5" fill="#FF8F00" opacity=".8"/>
      </svg>

      <!-- APPROVED banner overlay -->
      <div class="vbn" class:on={vbnVisible}>{vbnText}</div>

      <!-- Knights -->
      {#each knights as k, i}
        <div
          class="kn"
          class:mv={k.moving}
          class:fl={k.flip}
          style="left:{k.pos.l}px;top:{k.pos.t}px"
          on:click={() => showBubble(i, AGENTS_DEF[i].idle[Math.floor(Math.random()*2)], 2800)}
        >
          <div class="ki">{@html C[k.char]}</div>
          <div class="bbl" class:on={k.bubbleOn}>{k.bubble}</div>
          <div class="ntg">{k.name}</div>
        </div>
      {/each}
    </div>

    <!-- Scan Log -->
    <div class="log">
      <div class="lh">
        <span>Scan log</span>
        <span style="font-size:9px;color:#9ca3af">{scanLog.length}</span>
      </div>
      <div class="lb">
        {#each scanLog as l}
          <div class="li" class:li-ok={l.c==='ok'||l.result==='ok'} class:li-bl={l.c==='bl'||l.result==='bl'} class:li-no={l.c==='no'||l.result==='no'||(!l.c&&!l.result)}>
            <div class="lt">{l.t ?? l.time}</div>
            <div class="ltx">{l.tx ?? l.text}</div>
            <div class="ls">{l.s ?? l.sub}</div>
          </div>
        {/each}
      </div>
    </div>
  </div>

  <!-- ABAR: status + scan button -->
  <div class="abar">
    <span id="sbl" style="font-size:10px;color:#6b7280">{statusText}</span>
    <div style="display:flex;align-items:center;gap:10px">
      <div class="live-dot" class:live={connected}></div>
      <button class="scb" disabled={scanRunning} on:click={triggerScan}>
        {scanRunning ? '⏳ Scanning...' : '⚡ Scan now'}
      </button>
    </div>
  </div>

  <!-- ROW 2: signal + ask -->
  <div class="row2">
    <!-- Latest Signal -->
    <div class="mc">
      <div class="ph2">
        <span>Latest signal</span>
        <span style="color:#4ade80;font-size:9px">{fmtPnl(signal?.pnl ?? 0)} open</span>
      </div>
      <div class="srb">
        <div class="sc2">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
            <div class="sd" style="color:{!signal?.approved ? '#6b7280' : signal?.direction==='BUY' ? '#4ade80' : '#f87171'}">
              {signal?.direction === 'BUY' ? '▲' : '▼'} {signal?.direction} XAUUSD
            </div>
            <span style="font-size:9px;background:#1c1c25;border:0.5px solid #2d2d3a;padding:1px 6px;border-radius:99px;color:#9ca3af">
              {signal?.setup_type} {signal?.stars}
            </span>
          </div>
          <div style="font-size:9.5px;color:#6b7280;margin-bottom:5px">{signal?.reason ?? ''}</div>
          <div class="lvs">
            <div class="lv"><div class="ll">Entry</div><div class="lv2">{signal?.entry}</div></div>
            <div class="lv"><div class="ll">SL</div><div class="lv2" style="color:#dc2626">{signal?.sl}</div></div>
            <div class="lv"><div class="ll">TP</div><div class="lv2" style="color:#15803d">{signal?.tp}</div></div>
            <div class="lv"><div class="ll">Lot</div><div class="lv2">{signal?.lot}</div></div>
          </div>
          <div style="margin-top:4px;font-size:9px;color:#9ca3af">
            RR 1:{signal?.rr} · {signal?.time} · <span style="color:#15803d;font-weight:500">{fmtPnl(signal?.pnl??0)}</span>
          </div>
        </div>
        <div class="vch">
          {#each Object.entries(signal?.votes ?? {}) as [k, v]}
            <div class="vc" class:vno={!v}>{v ? '✓' : '✗'} {k}</div>
          {/each}
          <div class="vc" class:vno={!signal?.approved} style="flex-basis:100%">
            👑 King Arthur: {signal?.approved ? 'APPROVED' : 'REJECTED'}
          </div>
        </div>
        {#if signal?.approved}
          <div style="margin-top:6px;padding:5px 8px;background:rgba(74,222,128,.08);border:0.5px solid rgba(74,222,128,.2);border-radius:8px;font-size:9.5px;color:#4ade80">
            ✔ {signal?.reason}
          </div>
        {/if}
      </div>
    </div>

    <!-- Ask Agents -->
    <div class="mc">
      <div class="ph2">
        <span>Ask agents</span>
        <span style="font-size:9px;color:#9ca3af">Haiku 4.5</span>
      </div>
      <div class="qcs">
        <div class="qc" on:click={() => fillAsk('OB zone อยู่ที่ไหน?')}>OB zone?</div>
        <div class="qc" on:click={() => fillAsk('ควร close ไหม?')}>Close?</div>
        <div class="qc" on:click={() => fillAsk('bias วันนี้?')}>Bias?</div>
      </div>
      <div class="msgs" id="msgs">
        {#each messages as m}
          <div class="{m.role === 'user' ? 'mu' : 'mb'}">{m.text}</div>
        {/each}
        {#if askLoading}
          <div class="mb" style="color:#6b7280;font-style:italic">กำลังถามอัศวิน...</div>
        {/if}
      </div>
      <div class="cir">
        <input type="text" placeholder="ถาม XAUUSD..." bind:value={askInput} on:keydown={askKey}/>
        <button class="sbtn" on:click={sendAsk}>Send</button>
      </div>
    </div>
  </div>

  <!-- ROW 3: period tabs + stats -->
  <div class="row3hdr">
    {#each PERIODS as p}
      <button class="ptab" class:ptab-on={activePeriod===p.key} on:click={()=>switchPeriod(p.key)}>{p.label}</button>
    {/each}
  </div>
  <div class="row3">
    <div class="s3">
      <div class="s3l">P&L</div>
      <div class="s3v" style="color:{stats.today_pnl>=0?'#15803d':'#991b1b'}">{fmtPnl(stats.today_pnl)}</div>
      <div class="s3s">{fmtPnl(stats.open_pnl)} open</div>
    </div>
    <div class="s3">
      <div class="s3l">Win rate</div>
      <div class="s3v">{stats.win_rate}%</div>
      <div class="s3s">{stats.wins} of {stats.wins + stats.losses} closed</div>
    </div>
    <div class="s3">
      <div class="s3l">W / L</div>
      <div class="s3v"><span style="color:#15803d">{stats.wins}</span> / <span style="color:#991b1b">{stats.losses}</span></div>
      <div class="s3s">{stats.pending > 0 ? `+${stats.pending} pending` : '—'}</div>
    </div>
    <div class="s3">
      <div class="s3l">Best trade</div>
      <div class="s3v" style="color:#15803d">{fmtPnl(stats.best_trade ?? 0)}</div>
      <div class="s3s">{stats.best_setup || '—'}</div>
    </div>
  </div>

  <!-- ROW 4: equity bars -->
  <div class="row4">
    {#each (stats.trades ?? []) as t}
      {@const pval = t.p ?? t.pnl ?? 0}
      {@const h = Math.max(3, Math.round(Math.abs(pval) / tradeMax * 28))}
      <div style="display:flex;flex-direction:column;align-items:center;flex:1">
        <div class="bx" class:bw={t.r==='w'} class:bl2={t.r==='l'} class:bo={t.r==='o'} style="height:{h}px"></div>
      </div>
    {/each}
  </div>

  <!-- Footer -->
  <div class="foot">
    <span class="ft">Trade history · Jun {new Date().getDate()}</span>
    <span class="ft">{sessionLabel()} {nowStr()}</span>
  </div>
</div>

<style>
  :global(*){box-sizing:border-box;margin:0;padding:0}
  :global(body){background:#0d0d14;display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}

  .app{width:680px;border:0.5px solid rgba(255,255,255,.1);border-radius:12px;overflow:hidden;background:#111118}

  /* ROW 1 */
  .row1{display:flex;height:340px}
  .vr{position:relative;flex:1;overflow:hidden;border-right:0.5px solid rgba(255,255,255,.1)}
  .bg{position:absolute;inset:0;background:#0E0903}
  .bg::before{content:'';position:absolute;inset:0;background-image:repeating-linear-gradient(90deg,rgba(255,255,255,.01) 0 1px,transparent 1px 48px),repeating-linear-gradient(0deg,rgba(255,255,255,.01) 0 1px,transparent 1px 48px)}
  .wall{position:absolute;top:0;left:0;right:0;height:58px;background:#150A03;border-bottom:2px solid #291505}
  .wall::before{content:'';position:absolute;inset:0;background-image:repeating-linear-gradient(90deg,rgba(255,255,255,.015) 0 1px,transparent 1px 60px),repeating-linear-gradient(0deg,rgba(255,255,255,.015) 0 1px,transparent 1px 30px)}

  /* Banner */
  .ban{position:absolute;top:3px;left:50%;transform:translateX(-50%);display:flex;gap:9px;align-items:flex-start;z-index:5}
  .ban-p{width:3px;height:50px;background:#7B4E28}
  .ban-f{background:#6B0000;width:56px;height:55px;clip-path:polygon(0 0,100% 0,100% 75%,50% 100%,0 75%);display:flex;align-items:center;justify-content:center}

  /* Torches */
  .tr{position:absolute;width:12px;z-index:4}
  .tf{width:9px;height:14px;margin:0 auto;position:relative;animation:flk .42s alternate infinite ease-in-out}
  @keyframes flk{0%{transform:scaleX(1)}100%{transform:scaleX(.55) scaleY(.75)}}
  .tf::before{content:'';position:absolute;inset:0;background:#FF6D00;border-radius:50% 50% 25% 25%}
  .tf::after{content:'';position:absolute;top:2px;left:1px;right:1px;bottom:2px;background:#FFE082;border-radius:50%}
  .ts{width:4px;height:19px;background:#5D3A1A;margin:0 auto}
  .tg{position:absolute;top:-7px;left:50%;transform:translateX(-50%);width:42px;height:42px;background:radial-gradient(circle,rgba(255,140,10,.18) 0%,transparent 70%)}

  /* Knights */
  .kn{position:absolute;width:38px;z-index:12;cursor:pointer;transition:left 1.4s cubic-bezier(.3,.6,.4,.95),top 1.4s cubic-bezier(.3,.6,.4,.95)}
  .ki{display:block}
  .kn.mv .ki{animation:wb .28s steps(2) infinite}
  @keyframes wb{0%{transform:translateY(0)}50%{transform:translateY(-4px)}}
  .kn.fl .ki{transform:scaleX(-1)}
  .kn.mv.fl .ki{animation:wbf .28s steps(2) infinite}
  @keyframes wbf{0%{transform:scaleX(-1) translateY(0)}50%{transform:scaleX(-1) translateY(-4px)}}

  /* Speech bubble */
  .bbl{position:absolute;bottom:calc(100% + 4px);left:50%;transform:translateX(-50%);background:rgba(255,248,215,.97);color:#1A0E00;font-size:9px;line-height:1.45;padding:3px 8px;border-radius:5px;white-space:nowrap;border:0.5px solid rgba(160,100,20,.3);opacity:0;transition:opacity .3s;pointer-events:none;z-index:20;max-width:170px;white-space:normal;text-align:center}
  .bbl::after{content:'';position:absolute;top:100%;left:50%;transform:translateX(-50%);border:4px solid transparent;border-top-color:rgba(255,248,215,.97)}
  .bbl.on{opacity:1}
  .ntg{position:absolute;top:calc(100% + 2px);left:50%;transform:translateX(-50%);font-size:8px;color:rgba(255,200,80,.65);white-space:nowrap;text-shadow:0 1px 4px rgba(0,0,0,.95);pointer-events:none}

  /* APPROVED overlay */
  .vbn{position:absolute;top:48%;left:50%;transform:translate(-50%,-50%);background:rgba(6,3,0,.96);border:1px solid #D4A017;border-radius:8px;padding:11px 20px;text-align:center;z-index:40;color:#FFD54F;font-size:11px;font-weight:500;pointer-events:none;opacity:0;transition:opacity .4s;white-space:nowrap}
  .vbn.on{opacity:1}

  /* Scan log */
  .log{width:182px;flex-shrink:0;display:flex;flex-direction:column;overflow:hidden}
  .lh{padding:7px 9px;border-bottom:0.5px solid rgba(255,255,255,.08);font-size:10px;font-weight:500;color:#9ca3af;background:#18181f;display:flex;justify-content:space-between;flex-shrink:0}
  .lb{flex:1;overflow-y:auto;padding:5px 6px}
  .li{padding:3px 5px;margin-bottom:3px;border-left:2px solid;border-radius:0 3px 3px 0}
  .li-ok{background:rgba(59,109,17,.18);border-color:#4ade80}
  .li-no{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.12)}
  .li-bl{background:rgba(133,79,11,.2);border-color:#fbbf24}
  .lt{font-size:8.5px;color:#6b7280;font-family:monospace}
  .ltx{font-size:9.5px;font-weight:500;line-height:1.3;color:#e5e7eb}
  .ls{font-size:8.5px;color:#9ca3af;line-height:1.3}

  /* Action bar */
  .abar{display:flex;align-items:center;justify-content:space-between;padding:7px 14px;border-top:0.5px solid rgba(255,255,255,.08);border-bottom:0.5px solid rgba(255,255,255,.08);background:#18181f}
  .scb{padding:4px 13px;background:#C9A800;color:#0E0800;border:none;border-radius:4px;font-size:9.5px;font-weight:500;cursor:pointer}
  .scb:hover:not(:disabled){background:#E0C000}
  .scb:disabled{opacity:.35;cursor:default}
  .live-dot{width:7px;height:7px;border-radius:50%;background:#4b5563;transition:all .3s}
  .live-dot.live{background:#4ade80;box-shadow:0 0 6px #4ade80}

  /* ROW 2 */
  .row2{display:grid;grid-template-columns:1fr 1fr;border-bottom:0.5px solid rgba(255,255,255,.08)}
  .mc{display:flex;flex-direction:column;min-width:0}
  .mc:first-child{border-right:0.5px solid rgba(255,255,255,.08)}
  .ph2{padding:6px 10px;border-bottom:0.5px solid rgba(255,255,255,.08);font-size:10px;font-weight:500;color:#9ca3af;background:#18181f;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
  .srb{padding:8px 10px;overflow-y:auto;flex:1}
  .sc2{border:0.5px solid rgba(255,255,255,.1);border-radius:8px;padding:7px 9px;margin-bottom:5px;background:#1c1c25}
  .sd{font-size:14px;font-weight:500;display:flex;align-items:center;gap:5px}
  .lvs{display:grid;grid-template-columns:repeat(4,1fr);gap:2px;margin-top:5px}
  .lv{background:#18181f;border-radius:3px;padding:3px;text-align:center;border:0.5px solid rgba(255,255,255,.07)}
  .ll{font-size:8.5px;color:#6b7280}
  .lv2{font-size:9.5px;font-weight:500;font-family:monospace;color:#e5e7eb}
  .vch{display:flex;flex-wrap:wrap;gap:2px;margin-top:5px}
  .vc{flex:1;min-width:40px;padding:3px;text-align:center;border-radius:3px;font-size:8.5px;font-weight:500;background:rgba(74,222,128,.12);color:#4ade80;border:0.5px solid rgba(74,222,128,.3)}
  .vc.vno{background:rgba(248,113,113,.1);color:#f87171;border-color:rgba(248,113,113,.3)}

  /* Ask panel */
  .msgs{flex:1;overflow-y:auto;padding:7px;display:flex;flex-direction:column;gap:4px;height:130px}
  .mu{align-self:flex-end;background:#1e3a6e;color:#93c5fd;padding:4px 8px;border-radius:7px 7px 2px 7px;font-size:10px;max-width:90%;line-height:1.4}
  .mb{align-self:flex-start;background:#1c1c25;border:0.5px solid rgba(255,255,255,.1);padding:4px 8px;border-radius:7px 7px 7px 2px;font-size:10px;max-width:95%;line-height:1.5;color:#d1d5db}
  .qcs{padding:0 8px 4px;display:flex;gap:3px;flex-wrap:wrap;flex-shrink:0}
  .qc{font-size:9px;padding:2px 7px;border:0.5px solid rgba(255,255,255,.12);border-radius:99px;cursor:pointer;color:#9ca3af;background:#18181f}
  .qc:hover{background:#22222e}
  .cir{padding:5px 7px;border-top:0.5px solid rgba(255,255,255,.08);display:flex;gap:4px;flex-shrink:0}
  .cir input{flex:1;padding:4px 7px;font-size:10px;border:0.5px solid rgba(255,255,255,.15);border-radius:4px;outline:none;color:#e5e7eb;background:#18181f}
  .cir input:focus{border-color:#3b82f6}
  .sbtn{padding:4px 10px;background:#1d4ed8;color:#fff;border:none;border-radius:4px;font-size:10px;cursor:pointer}

  /* ROW 3 period tabs */
  .row3hdr{display:flex;gap:4px;padding:5px 10px 4px;background:#18181f;border-bottom:0.5px solid rgba(255,255,255,.06)}
  .ptab{padding:2px 9px;border-radius:10px;font-size:9px;font-weight:500;cursor:pointer;
        border:0.5px solid rgba(255,255,255,.12);background:transparent;color:#6b7280;transition:all .15s}
  .ptab:hover{color:#d1d5db;border-color:rgba(255,255,255,.25)}
  .ptab-on{background:#d97706;border-color:#d97706;color:#fff}

  /* ROW 3 stats */
  .row3{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:0.5px solid rgba(255,255,255,.08)}
  .s3{padding:7px 10px;border-right:0.5px solid rgba(255,255,255,.08);background:#18181f}
  .s3:last-child{border-right:none}
  .s3l{font-size:9px;color:#6b7280}
  .s3v{font-size:15px;font-weight:500;margin-top:1px;color:#f3f4f6}
  .s3s{font-size:8.5px;color:#6b7280;margin-top:1px}

  /* ROW 4 equity bars */
  .row4{display:flex;align-items:flex-end;gap:3px;height:32px;padding:3px 10px 0;background:#18181f}
  .bx{flex:1;border-radius:2px 2px 0 0;background:#374151}
  .bw{background:#4ade80}
  .bl2{background:#f87171}
  .bo{background:#fbbf24}

  /* Footer */
  .foot{padding:3px 10px 5px;background:#18181f;display:flex;justify-content:space-between}
  .ft{font-size:9px;color:#6b7280}
</style>
