# Stocks Arcade — Browser Multiplayer

A browser-based conversion of the uploaded `party_room.py`, `network_client.py`, and `network_server.py` online party-room/trading flow. The browser interface is HTML + CSS + JavaScript. `config.xml` stores game defaults/ranges and is read by the frontend. A Node.js WebSocket server is required for rooms and authoritative multiplayer state; this cannot be a purely static HTML page because browsers cannot accept the original Python TCP/UDP sockets.

## Run locally

1. Install Node.js 18 or newer.
2. Open a terminal in this folder.
3. Run:

   ```bash
   npm install
   npm start
   ```

4. Open <http://localhost:3000> in your browser. Use a second browser/private window to test another player. To test over a local network, use the host computer's LAN IP and port 3000 in the browser, with the firewall allowing the port.

## Deploy to Render

1. Upload this folder to a GitHub repository.
2. In Render, create a **Web Service** from the repository.
3. Use **Node** as the runtime, `npm install` as the build command, and `npm start` as the start command.
4. Use the free instance if available for your account. Keep in mind free services may sleep when idle, so the first connection can take a little while to wake.
5. Open the Render HTTPS URL. WebSocket traffic will automatically use secure `wss://` on HTTPS. Share the same site URL and the room code with friends.

Render instances are ephemeral, but this game stores active rooms in memory only, so a restart closes active rooms anyway. Do not run multiple server instances for the same room system without adding shared state/pub-sub.

## Implemented

- Create private six-character room codes and join by code
- Maximum 8 players; lobby roster and ready flags
- Host-only match start, with at least 2 players and every player ready
- Configurable starting cash, stock price, stock count, number of turns, difficulty, volatility
- Shared price simulation, news events, RSI/trend/volume display and price-history chart
- Host-authoritative buy/sell/skip validation and turn order
- Live player net-worth leaderboard and final rankings
- WebSocket disconnect handling and return-to-lobby flow after a completed game

## Notes / limitations

- LAN UDP auto-discovery and direct-IP room lookup were replaced with room codes over a central WebSocket server, which is the appropriate setup for browser internet play.
- The original main Pygame game file (`STOCKS New.py`) was not included in the supplied files. This project recreates the uploaded online party-room/trading mode, not every offline screen, sound, animation, AI, or menu from the full game.
- If deploying on Render, set the service to use a single instance because room membership is held in memory.
