from flask import Flask, jsonify, redirect, url_for

import config
from auth.msal_client import get_token_silent
from auth.routes import bp as auth_bp
from graph.client import get_recent_messages


def create_app():
    app = Flask(__name__)
    app.secret_key = config.FLASK_SECRET_KEY
    app.register_blueprint(auth_bp)

    @app.route("/")
    def fetch_messages():
        token = get_token_silent()
        if not token:
            return redirect(url_for("auth.login"))
        return jsonify(get_recent_messages(token))

    return app


if __name__ == "__main__":
    create_app().run(port=5000, debug=True)
