import msal

import config


def _load_cache():
    cache = msal.SerializableTokenCache()
    try:
        with open(config.TOKEN_CACHE_PATH, "r") as f:
            cache.deserialize(f.read())
    except FileNotFoundError:
        pass
    return cache


def _save_cache(cache):
    if cache.has_state_changed:
        with open(config.TOKEN_CACHE_PATH, "w") as f:
            f.write(cache.serialize())


def build_msal_app():
    cache = _load_cache()
    app = msal.PublicClientApplication(
        config.CLIENT_ID,
        authority=config.AUTHORITY,
        token_cache=cache,
    )
    return app, cache


def get_token_silent():
    """Returns a valid access token from the cache, or None if interactive
    login (via /auth/login) is required."""
    app, cache = build_msal_app()
    accounts = app.get_accounts()
    if not accounts:
        return None

    result = app.acquire_token_silent(config.GRAPH_SCOPES, account=accounts[0])
    _save_cache(cache)

    if result and "access_token" in result:
        return result["access_token"]
    return None
