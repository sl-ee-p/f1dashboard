import gzip
import json
from pathlib import Path

import pytest

from f1dash import create_app
from f1dash.config import Config
from f1dash.data.http import NotFound
from f1dash.data.sources import DataHub

FIXTURES = Path(__file__).parent / "fixtures" / "upstream.json.gz"
RACE, QUALI = 11388, 11384


class FixtureFetcher:
    """Serves recorded upstream responses (see scripts/record_fixtures.py)."""

    def __init__(self) -> None:
        self.data = json.loads(gzip.decompress(FIXTURES.read_bytes()))
        self.misses: list[str] = []

    def get_json(self, url, params=None, expire=None):
        key = url
        if params:
            key += "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        if key in self.data:
            return self.data[key]
        # Telemetry windows: match on driver regardless of the exact time range.
        if params and "driver_number" in params:
            for k, v in self.data.items():
                if k.startswith(url + "?") and f"driver_number={params['driver_number']}" in k:
                    return v
        self.misses.append(key)
        raise NotFound(key)


@pytest.fixture(scope="session")
def fetcher():
    return FixtureFetcher()


@pytest.fixture(scope="session")
def hub(fetcher, tmp_path_factory):
    config = Config(cache_dir=tmp_path_factory.mktemp("cache"), secret_key="test")
    return DataHub(config, fetcher=fetcher)


@pytest.fixture(scope="session")
def app(hub, tmp_path_factory):
    config = Config(cache_dir=tmp_path_factory.mktemp("cache2"), secret_key="test")
    app = create_app(config, hub=hub)
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def race(hub):
    return hub.openf1.session(RACE)


@pytest.fixture(scope="session")
def quali(hub):
    return hub.openf1.session(QUALI)
