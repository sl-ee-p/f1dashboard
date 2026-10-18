"""Jinja filters and globals."""

import json
from datetime import date, datetime

from markupsafe import Markup

COMPOUND_LETTER = {"SOFT": "S", "MEDIUM": "M", "HARD": "H", "INTERMEDIATE": "I", "WET": "W"}


def register(app):
    @app.template_filter("tojson_attr")
    def tojson_attr(value):
        """JSON safe to embed in <script type="application/json">."""
        text = json.dumps(value, default=str, separators=(",", ":"))
        return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))

    @app.template_filter("isotime")
    def isotime(value):
        if isinstance(value, datetime | date):
            return value.isoformat()
        return str(value or "")

    @app.template_filter("nicedate")
    def nicedate(value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{value.day} {value:%b %Y}"

    @app.template_filter("points")
    def points(value):
        if value is None:
            return ""
        return f"{value:g}"

    @app.template_filter("compound_letter")
    def compound_letter(value):
        return COMPOUND_LETTER.get((value or "").upper(), "?")

    app.jinja_env.globals["COMPOUND_LETTER"] = COMPOUND_LETTER
