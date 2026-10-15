'use strict';

const $ = (id) => document.getElementById(id);
const views = ['setupView', 'lobbyView', 'gameView', 'winnerView'];
const PLAYER_COLORS = ['#00c8ff', '#00ff6e', '#ffdc00', '#ff1c50', '#be00ff', '#ffcd00', '#ff00c8', '#ff9400'];

const state = {
  ws: null, connected: false, playerId: null, isHost: false, roomCode: '',
  players: [], settings: {}, status: 'setup', game: null, rankings: [], winnerName: '',
  chartLastPrice: null, toastTimer: null
};

function showView(id) {
  views.forEach((viewId) => $(viewId).classList.toggle('hidden', viewId !== id));
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (id === 'gameView') requestAnimationFrame(drawChart);
}

function toast(message, isError = false) {
  const el = $('toast');
  el.textContent = message;
  el.style.borderColor = isError ? '#ff4d7888' : '#34495e';
  el.classList.add('show');
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
}

function setConnection(connected, message) {
  state.connected = connected;
  $('connectionDot').className = `dot ${connected ? 'connected' : 'disconnected'}`;
  $('connectionText').textContent = message || (connected ? 'SERVER CONNECTED' : 'RECONNECTING');
}

function connect() {
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) return;
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${location.host}/ws`;
  const ws = new WebSocket(wsUrl);
  state.ws = ws;
  setConnection(false, 'CONNECTING');
  ws.addEventListener('open', () => setConnection(true, 'SERVER CONNECTED'));
  ws.addEventListener('message', (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    handleMessage(msg);
  });
  ws.addEventListener('close', () => {
    setConnection(false, 'SERVER OFFLINE');
    state.ws = null;
    setTimeout(connect, 1800);
  });
  ws.addEventListener('error', () => setConnection(false, 'CONNECTION ERROR'));
}

function send(type, payload = {}) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    toast('Not connected to the game server. Check your connection.', true);
    return false;
  }
  state.ws.send(JSON.stringify({ type, ...payload }));
  return true;
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'JOINED':
      state.playerId = msg.player_id;
      state.isHost = Boolean(msg.is_host);
      state.roomCode = msg.room_code;
      state.players = msg.players || [];
      state.settings = msg.settings || {};
      state.status = msg.status || 'lobby';
      state.game = msg.game_state || null;
      renderLobby();
      showView('lobbyView');
      toast(`${state.isHost ? 'Room created' : 'Joined room'} ${state.roomCode}`);
      break;
    case 'ROOM_UPDATE':
      state.players = msg.players || state.players;
      const self = state.players.find((p) => p.player_id === state.playerId);
      if (self) state.isHost = Boolean(self.is_host);
      state.settings = msg.settings || state.settings;
      state.status = msg.status || state.status;
      state.roomCode = msg.room_code || state.roomCode;
      if (state.status === 'lobby') {
        renderLobby();
        showView('lobbyView');
      }
      break;
    case 'GAME_START':
      state.players = msg.players || [];
      state.game = msg;
      state.settings = msg.settings || state.settings;
      state.status = 'in_game';
      $('eventBanner').classList.add('hidden');
      showView('gameView');
      renderGame();
      toast('Match started. Trade wisely!');
      break;
    case 'STATE_UPDATE':
      state.players = msg.players || state.players;
      state.game = { ...(state.game || {}), ...msg };
      renderGame();
      if (msg.active_event) showEvent(msg.active_event);
      if (msg.last_action) toast(msg.last_action);
      break;
    case 'TRADE_RESULT':
      toast(msg.message || (msg.success ? 'Trade executed.' : 'Trade rejected.'), !msg.success);
      break;
    case 'GAME_OVER':
      state.status = 'game_over';
      state.rankings = msg.rankings || [];
      state.winnerName = msg.winner_name || state.rankings[0]?.name || 'Unknown';
      renderWinner(msg);
      showView('winnerView');
      break;
    case 'PLAYER_DISCONNECTED':
      state.players = msg.players || state.players;
      renderGame();
      toast(`${msg.name || 'A trader'} disconnected.`, true);
      break;
    case 'LEFT_ROOM':
      resetSession();
      toast('You left the room.');
      break;
    case 'ERROR':
      toast(msg.message || 'Something went wrong.', true);
      break;
    case 'ROOM_CLOSED':
      resetSession();
      toast(msg.reason || 'The room was closed.', true);
      break;
    default:
      break;
  }
}

function resetSession() {
  state.playerId = null; state.isHost = false; state.roomCode = '';
  state.players = []; state.settings = {}; state.status = 'setup'; state.game = null;
  state.rankings = []; state.winnerName = ''; state.chartLastPrice = null;
  showView('setupView');
}

function validName(value, fallback = 'Trader') {
  return String(value || '').trim().slice(0, 16) || fallback;
}

$('createForm').addEventListener('submit', (event) => {
  event.preventDefault();
  const settings = {
    start_cash: Number($('startCash').value),
    start_price: Number($('startPrice').value),
    start_stocks: Number($('startStocks').value),
    max_turns: Number($('maxTurns').value),
    difficulty: $('difficulty').value,
    enable_volatility: $('volatility').checked
  };
  if (!Object.values(settings).slice(0, 4).every(Number.isFinite)) return toast('Check the market settings.', true);
  if (!state.connected) return toast('Waiting for the game server connection.', true);
  send('HOST_ROOM', { name: validName($('createName').value, 'Host'), settings });
});

$('joinForm').addEventListener('submit', (event) => {
  event.preventDefault();
  const code = $('roomCode').value.trim().toUpperCase();
  if (!/^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$/.test(code)) return toast('Enter a valid 6-character room code.', true);
  if (!state.connected) return toast('Waiting for the game server connection.', true);
  send('JOIN_ROOM', { code, name: validName($('joinName').value) });
});

$('roomCode').addEventListener('input', () => { $('roomCode').value = $('roomCode').value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6); });
$('copyCode').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(state.roomCode); toast('Room code copied.'); }
  catch { const input = document.createElement('textarea'); input.value = state.roomCode; document.body.appendChild(input); input.select(); const ok = document.execCommand('copy'); input.remove(); toast(ok ? 'Room code copied.' : `Room code: ${state.roomCode}`); }
});
$('readyButton').addEventListener('click', () => {
  const me = state.players.find((p) => p.player_id === state.playerId);
  send('SET_READY', { ready: !(me && me.ready) });
});
$('startButton').addEventListener('click', () => send('START_GAME'));
$('lobbyLeave').addEventListener('click', leaveRoom);
$('gameLeave').addEventListener('click', leaveRoom);
$('winnerLeave').addEventListener('click', leaveRoom);
$('winnerLobby').addEventListener('click', () => send('RETURN_TO_LOBBY'));
$('qtyMinus').addEventListener('click', () => setQuantity(quantity() - 1));
$('qtyPlus').addEventListener('click', () => setQuantity(quantity() + 1));
$('quantity').addEventListener('input', () => { setQuantity(quantity()); updateTradeEstimate(); });
$('buyButton').addEventListener('click', () => send('TRADE', { action: 'buy', quantity: quantity() }));
$('sellButton').addEventListener('click', () => send('TRADE', { action: 'sell', quantity: quantity() }));
$('skipButton').addEventListener('click', () => send('TRADE', { action: 'skip', quantity: 1 }));

function leaveRoom() {
  if (!state.roomCode) { resetSession(); return; }
  send('LEAVE_ROOM');
  // Leave the UI immediately; the server also cleans up the room membership.
  resetSession();
}

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}
function money(value) { return '$' + Number(value || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function me() { return state.players.find((p) => p.player_id === state.playerId); }
function colorFor(player) { return player.color || PLAYER_COLORS[(Number(player.player_id || 1) - 1) % PLAYER_COLORS.length]; }
function initialFor(name) { return String(name || 'T').trim().slice(0, 1).toUpperCase(); }

function renderLobby() {
  $('lobbyCode').textContent = state.roomCode || '------';
  $('lobbyCount').textContent = `${state.players.length} / 8`;
  $('lobbyPlayers').innerHTML = state.players.map((p) => {
    const own = p.player_id === state.playerId;
    const tags = `${p.is_host ? 'HOST' : 'TRADER'}${own ? ' · YOU' : ''}`;
    return `<div class="player-row"><div class="player-info"><span class="avatar" style="background:${colorFor(p)}">${escapeHTML(initialFor(p.name))}</span><div><b>${escapeHTML(p.name)}${own ? ' <span class="you-tag">YOU</span>' : ''}</b><small>${tags}</small></div></div><span class="status-tag ${p.ready ? 'ready' : ''}">${p.ready ? '✓ READY' : 'NOT READY'}</span></div>`;
  }).join('');
  $('lobbyEmpty').classList.toggle('hidden', state.players.length > 0);
  const settings = state.settings || {};
  $('lobbySettings').innerHTML = [
    ['Opening share price', money(settings.start_price)], ['Starting cash', money(settings.start_cash)],
    ['Starting shares', settings.start_stocks], ['Turn limit', settings.max_turns],
    ['Difficulty', settings.difficulty || 'Normal'], ['Volatility', settings.enable_volatility === false ? 'Off' : 'On']
  ].map(([label, value]) => `<div class="setting-line"><span>${label}</span><b>${escapeHTML(value)}</b></div>`).join('');
  const player = me();
  const ready = Boolean(player?.ready);
  $('readyButton').textContent = ready ? '✓ READY — CLICK TO UNREADY' : '✓ READY UP';
  $('readyButton').classList.toggle('btn-primary', !ready);
  $('readyButton').classList.toggle('btn-ghost', ready);
  $('startButton').classList.toggle('hidden', !state.isHost);
  const allReady = state.players.length >= 2 && state.players.every((p) => p.ready);
  $('startButton').disabled = !allReady;
  $('startButton').style.opacity = allReady ? '1' : '.45';
  $('startHint').textContent = state.players.length < 2 ? 'Waiting for at least 2 players.' : (!allReady ? 'Every trader must be READY before starting.' : 'All traders ready. Let the market begin!');
}

function activePlayer() {
  if (!state.game) return null;
  const pid = (state.game.turn_order || [])[state.game.turn_index || 0];
  return state.players.find((p) => p.player_id === pid) || null;
}
function isMyTurn() { const p = activePlayer(); return Boolean(p && p.player_id === state.playerId); }

function renderGame() {
  if (!state.game) return;
  $('gameRoomCode').textContent = state.roomCode || '------';
  const g = state.game;
  $('turnLabel').textContent = `${Number(g.current_turn || 0)} / ${Number(g.max_turns || state.settings.max_turns || 50)}`;
  $('marketPrice').textContent = money(g.price);
  if (state.chartLastPrice !== null && Number(g.price) !== state.chartLastPrice) {
    const wentUp = Number(g.price) >= state.chartLastPrice;
    $('marketMove').textContent = wentUp ? '▲ PRICE MOVED UP' : '▼ PRICE MOVED DOWN';
    $('marketMove').style.color = wentUp ? 'var(--green)' : 'var(--red)';
  }
  state.chartLastPrice = Number(g.price || 0);
  const player = me();
  $('myWorth').textContent = money(player?.net_worth);
  $('myCash').textContent = money(player?.cash);
  $('myStocks').textContent = Number(player?.stocks || 0).toLocaleString('en-US');
  $('rsiValue').textContent = Number(g.rsi ?? 50).toFixed(1);
  $('trendValue').textContent = `${(Number(g.trend || 0) * 100).toFixed(2)}%`;
  $('trendValue').style.color = Number(g.trend || 0) >= 0 ? 'var(--green)' : 'var(--red)';
  $('volumeValue').textContent = Number(g.volume || 100).toLocaleString('en-US');
  const cur = activePlayer();
  $('turnMessage').textContent = cur ? (cur.player_id === state.playerId ? 'Your turn — make a trade.' : `Waiting for ${cur.name} to trade…`) : 'Waiting for the next trader…';
  $('turnBadge').textContent = cur ? (cur.player_id === state.playerId ? 'YOUR TURN' : 'WAITING') : 'WAIT';
  $('turnBadge').classList.toggle('active', isMyTurn());
  ['buyButton', 'sellButton', 'skipButton', 'quantity', 'qtyMinus', 'qtyPlus'].forEach((id) => { $(id).disabled = !isMyTurn(); });
  $('buyButton').style.opacity = isMyTurn() ? '1' : '.45';
  $('sellButton').style.opacity = isMyTurn() ? '1' : '.45';
  $('skipButton').style.opacity = isMyTurn() ? '1' : '.45';
  $('gamePlayerCount').textContent = `${state.players.length} PLAYERS`;
  const sorted = [...state.players].sort((a, b) => Number(b.net_worth || 0) - Number(a.net_worth || 0));
  $('gamePlayers').innerHTML = sorted.map((p, i) => `<div class="standing-row"><span class="rank ${i === 0 ? 'first' : ''}">${String(i + 1).padStart(2, '0')}</span><div class="standing-name"><i class="mini-dot" style="background:${colorFor(p)}"></i><b>${escapeHTML(p.name)}</b>${p.player_id === state.playerId ? '<span class="you-tag">YOU</span>' : ''}${p.is_host ? '<span class="you-tag">HOST</span>' : ''}</div><div class="standing-stat"><small>NET WORTH</small><b>${money(p.net_worth)}</b></div><div class="standing-stat"><small>CASH</small><b>${money(p.cash)}</b></div><div class="standing-stat"><small>SHARES</small><b>${Number(p.stocks || 0)}</b></div></div>`).join('');
  updateTradeEstimate();
  requestAnimationFrame(drawChart);
}

function quantity() { return Math.max(1, Math.min(20, Math.round(Number($('quantity').value) || 1))); }
function setQuantity(value) { $('quantity').value = Math.max(1, Math.min(20, Math.round(Number(value) || 1))); updateTradeEstimate(); }
function updateTradeEstimate() { const total = quantity() * Number(state.game?.price || state.settings.start_price || 10); $('estimateValue').textContent = money(total); }
function showEvent(event) {
  const el = $('eventBanner');
  el.classList.remove('hidden');
  el.classList.toggle('down', Number(event.impact || 1) < 1);
  const direction = Number(event.impact || 1) >= 1 ? '▲ MARKET SURGE' : '▼ MARKET DROP';
  el.innerHTML = `<b>${escapeHTML(event.title || 'MARKET EVENT')} · ${direction}</b><p>${escapeHTML(event.description || '')}</p>`;
}

function drawChart() {
  const canvas = $('priceChart');
  if (!canvas || $('gameView').classList.contains('hidden')) return;
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 2 || rect.height < 2) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * dpr); canvas.height = Math.floor(rect.height * dpr);
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height;
  ctx.clearRect(0, 0, w, h);
  const history = (state.game?.price_history || []).map(Number).filter(Number.isFinite);
  if (history.length < 2) { ctx.fillStyle = '#718399'; ctx.font = '11px monospace'; ctx.fillText('Waiting for price movement…', 14, h / 2); return; }
  const pad = { left: 10, right: 62, top: 18, bottom: 18 };
  const min = Math.min(...history), max = Math.max(...history), range = Math.max(max - min, max * .02, .05);
  const lo = min - range * .12, hi = max + range * .12;
  const cw = w - pad.left - pad.right, ch = h - pad.top - pad.bottom;
  ctx.font = '9px "DM Mono", monospace'; ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + ch * i / 4;
    ctx.beginPath(); ctx.strokeStyle = '#243343'; ctx.lineWidth = 1; ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + cw, y); ctx.stroke();
    const value = hi - (hi - lo) * i / 4;
    ctx.fillStyle = '#718399'; ctx.fillText('$' + value.toFixed(2), w - 4, y + 3);
  }
  const points = history.map((price, i) => ({ x: pad.left + cw * i / (history.length - 1), y: pad.top + ch * (hi - price) / (hi - lo) }));
  const current = history[history.length - 1], start = history[0], color = current >= start ? '#00f5a0' : '#ff4d78';
  ctx.beginPath(); ctx.moveTo(points[0].x, points[0].y); points.forEach(p => ctx.lineTo(p.x, p.y));
  ctx.lineTo(points[points.length - 1].x, pad.top + ch); ctx.lineTo(points[0].x, pad.top + ch); ctx.closePath();
  const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + ch); grad.addColorStop(0, color + '30'); grad.addColorStop(1, color + '00'); ctx.fillStyle = grad; ctx.fill();
  ctx.beginPath(); points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.shadowColor = color; ctx.shadowBlur = 10; ctx.stroke(); ctx.shadowBlur = 0;
  const last = points[points.length - 1]; ctx.beginPath(); ctx.arc(last.x, last.y, 4, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
  ctx.textAlign = 'left'; ctx.fillStyle = '#dfeaf5'; ctx.font = '10px "DM Mono", monospace'; ctx.fillText('$' + current.toFixed(2), Math.min(last.x + 8, w - 61), Math.max(12, last.y - 10));
}
window.addEventListener('resize', () => requestAnimationFrame(drawChart));

function renderWinner(msg = {}) {
  const rankings = state.rankings || [];
  $('winnerSubtitle').textContent = `${state.winnerName} wins with ${money(msg.winner_net_worth ?? rankings[0]?.net_worth)} net worth.`;
  $('finalRankings').innerHTML = rankings.map((p, i) => `<div class="final-row"><span class="final-rank">#${i + 1}</span><div class="final-name"><b>${escapeHTML(p.name)}${p.player_id === state.playerId ? ' <span class="you-tag">YOU</span>' : ''}</b><small>${Number(p.stocks || 0)} shares · ${money(p.cash)} cash</small></div><strong class="final-worth">${money(p.net_worth)}</strong></div>`).join('');
}

async function loadXmlConfig() {
  try {
    const response = await fetch('/config.xml', { cache: 'no-store' });
    if (!response.ok) return;
    const xmlText = await response.text();
    const xml = new DOMParser().parseFromString(xmlText, 'application/xml');
    if (xml.querySelector('parsererror')) return;
    for (const setting of xml.querySelectorAll('settings setting')) {
      const key = setting.getAttribute('key');
      const elementMap = { start_price: 'startPrice', start_cash: 'startCash', start_stocks: 'startStocks', max_turns: 'maxTurns' };
      const input = $(elementMap[key]);
      if (!input) continue;
      input.min = setting.getAttribute('min') || input.min;
      input.max = setting.getAttribute('max') || input.max;
      input.value = setting.getAttribute('default') || input.value;
    }
  } catch (error) {
    console.warn('Could not load config.xml; using built-in defaults.', error);
  }
}

connect();
loadXmlConfig();

setInterval(() => {
  const socket = state.ws;

  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      type: "PING",
      timestamp: Date.now()
    }));
  }
}, 30_000);
