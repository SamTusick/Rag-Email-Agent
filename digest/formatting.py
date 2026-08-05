import html

URGENCY_ORDER = ["urgent", "high", "medium", "low"]
URGENCY_LABELS = {"urgent": "Urgent", "high": "High", "medium": "Medium", "low": "Low"}


def build_digest_html(digest_date, summaries_by_urgency):
    sections = []
    for level in URGENCY_ORDER:
        items = summaries_by_urgency.get(level, [])
        if not items:
            continue
        rows = "".join(
            f"<li><strong>{html.escape(item['subject'] or '(no subject)')}</strong> "
            f"— {html.escape(item['summary'])} "
            f"<span style=\"color:#666\">({html.escape(item['sender'] or '')})</span></li>"
            for item in items
        )
        sections.append(f"<h3>{URGENCY_LABELS[level]}</h3><ul>{rows}</ul>")

    return f"<h2>Daily Digest — {digest_date.isoformat()}</h2>" + "".join(sections)
