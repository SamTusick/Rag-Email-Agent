import sys

from auth.msal_client import get_token_silent
from digest.db import already_sent, get_account_ids_with_summaries, get_summaries_for_digest, mark_sent
from digest.formatting import build_digest_html
from graph.client import send_mail
from ingest.db import get_connection
from triage.time_window import previous_day_window


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    window_start, window_end = previous_day_window()
    digest_date = window_start.date()

    conn = get_connection()
    try:
        account_ids = get_account_ids_with_summaries(conn, window_start, window_end)
        if not account_ids:
            print(f"No summaries found for {digest_date}")
            return

        for account_id in account_ids:
            if already_sent(conn, account_id, digest_date):
                print(f"Digest for {account_id} on {digest_date} already sent, skipping")
                continue

            token = get_token_silent(account_id)
            if not token:
                print(f"Not authenticated for {account_id} — log in via /auth/login first")
                continue

            grouped = get_summaries_for_digest(conn, account_id, window_start, window_end)
            html_body = build_digest_html(digest_date, grouped)

            send_mail(token, account_id, f"Daily Digest — {digest_date.isoformat()}", html_body)

            mark_sent(conn, account_id, digest_date)
            print(f"Sent digest for {account_id} on {digest_date}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
