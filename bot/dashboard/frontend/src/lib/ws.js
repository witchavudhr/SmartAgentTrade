import { writable, get } from 'svelte/store';

export const scanPhase   = writable('idle');
export const signal      = writable(null);
export const scanLog     = writable([]);
export const stats       = writable({ today_pnl: 0, open_pnl: 0, win_rate: 0, wins: 0, losses: 0, trades: [] });
export const agentBubble = writable({});  // { agentId: text }
export const connected   = writable(false);

let ws = null;
let reconnectTimer = null;

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const host  = location.host;  // dev: proxied, prod: same host
  ws = new WebSocket(`${proto}://${host}/ws`);

  ws.onopen = () => {
    connected.set(true);
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case 'init':
        scanPhase.set(msg.scan_phase);
        if (msg.signal) signal.set(msg.signal);
        if (msg.scan_log) scanLog.set(msg.scan_log);
        if (msg.stats) stats.set(msg.stats);
        break;
      case 'scan_phase':
        scanPhase.set(msg.phase);
        if (msg.phase === 'idle') agentBubble.set({});
        break;
      case 'agent_bubble':
        agentBubble.update(b => ({ ...b, [msg.agent]: msg.text }));
        setTimeout(() => agentBubble.update(b => { const n={...b}; delete n[msg.agent]; return n; }), 4500);
        break;
      case 'signal':
        signal.set(msg.data);
        break;
      case 'scan_log':
        scanLog.update(l => [msg.data, ...l].slice(0, 12));
        break;
      case 'stats':
        stats.set(msg.data);
        break;
    }
  };

  ws.onclose = () => {
    connected.set(false);
    reconnectTimer = setTimeout(connect, 3000);
  };
  ws.onerror = () => ws.close();
}

export function initWs() {
  if (typeof window === 'undefined') return;
  connect();
}

export async function triggerScan() {
  const res = await fetch('/api/scan', { method: 'POST' });
  return res.json();
}

export async function askAgents(question) {
  const res = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
  const d = await res.json();
  return d.response;
}
