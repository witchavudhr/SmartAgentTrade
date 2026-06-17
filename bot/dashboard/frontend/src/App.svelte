<script>
  import { onMount, onDestroy } from 'svelte';

  const C = {
    arthur: `<svg width="38" height="60" viewBox="0 0 38 60"><polygon points="5,15 8,8 12,13 16,6 19,11 22,6 26,13 30,8 33,15" fill="#D4A017"/><rect x="10" y="13" width="18" height="4" rx="1" fill="#B8860B"/><ellipse cx="19" cy="25" rx="10" ry="11" fill="#F0C090"/><circle cx="15" cy="24" r="1.5" fill="#2C1A08"/><circle cx="23" cy="24" r="1.5" fill="#2C1A08"/><path d="M15 30 Q19 33 23 30" fill="none" stroke="#9B6040" stroke-width="1.1" stroke-linecap="round"/><rect x="11" y="36" width="16" height="5" rx="2" fill="#D4A017"/><rect x="9" y="39" width="20" height="13" rx="3" fill="#1565C0"/><rect x="17" y="39" width="4" height="13" fill="#D4A017"/><rect x="11" y="45" width="16" height="2.5" fill="#D4A017"/><rect x="5" y="38" width="9" height="5" rx="2" fill="#D4A017"/><rect x="24" y="38" width="9" height="5" rx="2" fill="#D4A017"/><rect x="11" y="52" width="7" height="7" rx="2" fill="#0D47A1"/><rect x="20" y="52" width="7" height="7" rx="2" fill="#0D47A1"/><rect x="9" y="56" width="9" height="4" rx="2" fill="#050A20"/><rect x="20" y="56" width="9" height="4" rx="2" fill="#050A20"/></svg>`,
    dark:   `<svg width="38" height="60" viewBox="0 0 38 60"><path d="M9 21 Q11 8 19 8 Q27 8 29 21Z" fill="#1A1A3E"/><ellipse cx="19" cy="25" rx="10" ry="11" fill="#2C2C54"/><rect x="12" y="20" width="14" height="8" rx="2" fill="#050514"/><line x1="13" y1="23" x2="25" y2="23" stroke="#7C4DFF" stroke-width="1.2" opacity=".9"/><polygon points="17,16 19,10 21,16" fill="#3A3A6E"/><polygon points="9,18 6,9 12,16" fill="#3A3A6E"/><polygon points="29,18 32,9 26,16" fill="#3A3A6E"/><rect x="9" y="36" width="20" height="14" rx="3" fill="#1A1A3E"/><rect x="3" y="35" width="10" height="6" rx="3" fill="#7C4DFF"/><rect x="25" y="35" width="10" height="6" rx="3" fill="#7C4DFF"/><rect x="11" y="50" width="7" height="8" rx="2" fill="#111130"/><rect x="20" y="50" width="7" height="8" rx="2" fill="#111130"/><rect x="9" y="55" width="9" height="4" rx="2" fill="#050514"/><rect x="20" y="55" width="9" height="4" rx="2" fill="#050514"/></svg>`,
    purple: `<svg width="38" height="60" viewBox="0 0 38 60"><ellipse cx="19" cy="24" rx="11" ry="12" fill="#6A1B9A"/><rect x="11" y="19" width="16" height="9" rx="2" fill="#0D001A"/><circle cx="14.5" cy="21" r="1.7" fill="#CE93D8" opacity=".9"/><circle cx="23.5" cy="21" r="1.7" fill="#CE93D8" opacity=".9"/><path d="M19 9 Q13 5 14 12" stroke="#E91E63" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M19 9 Q25 4 24 11" stroke="#FF5722" stroke-width="2" fill="none" stroke-linecap="round"/><rect x="9" y="36" width="20" height="14" rx="3" fill="#7B1FA2"/><rect x="3" y="35" width="10" height="7" rx="3" fill="#9C27B0"/><rect x="25" y="35" width="10" height="7" rx="3" fill="#9C27B0"/><rect x="11" y="50" width="7" height="8" rx="2" fill="#4A148C"/><rect x="20" y="50" width="7" height="8" rx="2" fill="#4A148C"/><rect x="9" y="55" width="9" height="4" rx="2" fill="#1A0030"/><rect x="20" y="55" width="9" height="4" rx="2" fill="#1A0030"/></svg>`,
    barb:   `<svg width="38" height="60" viewBox="0 0 38 60"><path d="M8 21 Q6 9 10 5 Q14 2 17 8" fill="#C62828"/><path d="M30 21 Q32 9 28 5 Q24 2 21 8" fill="#B71C1C"/><path d="M13 9 Q19 4 25 9" fill="#D32F2F"/><ellipse cx="19" cy="25" rx="10" ry="11" fill="#D4926A"/><circle cx="15" cy="24" r="1.8" fill="#1A1A1A"/><circle cx="23" cy="24" r="1.8" fill="#1A1A1A"/><path d="M8 37 Q19 32 30 37 L30 43 Q19 39 8 43Z" fill="#6D4C41"/><rect x="10" y="41" width="18" height="11" rx="3" fill="#5D4037"/><rect x="4" y="37" width="9" height="6" rx="3" fill="#4E342E"/><rect x="25" y="37" width="9" height="6" rx="3" fill="#4E342E"/><rect x="11" y="52" width="7" height="7" rx="2" fill="#4E342E"/><rect x="20" y="52" width="7" height="7" rx="2" fill="#4E342E"/><rect x="9" y="56" width="9" height="4" rx="2" fill="#2C1A10"/><rect x="20" y="56" width="9" height="4" rx="2" fill="#2C1A10"/></svg>`,
    gold:   `<svg width="38" height="60" viewBox="0 0 38 60"><ellipse cx="19" cy="23" rx="11" ry="12" fill="#F9A825"/><rect x="12" y="18" width="14" height="8" rx="2" fill="#0D0800"/><rect x="16" y="9" width="6" height="10" rx="2" fill="#F57F17"/><path d="M19 5 Q14 2 15 9" stroke="#FFB300" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M19 5 Q24 2 23 9" stroke="#FF9800" stroke-width="2" fill="none" stroke-linecap="round"/><rect x="9" y="35" width="20" height="14" rx="3" fill="#F9A825"/><rect x="17" y="35" width="4" height="14" fill="#FFD54F"/><rect x="11" y="42" width="16" height="3" fill="#FFD54F"/><rect x="4" y="35" width="9" height="6" rx="3" fill="#F57F17"/><rect x="25" y="35" width="9" height="6" rx="3" fill="#F57F17"/><rect x="11" y="49" width="7" height="9" rx="2" fill="#F57F17"/><rect x="20" y="49" width="7" height="9" rx="2" fill="#F57F17"/><rect x="9" y="55" width="9" height="4" rx="2" fill="#BF360C"/><rect x="20" y="55" width="9" height="4" rx="2" fill="#BF360C"/></svg>`,
  };

  const AGENTS_DEF = [
    { id:'a0', char:'arthur', name:'King Arthur',   seat:{l:229,t:2},   patrol:[{l:222,t:4},{l:240,t:3},{l:218,t:12}],  idle:['Watching the realm...','Awaiting intel...'] },
    { id:'a1', char:'dark',   name:'Chart Analyst', seat:{l:408,t:130}, patrol:[{l:448,t:58},{l:454,t:122},{l:449,t:65}], idle:['M5 OB forming...','BOS confirmed...'] },
    { id:'a2', char:'purple', name:'Bias Analyst',  seat:{l:347,t:263}, patrol:[{l:438,t:267},{l:453,t:296},{l:440,t:274}],idle:['H4 in demand...','Daily bias bullish...'] },
    { id:'a3', char:'barb',   name:'News Scout',    seat:{l:53,t:263},  patrol:[{l:4,t:267},{l:18,t:296},{l:6,t:274}],   idle:['Calendar quiet...','No impact 4h...'] },
    { id:'a4', char:'gold',   name:'Risk Manager',  seat:{l:30,t:130},  patrol:[{l:3,t:58},{l:6,t:122},{l:4,t:65}],      idle:['2% rule holding...','RR calc ready...'] },
  ];

  // ── State ─────────────────────────────────────────────────────────────────
  let statusText = 'Watching the market...';
  let connected = false;
  let scanPhase = 'idle';
  let scanRunning = false;
  let vbnVisible = false;
  let vbnText = '';

  let knights = AGENTS_DEF.map(a => ({
    ...a, pos:{...a.patrol[0]}, pIdx:0, moving:false, flip:false, bubble:'', bubbleOn:false,
  }));

  let scanLog = [];
  let signal = {
    direction:'—', setup_type:'—', stars:'', entry:0, sl:0, tp:0, lot:0.01, rr:0,
    approved:false, time:'—',
    votes:{chart:false, bias:false, news:false, risk:false},
    reason:'Waiting for first scan…',
  };
  let stats = { today_pnl:0, open_pnl:0, win_rate:0, wins:0, losses:0, best_trade:0, trades:[], period:'today' };
  let activePeriod = 'today';
  const PERIODS = [
    {key:'today',label:'Today'},{key:'week',label:'Week'},
    {key:'month',label:'Month'},{key:'year',label:'Year'},{key:'all',label:'All'},
  ];

  async function switchPeriod(p) {
    activePeriod = p;
    try {
      const r = await fetch(`/api/stats?period=${p}`);
      const d = await r.json();
      if (!d.error) stats = d;
    } catch(e) {}
    if (rightTab === 'trades') fetchTransactions(p);
  }

  // Ask agents
  let askInput = '';
  let askLoading = false;
  let messages = [{ role:'ai', text:'สวัสดีครับ — พร้อมแล้ว' }];
  let msgsEl;

  // Right column tab
  let rightTab = 'scans';  // 'scans' | 'trades'
  let transactions = [];
  let txLoading = false;

  async function fetchTransactions(period = activePeriod) {
    if (txLoading) return;
    txLoading = true;
    try {
      const r = await fetch(`/api/transactions?period=${period}`);
      const d = await r.json();
      if (d.transactions) transactions = d.transactions.slice().reverse();
    } catch(e) {}
    txLoading = false;
  }
  function switchRightTab(tab) {
    rightTab = tab;
    if (tab === 'trades' && transactions.length === 0) fetchTransactions();
  }
  function fmtTxTime(ts) {
    if (!ts) return '—';
    // "2026-06-17 10:30:00" → "10:30"
    return ts.length >= 16 ? ts.slice(11, 16) : ts;
  }
  function fmtTxDate(ts) {
    if (!ts) return '';
    return ts.length >= 10 ? ts.slice(5, 10) : ts;
  }

  // ── Live market data ───────────────────────────────────────────────────────
  let price = 0;
  let htfBias = { overall:'?', h4:'?', h1:'?', daily:'?' };
  let nearestOb = { type:'?', lo:0, hi:0, dist:0 };
  let nextScanMins = 0;

  // Scan windows in Thai time (UTC+7) [startH, startM, endH, endM]
  const SCAN_WINS = [
    [6,30,8,30],[10,30,12,0],[13,45,14,30],[15,45,16,15],
    [16,30,17,30],[19,0,21,45],[22,0,23,0],[23,15,23,45],[1,0,2,0],
  ];

  function calcNextScan() {
    const n = new Date();
    const nowM = n.getHours()*60 + n.getMinutes();
    let best = Infinity;
    for (const [sh,sm,eh,em] of SCAN_WINS) {
      const s = sh*60+sm, e = eh*60+em;
      // next 15-min slot inside this window
      let slot = Math.ceil((nowM+1)/15)*15;
      while (slot <= e) {
        if (slot >= s) { const d = slot - nowM; if (d > 0 && d < best) best = d; break; }
        slot += 15;
      }
      // or window start itself (next day wrap)
      const ds = s > nowM ? s - nowM : s + 1440 - nowM;
      if (ds < best) best = ds;
    }
    nextScanMins = best === Infinity ? 0 : best;
  }

  function biasArrow(v) {
    if (!v || v === '?') return '—';
    const lc = v.toLowerCase();
    if (lc.includes('bull') || lc === 'up' || lc === 'long') return '▲';
    if (lc.includes('bear') || lc === 'down' || lc === 'short') return '▼';
    return '—';
  }
  function biasColor(v) {
    if (!v || v === '?') return '#4b5563';
    const lc = v.toLowerCase();
    if (lc.includes('bull') || lc === 'up') return '#22c55e';
    if (lc.includes('bear') || lc === 'down') return '#ef4444';
    return '#9ca3af';
  }

  // News events in Thai time UTC+7 [h, m, label, impact:'high'|'med']
  const NEWS_EVENTS = [
    [2,30,'ADP','med'],[3,0,'ISM','med'],
    [14,30,'CPI','high'],[14,30,'PPI','high'],[14,30,'PCE','high'],
    [19,30,'NFP','high'],[19,30,'Unemp','high'],
    [21,0,'Fed','high'],[21,30,'FOMC','high'],
    [15,0,'Retail Sales','med'],[16,15,'Industrial','med'],
    [2,0,'BOJ','high'],[3,30,'GDP','med'],
  ];

  // Session windows in Thai time UTC+7
  // Sydney: 05:00–07:00  (ASX open, before Tokyo)
  // Asia:   07:00–14:00  (Tokyo/Singapore open)
  // London: 14:00–23:00  (London open)
  // NY:     19:00–04:00  (NY open, wraps midnight)
  // Off:    04:00–05:00
  function sessionLabel() {
    const h = new Date().getHours();
    if (h >= 19 || h < 4)  return 'New York';
    if (h >= 14)            return 'London';
    if (h >= 7)             return 'Asia';
    if (h >= 5)             return 'Sydney';
    return 'Off-hours';   // 04:00–05:00
  }
  function sessionHours() {
    const h = new Date().getHours();
    if (h >= 19 || h < 4)  return '19:00→04:00';
    if (h >= 14)            return '14:00→23:00';
    if (h >= 7)             return '07:00→14:00';
    if (h >= 5)             return '05:00→07:00';
    return '—';
  }
  function sessionProgress() {
    const n = new Date(); const nowM = n.getHours()*60 + n.getMinutes();
    const h = n.getHours();
    if (h >= 19 || h < 4) {
      const s = 19*60, dur = 9*60;
      const elapsed = nowM >= 19*60 ? nowM - s : nowM + (24*60 - s);
      return Math.round(Math.min(elapsed, dur) / dur * 100);
    }
    if (h >= 14) return Math.round((nowM - 14*60) / (9*60) * 100);
    if (h >= 7)  return Math.round((nowM - 7*60)  / (7*60) * 100);
    if (h >= 5)  return Math.round((nowM - 5*60)  / (2*60) * 100);
    return 0;
  }
  function nextNews() {
    const n = new Date(); const nowM = n.getHours()*60 + n.getMinutes();
    let best = null, bestD = Infinity;
    for (const [h,m,label,impact] of NEWS_EVENTS) {
      const nm = h*60+m;
      const d = nm > nowM ? nm - nowM : nm + 1440 - nowM;
      if (d < bestD) { bestD = d; best = {label, impact, mins: d}; }
    }
    return best;
  }
  function nowHHMM() {
    const n = new Date();
    return `${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}`;
  }
  function todayStr() {
    const n = new Date();
    return `${n.toLocaleDateString('en-US',{month:'short'})} ${n.getDate()}`;
  }
  let timeStr   = nowHHMM();
  let sessStr   = sessionLabel();
  let sessHrs   = sessionHours();
  let sessPct   = sessionProgress();
  let newsInfo  = nextNews();

  // ── Knights ───────────────────────────────────────────────────────────────
  function showBubble(idx, txt, dur=2500) {
    knights[idx] = {...knights[idx], bubble:txt, bubbleOn:true};
    knights = [...knights];
    if (dur) setTimeout(() => { knights[idx]={...knights[idx],bubbleOn:false}; knights=[...knights]; }, dur);
  }
  function moveKnight(idx, l, t) {
    const flip = l < (knights[idx].pos.l - 15);
    knights[idx] = {...knights[idx], pos:{l,t}, moving:true, flip};
    knights = [...knights];
  }
  let patrolTimer;
  function startPatrol() {
    clearInterval(patrolTimer);
    patrolTimer = setInterval(() => {
      if (scanPhase !== 'idle') return;
      knights.forEach((k,i) => {
        if (Math.random() < 0.55) {
          const ni = (k.pIdx+1) % AGENTS_DEF[i].patrol.length;
          const wp = AGENTS_DEF[i].patrol[ni];
          knights[i] = {...k, pIdx:ni};
          moveKnight(i, wp.l, wp.t);
        }
      });
      if (Math.random() < 0.38) {
        const i = Math.floor(Math.random()*knights.length);
        showBubble(i, AGENTS_DEF[i].idle[Math.floor(Math.random()*2)], 2400);
      }
    }, 3000);
  }

  // ── Scan ──────────────────────────────────────────────────────────────────
  async function triggerScan() {
    if (scanRunning) return;
    try {
      const r = await fetch('/api/scan', {method:'POST'});
      const d = await r.json();
      if (!d.ok) return;
    } catch(e) { return; }
    scanRunning = true;
    clearInterval(patrolTimer);
  }

  // ── WebSocket ─────────────────────────────────────────────────────────────
  let ws;
  function initWs() {
    const proto = location.protocol==='https:'?'wss':'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen  = () => { connected = true; };
    ws.onclose = () => { connected = false; setTimeout(initWs, 3000); };
    const AIDX = { supervisor:0, chart_analyst:1, bias_analyst:2, news_scout:3, risk_manager:4 };
    ws.onmessage = ev => {
      const msg = JSON.parse(ev.data);
      if (msg.type==='init') {
        if (msg.signal)   signal  = msg.signal;
        if (msg.scan_log) scanLog = msg.scan_log;
        if (msg.stats)    stats   = {...stats, ...msg.stats};
        if (msg.price)       price      = msg.price;
        if (msg.htf_bias)    htfBias    = msg.htf_bias;
        if (msg.nearest_ob)  nearestOb  = msg.nearest_ob;
      } else if (msg.type==='market_update') {
        if (msg.price)      price     = msg.price;
        if (msg.htf_bias)   htfBias   = msg.htf_bias;
        if (msg.nearest_ob) nearestOb = msg.nearest_ob;
      } else if (msg.type==='price_update') {
        if (msg.price) price = msg.price;
        if (msg.ob)    nearestOb = msg.ob;
      } else if (msg.type==='signal') {
        signal = msg.data;
      } else if (msg.type==='scan_log') {
        scanLog = [msg.data, ...scanLog].slice(0,8);
      } else if (msg.type==='stats') {
        stats = {...stats, ...msg.data};
      } else if (msg.type==='scan_phase') {
        const ph = msg.phase; scanPhase = ph;
        if (ph==='gathering') {
          statusText = 'Knights to the round table...';
          clearInterval(patrolTimer); scanRunning = true;
          knights.forEach((_,i) => { const s=AGENTS_DEF[i].seat; moveKnight(i,s.l,s.t); });
        } else if (ph==='scanning') {
          statusText = 'Scanning XAUUSD...';
          knights.forEach((_,i) => { knights[i]={...knights[i],moving:false}; }); knights=[...knights];
        } else if (ph==='approved') {
          statusText = 'APPROVED — sending alert';
          vbnText = `APPROVED — ${signal.direction} XAUUSD  Entry ${signal.entry}  TP ${signal.tp}`;
          vbnVisible = true; setTimeout(()=>{ vbnVisible=false; }, 3400);
        } else if (ph==='rejected') {
          statusText = 'No setup found';
        } else if (ph==='idle') {
          statusText = 'Watching the market...'; scanRunning = false;
          knights.forEach((_,i) => { const p=AGENTS_DEF[i].patrol[0]; moveKnight(i,p.l,p.t); knights[i]={...knights[i],pIdx:0}; });
          knights=[...knights]; startPatrol();
        }
      } else if (msg.type==='agent_bubble') {
        const idx = AIDX[msg.agent];
        if (idx!==undefined) showBubble(idx, msg.text, 3000);
      }
    };
  }

  async function sendAsk() {
    const q = askInput.trim();
    if (!q || askLoading) return;
    askInput = ''; askLoading = true;
    messages = [...messages, {role:'user', text:q}];
    try {
      const r = await fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:q})});
      const d = await r.json();
      messages = [...messages, {role:'ai', text:d.response}];
    } catch(e) {
      messages = [...messages, {role:'ai', text:'[ไม่สามารถเชื่อมต่อได้]'}];
    }
    askLoading = false;
    setTimeout(() => { if(msgsEl) msgsEl.scrollTop=msgsEl.scrollHeight; }, 50);
  }
  function askKey(e) { if (e.key==='Enter') sendAsk(); }

  function fmtPnl(v) { return (v>=0?'+$':'-$')+Math.abs(v??0).toFixed(2); }
  $: tradeMax = Math.max(...(stats.trades||[]).map(t=>Math.abs(t.p??t.pnl??0)), 1);
  $: hasTradeData = (stats.wins + stats.losses) > 0;

  onMount(() => {
    initWs(); startPatrol();
    AGENTS_DEF.forEach((a,i) => setTimeout(()=>showBubble(i,a.idle[0],2400), i*500+600));
    calcNextScan();
    setInterval(() => {
      timeStr=nowHHMM(); sessStr=sessionLabel(); sessHrs=sessionHours();
      sessPct=sessionProgress(); newsInfo=nextNews(); calcNextScan();
    }, 30000);
  });
  onDestroy(() => { clearInterval(patrolTimer); if(ws) ws.close(); });
