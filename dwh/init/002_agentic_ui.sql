CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS ops.agent_query_audit (
    audit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_id TEXT NOT NULL UNIQUE,
    endpoint TEXT NOT NULL,
    request_type TEXT NOT NULL,
    question TEXT,
    generated_sql TEXT,
    target TEXT,
    status TEXT NOT NULL,
    row_count BIGINT,
    execution_time_ms BIGINT,
    selected_tables JSONB NOT NULL DEFAULT '[]'::JSONB,
    attempts JSONB NOT NULL DEFAULT '[]'::JSONB,
    token_usage JSONB NOT NULL DEFAULT '{}'::JSONB,
    error_code TEXT,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    user_id TEXT,
    username TEXT,
    user_role TEXT,
    user_team TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_query_audit_created_at
    ON ops.agent_query_audit (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_query_audit_status
    ON ops.agent_query_audit (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_query_audit_user_created_at
    ON ops.agent_query_audit (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS auth.users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
    team TEXT NOT NULL,
    allowed_services TEXT[] NOT NULL DEFAULT '{}',
    allowed_source_apps TEXT[] NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ops.chat_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES auth.users(user_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    structured_memory JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated
    ON ops.chat_sessions (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS ops.chat_messages (
    message_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES ops.chat_sessions(session_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES auth.users(user_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    structured_state JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
    ON ops.chat_messages (session_id, created_at);
