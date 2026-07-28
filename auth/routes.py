from flask import Blueprint, redirect, request, session, url_for

import config
from auth.msal_client import _save_cache, build_msal_app

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login")
def login():
    app, _cache = build_msal_app()
    flow = app.initiate_auth_code_flow(
        config.GRAPH_SCOPES,
        redirect_uri=config.REDIRECT_URI,
    )
    # MSAL's flow dict carries the PKCE code_verifier and state — it must
    # survive until the callback request, so it goes in the session rather
    # than being recomputed.
    session["auth_flow"] = flow
    return redirect(flow["auth_uri"])


@bp.route("/callback")
def callback():
    app, cache = build_msal_app()
    flow = session.pop("auth_flow", {})
    result = app.acquire_token_by_auth_code_flow(flow, request.args)
    _save_cache(cache)

    if "access_token" not in result:
        return f"Auth failed: {result.get('error_description', result)}", 400

    return redirect(url_for("fetch_messages"))
