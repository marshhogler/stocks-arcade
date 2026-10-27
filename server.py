"""
====================================================================
  STOCKS ARCADE — CLOUD MULTIPLAYER & PERSISTENT ENGINE (PRODUCTION)
====================================================================
FastAPI/Uvicorn backend powering Stocks Arcade with:
- Standards-compliant JWT authentication (PyJWT) with verified claims & signature
- In-production fail-fast configuration for DATABASE_URL and JWT_SECRET
- Authenticated WebSocket Identity & Server-Side Turn/Rule Validation
- Authoritative Atomic Trade Execution with Idempotency & Concurrency Locks
- Saved Games & Multi-Season Arcade Progression
- 5 Mathematical Stock Market Engines (Trend, Reversion, Cyclical, Regime, Hybrid)
- 5 Adaptive AI Opponents (Ragnarok, Megatron, Overlord, Nemesis, Singularity)
"""

import os
import sys
import math
import time
import json
import uuid
import random
import re
import urllib.parse
import ipaddress
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Header, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
import uvicorn

# Standards-compliant JWT library
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

# Argon2 Password Hashing
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

ph = PasswordHasher()

# Optional asyncpg for PostgreSQL (Supabase)
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False

import sqlite3

# ====================================================================
# POSTGRESQL URL PARSING & SANITIZATION (DNS HOSTNAME COMPLIANT)
# ====================================================================
def is_valid_db_host(host: str) -> bool:
    """
    Validates whether the given host is a valid IPv4/IPv6 address OR a valid DNS hostname.
    Prevents ValueError when non-IP hostnames (e.g. Supabase Session Pooler URIs) are inspected.
    """
    if not host or not isinstance(host, str):
        return False
    host_clean = host.strip()
    if not host_clean:
        return False

    # 1. Attempt IP address parsing (IPv4 / IPv6) safely catching ValueError
    try:
        ipaddress.ip_address(host_clean)
        return True
    except ValueError:
        # Expected for DNS domain names; do not raise or fail!
        pass

    # 2. Accept localhost / loopback aliases
    if host_clean.lower() in ("localhost", "127.0.0.1", "::1"):
        return True

    # 3. Validate standard DNS hostname (RFC 1123 / RFC 952)
    # Allows cloud database endpoints, e.g. aws-0-ap-southeast-2.pooler.supabase.com
    dns_pattern = re.compile(
        r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$'
    )
    return bool(dns_pattern.match(host_clean))

def sanitize_database_url(url_or_text: str) -> str:
    """
    Sanitizes database URLs and error messages to ensure credentials/passwords
    are never printed in logs or exception traces.
    """
    if not url_or_text or not isinstance(url_or_text, str):
        return ""
    # URL parsing sanitization
    try:
        if "://" in url_or_text and not "\n" in url_or_text:
            parsed = urllib.parse.urlparse(url_or_text)
            if parsed.password:
                user = parsed.username or ""
                host = parsed.hostname or ""
                port_str = f":{parsed.port}" if parsed.port else ""
                redacted_netloc = f"{user}:****@{host}{port_str}"
                return parsed._replace(netloc=redacted_netloc).geturl()
    except Exception:
        pass
    # Regex fallback for embedded connection strings in messages
    return re.sub(r':([^/@:\s]+)@', ':****@', url_or_text)

def parse_database_url(url: str) -> Dict[str, Any]:
    """
    Parses and validates a PostgreSQL connection URL.
    Supports both literal IP addresses and valid DNS hostnames
    (e.g., Supabase Session Pooler URIs with host 'aws-0-ap-southeast-2.pooler.supabase.com' and port 5432).
    Returns normalized connection parameters suitable for asyncpg.
    """
    if not url or not isinstance(url, str):
        raise ValueError("DATABASE_URL must be a non-empty string.")

    clean_url = url.strip()
    if not (clean_url.startswith("postgres://") or clean_url.startswith("postgresql://")):
        raise ValueError("DATABASE_URL must start with 'postgres://' or 'postgresql://'")

    # Normalize scheme to postgresql:// for asyncpg and standard RFC parsing
    if clean_url.startswith("postgres://"):
        norm_url = "postgresql://" + clean_url[11:]
    else:
        norm_url = clean_url

    parsed = urllib.parse.urlparse(norm_url)

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("DATABASE_URL is missing a valid host.")

    if hostname.startswith("[") and hostname.endswith("]"):
        hostname = hostname[1:-1]

    if not is_valid_db_host(hostname):
        raise ValueError(f"DATABASE_URL contains an invalid host format: {hostname!r}")

    port = parsed.port or 5432
    if not (1 <= port <= 65535):
        raise ValueError(f"DATABASE_URL port is out of valid range (1-65535): {port}")

    database = (parsed.path or "").lstrip("/") or "postgres"
    username = urllib.parse.unquote(parsed.username) if parsed.username else None
    password = urllib.parse.unquote(parsed.password) if parsed.password else None

    # Parse query parameters (e.g. sslmode)
    query_params = {}
    if parsed.query:
        qs = urllib.parse.parse_qs(parsed.query)
        for k, v in qs.items():
            query_params[k] = v[-1] if isinstance(v, list) and v else ""

    # Supabase session/transaction poolers and cloud databases typically require SSL
    ssl_mode = query_params.get("sslmode", "").lower()
    needs_ssl = ssl_mode in ("require", "verify-ca", "verify-full") or (
        hostname.lower() not in ("localhost", "127.0.0.1", "::1") and ssl_mode != "disable"
    )

    return {
        "dsn": norm_url,
        "host": hostname,
        "port": port,
        "database": database,
        "user": username,
        "password": password,
        "ssl": "require" if needs_ssl else None,
        "query": query_params,
        "sanitized_url": sanitize_database_url(norm_url)
    }

# ====================================================================
# CONFIGURATION & PRODUCTION FAIL-FAST VALIDATION
# ====================================================================
APP_ENV = os.getenv("APP_ENV", "production").strip().lower()
PORT = int(os.getenv("PORT", 3000))
HOST = os.getenv("HOST", "0.0.0.0")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS", "").strip()

# Insecure legacy fallback secret for comparison
INSECURE_DEFAULT_SECRET = "stocks-arcade-retro-cyber-secret-key-3000"

