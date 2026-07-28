import requests

import config


def get_recent_messages(access_token, top=10):
    response = requests.get(
        f"{config.GRAPH_BASE_URL}/me/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "$top": top,
            "$select": "subject,from,receivedDateTime",
        },
    )
    response.raise_for_status()
    return response.json().get("value", [])
