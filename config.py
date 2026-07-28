import os

from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["CLIENT_ID"]
AUTHORITY = os.environ["AUTHORITY"]
REDIRECT_URI = os.environ["REDIRECT_URI"]
GRAPH_SCOPES = os.environ["GRAPH_SCOPES"].split()
FLASK_SECRET_KEY = os.environ["FLASK_SECRET_KEY"]
TOKEN_CACHE_PATH = os.environ.get("TOKEN_CACHE_PATH", "token_cache.bin")

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
