import sys

import config
from ingest.db import get_connection
from triage.db import get_chunk_embeddings, get_target_emails, sender_context, similar_past_emails, upsert_summary
from triage.llm import summarize_and_grade
from triage.time_window import previous_day_window


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    window_start, window_end = previous_day_window()
    conn = get_connection()
    try:
        emails = get_target_emails(conn, config.ACCOUNT_ID, window_start, window_end)
        if not emails:
            print(f"No emails found for {config.ACCOUNT_ID} in {window_start} .. {window_end}")
            return

        for email in emails:
            context = sender_context(
                conn,
                config.ACCOUNT_ID,
                email["sender"],
                email["id"],
                email["received_at"],
                config.SENDER_CONTEXT_LIMIT,
            )

            query_embeddings = get_chunk_embeddings(conn, email["id"])
            grounding = similar_past_emails(
                conn,
                config.ACCOUNT_ID,
                email["id"],
                email["received_at"],
                query_embeddings,
                config.GROUNDING_LIMIT,
            )

            summary, urgency = summarize_and_grade(email, context, grounding)

            with conn.transaction():
                upsert_summary(conn, config.ACCOUNT_ID, email["id"], summary, urgency)

            print(f"Summarized {email['id']} ({urgency}): {email['subject']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