if APP_ENV == "production":
    # 1. Fail startup if JWT_SECRET is missing or insecure default in production
    if not JWT_SECRET or JWT_SECRET == INSECURE_DEFAULT_SECRET or len(JWT_SECRET) < 16:
        raise RuntimeError(
            "CRITICAL STARTUP FAILURE: In production mode, JWT_SECRET must be set to a secure, "
            "unique secret with at least 16 characters. The hard-coded default secret is prohibited."
        )
    # 2. Fail startup if DATABASE_URL is missing in production
    if not DATABASE_URL:
        raise RuntimeError(
            "CRITICAL STARTUP FAILURE: In production mode, DATABASE_URL environment variable is required. "
            "Silent fallback to SQLite is prohibited in production."
        )
    if not (DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")):
        raise RuntimeError(
            "CRITICAL STARTUP FAILURE: In production mode, DATABASE_URL must be a valid PostgreSQL connection URL."
        )
    try:
        parse_database_url(DATABASE_URL)
    except Exception as _e:
        raise RuntimeError(
            f"CRITICAL STARTUP FAILURE: In production mode, DATABASE_URL is invalid: {sanitize_database_url(str(_e))}"
        )
else:
    # Development / Testing fallback
    if not JWT_SECRET:
        JWT_SECRET = "dev-insecure-test-secret-stocks-arcade-2026"

CODE_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

# ====================================================================
# FASTAPI APP SETUP & CORS CONFIGURATION
# ====================================================================
app = FastAPI(
    title="Stocks Arcade Multiplayer",
    description="Authoritative production backend for Stocks Arcade",
    version="2.1.0"
)

# CORS Configuration
if ALLOWED_ORIGINS_RAW:
    allowed_origins = [o.strip() for o in ALLOWED_ORIGINS_RAW.split(",") if o.strip()]
else:
    if APP_ENV == "production":
        allowed_origins = [
            "https://stocks-arcade-multiplayer.onrender.com",
            "http://localhost:3000",
            "http://127.0.0.1:3000"
        ]
    else:
        allowed_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if allowed_origins != ["*"] else ["*"],
    allow_credentials=True if allowed_origins != ["*"] else False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ====================================================================
# STANDARDS-COMPLIANT JWT AUTHENTICATION
# ====================================================================
JWT_ALGORITHM = "HS256"

def create_session_token(player_id: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(player_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=30)).timestamp())
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_session_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat"]}
        )
        return payload
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired. Please log in again."
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token signature or claims."
        )

def hash_password(password: str) -> str:
    return ph.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    try:
        return ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False

