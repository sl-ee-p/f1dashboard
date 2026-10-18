"""F1 dashboard: session analysis, season overview and live-timing replay."""

from __future__ import annotations

import logging

from flask import Flask, render_template

from .config import Config
from .data.http import UpstreamError
from .data.sources import DataHub
from .services import Dashboard, NoSuchSession


def create_app(config: Config | None = None, hub: DataHub | None = None) -> Flask:
    config = config or Config()
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key
    app.config["F1DASH"] = config
    app.extensions["dashboard"] = Dashboard(hub or DataHub(config))

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    from .web import api, filters, live, pages

    filters.register(app)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(live.bp)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.errorhandler(NoSuchSession)
    @app.errorhandler(404)
    def not_found(_exc):
        return render_template("error.html", code=404, message="Nothing here."), 404

    @app.errorhandler(UpstreamError)
    def upstream(exc):
        app.logger.warning("upstream failure: %s", exc)
        return (
            render_template(
                "error.html",
                code=503,
                message="A data source (OpenF1 or Jolpica) didn't answer. Try again shortly.",
            ),
            503,
        )

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp

    return app
