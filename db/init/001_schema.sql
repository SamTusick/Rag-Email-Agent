CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE emails (
    id                BIGSERIAL PRIMARY KEY,
    account_id        TEXT NOT NULL,
    graph_message_id  TEXT NOT NULL,
    subject           TEXT,
    sender            TEXT,
    received_at       TIMESTAMPTZ,
    raw_body          TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, graph_message_id)
);

CREATE INDEX emails_account_id_idx ON emails (account_id);

-- vector dimension must match OPENAI_EMBEDDING_MODEL's output (text-embedding-3-small = 1536)
CREATE TABLE email_chunks (
    id          BIGSERIAL PRIMARY KEY,
    account_id  TEXT NOT NULL,
    email_id    BIGINT NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content     TEXT NOT NULL,
    embedding   VECTOR(1536) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (email_id, chunk_index)
);

CREATE INDEX email_chunks_account_id_idx ON email_chunks (account_id);
