import sys

import config
from auth.accounts import get_all_account_ids, get_priority_context
from ingest.db import get_connection
from triage.db import get_chunk_embeddings, get_target_emails, sender_context, similar_past_emails, upsert_summary
from triage.llm import summarize_and_grade
from triage.time_window import previous_day_window


def triage_account(conn, account_id, window_start, window_end):
    emails = get_target_emails(conn, account_id, window_start, window_end)
    if not emails:
        print(f"No emails found for {account_id} in {window_start} .. {window_end}")
        return

    priority_context = get_priority_context(conn, account_id)

    for email in emails:
        context = sender_context(
            conn,
            account_id,
            email["sender"],
            email["id"],
            email["received_at"],
            config.SENDER_CONTEXT_LIMIT,
        )

        query_embeddings = get_chunk_embeddings(conn, email["id"])
        grounding = similar_past_emails(
            conn,
            account_id,
            email["id"],
            email["received_at"],
            query_embeddings,
            config.GROUNDING_LIMIT,
        )

        summary, urgency = summarize_and_grade(email, context, grounding, priority_context)

        with conn.transaction():
            upsert_summary(conn, account_id, email["id"], summary, urgency)

        print(f"Summarized {email['id']} ({urgency}): {email['subject']}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    window_start, window_end = previous_day_window()
    conn = get_connection()
    try:
        account_ids = get_all_account_ids(conn)
        if not account_ids:
            print("No provisioned accounts found.")
            return

        for account_id in account_ids:
            try:
                print(f"--- Triaging for {account_id} ---")
                triage_account(conn, account_id, window_start, window_end)
            except Exception as exc:
                print(f"Triage failed for {account_id}: {type(exc).__name__}: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
