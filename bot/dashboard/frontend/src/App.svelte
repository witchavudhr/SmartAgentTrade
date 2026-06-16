<script>
  import { onMount, onDestroy } from 'svelte';

  // ── Knight SVG sprites ────────────────────────────────────────────────────
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

  // ── Agent panel cards (top row) ───────────────────────────────────────────
  const AGENT_CARDS = [
    { key:'chart', name:'Chart Analyst', role:'M5 OB · BOS · Sweep', emoji:'⚔️', bg:'#1A237E' },
    { key:'bias',  name:'Bias Analyst',  role:'H1 / H4 / Daily',      emoji:'🛡️', bg:'#311B92' },
    { key:'news',  name:'News Scout',    role:'Economic Cal.',         emoji:'🪃', bg:'#4E342E' },
    { key:'risk',  name:'Risk Manager',  role:'Lot · VETO',           emoji:'🗡️', bg:'#1B5E20' },
    { key:'sup',   name:'Supervisor',    role:'Final vote',            emoji:'🏹', bg:'#7B1FA2' },
    { key:'pos',   name:'POS Guard',     role:'Trail SL',              emoji:'🛡',  bg:'#0D47A1' },
  ];

  const PROMPTS = {
    chart: { title:'Chart Analyst — System Prompt', body:`คุณคือ Chart Analyst Agent — วิเคราะห์ XAUUSD หาจุดเข้า trade\n\nSTEP 0 — OB PROXIMITY\nวัดระยะราคาปัจจุบัน vs Bear OB และ Bull OB\n  CASE A — ใกล้ Bear OB → รอ SELL ที่ Bear OB\n  CASE B — ใกล้ Bull OB → โอกาส Swing ขึ้นหรือ bounce\n\nSTEP 1 — TREND SETUP (CASE A)\n  - direction ตรง macro bias\n  - ราคาอยู่ใน M15/M5 OB ≤30 pips\n  - มี BOS ตาม trend + pullback + confirm candle\n  - RR ≥ 1.5\n\nSTEP 2 — SWING OB ENTRY (CASE B)\n  TP = next swing high/low เท่านั้น\n\nตอบ JSON: { vote, signal, setup_type, confidence, entry_zone, stop_loss, take_profit, rr_ratio, reasoning }` },
    bias:  { title:'Bias Analyst — System Prompt', body:`คุณคือ Bias Analyst Agent — วิเคราะห์ภาพใหญ่ XAUUSD จาก Weekly/Daily/H4/H1\n\n① อ่าน macro trend (Weekly > Daily > H4 > H1)\n② ตรวจสอบ Demand/Supply Zone ของ HTF\n③ ประเมิน Signal ที่เสนอ\n   YES ถ้า: A) with-trend B) ถึง HTF zone แม้ counter-trend\n   NO  ถ้า: C) counter-trend ยังไม่ถึง level D) trend แข็งมาก\n\nตอบ JSON: { vote, case, at_htf_level, overall_bias, h4_bias, h1_bias, trade_direction, reasoning }` },
    news:  { title:'News Scout — System Prompt', body:`คุณคือ News Scout Agent — วิเคราะห์ข่าวเศรษฐกิจที่กระทบ Gold\n\nBlock window:\n  ก่อนข่าว: 30 นาที\n  หลังข่าว: 30 นาที\n\nถ้าไม่มีข่าว → vote YES อัตโนมัติ (ไม่เรียก Claude)\n\nตอบ JSON: { vote, risk_level, safe_to_trade, key_event, gold_impact, reasoning }` },
    risk:  { title:'Risk Manager — Rules', body:`VETO conditions (auto-reject ก่อนส่ง Supervisor):\n  1. Loss streak ≥ 3 ครั้งติด → หยุดพัก\n  2. Daily loss > 3% → หยุดวันนี้\n  3. ไม่มี SL หรือ SL pips ≤ 0\n  4. RR < 1.5\n\nLot calculation:\n  lot = (balance × risk%) / (sl_pips × pip_value × 100)\n\nCaution mode (H4 ขัด bias): lot ลด 50%\n\nScale-in Pyramid (OB zone):\n  Entry 1 (OB top):    20% lot\n  Entry 2 (OB middle): 40% lot\n  Entry 3 (Sweep zone): 40% lot` },
    sup:   { title:'Supervisor — System Prompt', body:`คุณคือ Supervisor Agent — ตัดสินใจสุดท้าย APPROVE หรือ REJECT\n\nVote รวม X/3 — อ่านเหตุผลของทุก agent แล้วชั่งน้ำหนักเอง\n\nวิธีตัดสิน:\n1. Chart เป็น agent หลัก — YES + setup ชัด → น้ำหนักสูงสุด\n2. vote 1/3 แต่ YES เหตุผลแข็งมาก → APPROVE ได้\n3. vote 2/3 แต่ YES อ่อน, NO ชัดเจน → REJECT ได้\n\nตอบ JSON: { approve, confidence, key_agent, reasoning }` },
    pos:   { title:'POS Guard — Auto Trail SL', body:`POS Guard ทำงานทุก interval วินาที\nตรวจ open positions ทุก ticket ใน MT5\n\nTrigger: profit ≥ POSGUARD_TRIGGER_USD ($20)\n  → Lock SL ที่ open price + POSGUARD_LOCK_TICKS\n  → Trail ทุก POSGUARD_STEP_TICKS\n\nตั้งค่าใน .env:\n  POSGUARD_ENABLED=true\n  POSGUARD_TRIGGER_USD=20.0\n  POSGUARD_LOCK_TICKS=10.0\n  POSGUARD_STEP_TICKS=500.0\n  POSGUARD_CHECK_INTERVAL=10` },
  };

  // ── State ─────────────────────────────────────────────────────────────────
  let statusText = 'Watching the market...';
  let connected = false;
  let scanPhase = 'idle';
  let scanRunning = false;
  let vbnText = '';
  let vbnVisible = false;

  let knights = AGENTS_DEF.map(a => ({
    ...a, pos:{...a.patrol[0]}, pIdx:0, moving:false, flip:false, bubble:'', bubbleOn:false,
  }));

  let scanLog = [];
  let signal = {
    direction:'—', setup_type:'—', stars:'', entry:0, sl:0, tp:0, lot:0, rr:0,
    pnl:0, time:'—', approved:false,
    votes:{chart:false, bias:false, news:false, risk:false},
    reason:'Waiting for first scan…',
  };
  let stats = { today_pnl:0, open_pnl:0, win_rate:0, wins:0, losses:0, pending:0, best_trade:0, trades:[], period:'today' };
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
  }

  let askInput = '';
  let askLoading = false;
  let messages = [{ role:'ai', text:'สวัสดีครับ — War Room พร้อมแล้ว รอผลจาก bot…' }];
  let activeModal = null;
  let msgsEl;

  function sessionLabel() {
    const h = new Date().getHours();
    if (h >= 8 && h < 17) return 'London';
    if (h >= 13 && h < 22) return 'NY';
    return 'Off-hours';
  }
  function nowHHMM() {
    const n = new Date();
    return `${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}`;
  }
  let timeStr = nowHHMM();
  let sessStr = sessionLabel();

  // ── Agent dot/vote helpers ─────────────────────────────────────────────────
  function agentDot(key) {
    if (scanPhase === 'gathering' || scanPhase === 'scanning') return 'yellow';
    if (key === 'pos') return 'gray';
    if (key === 'sup') return signal.direction==='—' ? 'gray' : (signal.approved ? 'green' : 'red');
    if (key === 'chart') return signal.direction==='—' ? 'gray' : (signal.votes?.chart  ? 'green' : 'red');
    if (key === 'bias')  return signal.direction==='—' ? 'gray' : (signal.votes?.bias   ? 'green' : 'red');
    if (key === 'news')  return signal.direction==='—' ? 'gray' : (signal.votes?.news   ? 'green' : 'red');
    if (key === 'risk')  return signal.direction==='—' ? 'gray' : (signal.votes?.risk   ? 'green' : 'red');
    return 'gray';
  }
  function agentVoteLabel(key) {
    if (scanPhase === 'gathering') return 'Gathering…';
    if (scanPhase === 'scanning')  return 'Analyzing…';
    if (signal.direction === '—') return 'Standby';
    if (key === 'chart') return signal.votes?.chart  ? `YES ${signal.stars}` : 'NO';
    if (key === 'bias')  return signal.votes?.bias   ? 'YES — Bullish' : 'NO';
    if (key === 'news')  return signal.votes?.news   ? 'YES — Low risk' : 'BLOCKED';
    if (key === 'risk')  return signal.votes?.risk   ? `OK · ${signal.lot}L` : 'VETO';
    if (key === 'sup')   return signal.approved ? 'APPROVED ✓' : 'REJECTED';
    return 'Standby';
  }

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
    statusText = 'Sending knights to the field...';
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
      } else if (msg.type==='signal') {
        signal = msg.data;
      } else if (msg.type==='scan_log') {
        scanLog = [msg.data, ...scanLog].slice(0,12);
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
          vbnText = `APPROVED — ${signal.direction} XAUUSD  |  Entry ${signal.entry}  |  TP ${signal.tp}`;
          vbnVisible = true; setTimeout(()=>{vbnVisible=false;}, 3400);
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

  // ── Ask ───────────────────────────────────────────────────────────────────
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
    setTimeout(() => { if (msgsEl) msgsEl.scrollTop=msgsEl.scrollHeight; }, 50);
  }
  function askKey(e) { if (e.key==='Enter') sendAsk(); }

  function fmtPnl(v) { return (v>=0?'+$':'-$')+Math.abs(v??0).toFixed(2); }
  function signColor(v) { return v>=0 ? '#16a34a' : '#dc2626'; }

  onMount(() => {
    initWs(); startPatrol();
    AGENTS_DEF.forEach((a,i) => setTimeout(()=>showBubble(i,a.idle[0],2400), i*500+600));
    setInterval(() => { timeStr=nowHHMM(); sessStr=sessionLabel(); }, 30000);
  });
  onDestroy(() => { clearInterval(patrolTimer); if(ws) ws.close(); });
