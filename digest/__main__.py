import sys

from auth.accounts import get_all_account_ids, get_token_for_account
from digest.db import already_sent, get_summaries_for_digest, mark_sent
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
        account_ids = get_all_account_ids(conn)
        if not account_ids:
            print("No provisioned accounts found.")
            return

        for account_id in account_ids:
            try:
                if already_sent(conn, account_id, digest_date):
                    print(f"Digest for {account_id} on {digest_date} already sent, skipping")
                    continue

                grouped = get_summaries_for_digest(conn, account_id, window_start, window_end)
                if not grouped:
                    print(f"No summaries for {account_id} on {digest_date}, skipping")
                    continue

                token = get_token_for_account(conn, account_id)
                if not token:
                    print(f"Could not get a token for {account_id}, skipping.")
                    continue

                html_body = build_digest_html(digest_date, grouped)
                send_mail(token, account_id, f"Daily Digest — {digest_date.isoformat()}", html_body)

                mark_sent(conn, account_id, digest_date)
                print(f"Sent digest for {account_id} on {digest_date}")
            except Exception as exc:
                print(f"Digest failed for {account_id}: {type(exc).__name__}: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