# ====================================================================
# DATABASE ADAPTER WITH STRICT PRODUCTION POSTGRESQL & ATOMIC TRADES
# ====================================================================
class Database:
    def __init__(self, db_url: str):
        self.db_url = db_url.strip()
        self.is_pg = bool(self.db_url and (self.db_url.startswith("postgres://") or self.db_url.startswith("postgresql://")) and HAS_ASYNCPG)
        self.pg_pool = None
        self.sqlite_path = os.path.join(os.path.dirname(__file__), "stocks_arcade.db")
        self._sqlite_lock = asyncio.Lock()

    async def init(self):
        current_env = os.getenv("APP_ENV", APP_ENV).strip().lower()
        if current_env == "production":
            if not self.db_url:
                raise RuntimeError("CRITICAL STARTUP FAILURE: DATABASE_URL must be set in production mode.")
            if not HAS_ASYNCPG:
                raise RuntimeError("CRITICAL STARTUP FAILURE: 'asyncpg' library is required to connect to PostgreSQL in production.")
            try:
                conn_info = parse_database_url(self.db_url)
                print(f"[DB] Initializing PostgreSQL connection to {conn_info['host']}:{conn_info['port']} (database: {conn_info['database']})...")
                pool_kwargs = {
                    "min_size": 1,
                    "max_size": 10,
                    "timeout": 15
                }
                if conn_info.get("ssl"):
                    pool_kwargs["ssl"] = conn_info["ssl"]
                self.pg_pool = await asyncpg.create_pool(conn_info["dsn"], **pool_kwargs)
                print(f"[DB] Connected to PostgreSQL ({conn_info['host']}:{conn_info['port']}) in production mode.")
                await self._init_pg_schema()
                self.is_pg = True
                return
            except Exception as e:
                sanitized_msg = sanitize_database_url(str(e))
                raise RuntimeError(f"CRITICAL STARTUP FAILURE: Failed to connect to PostgreSQL or initialize schema: {sanitized_msg}") from e

        # Development / Testing Mode
        if self.is_pg:
            try:
                conn_info = parse_database_url(self.db_url)
                pool_kwargs = {
                    "min_size": 1,
                    "max_size": 5,
                    "timeout": 5
                }
                if conn_info.get("ssl"):
                    pool_kwargs["ssl"] = conn_info["ssl"]
                self.pg_pool = await asyncpg.create_pool(conn_info["dsn"], **pool_kwargs)
                print(f"[DB] Connected to PostgreSQL ({conn_info['host']}:{conn_info['port']}) in dev mode.")
                await self._init_pg_schema()
                return
            except Exception as e:
                sanitized_msg = sanitize_database_url(str(e))
                print(f"[DB] PostgreSQL dev connection failed: {sanitized_msg}. Falling back to SQLite.")
                self.is_pg = False

        print(f"[DB] Using local SQLite database (Development/Testing only): {self.sqlite_path}")
        self._init_sqlite_schema()

    async def _init_pg_schema(self):
        schema_file = os.path.join(os.path.dirname(__file__), "schema.sql")
        if os.path.exists(schema_file):
            with open(schema_file, "r", encoding="utf-8") as f:
                sql = f.read()
            async with self.pg_pool.acquire() as conn:
                await conn.execute(sql)
                print("[DB] PostgreSQL schema verified/initialized.")

    def _init_sqlite_schema(self):
        conn = sqlite3.connect(self.sqlite_path)
        cur = conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS players (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                username_clean TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                avatar_color TEXT NOT NULL DEFAULT '#00c8ff',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portfolios (
                id TEXT PRIMARY KEY,
                player_id TEXT NOT NULL UNIQUE,
                cash_balance REAL NOT NULL DEFAULT 1000.00,
                currency TEXT NOT NULL DEFAULT 'USD',
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS stock_holdings (
                id TEXT PRIMARY KEY,
                player_id TEXT NOT NULL,
                stock_symbol TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                average_cost_basis REAL NOT NULL DEFAULT 0.00,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(player_id, stock_symbol),
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS saved_games (
                id TEXT PRIMARY KEY,
                owner_player_id TEXT NOT NULL,
                name TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'private',
                invite_code TEXT UNIQUE,
                invite_code_hash TEXT,
                current_season INTEGER NOT NULL DEFAULT 1,
                configuration TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (owner_player_id) REFERENCES players(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS seasons (
                id TEXT PRIMARY KEY,
                saved_game_id TEXT NOT NULL,
                season_number INTEGER NOT NULL,
                season_name TEXT NOT NULL,
                market_model TEXT NOT NULL DEFAULT 'trend_momentum',
                deterministic_seed INTEGER NOT NULL DEFAULT 42,
                max_turns INTEGER NOT NULL DEFAULT 50,
                current_turn INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'in_progress',
                config_snapshot TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                UNIQUE(saved_game_id, season_number),
                FOREIGN KEY (saved_game_id) REFERENCES saved_games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS game_participants (
                id TEXT PRIMARY KEY,
                saved_game_id TEXT NOT NULL,
                player_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                joined_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                UNIQUE(saved_game_id, player_id),
                FOREIGN KEY (saved_game_id) REFERENCES saved_games(id) ON DELETE CASCADE,
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                player_id TEXT NOT NULL,
                saved_game_id TEXT,
                season_id TEXT,
                stock_symbol TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                execution_price REAL NOT NULL,
                total_amount REAL NOT NULL,
                fees REAL NOT NULL DEFAULT 0.00,
                cash_delta REAL NOT NULL,
                realized_pnl REAL DEFAULT 0.00,
                idempotency_key TEXT UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS player_statistics (
                player_id TEXT PRIMARY KEY,
                games_participated INTEGER NOT NULL DEFAULT 0,
                seasons_completed INTEGER NOT NULL DEFAULT 0,
                seasons_won INTEGER NOT NULL DEFAULT 0,
                total_trades INTEGER NOT NULL DEFAULT 0,
                total_volume REAL NOT NULL DEFAULT 0.00,
                all_time_profit REAL NOT NULL DEFAULT 0.00,
                highest_net_worth REAL NOT NULL DEFAULT 1000.00,
                last_active TEXT NOT NULL,
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
            );
        """)
        conn.commit()
        conn.close()

    async def fetchrow(self, query: str, *args):
        if self.is_pg and self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow(query, *args)
                return dict(row) if row else None
        else:
            def _run():
                conn = sqlite3.connect(self.sqlite_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                import re
                q = re.sub(r'\$\d+', '?', query)
                cur.execute(q, args)
                row = cur.fetchone()
                res = dict(row) if row else None
                conn.close()
                return res
            return await asyncio.to_thread(_run)

    async def fetch(self, query: str, *args):
        if self.is_pg and self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch(query, *args)
                return [dict(r) for r in rows]
        else:
            def _run():
                conn = sqlite3.connect(self.sqlite_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                import re
                q = re.sub(r'\$\d+', '?', query)
                cur.execute(q, args)
                rows = cur.fetchall()
                res = [dict(r) for r in rows]
                conn.close()
                return res
            return await asyncio.to_thread(_run)

    async def execute(self, query: str, *args):
        if self.is_pg and self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                return await conn.execute(query, *args)
        else:
            def _run():
                conn = sqlite3.connect(self.sqlite_path)
                cur = conn.cursor()
                import re
                q = re.sub(r'\$\d+', '?', query)
                cur.execute(q, args)
                conn.commit()
                conn.close()
            return await asyncio.to_thread(_run)

    async def execute_atomic_trade(
        self,
        player_id: str,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        game_id: Optional[str] = None,
        season_id: Optional[str] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a trade atomically with database-level concurrency locks and idempotency.
        Prevents balance corruption, negative balances, and double-spending race conditions.
        """
        now = datetime.now(timezone.utc).isoformat()
        action = action.upper()
        total_amount = round(quantity * price, 2)

        # 1. PostgreSQL Atomic Execution via Transaction with Row-Level Locking
        if self.is_pg and self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                async with conn.transaction():
                    # Idempotency Check within Transaction
                    if idempotency_key:
                        tx = await conn.fetchrow(
                            "SELECT id, total_amount, cash_delta, realized_pnl FROM transactions WHERE idempotency_key = $1",
                            idempotency_key
                        )
                        if tx:
                            port = await conn.fetchrow("SELECT cash_balance FROM portfolios WHERE player_id = $1", player_id)
                            hold = await conn.fetchrow(
                                "SELECT quantity FROM stock_holdings WHERE player_id = $1 AND stock_symbol = $2",
                                player_id, symbol
                            )
                            return {
                                "success": True,
                                "duplicate": True,
                                "transaction_id": str(tx["id"]),
                                "action": action,
                                "quantity": quantity,
                                "execution_price": price,
                                "total_amount": float(tx["total_amount"]),
                                "remaining_cash": float(port["cash_balance"]),
                                "remaining_stocks": int(hold["quantity"]) if hold else 0,
                                "realized_pnl": float(tx["realized_pnl"] or 0.0)
                            }

                    # Lock Portfolio row FOR UPDATE
                    port = await conn.fetchrow(
                        "SELECT id, cash_balance FROM portfolios WHERE player_id = $1 FOR UPDATE",
                        player_id
                    )
                    if not port:
                        raise HTTPException(status_code=400, detail="Portfolio not found.")
                    cash = float(port["cash_balance"])

                    # Lock Stock Holding row FOR UPDATE
                    hold = await conn.fetchrow(
                        "SELECT quantity, average_cost_basis FROM stock_holdings WHERE player_id = $1 AND stock_symbol = $2 FOR UPDATE",
                        player_id, symbol
                    )
                    cur_qty = int(hold["quantity"]) if hold else 0
                    cur_cost = float(hold["average_cost_basis"]) if hold else 0.0

                    realized_pnl = 0.0
                    if action == "BUY":
                        if cash < total_amount:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Insufficient funds: Have ${cash:.2f}, need ${total_amount:.2f}"
                            )
                        new_cash = round(cash - total_amount, 2)
                        new_qty = cur_qty + quantity
                        new_cost = round(((cur_qty * cur_cost) + total_amount) / new_qty, 2)
                        cash_delta = -total_amount
                    elif action == "SELL":
                        if cur_qty < quantity:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Insufficient shares: Have {cur_qty}, selling {quantity}"
                            )
                        new_cash = round(cash + total_amount, 2)
                        new_qty = cur_qty - quantity
                        new_cost = cur_cost if new_qty > 0 else 0.0
                        realized_pnl = round(quantity * (price - cur_cost), 2)
                        cash_delta = total_amount
                    else:
                        raise HTTPException(status_code=400, detail="Invalid trade action.")

                    # Apply Updates Atomically
                    await conn.execute(
                        "UPDATE portfolios SET cash_balance = $1, updated_at = $2 WHERE player_id = $3",
                        new_cash, now, player_id
                    )

                    if hold:
                        await conn.execute(
                            "UPDATE stock_holdings SET quantity = $1, average_cost_basis = $2, updated_at = $3 WHERE player_id = $4 AND stock_symbol = $5",
                            new_qty, new_cost, now, player_id, symbol
                        )
                    else:
                        await conn.execute(
                            "INSERT INTO stock_holdings (id, player_id, stock_symbol, quantity, average_cost_basis, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                            str(uuid.uuid4()), player_id, symbol, new_qty, new_cost, now, now
                        )

                    tx_id = str(uuid.uuid4())
                    await conn.execute(
                        """INSERT INTO transactions (id, player_id, saved_game_id, season_id, stock_symbol, transaction_type, quantity, execution_price, total_amount, fees, cash_delta, realized_pnl, idempotency_key, created_at)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)""",
                        tx_id, player_id, game_id, season_id, symbol, action, quantity, price, total_amount, 0.00, cash_delta, realized_pnl, idempotency_key, now
                    )

                    await conn.execute(
                        """INSERT INTO player_statistics (player_id, total_trades, total_volume, all_time_profit, last_active)
                           VALUES ($1, 1, $2, $3, $4)
                           ON CONFLICT (player_id)
                           DO UPDATE SET total_trades = player_statistics.total_trades + 1,
                                         total_volume = player_statistics.total_volume + EXCLUDED.total_volume,
                                         all_time_profit = player_statistics.all_time_profit + EXCLUDED.all_time_profit,
                                         last_active = EXCLUDED.last_active""",
                        player_id, total_amount, realized_pnl, now
                    )

                    return {
                        "success": True,
                        "transaction_id": tx_id,
                        "action": action,
                        "quantity": quantity,
                        "execution_price": price,
                        "total_amount": total_amount,
                        "remaining_cash": new_cash,
                        "remaining_stocks": new_qty,
                        "realized_pnl": realized_pnl
                    }

        # 2. SQLite Atomic Execution via BEGIN IMMEDIATE & Async Lock
        async with self._sqlite_lock:
            def _sqlite_atomic():
                conn = sqlite3.connect(self.sqlite_path, timeout=10)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                try:
                    cur.execute("BEGIN IMMEDIATE")

                    if idempotency_key:
                        cur.execute("SELECT id, total_amount, cash_delta, realized_pnl FROM transactions WHERE idempotency_key = ?", (idempotency_key,))
                        tx = cur.fetchone()
                        if tx:
                            cur.execute("SELECT cash_balance FROM portfolios WHERE player_id = ?", (player_id,))
                            port = cur.fetchone()
                            cur.execute("SELECT quantity FROM stock_holdings WHERE player_id = ? AND stock_symbol = ?", (player_id, symbol))
                            hold = cur.fetchone()
                            conn.rollback()
                            conn.close()
                            return {
                                "success": True,
                                "duplicate": True,
                                "transaction_id": tx["id"],
                                "action": action,
                                "quantity": quantity,
                                "execution_price": price,
                                "total_amount": float(tx["total_amount"]),
                                "remaining_cash": float(port["cash_balance"]),
                                "remaining_stocks": int(hold["quantity"]) if hold else 0,
                                "realized_pnl": float(tx["realized_pnl"] or 0.0)
                            }

                    cur.execute("SELECT id, cash_balance FROM portfolios WHERE player_id = ?", (player_id,))
                    port = cur.fetchone()
                    if not port:
                        raise ValueError("Portfolio not found.")
                    cash = float(port["cash_balance"])

                    cur.execute("SELECT quantity, average_cost_basis FROM stock_holdings WHERE player_id = ? AND stock_symbol = ?", (player_id, symbol))
                    hold = cur.fetchone()
                    cur_qty = int(hold["quantity"]) if hold else 0
                    cur_cost = float(hold["average_cost_basis"]) if hold else 0.0

                    realized_pnl = 0.0
                    if action == "BUY":
                        if cash < total_amount:
                            raise ValueError(f"Insufficient funds: Have ${cash:.2f}, need ${total_amount:.2f}")
                        new_cash = round(cash - total_amount, 2)
                        new_qty = cur_qty + quantity
                        new_cost = round(((cur_qty * cur_cost) + total_amount) / new_qty, 2)
                        cash_delta = -total_amount
                    elif action == "SELL":
                        if cur_qty < quantity:
                            raise ValueError(f"Insufficient shares: Have {cur_qty}, selling {quantity}")
                        new_cash = round(cash + total_amount, 2)
                        new_qty = cur_qty - quantity
                        new_cost = cur_cost if new_qty > 0 else 0.0
                        realized_pnl = round(quantity * (price - cur_cost), 2)
                        cash_delta = total_amount
                    else:
                        raise ValueError("Invalid trade action.")

                    cur.execute("UPDATE portfolios SET cash_balance = ?, updated_at = ? WHERE player_id = ?", (new_cash, now, player_id))
                    if hold:
                        cur.execute("UPDATE stock_holdings SET quantity = ?, average_cost_basis = ?, updated_at = ? WHERE player_id = ? AND stock_symbol = ?", (new_qty, new_cost, now, player_id, symbol))
                    else:
                        cur.execute("INSERT INTO stock_holdings (id, player_id, stock_symbol, quantity, average_cost_basis, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), player_id, symbol, new_qty, new_cost, now, now))

                    tx_id = str(uuid.uuid4())
                    cur.execute(
                        """INSERT INTO transactions (id, player_id, saved_game_id, season_id, stock_symbol, transaction_type, quantity, execution_price, total_amount, fees, cash_delta, realized_pnl, idempotency_key, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (tx_id, player_id, game_id, season_id, symbol, action, quantity, price, total_amount, 0.00, cash_delta, realized_pnl, idempotency_key, now)
                    )

                    cur.execute(
                        """INSERT INTO player_statistics (player_id, total_trades, total_volume, all_time_profit, last_active)
                           VALUES (?, 1, ?, ?, ?)
                           ON CONFLICT (player_id)
                           DO UPDATE SET total_trades = player_statistics.total_trades + 1,
                                         total_volume = player_statistics.total_volume + excluded.total_volume,
                                         all_time_profit = player_statistics.all_time_profit + excluded.all_time_profit,
                                         last_active = excluded.last_active""",
                        (player_id, total_amount, realized_pnl, now)
                    )

                    conn.commit()
                    conn.close()
                    return {
                        "success": True,
                        "transaction_id": tx_id,
                        "action": action,
                        "quantity": quantity,
                        "execution_price": price,
                        "total_amount": total_amount,
                        "remaining_cash": new_cash,
                        "remaining_stocks": new_qty,
                        "realized_pnl": realized_pnl
                    }
                except Exception as ex:
                    conn.rollback()
                    conn.close()
                    raise ex

            try:
                return await asyncio.to_thread(_sqlite_atomic)
            except ValueError as ve:
                raise HTTPException(status_code=400, detail=str(ve))

db = Database(DATABASE_URL)

async def get_current_player(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication token. Bearer scheme required."
        )
    token = authorization[7:].strip()
    payload = verify_session_token(token)
    player = await db.fetchrow(
        "SELECT id, username, avatar_color, status FROM players WHERE id = $1",
        payload["sub"]
    )
    if not player:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Player not found.")
    if player.get("status") != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Player account inactive.")
    return player

async def get_player_from_token(token: str) -> Dict[str, Any]:
    """Helper to authenticate token from WebSocket query or message."""
    payload = verify_session_token(token)
    player = await db.fetchrow(
        "SELECT id, username, avatar_color, status FROM players WHERE id = $1",
        payload["sub"]
    )
    if not player or player.get("status") != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid player account.")
    return player

# ====================================================================
# PHASE 8: FIVE MATHEMATICAL STOCK MARKET ENGINES
# ====================================================================
class MathMarketEngine:
    @staticmethod
    def step(model: str, price: float, history: List[float], turn: int, seed: int = 42, volatility_enabled: bool = True) -> Dict[str, Any]:
        rng = random.Random(seed + turn * 1009)
        u1 = max(1e-7, rng.random())
        u2 = rng.random()
        gauss = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        cur_p = max(0.25, price)

        next_p = cur_p
        regime = "NORMAL"
        headline = None

        if model == "trend_momentum":
            lookback = 4
            past_p = history[-lookback] if len(history) >= lookback else cur_p
            momentum = (cur_p - past_p) / max(0.1, past_p)
            drift = 0.001 + 0.45 * momentum
            if rng.random() < 0.06:
                drift = -drift * 1.5
                regime = "TREND_BREAK"
                headline = ["TREND SHIFT", "Institutional volume triggers an aggressive reversal.", 0.96 if momentum > 0 else 1.05]
            sig = 0.04 if volatility_enabled else 0.015
            log_ret = drift - 0.5 * sig * sig + sig * gauss
            next_p = cur_p * math.exp(log_ret)

        elif model == "mean_reversion":
            window = history[-15:] if history else [cur_p]
            anchor = sum(window) / len(window)
            if rng.random() < 0.04:
                anchor *= (1.0 + (rng.random() - 0.5) * 0.12)
                regime = "ANCHOR_SHIFT"
            theta = 0.22
            distance = (anchor - cur_p) / anchor
            sig = 0.035 if volatility_enabled else 0.012
            drift = theta * distance
            next_p = cur_p * math.exp(drift + sig * gauss)
            if abs(distance) > 0.08 and rng.random() < 0.12:
                headline = ["MEAN REVERSION", "Arbitrageurs step in to restore fair market value.", 1.04 if distance > 0 else 0.96]

        elif model == "cyclical_harmonic":
            t = turn * 0.35
            wave1 = math.sin(t) * 0.035
            wave2 = math.cos(t * 0.53 + 1.2) * 0.025
            wave3 = math.sin(t * 0.18 + 2.7) * 0.045
            cycle_drift = wave1 + wave2 + wave3
            sig = 0.025 if volatility_enabled else 0.01
            next_p = cur_p * math.exp(cycle_drift + sig * gauss)
            regime = "CYCLICAL"

        elif model == "volatility_regime":
            r_val = rng.random()
            if r_val < 0.65:
                regime = "CALM"
                sig = 0.018
                drift = 0.002
            elif r_val < 0.92:
                regime = "TURBULENT"
                sig = 0.065
                drift = (rng.random() - 0.5) * 0.04
            else:
                regime = "SHOCK"
                sig = 0.11
                drift = -0.06 if rng.random() < 0.6 else 0.06
                headline = ["VOLATILITY CLUSTER", "High-frequency desks spark extreme order book turbulence.", 1.08 if drift > 0 else 0.92]
            next_p = cur_p * math.exp(drift - 0.5 * sig * sig + sig * gauss)

        else: # complex_hybrid
            t = turn * 0.25
            harmonic = math.sin(t) * 0.02
            anchor = history[0] if history else cur_p
            revert = 0.08 * (anchor - cur_p) / anchor
            jump = 0.0
            if rng.random() < 0.05:
                jump = (rng.random() - 0.48) * 0.14
                regime = "JUMP_EVENT"
                headline = ["MARKET CATALYST", "Unscheduled macro announcement triggers liquidity sweep.", 1.0 + jump]
            sig = 0.038 if volatility_enabled else 0.015
            drift = 0.001 + harmonic + revert + jump
            next_p = cur_p * math.exp(drift + sig * gauss)

        next_p = max(0.50, min(100000.0, next_p))
        return {
            "price": round(next_p, 2),
            "regime": regime,
            "headline": headline
        }

# ====================================================================
# PHASE 9: ADAPTIVE AI TRADING OPPONENTS
# ====================================================================
class AdaptiveAIEngine:
    BOT_PROFILES = {
        "Ragnarok": {"style": "Aggressive Momentum", "color": "#ff1c50", "risk_ratio": 0.40},
        "Megatron": {"style": "Quantitative Trend", "color": "#00c8ff", "risk_ratio": 0.25},
        "Overlord": {"style": "Capital Preservation", "color": "#ffdc00", "risk_ratio": 0.12},
        "Nemesis": {"style": "Mean Reversion", "color": "#be00ff", "risk_ratio": 0.22},
        "Singularity": {"style": "Adaptive Hybrid", "color": "#00ff6e", "risk_ratio": 0.30}
    }

    @classmethod
    def decide(cls, bot_name: str, cash: float, stocks: int, price: float, history: List[float], rsi: float) -> Dict[str, Any]:
        cur_p = max(0.1, price)
        max_affordable = int(cash // cur_p)
        profile = cls.BOT_PROFILES.get(bot_name, cls.BOT_PROFILES["Megatron"])
        risk = profile["risk_ratio"]

        if len(history) < 3:
            return {"action": "HOLD", "quantity": 0}

        roc = (cur_p - history[-3]) / max(0.1, history[-3])
        avg_price = sum(history[-6:]) / len(history[-6:])

        if bot_name == "Ragnarok":
            if roc > 0.015 and rsi < 78 and max_affordable > 0:
                qty = max(1, min(max_affordable, int(max_affordable * risk * 1.5)))
                return {"action": "BUY", "quantity": qty}
            elif (roc < -0.02 or rsi > 78) and stocks > 0:
                qty = max(1, min(stocks, int(stocks * 0.6)))
                return {"action": "SELL", "quantity": qty}

        elif bot_name == "Megatron":
            if cur_p > avg_price * 1.01 and roc > 0 and rsi < 68 and max_affordable > 0:
                qty = max(1, min(max_affordable, int(max_affordable * risk)))
                return {"action": "BUY", "quantity": qty}
            elif cur_p < avg_price * 0.985 and stocks > 0:
                qty = max(1, min(stocks, int(stocks * 0.5)))
                return {"action": "SELL", "quantity": qty}

        elif bot_name == "Overlord":
            if rsi < 42 and max_affordable > 0:
                qty = max(1, min(max_affordable, int(max_affordable * risk)))
                return {"action": "BUY", "quantity": qty}
            elif (rsi > 58 or roc > 0.03) and stocks > 0:
                qty = max(1, min(stocks, int(stocks * 0.4)))
                return {"action": "SELL", "quantity": qty}

        elif bot_name == "Nemesis":
            if (cur_p < avg_price * 0.96 or rsi < 32) and max_affordable > 0:
                qty = max(1, min(max_affordable, int(max_affordable * risk * 1.3)))
                return {"action": "BUY", "quantity": qty}
            elif (cur_p > avg_price * 1.05 or rsi > 70) and stocks > 0:
                qty = max(1, min(stocks, int(stocks * 0.75)))
                return {"action": "SELL", "quantity": qty}

        else: # Singularity
            buy_signal = 0
            sell_signal = 0
            if roc > 0.01: buy_signal += 1
            if cur_p < avg_price * 0.98: buy_signal += 1
            if rsi < 45: buy_signal += 1
            if roc < -0.01: sell_signal += 1
            if cur_p > avg_price * 1.03: sell_signal += 1
            if rsi > 65: sell_signal += 1

            if buy_signal >= 2 and max_affordable > 0:
                qty = max(1, min(max_affordable, int(max_affordable * risk)))
                return {"action": "BUY", "quantity": qty}
            elif sell_signal >= 2 and stocks > 0:
                qty = max(1, min(stocks, int(stocks * 0.5)))
                return {"action": "SELL", "quantity": qty}

        return {"action": "HOLD", "quantity": 0}

# ====================================================================
# PYDANTIC REQUEST MODELS WITH STRICT INPUT VALIDATION
# ====================================================================
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=22)
    password: str = Field(..., min_length=8, max_length=128)
    avatar_color: Optional[str] = "#00c8ff"

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        clean = v.strip()
        if not clean.replace("_", "").isalnum():
            raise ValueError("Username can only contain alphanumeric characters and underscores.")
        return clean

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v.strip()) < 8:
            raise ValueError("Password must contain at least 8 non-whitespace characters.")
        return v

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class TradeRequest(BaseModel):
    symbol: str = Field("STK", min_length=1, max_length=12)
    action: str = Field(..., pattern="^(BUY|SELL|buy|sell)$")
    quantity: int = Field(..., gt=0, le=10000)
    execution_price: float = Field(..., gt=0)
    game_id: Optional[str] = None
    season_id: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=64)

    @field_validator("execution_price")
    @classmethod
    def validate_price(cls, v: float) -> float:
        if not math.isfinite(v) or math.isnan(v) or v <= 0:
            raise ValueError("Execution price must be a finite positive number.")
        return round(v, 2)

class CreateSavedGameRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=40)
    visibility: str = Field("private", pattern="^(private|invite_code|public)$")
    market_model: str = Field("trend_momentum", pattern="^(trend_momentum|mean_reversion|cyclical_harmonic|volatility_regime|complex_hybrid)$")
    max_turns: int = Field(50, ge=10, le=200)

class JoinSavedGameRequest(BaseModel):
    invite_code: str = Field(..., min_length=6, max_length=6)

# ====================================================================
# REST API ROUTES
# ====================================================================
@app.on_event("startup")
async def on_startup():
    await db.init()

@app.get("/")
async def root():
    return {
        "service": "Stocks Arcade Multiplayer",
        "version": "2.1.0",
        "websocket": "/ws",
        "status": "ok",
        "environment": APP_ENV,
        "database": "postgresql" if db.is_pg else "sqlite"
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "rooms": len(active_rooms),
        "database": "connected"
    }

# --- AUTHENTICATION ---
@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    clean_user = req.username.strip().lower()

    existing = await db.fetchrow("SELECT id FROM players WHERE username_clean = $1", clean_user)
    if existing:
        raise HTTPException(status_code=409, detail="Username is already registered. Choose another.")

    p_id = str(uuid.uuid4())
    pw_hash = hash_password(req.password)
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        "INSERT INTO players (id, username, username_clean, password_hash, avatar_color, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        p_id, req.username.strip(), clean_user, pw_hash, req.avatar_color or "#00c8ff", now, now
    )

    port_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO portfolios (id, player_id, cash_balance, currency, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6)",
        port_id, p_id, 1000.00, "USD", now, now
    )
    hold_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO stock_holdings (id, player_id, stock_symbol, quantity, average_cost_basis, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        hold_id, p_id, "STK", 10, 10.00, now, now
    )

    token = create_session_token(p_id, req.username.strip())
    return {
        "success": True,
        "token": token,
        "player": {
            "id": p_id,
            "username": req.username.strip(),
            "avatar_color": req.avatar_color,
            "cash": 1000.00,
            "stocks": 10
        }
    }

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    clean_user = req.username.strip().lower()
    player = await db.fetchrow(
        "SELECT id, username, password_hash, avatar_color, status FROM players WHERE username_clean = $1",
        clean_user
    )
    if not player or not verify_password(req.password, player["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    if player.get("status") != "active":
        raise HTTPException(status_code=403, detail="Account is disabled.")

    token = create_session_token(player["id"], player["username"])
    portfolio = await db.fetchrow("SELECT cash_balance FROM portfolios WHERE player_id = $1", player["id"])
    holding = await db.fetchrow("SELECT quantity FROM stock_holdings WHERE player_id = $1 AND stock_symbol = 'STK'", player["id"])

    return {
        "success": True,
        "token": token,
        "player": {
            "id": player["id"],
            "username": player["username"],
            "avatar_color": player["avatar_color"],
            "cash": float(portfolio["cash_balance"]) if portfolio else 1000.0,
            "stocks": int(holding["quantity"]) if holding else 0
        }
    }

@app.get("/api/auth/me")
async def get_me(player: Dict[str, Any] = Depends(get_current_player)):
    portfolio = await db.fetchrow("SELECT cash_balance FROM portfolios WHERE player_id = $1", player["id"])
    holding = await db.fetchrow("SELECT quantity, average_cost_basis FROM stock_holdings WHERE player_id = $1 AND stock_symbol = 'STK'", player["id"])
    stats = await db.fetchrow("SELECT * FROM player_statistics WHERE player_id = $1", player["id"])
    return {
        "player": player,
        "portfolio": {
            "cash": float(portfolio["cash_balance"]) if portfolio else 1000.0,
            "stocks": int(holding["quantity"]) if holding else 0,
            "avg_cost": float(holding["average_cost_basis"]) if holding else 0.0
        },
        "statistics": stats or {
            "games_participated": 0,
            "seasons_completed": 0,
            "seasons_won": 0,
            "total_trades": 0,
            "total_volume": 0.0,
            "all_time_profit": 0.0
        }
    }

# --- PORTFOLIO & TRANSACTIONS ---
@app.get("/api/players/me/portfolio")
async def get_portfolio(player: Dict[str, Any] = Depends(get_current_player)):
    port = await db.fetchrow("SELECT cash_balance, currency, updated_at FROM portfolios WHERE player_id = $1", player["id"])
    holdings = await db.fetch("SELECT stock_symbol, quantity, average_cost_basis FROM stock_holdings WHERE player_id = $1", player["id"])
    return {
        "cash": float(port["cash_balance"]) if port else 1000.0,
        "holdings": holdings or []
    }

@app.get("/api/players/me/transactions")
async def get_transactions(player: Dict[str, Any] = Depends(get_current_player)):
    rows = await db.fetch(
        "SELECT id, stock_symbol, transaction_type, quantity, execution_price, total_amount, realized_pnl, created_at FROM transactions WHERE player_id = $1 ORDER BY created_at DESC LIMIT 50",
        player["id"]
    )
    return {"transactions": rows}

# --- ATOMIC AUTHORITATIVE TRADE EXECUTION ---
@app.post("/api/trades")
async def execute_trade(req: TradeRequest, player: Dict[str, Any] = Depends(get_current_player)):
    return await db.execute_atomic_trade(
        player_id=player["id"],
        symbol=req.symbol.upper(),
        action=req.action.upper(),
        quantity=req.quantity,
        price=req.execution_price,
        game_id=req.game_id,
        season_id=req.season_id,
        idempotency_key=req.idempotency_key
    )

# --- SAVED GAMES & SEASONS ---
@app.get("/api/saved-games")
async def list_saved_games(player: Dict[str, Any] = Depends(get_current_player)):
    games = await db.fetch(
        """SELECT g.id, g.name, g.visibility, g.invite_code, g.current_season, g.status, g.created_at, g.owner_player_id,
                  p.username as owner_name,
                  (SELECT COUNT(*) FROM game_participants gp WHERE gp.saved_game_id = g.id) as participant_count
           FROM saved_games g
           JOIN players p ON p.id = g.owner_player_id
           WHERE g.owner_player_id = $1 OR g.id IN (SELECT saved_game_id FROM game_participants WHERE player_id = $1)
           ORDER BY g.updated_at DESC""",
        player["id"]
    )
    return {"games": games}

@app.post("/api/saved-games")
async def create_saved_game(req: CreateSavedGameRequest, player: Dict[str, Any] = Depends(get_current_player)):
    game_id = str(uuid.uuid4())
    invite_code = "".join(random.choice(CODE_CHARS) for _ in range(6))
    code_hash = jwt.utils.base64url_encode(invite_code.encode()).decode() if hasattr(jwt, 'utils') else invite_code
    now = datetime.now(timezone.utc).isoformat()

    config = {
        "market_model": req.market_model,
        "max_turns": req.max_turns
    }

    await db.execute(
        """INSERT INTO saved_games (id, owner_player_id, name, visibility, invite_code, invite_code_hash, current_season, configuration, status, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, 1, $7, 'active', $8, $9)""",
        game_id, player["id"], req.name, req.visibility, invite_code, code_hash, json.dumps(config), now, now
    )

    await db.execute(
        "INSERT INTO game_participants (id, saved_game_id, player_id, role, joined_at, status) VALUES ($1, $2, $3, 'owner', $4, 'active')",
        str(uuid.uuid4()), game_id, player["id"], now
    )

    season_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO seasons (id, saved_game_id, season_number, season_name, market_model, deterministic_seed, max_turns, current_turn, status, config_snapshot, created_at, started_at)
           VALUES ($1, $2, 1, 'Season 1: Genesis', $3, $4, $5, 0, 'in_progress', $6, $7, $8)""",
        season_id, game_id, req.market_model, random.randint(1000, 99999), req.max_turns, json.dumps(config), now, now
    )

    return {
        "success": True,
        "game": {
            "id": game_id,
            "name": req.name,
            "invite_code": invite_code,
            "current_season": 1,
            "season_id": season_id
        }
    }

@app.post("/api/saved-games/join")
async def join_saved_game(req: JoinSavedGameRequest, player: Dict[str, Any] = Depends(get_current_player)):
    code = req.invite_code.strip().upper()
    game = await db.fetchrow("SELECT id, name, status, current_season FROM saved_games WHERE invite_code = $1", code)
    if not game:
        raise HTTPException(status_code=404, detail="Invalid invitation code.")

    existing = await db.fetchrow("SELECT id FROM game_participants WHERE saved_game_id = $1 AND player_id = $2", game["id"], player["id"])
    now = datetime.now(timezone.utc).isoformat()
    if not existing:
        await db.execute(
            "INSERT INTO game_participants (id, saved_game_id, player_id, role, joined_at, status) VALUES ($1, $2, $3, 'member', $4, 'active')",
            str(uuid.uuid4()), game["id"], player["id"], now
        )

    return {
        "success": True,
        "game": game
    }

@app.get("/api/saved-games/{game_id}/seasons")
async def get_game_seasons(game_id: str, player: Dict[str, Any] = Depends(get_current_player)):
    seasons = await db.fetch(
        "SELECT id, season_number, season_name, market_model, max_turns, current_turn, status, created_at, ended_at FROM seasons WHERE saved_game_id = $1 ORDER BY season_number ASC",
        game_id
    )
    return {"seasons": seasons}

@app.post("/api/saved-games/{game_id}/seasons")
async def advance_to_next_season(game_id: str, player: Dict[str, Any] = Depends(get_current_player)):
    game = await db.fetchrow("SELECT id, owner_player_id, current_season FROM saved_games WHERE id = $1", game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found.")
    if game["owner_player_id"] != player["id"]:
        raise HTTPException(status_code=403, detail="Only the game owner can advance seasons.")

    now = datetime.now(timezone.utc).isoformat()
    await db.execute("UPDATE seasons SET status = 'completed', ended_at = $1 WHERE saved_game_id = $2 AND season_number = $3", now, game_id, game["current_season"])

    next_num = int(game["current_season"]) + 1
    models = ["trend_momentum", "mean_reversion", "cyclical_harmonic", "volatility_regime", "complex_hybrid"]
    next_model = models[(next_num - 1) % len(models)]

    season_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO seasons (id, saved_game_id, season_number, season_name, market_model, deterministic_seed, max_turns, current_turn, status, config_snapshot, created_at, started_at)
           VALUES ($1, $2, $3, $4, $5, $6, 50, 0, 'in_progress', '{}', $7, $8)""",
        season_id, game_id, next_num, f"Season {next_num}: Frontier", next_model, random.randint(1000, 99999), now, now
    )

    await db.execute("UPDATE saved_games SET current_season = $1, updated_at = $2 WHERE id = $3", next_num, now, game_id)

    return {
        "success": True,
        "season": {
            "id": season_id,
            "season_number": next_num,
            "season_name": f"Season {next_num}: Frontier",
            "market_model": next_model
        }
    }

# ====================================================================
# AUTHORITATIVE & AUTHENTICATED WEBSOCKET MULTIPLAYER PROTOCOL
# ====================================================================
class RoomPlayer:
    def __init__(self, player_id: str, name: str, socket: Optional[WebSocket], is_host: bool = False, is_ai: bool = False, color: str = "#00c8ff"):
        self.id = player_id
        self.name = name
        self.socket = socket
        self.is_host = is_host
        self.is_ai = is_ai
        self.color = color
        self.cash = 200.0
        self.stocks = 10
        self.connected = True
        self.trade_history = []

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "cash": self.cash,
            "stocks": self.stocks,
            "connected": self.connected,
            "ai": self.is_ai,
            "isHost": self.is_host,
            "tradeHistory": self.trade_history if self.trade_history else None
        }

class OnlineRoom:
    def __init__(self, code: str, host_id: str, raw_settings: Dict[str, Any]):
        self.code = code
        self.host_id = host_id
        self.status = "lobby"

        # Server-Side Strict Settings Validation & Clamping
        def safe_float(val, fallback, min_val, max_val):
            try:
                v = float(val)
                return max(min_val, min(max_val, v))
            except (ValueError, TypeError):
                return fallback

        def safe_int(val, fallback, min_val, max_val):
            try:
                v = int(val)
                return max(min_val, min(max_val, v))
            except (ValueError, TypeError):
                return fallback

        diff = str(raw_settings.get("difficulty", "Normal"))
        if diff not in ["Easy", "Normal", "Hard", "Extreme"]:
            diff = "Normal"

        model = str(raw_settings.get("marketModel", "trend_momentum"))
        if model not in ["trend_momentum", "mean_reversion", "cyclical_harmonic", "volatility_regime", "complex_hybrid"]:
            model = "trend_momentum"

        self.settings = {
            "startPrice": safe_float(raw_settings.get("startPrice"), 10.0, 1.0, 1000.0),
            "maxTurns": safe_int(raw_settings.get("maxTurns"), 50, 10, 200),
            "startCash": safe_float(raw_settings.get("startCash"), 200.0, 10.0, 10000.0),
            "startStocks": safe_int(raw_settings.get("startStocks"), 10, 0, 100),
            "difficulty": diff,
            "volatility": bool(raw_settings.get("volatility", True)),
            "marketModel": model
        }
        self.players: Dict[str, RoomPlayer] = {}
        self.game: Optional[Dict[str, Any]] = None

    def broadcast(self):
        data = {
            "type": "state",
            "room": {
                "code": self.code,
                "status": self.status,
                "hostPlayerId": self.host_id,
                "settings": self.settings,
                "players": [p.to_dict() for p in self.players.values()]
            },
            "game": self.game
        }
        for p in list(self.players.values()):
            if p.socket and p.connected:
                asyncio.create_task(self.send_to(p.socket, data, p.id))

    async def send_to(self, ws: WebSocket, data: Dict[str, Any], player_id: str):
        try:
            payload = dict(data)
            payload["you"] = {"playerId": player_id, "isHost": (player_id == self.host_id)}
            await ws.send_text(json.dumps(payload))
        except Exception:
            pass

active_rooms: Dict[str, OnlineRoom] = {}

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    # Authenticate Identity via Query Param or First Auth/Create/Join Message
    auth_player: Optional[Dict[str, Any]] = None
    client_room_code: Optional[str] = None

    # Check query param ?token=...
    token_param = ws.query_params.get("token")
    if token_param:
        try:
            auth_player = await get_player_from_token(token_param)
        except Exception:
            auth_player = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            mtype = msg.get("type")

            # 1. Heartbeat
            if mtype == "ping":
                await ws.send_text(json.dumps({"type": "pong", "time": time.time()}))
                continue

            # 2. Authentication Message (Alternative to query param)
            elif mtype == "auth":
                tok = msg.get("token")
                if tok:
                    try:
                        auth_player = await get_player_from_token(tok)
                        await ws.send_text(json.dumps({"type": "auth_ok", "username": auth_player["username"]}))
                    except Exception as e:
                        auth_player = None
                        await ws.send_text(json.dumps({"type": "error", "message": f"Authentication failed: {e.detail if hasattr(e, 'detail') else str(e)}"}))
                continue

            # 3. Create Room (Requires Authenticated Identity)
            elif mtype == "create_room":
                # Check for token in payload if not already authenticated
                if not auth_player and msg.get("token"):
                    try:
                        auth_player = await get_player_from_token(msg.get("token"))
                    except Exception:
                        auth_player = None

                if not auth_player:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Authentication required to create a room. Please sign in."
                    }))
                    continue

                # Server assigns verified identity strictly from database record
                p_id = str(auth_player["id"])
                p_name = str(auth_player["username"])
                p_color = str(auth_player.get("avatar_color") or "#00c8ff")

                code = "".join(random.choice(CODE_CHARS) for _ in range(6))
                while code in active_rooms:
                    code = "".join(random.choice(CODE_CHARS) for _ in range(6))

                room = OnlineRoom(code, p_id, msg.get("settings", {}))
                player = RoomPlayer(p_id, p_name, ws, is_host=True, color=p_color)
                player.cash = room.settings["startCash"]
                player.stocks = room.settings["startStocks"]
                room.players[p_id] = player
                active_rooms[code] = room
                client_room_code = code
                room.broadcast()

            # 4. Join Room (Requires Authenticated Identity)
            elif mtype == "join_room":
                if not auth_player and msg.get("token"):
                    try:
                        auth_player = await get_player_from_token(msg.get("token"))
                    except Exception:
                        auth_player = None

                if not auth_player:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Authentication required to join a room. Please sign in."
                    }))
                    continue

                code = (msg.get("room_code") or "").strip().upper()
                room = active_rooms.get(code)
                if not room:
                    await ws.send_text(json.dumps({"type": "error", "message": "Room not found."}))
                    continue
                if room.status != "lobby":
                    await ws.send_text(json.dumps({"type": "error", "message": "Game already in progress."}))
                    continue
                if len(room.players) >= 8:
                    await ws.send_text(json.dumps({"type": "error", "message": "Room is full (max 8 players)."}))
                    continue

                p_id = str(auth_player["id"])
                p_name = str(auth_player["username"])
                colors = ["#00c8ff", "#00ff6e", "#ffdc00", "#ff1c50", "#be00ff", "#ffcd00", "#ff00c8", "#ff9400"]
                p_color = colors[len(room.players) % len(colors)]

                player = RoomPlayer(p_id, p_name, ws, is_host=False, color=p_color)
                player.cash = room.settings["startCash"]
                player.stocks = room.settings["startStocks"]
                room.players[p_id] = player
                client_room_code = code
                room.broadcast()

            # 5. Start Game (Must be Room Host)
            elif mtype == "start_game":
                if not auth_player or not client_room_code or client_room_code not in active_rooms:
                    continue
                room = active_rooms[client_room_code]
                if room.host_id != str(auth_player["id"]):
                    await ws.send_text(json.dumps({"type": "error", "message": "Only the room host can start the match."}))
                    continue
                if len([p for p in room.players.values() if p.connected]) < 2:
                    await ws.send_text(json.dumps({"type": "error", "message": "At least 2 connected players are required."}))
                    continue

                start_p = room.settings["startPrice"]
                hist = [round(start_p * (1.0 + (random.random() - 0.5) * 0.05), 2) for _ in range(4)]
                hist.append(start_p)

                room.status = "playing"
                room.game = {
                    "players": [p.to_dict() for p in room.players.values()],
                    "price": start_p,
                    "history": hist,
                    "turn": 0,
                    "turnIndex": 0,
                    "maxTurns": room.settings["maxTurns"],
                    "difficulty": room.settings["difficulty"],
                    "volatility": room.settings["volatility"],
                    "marketModel": room.settings["marketModel"],
                    "news": None,
                    "rsi": 50.0,
                    "ended": False,
                    "quantity": 10,
                    "startPrice": start_p
                }
                room.broadcast()

            # 6. Player Action (Turn & Trade Rules Validated Authoritatively)
            elif mtype == "action":
                if not auth_player or not client_room_code or client_room_code not in active_rooms:
                    continue
                room = active_rooms[client_room_code]
                if not room.game or room.game["ended"]:
                    continue

                # Turn Validation
                cur_turn_p = room.game["players"][room.game["turnIndex"]]
                auth_id = str(auth_player["id"])
                if cur_turn_p["id"] != auth_id:
                    await ws.send_text(json.dumps({"type": "error", "message": "It is not your turn."}))
                    continue

                action = str(msg.get("action", "")).lower()
                try:
                    qty = int(msg.get("quantity") or 1)
                except (ValueError, TypeError):
                    qty = 1

                # Quantity Validation
                if qty < 1 or qty > 500:
                    await ws.send_text(json.dumps({"type": "error", "message": "Invalid trade quantity (1-500)."}))
                    continue

                cur_p = room.game["price"]
                p_obj = room.players.get(auth_id)

                if action == "buy":
                    cost = round(qty * cur_p, 2)
                    if p_obj.cash < cost:
                        await ws.send_text(json.dumps({"type": "error", "message": "Not enough cash to execute buy."}))
                        continue
                    p_obj.cash = round(p_obj.cash - cost, 2)
                    p_obj.stocks += qty
                    p_obj.trade_history.append({"action": "BUY", "qty": qty, "price": cur_p})
                elif action == "sell":
                    if p_obj.stocks < qty:
                        await ws.send_text(json.dumps({"type": "error", "message": "Not enough shares to sell."}))
                        continue
                    gain = round(qty * cur_p, 2)
                    p_obj.stocks -= qty
                    p_obj.cash = round(p_obj.cash + gain, 2)
                    p_obj.trade_history.append({"action": "SELL", "qty": qty, "price": cur_p})
                elif action == "skip":
                    pass
                else:
                    await ws.send_text(json.dumps({"type": "error", "message": "Invalid action. Use buy, sell, or skip."}))
                    continue

                # Step Market & Turn
                room.game["turn"] += 1
                model = room.settings.get("marketModel", "trend_momentum")
                step_res = MathMarketEngine.step(model, cur_p, room.game["history"], room.game["turn"], volatility_enabled=room.settings["volatility"])
                room.game["price"] = step_res["price"]
                room.game["history"].append(step_res["price"])
                if len(room.game["history"]) > 90:
                    room.game["history"].pop(0)

                # Compute RSI
                h = room.game["history"]
                if len(h) >= 2:
                    gains = sum(max(0, h[i] - h[i-1]) for i in range(1, len(h)))
                    losses = sum(max(0, h[i-1] - h[i]) for i in range(1, len(h)))
                    if losses == 0:
                        room.game["rsi"] = 100.0
                    else:
                        rs = (gains / len(h)) / (losses / len(h))
                        room.game["rsi"] = round(100.0 - (100.0 / (1.0 + rs)), 1)

                if step_res["headline"]:
                    room.game["news"] = {
                        "title": step_res["headline"][0],
                        "desc": step_res["headline"][1],
                        "impact": step_res["headline"][2]
                    }

                room.game["players"] = [p.to_dict() for p in room.players.values()]

                if room.game["turn"] >= room.game["maxTurns"]:
                    room.game["ended"] = True
                else:
                    room.game["turnIndex"] = (room.game["turnIndex"] + 1) % len(room.game["players"])

                room.broadcast()

            # 7. Leave Room
            elif mtype == "leave_room":
                if auth_player and client_room_code and client_room_code in active_rooms:
                    room = active_rooms[client_room_code]
                    p_id = str(auth_player["id"])
                    if p_id in room.players:
                        del room.players[p_id]
                        if not room.players or p_id == room.host_id:
                            del active_rooms[client_room_code]
                        else:
                            room.broadcast()
                break

    except WebSocketDisconnect:
        if auth_player and client_room_code and client_room_code in active_rooms:
            room = active_rooms[client_room_code]
            p_id = str(auth_player["id"])
            if p_id in room.players:
                room.players[p_id].connected = False
                room.broadcast()

if __name__ == "__main__":
    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)
