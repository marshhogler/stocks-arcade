-- ====================================================================
-- STOCKS ARCADE — SUPABASE POSTGRESQL SCHEMA (PHASE 3 & 4)
-- Compatible with PostgreSQL 14+ on Supabase Free Tier
-- ====================================================================

-- Enable pgcrypto extension for UUIDs and secure cryptographic hashing
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. PLAYERS TABLE
CREATE TABLE IF NOT EXISTS players (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(24) NOT NULL UNIQUE,
    username_clean VARCHAR(24) NOT NULL UNIQUE, -- Lowercase normalized for case-insensitive uniqueness
    password_hash TEXT NOT NULL,                -- Argon2id secure hash
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    avatar_color VARCHAR(12) NOT NULL DEFAULT '#00c8ff',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index on normalized username for fast case-insensitive logins
CREATE INDEX IF NOT EXISTS idx_players_username_clean ON players(username_clean);

-- 2. PORTFOLIOS TABLE (One Authoritative Global Portfolio Per Player)
CREATE TABLE IF NOT EXISTS portfolios (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID NOT NULL UNIQUE REFERENCES players(id) ON DELETE CASCADE,
    cash_balance NUMERIC(14, 2) NOT NULL DEFAULT 1000.00 CHECK (cash_balance >= 0),
    currency VARCHAR(8) NOT NULL DEFAULT 'USD',
    version INT NOT NULL DEFAULT 1, -- Optimistic concurrency counter
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. STOCK HOLDINGS TABLE
CREATE TABLE IF NOT EXISTS stock_holdings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    stock_symbol VARCHAR(12) NOT NULL,
    quantity INT NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    average_cost_basis NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_player_stock UNIQUE (player_id, stock_symbol)
);

CREATE INDEX IF NOT EXISTS idx_stock_holdings_player ON stock_holdings(player_id);

-- 4. SAVED GAMES TABLE
CREATE TABLE IF NOT EXISTS saved_games (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    name VARCHAR(48) NOT NULL,
    visibility VARCHAR(16) NOT NULL DEFAULT 'private', -- 'private', 'invite_code', 'public'
    invite_code VARCHAR(12) UNIQUE,                     -- 6-character uppercase alphanumeric code
    invite_code_hash VARCHAR(64),                      -- SHA-256 hash of the invite code
    current_season INT NOT NULL DEFAULT 1,
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'active',      -- 'active', 'paused', 'completed'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_saved_games_owner ON saved_games(owner_player_id);
CREATE INDEX IF NOT EXISTS idx_saved_games_invite_code ON saved_games(invite_code);

-- 5. SEASONS TABLE
CREATE TABLE IF NOT EXISTS seasons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    saved_game_id UUID NOT NULL REFERENCES saved_games(id) ON DELETE CASCADE,
    season_number INT NOT NULL,
    season_name VARCHAR(64) NOT NULL,
    market_model VARCHAR(32) NOT NULL DEFAULT 'trend_momentum', -- 'trend_momentum', 'mean_reversion', 'cyclical_harmonic', 'volatility_regime', 'complex_hybrid'
    deterministic_seed INT NOT NULL DEFAULT 42,
    max_turns INT NOT NULL DEFAULT 50,
    current_turn INT NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'in_progress', -- 'in_progress', 'completed'
    config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    CONSTRAINT uq_game_season UNIQUE (saved_game_id, season_number)
);

CREATE INDEX IF NOT EXISTS idx_seasons_game ON seasons(saved_game_id);

-- 6. GAME PARTICIPANTS TABLE
CREATE TABLE IF NOT EXISTS game_participants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    saved_game_id UUID NOT NULL REFERENCES saved_games(id) ON DELETE CASCADE,
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'member', -- 'owner', 'member', 'spectator'
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    CONSTRAINT uq_game_participant UNIQUE (saved_game_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_participants_player ON game_participants(player_id);

-- 7. TRANSACTIONS TABLE (Append-Only Audit Ledger)
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    saved_game_id UUID REFERENCES saved_games(id) ON DELETE SET NULL,
    season_id UUID REFERENCES seasons(id) ON DELETE SET NULL,
    stock_symbol VARCHAR(12) NOT NULL,
    transaction_type VARCHAR(8) NOT NULL, -- 'BUY', 'SELL'
    quantity INT NOT NULL CHECK (quantity > 0),
    execution_price NUMERIC(14, 2) NOT NULL CHECK (execution_price > 0),
    total_amount NUMERIC(14, 2) NOT NULL CHECK (total_amount > 0),
    fees NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    cash_delta NUMERIC(14, 2) NOT NULL,
    realized_pnl NUMERIC(14, 2) DEFAULT 0.00,
    idempotency_key VARCHAR(64) UNIQUE, -- Prevents duplicate submissions
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_player ON transactions(player_id);
CREATE INDEX IF NOT EXISTS idx_transactions_game_season ON transactions(saved_game_id, season_id);
CREATE INDEX IF NOT EXISTS idx_transactions_idempotency ON transactions(idempotency_key);

-- 8. MARKET SNAPSHOTS TABLE
CREATE TABLE IF NOT EXISTS market_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    saved_game_id UUID NOT NULL REFERENCES saved_games(id) ON DELETE CASCADE,
    season_id UUID NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    turn_index INT NOT NULL,
    price NUMERIC(14, 2) NOT NULL,
    history JSONB NOT NULL DEFAULT '[]'::jsonb,
    rsi NUMERIC(8, 2) NOT NULL DEFAULT 50.00,
    market_model VARCHAR(32) NOT NULL,
    active_regime VARCHAR(32) DEFAULT 'normal',
    headline JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_season ON market_snapshots(season_id, turn_index);

-- 9. PLAYER STATISTICS TABLE
CREATE TABLE IF NOT EXISTS player_statistics (
    player_id UUID PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    games_participated INT NOT NULL DEFAULT 0,
    seasons_completed INT NOT NULL DEFAULT 0,
    seasons_won INT NOT NULL DEFAULT 0,
    total_trades INT NOT NULL DEFAULT 0,
    total_volume NUMERIC(16, 2) NOT NULL DEFAULT 0.00,
    all_time_profit NUMERIC(16, 2) NOT NULL DEFAULT 0.00,
    highest_net_worth NUMERIC(16, 2) NOT NULL DEFAULT 1000.00,
    last_active TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ====================================================================
-- ATOMIC TRADE EXECUTION STORED PROCEDURE (For safe concurrent execution)
-- ====================================================================
CREATE OR REPLACE FUNCTION execute_player_trade(
    p_player_id UUID,
    p_game_id UUID,
    p_season_id UUID,
    p_symbol VARCHAR(12),
    p_action VARCHAR(8),
    p_quantity INT,
    p_price NUMERIC(14, 2),
    p_idempotency_key VARCHAR(64)
) RETURNS JSONB AS $$
DECLARE
    v_cash NUMERIC(14, 2);
    v_portfolio_id UUID;
    v_existing_qty INT := 0;
    v_existing_cost NUMERIC(14, 2) := 0.00;
    v_total NUMERIC(14, 2);
    v_realized_pnl NUMERIC(14, 2) := 0.00;
    v_new_qty INT;
    v_new_cost NUMERIC(14, 2);
    v_tx_id UUID;
BEGIN
    -- 1. Check idempotency
    IF p_idempotency_key IS NOT NULL THEN
        SELECT id INTO v_tx_id FROM transactions WHERE idempotency_key = p_idempotency_key;
        IF v_tx_id IS NOT NULL THEN
            RETURN jsonb_build_object('success', true, 'duplicate', true, 'transaction_id', v_tx_id);
        END IF;
    END IF;

    -- 2. Lock portfolio row for update to prevent concurrent race condition
    SELECT id, cash_balance INTO v_portfolio_id, v_cash 
    FROM portfolios WHERE player_id = p_player_id FOR UPDATE;

    IF v_portfolio_id IS NULL THEN
        RAISE EXCEPTION 'Portfolio not found for player %', p_player_id;
    END IF;

    -- 3. Lock stock holding row if it exists
    SELECT quantity, average_cost_basis INTO v_existing_qty, v_existing_cost
    FROM stock_holdings WHERE player_id = p_player_id AND stock_symbol = p_symbol FOR UPDATE;

    IF v_existing_qty IS NULL THEN
        v_existing_qty := 0;
        v_existing_cost := 0.00;
    END IF;

    v_total := ROUND(p_quantity * p_price, 2);

    -- 4. Process BUY
    IF UPPER(p_action) = 'BUY' THEN
        IF v_cash < v_total THEN
            RAISE EXCEPTION 'Insufficient cash: available %, required %', v_cash, v_total;
        END IF;

        -- Update cash
        UPDATE portfolios 
        SET cash_balance = cash_balance - v_total, updated_at = NOW(), version = version + 1
        WHERE id = v_portfolio_id;

        -- Update stock holdings with new average cost basis
        v_new_qty := v_existing_qty + p_quantity;
        v_new_cost := ROUND(((v_existing_qty * v_existing_cost) + v_total) / v_new_qty, 2);

        INSERT INTO stock_holdings (player_id, stock_symbol, quantity, average_cost_basis, updated_at)
        VALUES (p_player_id, p_symbol, v_new_qty, v_new_cost, NOW())
        ON CONFLICT (player_id, stock_symbol)
        DO UPDATE SET quantity = EXCLUDED.quantity, average_cost_basis = EXCLUDED.average_cost_basis, updated_at = NOW();

        -- Record transaction
        INSERT INTO transactions (
            player_id, saved_game_id, season_id, stock_symbol, transaction_type,
            quantity, execution_price, total_amount, fees, cash_delta, realized_pnl, idempotency_key
        ) VALUES (
            p_player_id, p_game_id, p_season_id, p_symbol, 'BUY',
            p_quantity, p_price, v_total, 0.00, -v_total, 0.00, p_idempotency_key
        ) RETURNING id INTO v_tx_id;

    -- 5. Process SELL
    ELSIF UPPER(p_action) = 'SELL' THEN
        IF v_existing_qty < p_quantity THEN
            RAISE EXCEPTION 'Insufficient shares: available %, requested %', v_existing_qty, p_quantity;
        END IF;

        v_realized_pnl := ROUND(p_quantity * (p_price - v_existing_cost), 2);

        -- Update cash
        UPDATE portfolios 
        SET cash_balance = cash_balance + v_total, updated_at = NOW(), version = version + 1
        WHERE id = v_portfolio_id;

        -- Update stock holdings
        v_new_qty := v_existing_qty - p_quantity;
        IF v_new_qty = 0 THEN
            UPDATE stock_holdings SET quantity = 0, average_cost_basis = 0.00, updated_at = NOW()
            WHERE player_id = p_player_id AND stock_symbol = p_symbol;
        ELSE
            UPDATE stock_holdings SET quantity = v_new_qty, updated_at = NOW()
            WHERE player_id = p_player_id AND stock_symbol = p_symbol;
        END IF;

        -- Record transaction
        INSERT INTO transactions (
            player_id, saved_game_id, season_id, stock_symbol, transaction_type,
            quantity, execution_price, total_amount, fees, cash_delta, realized_pnl, idempotency_key
        ) VALUES (
            p_player_id, p_game_id, p_season_id, p_symbol, 'SELL',
            p_quantity, p_price, v_total, 0.00, v_total, v_realized_pnl, p_idempotency_key
        ) RETURNING id INTO v_tx_id;

    ELSE
        RAISE EXCEPTION 'Invalid trade action: %', p_action;
    END IF;

    -- 6. Update player statistics
    INSERT INTO player_statistics (player_id, total_trades, total_volume, all_time_profit, last_active)
    VALUES (p_player_id, 1, v_total, v_realized_pnl, NOW())
    ON CONFLICT (player_id)
    DO UPDATE SET 
        total_trades = player_statistics.total_trades + 1,
        total_volume = player_statistics.total_volume + EXCLUDED.total_volume,
        all_time_profit = player_statistics.all_time_profit + EXCLUDED.all_time_profit,
        last_active = NOW();

    -- Return updated state
    SELECT cash_balance INTO v_cash FROM portfolios WHERE id = v_portfolio_id;
    RETURN jsonb_build_object(
        'success', true,
        'transaction_id', v_tx_id,
        'action', p_action,
        'quantity', p_quantity,
        'execution_price', p_price,
        'total_amount', v_total,
        'remaining_cash', v_cash,
        'remaining_quantity', v_new_qty,
        'realized_pnl', v_realized_pnl
    );
END;
$$ LANGUAGE plpgsql;


-- ====================================================================
-- ROW LEVEL SECURITY (RLS) POLICIES
-- ====================================================================
ALTER TABLE players ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolios ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_holdings ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_games ENABLE ROW LEVEL SECURITY;
ALTER TABLE seasons ENABLE ROW LEVEL SECURITY;
ALTER TABLE game_participants ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE player_statistics ENABLE ROW LEVEL SECURITY;

-- Allow service_role (used by backend with DATABASE_URL / connection pool) full operational access
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_players') THEN
        CREATE POLICY service_role_all_players ON players FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_portfolios') THEN
        CREATE POLICY service_role_all_portfolios ON portfolios FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_stock_holdings') THEN
        CREATE POLICY service_role_all_stock_holdings ON stock_holdings FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_saved_games') THEN
        CREATE POLICY service_role_all_saved_games ON saved_games FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_seasons') THEN
        CREATE POLICY service_role_all_seasons ON seasons FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_game_participants') THEN
        CREATE POLICY service_role_all_game_participants ON game_participants FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_transactions') THEN
        CREATE POLICY service_role_all_transactions ON transactions FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_market_snapshots') THEN
        CREATE POLICY service_role_all_market_snapshots ON market_snapshots FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'service_role_all_player_statistics') THEN
        CREATE POLICY service_role_all_player_statistics ON player_statistics FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;