</script>

<div class="shell">

  <!-- ── TOP BAR ────────────────────────────────────────────────────── -->
  <header class="topbar">
    <span class="tb-brand">SmartAgentTrade — War Room</span>
    <span class="tb-price">
      {#if price > 0}
        XAUUSD <b>{price.toFixed(2)}</b>
        {#if nearestOb.lo > 0}
          &nbsp;·&nbsp;<span class="ob-tag" style="color:{nearestOb.type==='BULL'?'#22c55e':'#ef4444'}">{nearestOb.type} OB</span>
          &nbsp;{nearestOb.lo}–{nearestOb.hi}
          &nbsp;<span class="ob-dist">({nearestOb.dist}p away)</span>
        {/if}
      {:else}
        <span style="color:#374151">XAUUSD —</span>
      {/if}
    </span>
    <span class="tb-status">{statusText}</span>
    <div class="tb-right">
      {#if nextScanMins > 0}
        <span class="tb-scan-cd">next scan {nextScanMins}m</span>
      {/if}
      <div class="ldot" class:live={connected}></div>
      <button class="scanbtn" disabled={scanRunning} on:click={triggerScan}>
        {scanRunning ? '⏳ Scanning...' : '⚡ Scan now'}
      </button>
    </div>
  </header>

  <!-- ── COLUMNS ────────────────────────────────────────────────────── -->
  <div class="cols">

    <!-- ─── LEFT ────────────────────────────────────────────────────── -->
    <aside class="lcol">

      <div class="sh">STATS</div>
      <div class="ptabs">
        {#each PERIODS as p}
          <button class="pt" class:pt-on={activePeriod===p.key} on:click={()=>switchPeriod(p.key)}>{p.label}</button>
        {/each}
      </div>
      <div class="sg">
        <div class="sc">
          <div class="sl">P&amp;L</div>
          <div class="sv" style="color:{stats.today_pnl>=0?'#22c55e':'#ef4444'}">{fmtPnl(stats.today_pnl)}</div>
        </div>
        <div class="sc">
          <div class="sl">Win rate</div>
          <div class="sv" style="color:{hasTradeData?'#e5e7eb':'#374151'}">
            {hasTradeData ? `${stats.win_rate}%` : '—'}
          </div>
        </div>
        <div class="sc">
          <div class="sl">W / L</div>
          <div class="sv">
            <span style="color:#22c55e">{stats.wins ?? 0}</span>
            <span style="color:#4b5563"> / </span>
            <span style="color:#ef4444">{stats.losses ?? 0}</span>
          </div>
        </div>
        <div class="sc">
          <div class="sl">Scans ✓/✗</div>
          <div class="sv">
            <span style="color:#22c55e">{stats.scans_approved ?? 0}</span>
            <span style="color:#4b5563"> / </span>
            <span style="color:#ef4444">{stats.scans_rejected ?? 0}</span>
          </div>
        </div>
      </div>

      <div class="sh">TRADE HISTORY BARS</div>
      <div class="bars">
        {#each (stats.trades||[]) as t}
          {@const pv = Math.abs(t.pnl ?? t.p ?? 1)}
          {@const h = Math.max(4, Math.round(pv/tradeMax*32))}
          {@const win = (t.r ?? (t.pnl >= 0 ? 'w' : 'l')) === 'w'}
          <div style="display:flex;flex-direction:column;align-items:center;flex:1">
            <div class="bar" class:bar-w={win} class:bar-l={!win} style="height:{h}px"></div>
          </div>
        {/each}
        {#if !(stats.trades||[]).length}
          <div style="font-size:10px;color:#374151;width:100%;text-align:center">No trades</div>
        {/if}
      </div>

      <div class="sh">SESSION</div>
      <div class="sess-card">
        <div class="sess-name">{sessStr}</div>
        <div class="sess-hrs">{sessHrs}</div>
        <div class="sess-news">
          <span class="news-pill">News: clear</span>
          <span class="news-pill">Next: CPI 21:30</span>
        </div>
      </div>

      <div class="sh">HTF BIAS</div>
      <div class="htf-row">
        <div class="htf-item">
          <div class="htf-label">Daily</div>
          <div class="htf-val" style="color:{biasColor(htfBias.daily)}">{biasArrow(htfBias.daily)}</div>
        </div>
        <div class="htf-item">
          <div class="htf-label">H4</div>
          <div class="htf-val" style="color:{biasColor(htfBias.h4)}">{biasArrow(htfBias.h4)}</div>
        </div>
        <div class="htf-item">
          <div class="htf-label">H1</div>
          <div class="htf-val" style="color:{biasColor(htfBias.h1)}">{biasArrow(htfBias.h1)}</div>
        </div>
        <div class="htf-item htf-overall">
          <div class="htf-label">Overall</div>
          <div class="htf-val" style="color:{biasColor(htfBias.overall)}">{biasArrow(htfBias.overall)}</div>
        </div>
      </div>

      <div class="sh">OPEN TRADE</div>
      {#if signal.approved && signal.entry > 0}
        <div class="ot-card" class:ot-buy={signal.direction==='BUY'} class:ot-sell={signal.direction==='SELL'}>
          <div class="ot-dir">{signal.direction} {signal.entry} → now</div>
          <div class="ot-sub">{fmtPnl(signal.pnl??0)} open | SL {signal.sl}</div>
        </div>
      {:else}
        <div class="ot-none">No open trade</div>
      {/if}

    </aside>

    <!-- ─── CENTER ──────────────────────────────────────────────────── -->
    <main class="ccol">
      <!-- War room animation -->
      <div class="vr-wrap">
        <div class="vr">
          <div class="bg"></div>
          <div class="wall"></div>
          <div class="ban">
            <div class="ban-p"></div>
            <div class="ban-f"><svg width="28" height="28" viewBox="0 0 24 24"><polygon points="12,2 15,9 22,9 17,13 19,20 12,16 5,20 7,13 2,9 9,9" fill="#D4A017"/></svg></div>
            <div class="ban-p"></div>
          </div>
          <div class="tr" style="left:32px;top:22px"><div class="tg"></div><div class="tf"></div><div class="ts"></div></div>
          <div class="tr" style="right:32px;top:22px"><div class="tg"></div><div class="tf"></div><div class="ts"></div></div>
          <div class="tr" style="left:32px;bottom:10px"><div class="tg"></div><div class="tf"></div><div class="ts"></div></div>
          <div class="tr" style="right:32px;bottom:10px"><div class="tg"></div><div class="tf"></div><div class="ts"></div></div>
          <svg style="position:absolute;left:39px;top:26px;z-index:3" width="420" height="306" viewBox="0 0 220 160">
            <ellipse cx="110" cy="92" rx="112" ry="77" fill="rgba(0,0,0,.45)"/>
            <ellipse cx="110" cy="82" rx="110" ry="74" fill="#3A1E08"/>
            <ellipse cx="110" cy="82" rx="110" ry="74" fill="none" stroke="#5A3010" stroke-width="8"/>
            <ellipse cx="110" cy="82" rx="98" ry="62" fill="#472A0C"/>
            <ellipse cx="110" cy="82" rx="84" ry="50" fill="#57361A"/>
            <circle cx="110" cy="82" r="30" fill="none" stroke="#B8900A" stroke-width="1.6" opacity=".55"/>
            <polygon points="110,53 117,72 137,72 122,84 128,103 110,91 92,103 98,84 83,72 103,72" fill="none" stroke="#C4980E" stroke-width="1.6" opacity=".65"/>
            <rect x="94" y="0" width="32" height="11" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
            <rect x="200" y="52" width="11" height="32" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
            <rect x="171" y="140" width="32" height="11" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
            <rect x="17" y="140" width="32" height="11" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
            <rect x="9" y="52" width="11" height="32" rx="2" fill="#2A1408" stroke="#5A3010" stroke-width=".9"/>
            <g transform="translate(110,82) rotate(-90)"><circle cx="-8" cy="0" r="3" fill="#D4A017"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#7B4920"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#D4A017"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#C8D4E0"/><polygon points="50,-1 50,1 56,0" fill="#D8E4F0"/></g>
            <g transform="translate(110,82) rotate(-18)"><circle cx="-8" cy="0" r="3" fill="#6030C0"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#0D0020"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#6030C0"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#6A6A90"/><polygon points="50,-1 50,1 56,0" fill="#8080B0"/></g>
            <g transform="translate(110,82) rotate(54)"><circle cx="-8" cy="0" r="3" fill="#9C27B0"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#380048"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#9C27B0"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#BCCCD8"/><polygon points="50,-1 50,1 56,0" fill="#CCDDE8"/></g>
            <g transform="translate(110,82) rotate(126)"><circle cx="-8" cy="0" r="3" fill="#7B2A0A"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#3A1500"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#C62020"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#9A9A9A"/><polygon points="50,-1 50,1 56,0" fill="#B0B0B0"/></g>
            <g transform="translate(110,82) rotate(198)"><circle cx="-8" cy="0" r="3" fill="#F57F17"/><rect x="-7" y="-1.8" width="11" height="3.6" rx="1.2" fill="#6A3600"/><rect x="3.5" y="-5" width="3" height="10" rx="1" fill="#F9A825"/><rect x="6" y="-1" width="44" height="2" rx="1" fill="#D4C040"/><polygon points="50,-1 50,1 56,0" fill="#E0D050"/></g>
          </svg>
          <div class="vbn" class:on={vbnVisible}>{vbnText}</div>

          <!-- ── Notice board (left wall) ────────────────────── -->
          <div class="nboard">
            <svg class="nb-bg" width="128" height="56" viewBox="0 0 128 56" xmlns="http://www.w3.org/2000/svg">
              <!-- frame shadow -->
              <rect x="2" y="2" width="124" height="52" rx="4" fill="rgba(0,0,0,.5)"/>
              <!-- wood body -->
              <rect x="0" y="0" width="124" height="52" rx="4" fill="#3D1F08" stroke="#7B4920" stroke-width="1.2"/>
              <rect x="3" y="3" width="118" height="46" rx="3" fill="#2A1408"/>
              <!-- grain lines -->
              <line x1="3" y1="14" x2="121" y2="14" stroke="#361A09" stroke-width=".7"/>
              <line x1="3" y1="26" x2="121" y2="26" stroke="#361A09" stroke-width=".7"/>
              <line x1="3" y1="38" x2="121" y2="38" stroke="#361A09" stroke-width=".7"/>
              <!-- pin left & right -->
              <circle cx="10" cy="6" r="3.5" fill="#C4980E" stroke="#8B6914" stroke-width=".8"/>
              <circle cx="114" cy="6" r="3.5" fill="#C4980E" stroke="#8B6914" stroke-width=".8"/>
            </svg>
            <div class="nb-content">
              <div class="nb-title">WAR ROOM</div>

              <!-- session row -->
              <div class="nb-row">
                <span class="nb-lbl">Session</span>
                <span class="nb-val">{sessStr}</span>
              </div>
              {#if sessPct > 0}
              <div class="nb-prog-wrap">
                <div class="nb-prog-bar" style="width:{sessPct}%"></div>
              </div>
              {/if}

              <!-- news row -->
              <div class="nb-row">
                <span class="nb-lbl">News</span>
                {#if newsInfo && newsInfo.mins <= 60}
                  <span class="nb-news-warn">⚠ {newsInfo.label} {newsInfo.mins}m</span>
                {:else if newsInfo}
                  <span class="nb-news-ok">✓ clear · {newsInfo.label} {Math.floor(newsInfo.mins/60)}h{newsInfo.mins%60>0?`${newsInfo.mins%60}m`:''}</span>
                {:else}
                  <span class="nb-news-ok">✓ clear</span>
                {/if}
              </div>

              <!-- next scan row -->
              <div class="nb-row">
                <span class="nb-lbl">Scan</span>
                <span class="nb-val">in {nextScanMins}m</span>
              </div>
            </div>
          </div>

          {#each knights as k, i}
            <div class="kn" class:mv={k.moving} class:fl={k.flip}
                 style="left:{k.pos.l}px;top:{k.pos.t}px"
                 on:click={() => showBubble(i, AGENTS_DEF[i].idle[Math.floor(Math.random()*2)], 2800)}
                 role="button" tabindex="0"
                 on:keydown={e=>e.key==='Enter'&&showBubble(i,AGENTS_DEF[i].idle[0],2800)}>
              <div class="ki">{@html C[k.char]}</div>
              <div class="bbl" class:on={k.bubbleOn}>{k.bubble}</div>
              <div class="ntg">{k.name}</div>
            </div>
          {/each}
        </div>
      </div>

      <!-- Signal panel below animation -->
      <div class="sig-panel">
        <div class="sig-status">{statusText}</div>
        <div class="sig-toggle"
             class:sig-has={signal.entry > 0}>
          {signal.entry > 0 ? `Latest signal details · ${signal.time}` : 'No signal yet'}
        </div>
        <div class="vote-row">
          <span class="vt" class:vt-yes={signal.votes?.chart} class:vt-no={!signal.votes?.chart}>chart</span>
          <span class="vt" class:vt-yes={signal.votes?.bias}  class:vt-no={!signal.votes?.bias}>bias</span>
          <span class="vt" class:vt-yes={signal.votes?.news}  class:vt-no={!signal.votes?.news}>news</span>
          <span class="vt" class:vt-yes={signal.votes?.risk}  class:vt-no={!signal.votes?.risk}>risk</span>
        </div>
        <div class="verdict" class:v-ok={signal.approved} class:v-no={!signal.approved && signal.entry>0}>
          King Arthur: {signal.entry>0 ? (signal.approved ? 'APPROVED' : 'REJECTED') : '—'}
        </div>
        {#if signal.reason && signal.reason !== 'Waiting for first scan…'}
          <div class="reason">{signal.reason}</div>
        {/if}
        <div class="levels">
          <div class="lv"><div class="ll">Entry</div><div class="lval">{signal.entry||'—'}</div></div>
          <div class="lv"><div class="ll">SL</div><div class="lval" style="color:#ef4444">{signal.sl||'—'}</div></div>
          <div class="lv"><div class="ll">TP</div><div class="lval" style="color:#22c55e">{signal.tp||'—'}</div></div>
          <div class="lv"><div class="ll">RR / Lot</div><div class="lval">{signal.rr||'—'} / {signal.lot||'.01'}</div></div>
        </div>
      </div>
    </main>

    <!-- ─── RIGHT ───────────────────────────────────────────────────── -->
    <aside class="rcol">

      <div class="sh">ASK AGENTS</div>
      <div class="qcs">
        <button class="qc" on:click={() => { askInput='OB zone อยู่ที่ไหน?'; }}>OB zone?</button>
        <button class="qc" on:click={() => { askInput='ควร close ไหม?'; }}>Close?</button>
        <button class="qc" on:click={() => { askInput='bias วันนี้คืออะไร?'; }}>Bias?</button>
      </div>
      <div class="msgs" bind:this={msgsEl}>
        {#each messages as m}
          <div class="msg" class:msg-u={m.role==='user'}>
            <div class="mb">{m.text}</div>
          </div>
        {/each}
        {#if askLoading}
          <div class="msg"><div class="mb" style="font-style:italic;color:#6b7280">กำลังถาม…</div></div>
        {/if}
      </div>
      <div class="cin">
        <input type="text" placeholder="ถาม XAUUSD..." bind:value={askInput} on:keydown={askKey}/>
        <button class="sendbtn" on:click={sendAsk}>Send</button>
      </div>

      <!-- Scan / Trades tab header -->
      <div class="rtab-hdr">
        <button class="rtab" class:rtab-on={rightTab==='scans'} on:click={()=>switchRightTab('scans')}>SCANS</button>
        <button class="rtab" class:rtab-on={rightTab==='trades'} on:click={()=>switchRightTab('trades')}>TRADES</button>
      </div>

      {#if rightTab === 'scans'}
      <div class="slog">
        {#if !scanLog.length}
          <div style="font-size:10px;color:#374151;padding:8px">No scans yet</div>
        {/if}
        {#each scanLog as l}
          <div class="sli" class:sli-ok={l.result==='ok'} class:sli-no={l.result==='no'}>
            <div class="sli-top">{l.time??l.t} {l.text??l.tx}</div>
            <div class="sli-sub">{l.sub??l.s}</div>
          </div>
        {/each}
      </div>
      {:else}
      <div class="slog">
        {#if txLoading}
          <div style="font-size:10px;color:#374151;padding:8px">Loading…</div>
        {:else if !transactions.length}
          <div style="font-size:10px;color:#374151;padding:8px">No transactions for {activePeriod}</div>
        {:else}
          {#each transactions as tx}
            {@const win = (tx.net_usd ?? tx.pnl_pips ?? 0) > 0}
            {@const be  = (tx.net_usd ?? tx.pnl_pips ?? 0) === 0}
            <div class="sli" class:sli-ok={win} class:sli-be={be} class:sli-no={!win && !be}>
              <div class="sli-top">
                <span>{fmtTxDate(tx.close_time ?? tx.timestamp)} {fmtTxTime(tx.close_time ?? tx.timestamp)}</span>
                <span style="margin-left:5px;font-weight:700">{tx.direction ?? '—'}</span>
                {#if tx.lot}<span style="font-size:9px;opacity:.7"> {tx.lot}L</span>{/if}
              </div>
              <div class="sli-sub" style="display:flex;justify-content:space-between">
                <span>{tx.open_price ?? '—'} → {tx.close_price ?? '—'}</span>
                <span style="font-weight:600">
                  {#if tx.pnl_pips != null}{(tx.pnl_pips >= 0 ? '+' : '')}{tx.pnl_pips}p{/if}
                  &nbsp;{(tx.net_usd >= 0 ? '+' : '')}${(tx.net_usd ?? 0).toFixed(2)}
                </span>
              </div>
            </div>
          {/each}
        {/if}
      </div>
      {/if}

      <div class="foot">{todayStr()} · {sessStr} {timeStr}</div>

    </aside>

  </div><!-- /cols -->
</div><!-- /shell -->

<style>
  :global(*){box-sizing:border-box;margin:0;padding:0}
  :global(html,body){height:100%;overflow:hidden;background:#0f0f17;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#e5e7eb}

  /* ── shell ──────────────────────────────────────────────── */
  .shell{display:grid;grid-template-rows:40px 1fr;height:100vh;overflow:hidden;background:#111118}

  /* ── TOP BAR ─────────────────────────────────────────────── */
  .topbar{
    display:flex;align-items:center;gap:10px;padding:0 14px;
    background:#0c0c14;border-bottom:1px solid rgba(255,255,255,.07);
    flex-shrink:0;
  }
  .tb-brand{font-size:13px;font-weight:500;color:#f3f4f6;white-space:nowrap}
  .tb-status{flex:1;font-size:10px;color:#4b5563;padding-left:10px}
  .tb-right{display:flex;align-items:center;gap:7px;flex-shrink:0}
  .ldot{width:6px;height:6px;border-radius:50%;background:#374151}
  .ldot.live{background:#4ade80;box-shadow:0 0 5px #4ade80}
  .scanbtn{padding:4px 12px;background:#d97706;color:#0E0800;border:none;border-radius:4px;font-size:10.5px;font-weight:500;cursor:pointer}
  .scanbtn:hover:not(:disabled){background:#f59e0b}
  .scanbtn:disabled{opacity:.4;cursor:default}

  /* ── COLUMNS ─────────────────────────────────────────────── */
  .cols{display:grid;grid-template-columns:216px 1fr 216px;height:100%;overflow:hidden}

  /* ── shared LEFT / RIGHT ─────────────────────────────────── */
  .lcol,.rcol{display:flex;flex-direction:column;overflow:hidden;background:#0f0f17}
  .lcol{border-right:1px solid rgba(255,255,255,.07)}
  .rcol{border-left:1px solid rgba(255,255,255,.07)}

  /* section header */
  .sh{
    font-size:9px;font-weight:600;letter-spacing:.08em;
    color:#4b5563;padding:7px 12px 4px;
    border-bottom:1px solid rgba(255,255,255,.05);
    flex-shrink:0;text-transform:uppercase;
  }

  /* ── LEFT: STATS ─────────────────────────────────────────── */
  .ptabs{display:flex;flex-wrap:wrap;gap:3px;padding:6px 10px;flex-shrink:0}
  .pt{padding:2px 8px;border-radius:99px;font-size:9px;cursor:pointer;
      border:0.5px solid rgba(255,255,255,.1);background:transparent;color:#6b7280}
  .pt:hover{color:#d1d5db}
  .pt-on{background:#d97706;border-color:#d97706;color:#fff}

  .sg{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:rgba(255,255,255,.06);flex-shrink:0}
  .sc{background:#0f0f17;padding:8px 10px}
  .sl{font-size:9px;color:#4b5563;margin-bottom:2px}
  .sv{font-size:17px;font-weight:500;color:#e5e7eb}

  /* ── LEFT: BARS ──────────────────────────────────────────── */
  .bars{display:flex;align-items:flex-end;gap:2px;padding:6px 10px;height:42px;flex-shrink:0}
  .bar{flex:1;border-radius:2px 2px 0 0;background:#1f2937}
  .bar-w{background:#16a34a}
  .bar-l{background:#dc2626}

  /* ── LEFT: SESSION ───────────────────────────────────────── */
  .sess-card{padding:8px 12px;flex-shrink:0}
  .sess-name{font-size:12px;font-weight:500;color:#f3f4f6}
  .sess-hrs{font-size:10px;color:#6b7280;margin:2px 0 5px}
  .sess-news{display:flex;flex-wrap:wrap;gap:3px}
  .news-pill{font-size:9px;padding:2px 7px;border-radius:99px;background:#1c1c25;color:#6b7280;border:0.5px solid rgba(255,255,255,.08)}

  /* ── LEFT: OPEN TRADE ────────────────────────────────────── */
  .ot-card{margin:0 10px 10px;padding:9px 10px;border-radius:6px;flex-shrink:0}
  .ot-buy{background:rgba(22,163,74,.18);border:1px solid rgba(22,163,74,.4)}
  .ot-sell{background:rgba(220,38,38,.15);border:1px solid rgba(220,38,38,.35)}
  .ot-dir{font-size:12px;font-weight:500;color:#f3f4f6}
  .ot-sub{font-size:10px;color:#9ca3af;margin-top:2px}
  .ot-none{margin:0 10px;padding:8px 10px;font-size:10px;color:#374151;background:#1c1c25;border-radius:6px;border:1px solid rgba(255,255,255,.05);flex-shrink:0}

  /* ── CENTER ──────────────────────────────────────────────── */
  .ccol{display:flex;flex-direction:column;overflow:hidden;background:#0a0604}

  /* war room */
  .vr-wrap{flex:1;overflow:hidden;display:flex;align-items:center;justify-content:center;min-height:0}
  .vr{position:relative;width:500px;height:340px;flex-shrink:0}
  .bg{position:absolute;inset:0;background:#0E0903}
  .bg::before{content:'';position:absolute;inset:0;background-image:repeating-linear-gradient(90deg,rgba(255,255,255,.012) 0 1px,transparent 1px 48px),repeating-linear-gradient(0deg,rgba(255,255,255,.012) 0 1px,transparent 1px 48px)}
  .wall{position:absolute;top:0;left:0;right:0;height:58px;background:#150A03;border-bottom:2px solid #291505}
  .wall::before{content:'';position:absolute;inset:0;background-image:repeating-linear-gradient(90deg,rgba(255,255,255,.015) 0 1px,transparent 1px 60px),repeating-linear-gradient(0deg,rgba(255,255,255,.015) 0 1px,transparent 1px 30px)}
  .ban{position:absolute;top:3px;left:50%;transform:translateX(-50%);display:flex;gap:9px;align-items:flex-start;z-index:5}
  .ban-p{width:3px;height:50px;background:#7B4E28}
  .ban-f{background:#6B0000;width:52px;height:52px;clip-path:polygon(0 0,100% 0,100% 75%,50% 100%,0 75%);display:flex;align-items:center;justify-content:center}
  .tr{position:absolute;width:12px;z-index:4}
  .tf{width:9px;height:14px;margin:0 auto;position:relative;animation:flk .42s alternate infinite ease-in-out}
  @keyframes flk{0%{transform:scaleX(1)}100%{transform:scaleX(.55) scaleY(.75)}}
  .tf::before{content:'';position:absolute;inset:0;background:#FF6D00;border-radius:50% 50% 25% 25%}
  .tf::after{content:'';position:absolute;top:2px;left:1px;right:1px;bottom:2px;background:#FFE082;border-radius:50%}
  .ts{width:4px;height:19px;background:#5D3A1A;margin:0 auto}
  .tg{position:absolute;top:-7px;left:50%;transform:translateX(-50%);width:42px;height:42px;background:radial-gradient(circle,rgba(255,140,10,.18) 0%,transparent 70%)}
  .kn{position:absolute;width:38px;z-index:12;cursor:pointer;transition:left 1.4s cubic-bezier(.3,.6,.4,.95),top 1.4s cubic-bezier(.3,.6,.4,.95)}
  .ki{display:block}
  .kn.mv .ki{animation:wb .28s steps(2) infinite}
  @keyframes wb{0%{transform:translateY(0)}50%{transform:translateY(-4px)}}
  .kn.fl .ki{transform:scaleX(-1)}
  .kn.mv.fl .ki{animation:wbf .28s steps(2) infinite}
  @keyframes wbf{0%{transform:scaleX(-1) translateY(0)}50%{transform:scaleX(-1) translateY(-4px)}}
  .bbl{position:absolute;bottom:calc(100% + 4px);left:50%;transform:translateX(-50%);background:rgba(255,248,215,.97);color:#1A0E00;font-size:9px;line-height:1.45;padding:3px 8px;border-radius:5px;white-space:nowrap;border:0.5px solid rgba(160,100,20,.3);opacity:0;transition:opacity .3s;pointer-events:none;z-index:20}
  .bbl::after{content:'';position:absolute;top:100%;left:50%;transform:translateX(-50%);border:4px solid transparent;border-top-color:rgba(255,248,215,.97)}
  .bbl.on{opacity:1}
  .ntg{position:absolute;top:calc(100% + 2px);left:50%;transform:translateX(-50%);font-size:8px;color:rgba(255,200,80,.65);white-space:nowrap;text-shadow:0 1px 4px rgba(0,0,0,.95);pointer-events:none}
  .vbn{position:absolute;top:48%;left:50%;transform:translate(-50%,-50%);background:rgba(6,3,0,.96);border:1px solid #D4A017;border-radius:8px;padding:10px 18px;z-index:40;color:#FFD54F;font-size:11px;font-weight:500;pointer-events:none;opacity:0;transition:opacity .4s;white-space:nowrap}
  .vbn.on{opacity:1}

  /* ── signal panel ─────────────────────────────────────────── */
  .sig-panel{flex-shrink:0;background:#0f0f17;border-top:1px solid rgba(255,255,255,.07)}
  .sig-status{padding:5px 12px;font-size:10px;color:#4b5563;border-bottom:1px solid rgba(255,255,255,.05)}
  .sig-toggle{
    padding:7px 12px;font-size:11px;font-weight:500;color:#9ca3af;
    border-bottom:1px solid rgba(255,255,255,.05);cursor:default;
    background:#111118;
  }
  .sig-has{color:#93c5fd;background:#0f1b38}
  .vote-row{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;padding:6px 10px;border-bottom:1px solid rgba(255,255,255,.05)}
  .vt{text-align:center;padding:4px;border-radius:5px;font-size:10px;font-weight:500;border:0.5px solid rgba(255,255,255,.08);color:#6b7280;background:#1c1c25}
  .vt-yes{background:rgba(22,163,74,.18);color:#4ade80;border-color:rgba(22,163,74,.35)}
  .vt-no{background:rgba(220,38,38,.12);color:#f87171;border-color:rgba(220,38,38,.3)}
  .verdict{padding:6px 12px;text-align:center;font-size:11px;font-weight:500;color:#6b7280;border-bottom:1px solid rgba(255,255,255,.05)}
  .v-ok{background:rgba(22,163,74,.18);color:#4ade80}
  .v-no{background:rgba(220,38,38,.1);color:#f87171}
  .reason{padding:4px 12px;font-size:10px;color:#6b7280;border-bottom:1px solid rgba(255,255,255,.05)}
  .levels{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:rgba(255,255,255,.06)}
  .lv{background:#0f0f17;padding:5px 8px;text-align:center}
  .ll{font-size:8.5px;color:#4b5563;margin-bottom:2px}
  .lval{font-size:12px;font-weight:500;font-family:monospace;color:#e5e7eb}

  /* ── RIGHT ───────────────────────────────────────────────── */
  .qcs{display:flex;gap:4px;padding:6px 10px;flex-shrink:0}
  .qc{font-size:9.5px;padding:3px 9px;border:0.5px solid rgba(255,255,255,.1);border-radius:99px;cursor:pointer;color:#9ca3af;background:transparent}
  .qc:hover{background:#1c1c25;color:#e5e7eb}
  .msgs{flex:1;overflow-y:auto;padding:7px 10px;display:flex;flex-direction:column;gap:5px;min-height:0}
  .msgs::-webkit-scrollbar{width:3px}
  .msgs::-webkit-scrollbar-thumb{background:#2a2a35;border-radius:99px}
  .msg{display:flex}
  .msg-u{justify-content:flex-end}
  .mb{padding:6px 9px;border-radius:7px;font-size:10.5px;line-height:1.5;max-width:94%;background:#1c1c25;border:0.5px solid rgba(255,255,255,.07);color:#d1d5db}
  .msg-u .mb{background:#1e3a5f;color:#93c5fd;border-color:rgba(59,130,246,.2)}
  .cin{display:flex;gap:4px;padding:6px 8px;border-top:1px solid rgba(255,255,255,.07);flex-shrink:0}
  .cin input{flex:1;padding:5px 8px;font-size:10.5px;border:0.5px solid rgba(255,255,255,.12);border-radius:4px;outline:none;color:#e5e7eb;background:#18181f}
  .cin input:focus{border-color:#3b82f6}
  .sendbtn{padding:5px 10px;background:#1d4ed8;color:#fff;border:none;border-radius:4px;font-size:10.5px;cursor:pointer;white-space:nowrap}

  /* ── Right: tab bar ─────────────────────────────────────── */
  .rtab-hdr{display:flex;gap:0;border-bottom:1px solid rgba(255,255,255,.07);flex-shrink:0}
  .rtab{
    flex:1;padding:5px 0;font-size:9px;font-weight:600;letter-spacing:.07em;
    text-transform:uppercase;background:transparent;border:none;
    color:#4b5563;cursor:pointer;border-bottom:2px solid transparent;
  }
  .rtab:hover{color:#9ca3af}
  .rtab-on{color:#d97706;border-bottom-color:#d97706}

  .slog{flex:1;overflow-y:auto;padding:5px 8px;min-height:0}
  .slog::-webkit-scrollbar{width:3px}
  .slog::-webkit-scrollbar-thumb{background:#2a2a35;border-radius:99px}
  .sli{border-radius:5px;padding:5px 8px;margin-bottom:4px;font-size:10px;line-height:1.45}
  .sli-top{font-weight:500;color:#f3f4f6}
  .sli-sub{font-size:9px;margin-top:1px;opacity:.7}
  .sli-ok{background:rgba(22,163,74,.18);border:0.5px solid rgba(22,163,74,.35);color:#4ade80}
  .sli-ok .sli-top{color:#86efac}
  .sli-no{background:rgba(220,38,38,.1);border:0.5px solid rgba(220,38,38,.25);color:#f87171}
  .sli-no .sli-top{color:#fca5a5}
  .sli-be{background:rgba(107,114,128,.12);border:0.5px solid rgba(107,114,128,.25);color:#9ca3af}
  .sli-be .sli-top{color:#d1d5db}

  .foot{padding:5px 10px;font-size:9px;color:#374151;border-top:1px solid rgba(255,255,255,.05);flex-shrink:0;background:#0c0c14}

  /* ── TOP BAR: price + OB ─────────────────────────────────── */
  .tb-price{font-size:11px;color:#9ca3af;white-space:nowrap;padding:0 8px;border-left:1px solid rgba(255,255,255,.08);border-right:1px solid rgba(255,255,255,.08)}
  .tb-price b{color:#f3f4f6;font-weight:600}
  .ob-tag{font-weight:600;font-size:10px}
  .ob-dist{font-size:10px;color:#6b7280}
  .tb-scan-cd{font-size:9.5px;color:#4b5563;white-space:nowrap}

  /* ── WAR ROOM: Notice board ─────────────────────────────── */
  .nboard{position:absolute;left:68px;top:3px;z-index:7;width:124px}
  .nb-bg{display:block}
  .nb-content{
    position:absolute;top:5px;left:5px;right:5px;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  }
  .nb-title{
    font-size:7px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
    color:#C4980E;text-align:center;margin-bottom:3px;
  }
  .nb-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;line-height:1.1}
  .nb-lbl{font-size:7.5px;color:#5a3a1a;font-weight:600;text-transform:uppercase;letter-spacing:.06em}
  .nb-val{font-size:8px;color:#d4a868;font-weight:500}
  .nb-news-warn{font-size:7.5px;color:#f87171;font-weight:600}
  .nb-news-ok{font-size:7.5px;color:#4ade80}
  .nb-prog-wrap{height:3px;background:rgba(255,255,255,.08);border-radius:99px;margin-bottom:3px;overflow:hidden}
  .nb-prog-bar{height:100%;border-radius:99px;background:linear-gradient(90deg,#d97706,#f59e0b);transition:width 1s}

  /* ── LEFT: HTF BIAS ─────────────────────────────────────── */
  .htf-row{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:rgba(255,255,255,.06);flex-shrink:0}
  .htf-item{background:#0f0f17;padding:6px 4px;text-align:center}
  .htf-overall{background:#131320}
  .htf-label{font-size:8.5px;color:#4b5563;margin-bottom:3px;text-transform:uppercase;letter-spacing:.05em}
  .htf-val{font-size:16px;font-weight:700;line-height:1}
</style>
