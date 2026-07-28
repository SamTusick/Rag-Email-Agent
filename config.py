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

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB = os.environ["POSTGRES_DB"]
POSTGRES_USER = os.environ["POSTGRES_USER"]
POSTGRES_PASSWORD = os.environ["POSTGRES_PASSWORD"]

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBEDDING_MODEL = os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")

CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "200"))
