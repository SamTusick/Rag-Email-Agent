import config
from auth.msal_client import get_token_silent
from graph.client import get_messages_with_body
from ingest.chunking import chunk_text
from ingest.cleaning import html_to_text, strip_quoted
from ingest.db import get_connection, replace_chunks, upsert_email
from ingest.embeddings import embed_text

EXPECTED_EMBEDDING_DIM = 768


def main():
    token = get_token_silent()
    if not token:
        print("Not authenticated — log in via /auth/login first.")
        return

    messages = get_messages_with_body(token, top=50)
    conn = get_connection()
    try:
        for msg in messages:
            body = msg["body"]
            if body["contentType"] == "html":
                text = html_to_text(body["content"])
            else:
                text = body["content"]
            text = strip_quoted(text)

            chunks = chunk_text(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
            if not chunks:
                continue

            embeddings = [embed_text(c) for c in chunks]
            if len(embeddings[0]) != EXPECTED_EMBEDDING_DIM:
                raise ValueError(
                    f"Embedding dimension {len(embeddings[0])} from "
                    f"{config.OLLAMA_EMBEDDING_MODEL} does not match schema's "
                    f"VECTOR({EXPECTED_EMBEDDING_DIM})"
                )

            with conn.transaction():
                email_id = upsert_email(
                    conn,
                    msg["id"],
                    msg.get("subject"),
                    msg["from"]["emailAddress"]["address"],
                    msg["receivedDateTime"],
                    text,
                )
                replace_chunks(conn, email_id, list(zip(chunks, embeddings)))

            print(f"Ingested {msg['id']}: {len(chunks)} chunks")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
