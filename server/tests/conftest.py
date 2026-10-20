from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scanview_server.config import Settings
from scanview_server.web import create_app

ORIGIN = "https://scanview.test"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    state = tmp_path / "state"
    data.mkdir()
    state.mkdir()
    return Settings(
        data_dir=data, state_dir=state, origin=ORIGIN, rp_id="scanview.test",
        rp_name="scanview", cookie_secure=True,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as c:
        yield c


def csrf() -> dict[str, str]:
    """Header, die jede ändernde Anfrage braucht."""
    return {"Origin": ORIGIN, "X-Scanview": "1"}
