import os

from dotenv import load_dotenv

load_dotenv()

ACCOUNT_ID = os.environ["ACCOUNT_ID"]
CLIENT_ID = os.environ["CLIENT_ID"]
AUTHORITY = os.environ["AUTHORITY"]
REDIRECT_URI = os.environ["REDIRECT_URI"]
GRAPH_SCOPES = os.environ["GRAPH_SCOPES"].split()
FLASK_SECRET_KEY = os.environ["FLASK_SECRET_KEY"]
TOKEN_CACHE_PATH = os.environ.get("TOKEN_CACHE_PATH", "token_cache.bin")

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB = os.environ["POSTGRES_DB"]
POSTGRES_USER = os.environ["POSTGRES_USER"]
POSTGRES_PASSWORD = os.environ["POSTGRES_PASSWORD"]

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "200"))

SUMMARIZATION_MODEL = os.environ.get("SUMMARIZATION_MODEL", "gpt-5-mini")
SENDER_CONTEXT_LIMIT = int(os.environ.get("SENDER_CONTEXT_LIMIT", "5"))
GROUNDING_LIMIT = int(os.environ.get("GROUNDING_LIMIT", "5"))
CONTEXT_SNIPPET_CHARS = int(os.environ.get("CONTEXT_SNIPPET_CHARS", "300"))
