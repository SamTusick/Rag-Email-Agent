CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE emails (
    id                BIGSERIAL PRIMARY KEY,
    graph_message_id  TEXT NOT NULL UNIQUE,
    subject           TEXT,
    sender            TEXT,
    received_at       TIMESTAMPTZ,
    raw_body          TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- vector dimension must match OLLAMA_EMBEDDING_MODEL's output (nomic-embed-text = 768)
CREATE TABLE email_chunks (
    id          BIGSERIAL PRIMARY KEY,
    email_id    BIGINT NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content     TEXT NOT NULL,
    embedding   VECTOR(768) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (email_id, chunk_index)
);
