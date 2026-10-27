"""
====================================================================
  STOCKS ARCADE — AUTOMATED COMPREHENSIVE TEST SUITE
====================================================================
Tests backend endpoints, database atomicity, idempotency,
5 mathematical market models, 5 adaptive AI bots, and WebSocket multiplayer.
"""

import os
import sys
import time
import json
import uuid
import math
import random
import asyncio
import threading
import requests
import uvicorn

# Set development environment for local testing
os.environ["APP_ENV"] = "development"
os.environ["JWT_SECRET"] = "dev-test-secret-key-at-least-16-bytes"

from server import app, MathMarketEngine, AdaptiveAIEngine

TEST_PORT = 8555
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"

def run_test_server():
    uvicorn.run(app, host="127.0.0.1", port=TEST_PORT, log_level="warning")

def run_tests():
    # 1. Start test server in background thread
    server_thread = threading.Thread(target=run_test_server, daemon=True)
    server_thread.start()
    time.sleep(2)  # Wait for startup

    print("====================================================================")
    print("STOCKS ARCADE AUTOMATED TEST SUITE EXECUTION")
    print("====================================================================")

    results = []

    # Helper assertion
    def check(name, condition, details=""):
        if condition:
            print(f"  [PASS] {name}")
            results.append((name, True, details))
        else:
            print(f"  [FAIL] {name} — {details}")
            results.append((name, False, details))

    # Test 1: Health check
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        check("Health Check", r.status_code == 200 and r.json().get("status") == "healthy")
    except Exception as e:
        check("Health Check", False, str(e))

    # Test 2: User Registration
    user1 = f"cyber_{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={"username": user1, "password": "SecretPassword123"})
    check("Player Registration", r.status_code == 200 and "token" in r.json())
    t1 = r.json().get("token")
    p1_id = r.json().get("player", {}).get("id")

    # Test 3: Duplicate Registration (Case-insensitive)
    r_dup = requests.post(f"{BASE_URL}/api/auth/register", json={"username": user1.upper(), "password": "DifferentPassword"})
    check("Duplicate Username Rejection (409)", r_dup.status_code == 409)

    # Test 4: Player Login
    r_login = requests.post(f"{BASE_URL}/api/auth/login", json={"username": user1, "password": "SecretPassword123"})
    check("Player Login & Token Issuance", r_login.status_code == 200 and r_login.json().get("player", {}).get("cash") == 1000.0)

    # Test 5: Portfolio & Initial Starting State
    headers1 = {"Authorization": f"Bearer {t1}"}
    r_me = requests.get(f"{BASE_URL}/api/auth/me", headers=headers1)
    me_data = r_me.json()
    check(
        "Initial Global Portfolio ($1,000 cash, 10 shares)",
        me_data.get("portfolio", {}).get("cash") == 1000.0 and me_data.get("portfolio", {}).get("stocks") == 10
    )

    # Test 6: Authoritative Atomic Trade Execution (BUY)
    idem_key1 = f"trade_{uuid.uuid4().hex}"
    r_buy = requests.post(
        f"{BASE_URL}/api/trades",
        headers=headers1,
        json={"symbol": "STK", "action": "BUY", "quantity": 5, "execution_price": 20.0, "idempotency_key": idem_key1}
    )
    buy_data = r_buy.json()
    check(
        "Authoritative BUY Execution ($100 deducted, cash: $900, stocks: 15)",
        buy_data.get("success") and buy_data.get("remaining_cash") == 900.0 and buy_data.get("remaining_stocks") == 15
    )

    # Test 7: Idempotency Verification (Prevent Double-Spending)
    r_buy_replay = requests.post(
        f"{BASE_URL}/api/trades",
        headers=headers1,
        json={"symbol": "STK", "action": "BUY", "quantity": 5, "execution_price": 20.0, "idempotency_key": idem_key1}
    )
    check(
        "Trade Idempotency (Duplicate Request Handled Without Double Deduction)",
        r_buy_replay.json().get("duplicate") is True and r_buy_replay.json().get("remaining_cash") == 900.0
    )

    # Test 8: Insufficient Funds Rejection
    r_fail_buy = requests.post(
        f"{BASE_URL}/api/trades",
        headers=headers1,
        json={"symbol": "STK", "action": "BUY", "quantity": 500, "execution_price": 100.0}
    )
    check("Insufficient Funds Validation (400 Rejection)", r_fail_buy.status_code == 400)

    # Test 9: Authoritative Atomic Trade Execution (SELL)
    r_sell = requests.post(
        f"{BASE_URL}/api/trades",
        headers=headers1,
        json={"symbol": "STK", "action": "SELL", "quantity": 4, "execution_price": 25.0}
    )
    sell_data = r_sell.json()
    check(
        "Authoritative SELL Execution ($100 added, cash: $1000, stocks: 11, profit recorded)",
        sell_data.get("success") and sell_data.get("remaining_cash") == 1000.0 and sell_data.get("remaining_stocks") == 11
    )

    # Test 10: Insufficient Shares Rejection
    r_fail_sell = requests.post(
        f"{BASE_URL}/api/trades",
        headers=headers1,
        json={"symbol": "STK", "action": "SELL", "quantity": 100, "execution_price": 25.0}
    )
    check("Insufficient Shares Validation (400 Rejection)", r_fail_sell.status_code == 400)

    # Test 11: Saved Games Creation & 6-Char Invite Code
    r_game = requests.post(
        f"{BASE_URL}/api/saved-games",
        headers=headers1,
        json={"name": "Cyber Syndicate Alpha", "visibility": "invite_code", "market_model": "trend_momentum", "max_turns": 50}
    )
    game_info = r_game.json().get("game", {})
    invite_code = game_info.get("invite_code")
    game_id = game_info.get("id")
    check(
        "Create Saved Game with 6-char Invite Code",
        len(invite_code) == 6 and invite_code.isalnum() and game_info.get("current_season") == 1
    )

    # Test 12: Second Player joins via Invite Code
    user2 = f"trader_{uuid.uuid4().hex[:6]}"
    r_reg2 = requests.post(f"{BASE_URL}/api/auth/register", json={"username": user2, "password": "Password456"})
    t2 = r_reg2.json().get("token")
    r_join = requests.post(f"{BASE_URL}/api/saved-games/join", headers={"Authorization": f"Bearer {t2}"}, json={"invite_code": invite_code})
    check("Player 2 Joins via Invite Code", r_join.status_code == 200 and r_join.json().get("success"))

    # Test 13: Season Progression
    r_next_season = requests.post(f"{BASE_URL}/api/saved-games/{game_id}/seasons", headers=headers1)
    season_data = r_next_season.json().get("season", {})
    check("Sequential Season Advancement (Season 2)", season_data.get("season_number") == 2)

    # Test 14: Five Mathematical Stock Market Models
    models = ["trend_momentum", "mean_reversion", "cyclical_harmonic", "volatility_regime", "complex_hybrid"]
    all_models_valid = True
    for m in models:
        cur_p = 50.0
        hist = [cur_p]
        for t in range(1, 20):
            res = MathMarketEngine.step(m, cur_p, hist, t, seed=12345)
            cur_p = res["price"]
            hist.append(cur_p)
            if cur_p <= 0 or not math.isfinite(cur_p):
                all_models_valid = False
                break
    check("Five Mathematical Stochastic Market Engines (All produce positive, valid paths)", all_models_valid)

    # Test 15: Five Adaptive AI Trading Personalities
    bots = ["Ragnarok", "Megatron", "Overlord", "Nemesis", "Singularity"]
    ai_decisions_valid = True
    test_hist = [10.0, 10.5, 11.2, 12.0, 12.8]
    for b in bots:
        dec = AdaptiveAIEngine.decide(b, cash=500.0, stocks=10, price=12.8, history=test_hist, rsi=65.0)
        if dec["action"] not in ["BUY", "SELL", "HOLD"]:
            ai_decisions_valid = False
    check("Five Adaptive AI Opponents (Ragnarok, Megatron, Overlord, Nemesis, Singularity)", ai_decisions_valid)

    # Test 16: WebSocket Multiplayer Room Creation & Joining (Authenticated)
    async def test_ws_flow():
        import websockets
        ws_url = f"ws://127.0.0.1:{TEST_PORT}/ws"
        async with websockets.connect(f"{ws_url}?token={t1}") as ws1, websockets.connect(f"{ws_url}?token={t2}") as ws2:
            # P1 creates room
            await ws1.send(json.dumps({"type": "create_room", "settings": {"startCash": 250.0}}))
            s1 = json.loads(await ws1.recv())
            rcode = s1["room"]["code"]

            # P2 joins room
            await ws2.send(json.dumps({"type": "join_room", "room_code": rcode}))
            s2 = json.loads(await ws2.recv())
            await ws1.recv() # room update on ws1

            # P1 starts match
            await ws1.send(json.dumps({"type": "start_game"}))
            g_state = json.loads(await ws1.recv())
            await ws2.recv()

            # P1 executes Buy action
            await ws1.send(json.dumps({"type": "action", "action": "buy", "quantity": 2}))
            g_after_trade = json.loads(await ws1.recv())
            return (
                rcode is not None and
                len(s2["room"]["players"]) == 2 and
                g_state["game"]["turn"] == 0 and
                g_after_trade["game"]["turn"] == 1
            )

    try:
        ws_success = asyncio.run(test_ws_flow())
        check("WebSocket Multiplayer (Room Create, Join, Start, Synchronized Trade Action)", ws_success)
    except Exception as e:
        check("WebSocket Multiplayer", False, str(e))

    print("\n====================================================================")
    passed = sum(1 for _, s, _ in results if s)
    total = len(results)
    print(f"TEST RESULTS: {passed}/{total} PASSED")
    print("====================================================================")
    return passed == total

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
