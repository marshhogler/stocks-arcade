'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { WebSocketServer, WebSocket } = require('ws');

const PORT = Number(process.env.PORT || 3000);
const ROOT = __dirname;
const ROOM_CODE_CHARS = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
const PLAYER_COLORS = ['#00c8ff', '#00ff6e', '#ffdc00', '#ff1c50', '#be00ff', '#ffcd00', '#ff00c8', '#ff9400'];
const DIFFICULTY_MULTIPLIER = { Easy: 0.6, Normal: 1.0, Hard: 1.6, Extreme: 2.4 };
const rooms = new Map();

const MIME_TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.xml': 'application/xml; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' };

const server = http.createServer((req, res) => {
  const pathname = decodeURIComponent(new URL(req.url, `http://${req.headers.host || 'localhost'}`).pathname);
  const requested = pathname === '/' ? '/index.html' : pathname;
  const filePath = path.resolve(ROOT, `.${requested}`);
  if (!filePath.startsWith(ROOT + path.sep) && filePath !== path.join(ROOT, 'index.html')) {
    res.writeHead(403); res.end('Forbidden'); return;
  }
  fs.readFile(filePath, (error, data) => {
    if (error) { res.writeHead(error.code === 'ENOENT' ? 404 : 500, { 'Content-Type': 'text/plain; charset=utf-8' }); res.end(error.code === 'ENOENT' ? 'Not found' : 'Server error'); return; }
    res.writeHead(200, { 'Content-Type': MIME_TYPES[path.extname(filePath)] || 'application/octet-stream', 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-cache' });
    res.end(data);
  });
});
const wss = new WebSocketServer({ server, path: '/ws', maxPayload: 16 * 1024 });

function send(ws, payload) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
}
function broadcast(room, payload) { for (const p of room.players.values()) send(p.socket, payload); }
function errorTo(ws, message) { send(ws, { type: 'ERROR', message }); }
function codeGen() {
  let code;
  do { code = Array.from({ length: 6 }, () => ROOM_CODE_CHARS[crypto.randomInt(ROOM_CODE_CHARS.length)]).join(''); } while (rooms.has(code));
  return code;
}
function normalizeSettings(raw = {}) {
  const allowed = ['Easy', 'Normal', 'Hard', 'Extreme'];
  const n = (key, fallback, min, max, integer = false) => {
    const value = Number(raw[key] ?? fallback);
    if (!Number.isFinite(value)) return fallback;
    const clamped = Math.max(min, Math.min(max, value));
    return integer ? Math.round(clamped) : clamped;
  };
  return {
    start_price: n('start_price', 10, 1, 1000),
    max_turns: n('max_turns', 50, 5, 200, true),
    start_cash: n('start_cash', 200, 10, 10000),
    start_stocks: n('start_stocks', 10, 0, 100, true),
    difficulty: allowed.includes(raw.difficulty) ? raw.difficulty : 'Normal',
    enable_volatility: raw.enable_volatility !== false
  };
}
function safeName(name, fallback = 'Trader') { return String(name || '').trim().slice(0, 16) || fallback; }
function playerList(room) { return [...room.players.values()].map(({ socket, ...p }) => ({ ...p })); }
function roomUpdate(room) { broadcast(room, { type: 'ROOM_UPDATE', room_code: room.code, status: room.status, players: playerList(room), settings: room.settings }); }
function getPlayer(ws) {
  const room = ws.roomCode && rooms.get(ws.roomCode);
  if (!room) return { room: null, player: null };
  const player = room.players.get(ws.playerId);
  return { room, player };
}
function addPlayer(room, ws, name, isHost = false) {
  if (room.players.size >= 8) return null;
  const id = room.nextPlayerId++;
  const cash = room.settings.start_cash;
  const stocks = room.settings.start_stocks;
  const player = {
    player_id: id, name: safeName(name, isHost ? 'Host' : 'Player'), is_host: isHost,
    color: PLAYER_COLORS[(room.players.size) % PLAYER_COLORS.length], ready: isHost,
    cash, stocks, net_worth: cash + stocks * room.settings.start_price,
    connected: true, socket: ws
  };
  room.players.set(id, player); ws.roomCode = room.code; ws.playerId = id;
  return player;
}
function joinResponse(room, player, ws) {
  send(ws, { type: 'JOINED', player_id: player.player_id, is_host: player.is_host, room_code: room.code, players: playerList(room), settings: room.settings, status: room.status });
  roomUpdate(room);
}
function createRoom(ws, msg) {
  if (ws.roomCode) leaveRoom(ws, false);
  const code = codeGen();
  const room = {
    code, status: 'lobby', settings: normalizeSettings(msg.settings), players: new Map(),
    nextPlayerId: 1, sim: null, currentTurn: 0, turnOrder: [], turnIndex: 0
  };
  rooms.set(code, room);
  const host = addPlayer(room, ws, msg.name || 'Host', true);
  if (!host) { rooms.delete(code); errorTo(ws, 'Could not create room.'); return; }
  joinResponse(room, host, ws);
  console.log(`[room ${code}] created by ${host.name}`);
}
function joinRoom(ws, msg) {
  if (ws.roomCode) leaveRoom(ws, false);
  const code = String(msg.code || '').trim().toUpperCase();
  const room = rooms.get(code);
  if (!room) return errorTo(ws, `Room '${code}' not found. Check the code and try again.`);
  if (room.status !== 'lobby') return errorTo(ws, 'Match already in progress.');
  if (room.players.size >= 8) return errorTo(ws, 'Room is full (maximum 8 players).');
  const player = addPlayer(room, ws, msg.name || 'Player', room.players.size === 0);
  if (!player) return errorTo(ws, 'Room is full.');
  joinResponse(room, player, ws);
  console.log(`[room ${code}] ${player.name} joined`);
}

class MarketSimulator {
  constructor(initialPrice) {
    this.initial_price = Number(initialPrice); this.current_price = Number(initialPrice);
    this.mu = 0.0005; this.sigma = 0.02; this.price_history = [this.current_price];
    this.volatility_enabled = true; this.news_cooldown = 0; this.active_events = [];
    this.rsi = 50; this.volume = 100;
  }
  calculateRSI(periods = 14) {
    const prices = this.price_history;
    if (prices.length < 2) return 50;
    const p = Math.max(2, Math.min(Number(periods), prices.length - 1));
    let gains = 0, losses = 0;
    const window = prices.slice(-(p + 1));
    for (let i = 1; i < window.length; i++) { const d = window[i] - window[i - 1]; if (d > 0) gains += d; else losses += Math.abs(d); }
    const ag = gains / p, al = losses / p;
    if (ag === 0 && al === 0) return 50;
    if (al <= 1e-12) return 100;
    const rs = ag / Math.max(al, 1e-12);
    return Math.max(0, Math.min(100, 100 - (100 / (1 + rs))));
  }
  generateNews() {
    const templates = [
      ['TECH BREAKTHROUGH', 'Major innovation announced', 1.15, 1.25],
      ['ECONOMIC SURGE', 'GDP exceeds expectations', 1.08, 1.15],
      ['MARKET CORRECTION', 'Analysts predict downturn', 0.85, 0.92],
      ['POLICY SHIFT', 'New regulations impact sector', 0.90, 1.10],
      ['MEGA MERGER', 'Corporate giants join forces', 1.10, 1.20],
      ['EARNINGS BEAT', 'Quarterly profits explode', 1.12, 1.18],
      ['SUPPLY CRISIS', 'Delays cripple production', 0.88, 0.95],
      ['CEO SCANDAL', 'Leadership shake-up rocks market', 0.92, 1.05],
      ['AI GOLD RUSH', 'AI investments hit peak frenzy', 1.18, 1.30],
      ['INTEREST HIKE', 'Central bank raises rates', 0.82, 0.92]
    ];
    const [title, description, lo, hi] = templates[crypto.randomInt(templates.length)];
    return { title, description, impact: lo + Math.random() * (hi - lo), duration: crypto.randomInt(3, 9) };
  }
  step(volMultiplier = 1) {
    let eventFactor = 1;
    for (const ev of [...this.active_events]) {
      ev.duration -= 1;
      if (ev.duration <= 0) this.active_events.splice(this.active_events.indexOf(ev), 1);
      else eventFactor *= 1 + (ev.impact - 1) * 0.2;
    }
    const z = gaussianRandom();
    const sigma = this.sigma * (this.volatility_enabled ? volMultiplier : 1);
    const drift = this.mu - 0.5 * sigma * sigma;
    const diff = sigma * z;
    let newPrice = this.current_price * Math.exp(drift + diff) * eventFactor;
    newPrice = Math.max(0.01, newPrice);
    let newEvent = null;
    if (this.news_cooldown <= 0 && Math.random() < 0.02 * volMultiplier) {
      newEvent = this.generateNews(); this.active_events.push(newEvent); newPrice *= newEvent.impact;
      this.news_cooldown = crypto.randomInt(5, 16);
    } else this.news_cooldown = Math.max(0, this.news_cooldown - 1);
    this.current_price = newPrice; this.price_history.push(newPrice);
    if (this.price_history.length > 1000) this.price_history = this.price_history.slice(-1000);
    this.rsi = this.calculateRSI(); this.volume = Math.max(10, Math.trunc(this.volume + (Math.random() * 20 - 10)));
    return { price: this.current_price, event: newEvent };
  }
  trend(periods = 10) {
    if (this.price_history.length < periods) return 0;
    const slice = this.price_history.slice(-periods);
    return (slice[slice.length - 1] - slice[0]) / slice[0];
  }
}
function gaussianRandom() { let u = 0, v = 0; while (u === 0) u = Math.random(); while (v === 0) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
function recalcNetWorth(room, price) { for (const p of room.players.values()) p.net_worth = p.cash + p.stocks * price; }
function advanceToConnected(room) {
  if (!room.turnOrder.length) return;
  for (let i = 0; i < room.turnOrder.length; i++) {
    const candidate = room.players.get(room.turnOrder[room.turnIndex]);
    if (candidate && candidate.connected) return;
    room.turnIndex = (room.turnIndex + 1) % room.turnOrder.length;
  }
}
function startGame(ws) {
  const { room, player } = getPlayer(ws);
  if (!room || !player) return errorTo(ws, 'You are not in a room.');
  if (!player.is_host) return errorTo(ws, 'Only the host can start the match.');
  if (room.status !== 'lobby') return errorTo(ws, 'This room is not in the lobby.');
  if (room.players.size < 2) return errorTo(ws, 'Need at least 2 players to start.');
  if (![...room.players.values()].every(p => p.ready)) return errorTo(ws, 'All players must be READY before starting.');
  room.status = 'in_game'; room.turnOrder = [...room.players.keys()]; room.turnIndex = 0; room.currentTurn = 0;
  room.sim = new MarketSimulator(room.settings.start_price);
  room.sim.volatility_enabled = room.settings.enable_volatility;
  const vol = DIFFICULTY_MULTIPLIER[room.settings.difficulty] || 1;
  for (let i = 0; i < 4; i++) room.sim.step(vol);
  for (const p of room.players.values()) { p.cash = room.settings.start_cash; p.stocks = room.settings.start_stocks; p.net_worth = p.cash + p.stocks * room.sim.current_price; }
  broadcast(room, {
    type: 'GAME_START', settings: room.settings, players: playerList(room), turn_order: room.turnOrder,
    turn_index: room.turnIndex, current_turn: room.currentTurn, max_turns: room.settings.max_turns,
    price: room.sim.current_price, price_history: [...room.sim.price_history], rsi: room.sim.rsi,
    trend: room.sim.trend(), volume: room.sim.volume
  });
}
function trade(ws, msg) {
  const { room, player } = getPlayer(ws);
  if (!room || !player) return errorTo(ws, 'You are not in a room.');
  const action = String(msg.action || '').toLowerCase();
  const qty = Number(msg.quantity);
  if (!Number.isInteger(qty)) return send(ws, { type: 'TRADE_RESULT', success: false, message: 'Quantity must be an integer!' });
  if (qty <= 0) return send(ws, { type: 'TRADE_RESULT', success: false, message: 'Quantity must be positive!' });
  if (room.status !== 'in_game' || !room.sim) return send(ws, { type: 'TRADE_RESULT', success: false, message: 'Game is not in session!' });
  if (!room.turnOrder.length) return;
  // Advance over disconnected players so the game cannot freeze on a departed player.
  advanceToConnected(room);
  const activeId = room.turnOrder[room.turnIndex];
  if (player.player_id !== activeId) return send(ws, { type: 'TRADE_RESULT', success: false, message: 'Not your turn!' });
  const price = room.sim.current_price;
  let description = '';
  if (action === 'buy') {
    const cost = qty * price;
    if (cost > player.cash + 0.01) return send(ws, { type: 'TRADE_RESULT', success: false, message: `Insufficient funds! Needs ${money(cost)}, has ${money(player.cash)}` });
    if (qty > 20) return send(ws, { type: 'TRADE_RESULT', success: false, message: 'Quantity must be between 1 and 20!' });
    player.cash -= cost; player.stocks += qty;
    description = `${player.name} BOUGHT ${qty} SHARES @ $${price.toFixed(2)}`;
  } else if (action === 'sell') {
    if (qty > player.stocks) return send(ws, { type: 'TRADE_RESULT', success: false, message: `Insufficient shares! Has ${player.stocks}, tried to sell ${qty}` });
    if (qty > 20) return send(ws, { type: 'TRADE_RESULT', success: false, message: 'Quantity must be between 1 and 20!' });
    player.stocks -= qty; player.cash += qty * price;
    description = `${player.name} SOLD ${qty} SHARES @ $${price.toFixed(2)}`;
  } else if (action === 'skip') description = `${player.name} SKIPPED TURN`;
  else return send(ws, { type: 'TRADE_RESULT', success: false, message: `Unknown action: ${action}` });
  recalcNetWorth(room, price);
  const multiplier = DIFFICULTY_MULTIPLIER[room.settings.difficulty] || 1;
  const marketStep = room.sim.step(multiplier);
  recalcNetWorth(room, marketStep.price);
  room.turnIndex = (room.turnIndex + 1) % room.turnOrder.length;
  advanceToConnected(room);
  room.currentTurn += 1;
  send(ws, { type: 'TRADE_RESULT', success: true, message: 'Trade executed successfully.' });
  if (room.currentTurn >= room.settings.max_turns) return endMatch(room);
  const event = marketStep.event ? { title: marketStep.event.title, description: marketStep.event.description, impact: marketStep.event.impact } : null;
  broadcast(room, {
    type: 'STATE_UPDATE', turn_index: room.turnIndex, current_turn: room.currentTurn,
    max_turns: room.settings.max_turns, price: marketStep.price,
    price_history: [...room.sim.price_history], rsi: room.sim.rsi, trend: room.sim.trend(), volume: room.sim.volume,
    players: playerList(room), last_action: description, active_event: event,
    turn_order: room.turnOrder
  });
}
function money(value) { return '$' + Number(value || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function endMatch(room) {
  room.status = 'game_over';
  const rankings = playerList(room).sort((a, b) => b.net_worth - a.net_worth);
  broadcast(room, { type: 'GAME_OVER', rankings, winner_name: rankings[0]?.name || 'Unknown', winner_net_worth: rankings[0]?.net_worth || 0, price: room.sim.current_price });
}
function returnToLobby(ws) {
  const { room, player } = getPlayer(ws);
  if (!room || !player) return errorTo(ws, 'You are not in a room.');
  if (room.status !== 'game_over') return errorTo(ws, 'The game has not ended yet.');
  room.status = 'lobby'; room.sim = null; room.currentTurn = 0; room.turnOrder = []; room.turnIndex = 0;
  for (const p of room.players.values()) {
    p.ready = p.is_host; p.cash = room.settings.start_cash; p.stocks = room.settings.start_stocks;
    p.net_worth = p.cash + p.stocks * room.settings.start_price;
  }
  roomUpdate(room);
}
function leaveRoom(ws, tellClient = true) {
  const { room, player } = getPlayer(ws);
  if (!room || !player) { ws.roomCode = null; ws.playerId = null; return; }
  const wasHost = player.is_host;
  if (room.status === 'lobby' || room.status === 'game_over') {
    room.players.delete(player.player_id);
    if (wasHost && room.players.size) {
      const next = room.players.values().next().value; next.is_host = true; next.ready = true;
    }
  } else {
    player.connected = false;
    // Don't remove their assets from the final score; do remove the socket association.
    player.socket = null;
    if (room.turnOrder.length && room.turnOrder[room.turnIndex] === player.player_id) room.turnIndex = (room.turnIndex + 1) % room.turnOrder.length;
    advanceToConnected(room);
  }
  ws.roomCode = null; ws.playerId = null;
  if (tellClient) send(ws, { type: 'LEFT_ROOM' });
  if (room.players.size === 0) rooms.delete(room.code);
  else if (room.status === 'lobby') roomUpdate(room);
  else broadcast(room, { type: 'PLAYER_DISCONNECTED', player_id: player.player_id, name: player.name, turn_index: room.turnIndex, players: playerList(room) });
}

wss.on('connection', (ws) => {
  ws.roomCode = null; ws.playerId = null;
  send(ws, { type: 'SERVER_READY', message: 'Stocks Arcade server connected.' });
  ws.on('message', (data) => {
    let msg;
    try { msg = JSON.parse(data.toString()); } catch { return errorTo(ws, 'Invalid message format.'); }
    if (!msg || typeof msg.type !== 'string') return errorTo(ws, 'Message type is required.');
    try {
      switch (msg.type) {
        case 'HOST_ROOM': createRoom(ws, msg); break;
        case 'JOIN_ROOM': joinRoom(ws, msg); break;
        case 'SET_READY': {
          const { room, player } = getPlayer(ws);
          if (!room || !player || room.status !== 'lobby') return errorTo(ws, 'Ready status can only be changed in the lobby.');
          player.ready = Boolean(msg.ready); roomUpdate(room); break;
        }
        case 'START_GAME': startGame(ws); break;
        case 'TRADE': trade(ws, msg); break;
        case 'LEAVE_ROOM': leaveRoom(ws, true); break;
        case 'RETURN_TO_LOBBY': returnToLobby(ws); break;
        case 'PING': send(ws, { type: 'PONG', timestamp: msg.timestamp || 0 }); break;
        default: errorTo(ws, `Unknown message type: ${msg.type}`);
      }
    } catch (err) {
      console.error('Message handler error:', err);
      errorTo(ws, 'The server could not process that action.');
    }
  });
  ws.on('close', () => leaveRoom(ws, false));
  ws.on('error', () => leaveRoom(ws, false));
});

server.listen(PORT, '0.0.0.0', () => console.log(`Stocks Arcade Web is listening on port ${PORT}`));