</script>

<!-- ── TOP BAR ─────────────────────────────────────────────────────────── -->
<div class="office">

<div class="topbar">
  <div class="tb-left">
    <span class="tb-title">SmartAgentTrade</span>
    <span class="badge-live">LIVE</span>
    <span class="badge-sess">{sessStr} · {timeStr}</span>
  </div>
  <div class="tb-mid">
    <span class="tb-pair">XAUUSD</span>
    <span class="tb-status">{statusText}</span>
  </div>
  <div class="tb-right">
    <div class="live-dot" class:live={connected}></div>
    <button class="scan-btn" disabled={scanRunning} on:click={triggerScan}>
      {scanRunning ? '⏳ Scanning...' : '⚡ Scan now'}
    </button>
  </div>
</div>

<!-- ── AGENT CARDS ROW ───────────────────────────────────────────────── -->
<div class="agents-row">
  {#each AGENT_CARDS as ac}
    {@const dot = agentDot(ac.key)}
    {@const vl  = agentVoteLabel(ac.key)}
    <div class="ac" class:ac-sup={ac.key==='sup'}
         on:click={() => activeModal = ac.key}
         role="button" tabindex="0"
         on:keydown={e => e.key==='Enter' && (activeModal=ac.key)}>
      <div class="ac-sprite" style="background:{ac.bg}">
        <span style="font-size:22px">{ac.emoji}</span>
        <span class="ac-dot dot-{dot}"></span>
      </div>
      <div class="ac-name">{ac.name}</div>
      <div class="ac-role">{ac.role}</div>
      <div class="ac-vote vote-{dot==='green'?'yes':dot==='red'?'no':'wait'}">{vl}</div>
    </div>
  {/each}
</div>

<!-- ── MAIN 3-COLUMN GRID ─────────────────────────────────────────────── -->
<div class="main-grid">

  <!-- LEFT: Scan log ──────────────────────── -->
  <div class="panel">
    <div class="ph">
      <span>Scan log</span>
      <span class="ph-sub">{scanLog.length} scans</span>
    </div>
    <div class="pb">
      {#if scanLog.length === 0}
        <div class="no-item">No scans yet today</div>
      {/if}
      {#each scanLog as l}
        <div class="le" class:le-ok={l.result==='ok'} class:le-no={l.result==='no'}>
          <div class="le-time">{l.time ?? l.t}</div>
          <div class="le-main">{l.text ?? l.tx}</div>
          <div class="le-sub">{l.sub ?? l.s}</div>
        </div>
      {/each}

      {#if signal.entry > 0}
        <div style="margin-top:12px">
          <div class="ph-sub" style="margin-bottom:6px">Latest signal · {signal.time}</div>
          <div class="sig-levels">
            <div class="sl-item"><div class="sl-lbl">Entry</div><div class="sl-val">{signal.entry}</div></div>
            <div class="sl-item"><div class="sl-lbl">SL</div><div class="sl-val" style="color:#dc2626">{signal.sl}</div></div>
            <div class="sl-item"><div class="sl-lbl">TP</div><div class="sl-val" style="color:#16a34a">{signal.tp}</div></div>
            <div class="sl-item"><div class="sl-lbl">RR</div><div class="sl-val">1:{signal.rr}</div></div>
          </div>
          {#if signal.reason}
            <div class="sig-reason" class:sig-ok={signal.approved}>{signal.reason}</div>
          {/if}
        </div>
      {/if}
    </div>
  </div>

  <!-- CENTER: Summary + War Room ──────────── -->
  <div class="center-col">
    <!-- stat strip -->
    <div class="sum-strip">
      <div class="ptabs">
        {#each PERIODS as p}
          <button class="ptab" class:ptab-on={activePeriod===p.key} on:click={()=>switchPeriod(p.key)}>{p.label}</button>
        {/each}
      </div>
      <div class="sum-cards">
        <div class="sc">
          <div class="sc-lbl">P&amp;L</div>
          <div class="sc-val" style="color:{signColor(stats.today_pnl)}">{fmtPnl(stats.today_pnl)}</div>
        </div>
        <div class="sc">
          <div class="sc-lbl">W / L</div>
          <div class="sc-val"><span style="color:#16a34a">{stats.wins}</span> / <span style="color:#dc2626">{stats.losses}</span></div>
        </div>
        <div class="sc">
          <div class="sc-lbl">Win rate</div>
          <div class="sc-val">{stats.win_rate}%</div>
        </div>
        <div class="sc">
          <div class="sc-lbl">Best</div>
          <div class="sc-val" style="color:#16a34a">{fmtPnl(stats.best_trade??0)}</div>
        </div>
      </div>
    </div>

    <!-- War room fills remaining space -->
    <div class="vr-wrap">
      <div class="vr">
        <div class="bg"></div>
        <div class="wall"></div>
        <div class="ban">
          <div class="ban-p"></div>
          <div class="ban-f"><svg width="30" height="30" viewBox="0 0 24 24"><polygon points="12,2 15,9 22,9 17,13 19,20 12,16 5,20 7,13 2,9 9,9" fill="#D4A017"/></svg></div>
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
  </div>

  <!-- RIGHT: Ask agents ────────────────────── -->
  <div class="panel chat-panel">
    <div class="ph">
      <span>Ask anything</span>
      <span class="ph-sub">Haiku 4.5</span>
    </div>
    <div class="qcs">
      <button class="qc" on:click={() => { askInput='ทองวันนี้มองยังไง?'; }}>ทอง?</button>
      <button class="qc" on:click={() => { askInput='OB ที่ใช้เข้าอยู่ที่ไหน?'; }}>OB?</button>
      <button class="qc" on:click={() => { askInput='ควร close trade ไหม?'; }}>Close?</button>
      <button class="qc" on:click={() => { askInput='news วันนี้มีอะไร?'; }}>News?</button>
    </div>
    <div class="msgs" bind:this={msgsEl}>
      {#each messages as m}
        <div class="msg" class:msg-user={m.role==='user'}>
          {#if m.role==='ai'}<div class="msg-role">Assistant</div>{/if}
          <div class="msg-bubble">{m.text}</div>
        </div>
      {/each}
      {#if askLoading}
        <div class="msg"><div class="msg-bubble" style="color:#6b7280;font-style:italic">กำลังถาม…</div></div>
      {/if}
    </div>
    <div class="chat-in-row">
      <input type="text" placeholder="ถามเกี่ยวกับ XAUUSD..." bind:value={askInput} on:keydown={askKey}/>
      <button class="send-btn" on:click={sendAsk}>Send</button>
    </div>
  </div>

</div><!-- /main-grid -->
</div><!-- /office -->

<!-- ── AGENT MODAL ────────────────────────────────────────────────────── -->
{#if activeModal}
  <div class="modal-bg" on:click={() => activeModal=null}
       role="button" tabindex="0" on:keydown={e=>e.key==='Escape'&&(activeModal=null)}>
    <div class="modal" on:click|stopPropagation={() => {}} role="dialog">
      <div class="modal-hdr">
        <span>{PROMPTS[activeModal]?.title}</span>
        <button on:click={() => activeModal=null}>✕</button>
      </div>
      <pre class="modal-body">{PROMPTS[activeModal]?.body}</pre>
    </div>
  </div>
{/if}

<style>
  :global(*){box-sizing:border-box;margin:0;padding:0}
  :global(html,body){height:100%;overflow:hidden;background:#0f0f17;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#e5e7eb}

  /* ── OFFICE shell ─────────────────────────── */
  .office{display:grid;grid-template-rows:44px auto 1fr;height:100vh;overflow:hidden;background:#111118}

  /* ── TOP BAR ──────────────────────────────── */
  .topbar{display:flex;align-items:center;justify-content:space-between;padding:0 16px;gap:12px;background:#0f0f17;border-bottom:0.5px solid rgba(255,255,255,.08);flex-shrink:0}
  .tb-left{display:flex;align-items:center;gap:8px;min-width:0}
  .tb-title{font-size:14px;font-weight:500;color:#f9fafb;white-space:nowrap}
  .badge-live{background:#14532d;color:#86efac;font-size:10px;padding:1px 7px;border-radius:99px;white-space:nowrap}
  .badge-sess{background:#1c1c25;color:#9ca3af;font-size:10px;padding:1px 8px;border-radius:99px;border:0.5px solid rgba(255,255,255,.1);white-space:nowrap}
  .tb-mid{display:flex;align-items:center;gap:10px;flex:1;justify-content:center}
  .tb-pair{font-size:15px;font-weight:500;color:#f9fafb}
  .tb-status{font-size:10px;color:#6b7280}
  .tb-right{display:flex;align-items:center;gap:8px;flex-shrink:0}
  .live-dot{width:7px;height:7px;border-radius:50%;background:#374151}
  .live-dot.live{background:#4ade80;box-shadow:0 0 5px #4ade80}
  .scan-btn{padding:5px 14px;background:#d97706;color:#0E0800;border:none;border-radius:5px;font-size:11px;font-weight:500;cursor:pointer;white-space:nowrap}
  .scan-btn:hover:not(:disabled){background:#f59e0b}
  .scan-btn:disabled{opacity:.4;cursor:default}

  /* ── AGENTS ROW ───────────────────────────── */
  .agents-row{display:flex;gap:8px;padding:8px 14px;background:#0f0f17;border-bottom:0.5px solid rgba(255,255,255,.08);overflow-x:auto;flex-shrink:0}
  .agents-row::-webkit-scrollbar{height:3px}
  .agents-row::-webkit-scrollbar-thumb{background:#2a2a35;border-radius:99px}

  .ac{flex:0 0 auto;min-width:100px;border:0.5px solid rgba(255,255,255,.1);border-radius:8px;padding:8px 6px;text-align:center;cursor:pointer;background:#18181f;transition:border-color .15s}
  .ac:hover{border-color:rgba(255,255,255,.25)}
  .ac-sup{border-color:rgba(217,119,6,.4);background:#1a1408}
  .ac-sprite{width:46px;height:46px;margin:0 auto 5px;border-radius:50%;display:flex;align-items:center;justify-content:center;position:relative}
  .ac-dot{position:absolute;bottom:1px;right:1px;width:9px;height:9px;border-radius:50%;border:1.5px solid #18181f}
  .dot-green{background:#43a047}
  .dot-red{background:#e53935}
  .dot-yellow{background:#f9a825;animation:pulse 1.2s infinite}
  .dot-gray{background:#6b7280}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .ac-name{font-size:10px;font-weight:500;color:#f9fafb;line-height:1.3}
  .ac-role{font-size:9px;color:#6b7280;margin-top:1px}
  .ac-vote{font-size:9px;margin-top:4px;padding:2px 5px;border-radius:99px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .vote-yes{background:rgba(34,197,94,.15);color:#4ade80;border:0.5px solid rgba(34,197,94,.3)}
  .vote-no{background:rgba(239,68,68,.12);color:#f87171;border:0.5px solid rgba(239,68,68,.25)}
  .vote-wait{background:#1c1c25;color:#6b7280;border:0.5px solid rgba(255,255,255,.08)}

  /* ── MAIN GRID ────────────────────────────── */
  .main-grid{display:grid;grid-template-columns:260px 1fr 280px;overflow:hidden;min-height:0}

  /* shared panel */
  .panel{display:flex;flex-direction:column;overflow:hidden;border-right:0.5px solid rgba(255,255,255,.07)}
  .chat-panel{border-right:none}
  .ph{padding:8px 12px;border-bottom:0.5px solid rgba(255,255,255,.07);font-size:11px;font-weight:500;color:#9ca3af;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
  .ph-sub{font-size:9px;color:#4b5563}
  .pb{flex:1;overflow-y:auto;padding:8px}
  .pb::-webkit-scrollbar{width:3px}
  .pb::-webkit-scrollbar-thumb{background:#2a2a35;border-radius:99px}
  .no-item{font-size:11px;color:#4b5563;text-align:center;padding:20px}

  /* scan log entries */
  .le{padding:6px 8px;border-radius:5px;margin-bottom:4px;font-size:11px;line-height:1.5}
  .le-time{font-size:9.5px;color:#6b7280;font-family:monospace}
  .le-main{font-weight:500;color:#e5e7eb}
  .le-sub{font-size:9.5px;color:#6b7280}
  .le-ok{background:rgba(22,163,74,.1);border-left:2px solid #16a34a}
  .le-no{background:rgba(255,255,255,.03);border-left:2px solid rgba(255,255,255,.12)}

  /* signal levels */
  .sig-levels{display:grid;grid-template-columns:repeat(4,1fr);gap:3px;margin-bottom:5px}
  .sl-item{background:#1c1c25;border-radius:4px;padding:4px;text-align:center;border:0.5px solid rgba(255,255,255,.07)}
  .sl-lbl{font-size:8.5px;color:#6b7280}
  .sl-val{font-size:11px;font-weight:500;font-family:monospace;color:#e5e7eb}
  .sig-reason{font-size:9.5px;color:#6b7280;padding:4px 6px;background:#1c1c25;border-radius:4px;line-height:1.4}
  .sig-ok{color:#4ade80;background:rgba(74,222,128,.06)}

  /* ── CENTER column ────────────────────────── */
  .center-col{display:flex;flex-direction:column;overflow:hidden;border-right:0.5px solid rgba(255,255,255,.07)}
  .sum-strip{flex-shrink:0;padding:6px 10px;border-bottom:0.5px solid rgba(255,255,255,.07);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .ptabs{display:flex;gap:3px}
  .ptab{padding:2px 9px;border-radius:99px;font-size:9px;font-weight:500;cursor:pointer;border:0.5px solid rgba(255,255,255,.1);background:transparent;color:#6b7280}
  .ptab:hover{color:#d1d5db}
  .ptab-on{background:#d97706;border-color:#d97706;color:#fff}
  .sum-cards{display:flex;gap:5px;flex:1;justify-content:flex-end}
  .sc{background:#1c1c25;border-radius:5px;padding:4px 10px;text-align:center;border:0.5px solid rgba(255,255,255,.07);min-width:64px}
  .sc-lbl{font-size:8.5px;color:#6b7280}
  .sc-val{font-size:13px;font-weight:500;color:#e5e7eb}

  /* War room */
  .vr-wrap{flex:1;overflow:hidden;display:flex;align-items:center;justify-content:center;background:#0a0604;min-height:0}
  .vr{position:relative;width:500px;height:340px;flex-shrink:0}
  .bg{position:absolute;inset:0;background:#0E0903}
  .bg::before{content:'';position:absolute;inset:0;background-image:repeating-linear-gradient(90deg,rgba(255,255,255,.012) 0 1px,transparent 1px 48px),repeating-linear-gradient(0deg,rgba(255,255,255,.012) 0 1px,transparent 1px 48px)}
  .wall{position:absolute;top:0;left:0;right:0;height:58px;background:#150A03;border-bottom:2px solid #291505}
  .wall::before{content:'';position:absolute;inset:0;background-image:repeating-linear-gradient(90deg,rgba(255,255,255,.015) 0 1px,transparent 1px 60px),repeating-linear-gradient(0deg,rgba(255,255,255,.015) 0 1px,transparent 1px 30px)}
  .ban{position:absolute;top:3px;left:50%;transform:translateX(-50%);display:flex;gap:9px;align-items:flex-start;z-index:5}
  .ban-p{width:3px;height:50px;background:#7B4E28}
  .ban-f{background:#6B0000;width:56px;height:55px;clip-path:polygon(0 0,100% 0,100% 75%,50% 100%,0 75%);display:flex;align-items:center;justify-content:center}
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
  .vbn{position:absolute;top:48%;left:50%;transform:translate(-50%,-50%);background:rgba(6,3,0,.96);border:1px solid #D4A017;border-radius:8px;padding:11px 20px;text-align:center;z-index:40;color:#FFD54F;font-size:11px;font-weight:500;pointer-events:none;opacity:0;transition:opacity .4s;white-space:nowrap}
  .vbn.on{opacity:1}

  /* ── RIGHT: chat ──────────────────────────── */
  .qcs{padding:6px 10px;display:flex;gap:4px;flex-wrap:wrap;flex-shrink:0;border-bottom:0.5px solid rgba(255,255,255,.07)}
  .qc{font-size:9.5px;padding:3px 9px;border:0.5px solid rgba(255,255,255,.1);border-radius:99px;cursor:pointer;color:#9ca3af;background:#18181f}
  .qc:hover{background:#1e1e2a;color:#e5e7eb}
  .msgs{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:5px;min-height:0}
  .msgs::-webkit-scrollbar{width:3px}
  .msgs::-webkit-scrollbar-thumb{background:#2a2a35;border-radius:99px}
  .msg{display:flex;flex-direction:column;gap:2px}
  .msg-role{font-size:9px;color:#6b7280}
  .msg-bubble{padding:7px 10px;border-radius:8px;font-size:11px;line-height:1.5;max-width:92%;background:#1c1c25;border:0.5px solid rgba(255,255,255,.08);color:#d1d5db}
  .msg-user{align-items:flex-end}
  .msg-user .msg-bubble{background:#1e3a6e;color:#93c5fd;border-color:rgba(59,130,246,.2)}
  .chat-in-row{padding:7px 8px;border-top:0.5px solid rgba(255,255,255,.07);display:flex;gap:5px;flex-shrink:0}
  .chat-in-row input{flex:1;padding:5px 9px;font-size:11px;border:0.5px solid rgba(255,255,255,.12);border-radius:5px;outline:none;color:#e5e7eb;background:#18181f}
  .chat-in-row input:focus{border-color:#3b82f6}
  .send-btn{padding:5px 11px;background:#1d4ed8;color:#fff;border:none;border-radius:5px;font-size:11px;cursor:pointer}

  /* ── MODAL ────────────────────────────────── */
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:100;display:flex;align-items:center;justify-content:center}
  .modal{background:#18181f;border:0.5px solid rgba(255,255,255,.12);border-radius:10px;width:520px;max-height:75vh;overflow:hidden;display:flex;flex-direction:column}
  .modal-hdr{padding:12px 16px;border-bottom:0.5px solid rgba(255,255,255,.08);display:flex;justify-content:space-between;align-items:center;font-size:13px;font-weight:500;color:#f9fafb;flex-shrink:0}
  .modal-hdr button{background:none;border:none;cursor:pointer;color:#9ca3af;font-size:17px;line-height:1}
  .modal-body{padding:16px;overflow-y:auto;font-size:12px;line-height:1.7;font-family:monospace;white-space:pre-wrap;color:#9ca3af}
</style>
