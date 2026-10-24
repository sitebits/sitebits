from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scanview_server.config import Settings
from scanview_server.web import create_app

ORIGIN = "https://scanview.test"

#: Tests mit Marker `oom_live` lassen eine Unit mit `Result=oom-kill` enden; der KDE-Melder zeigt
#: dann „Speichermangel abgewendet“. Sie laufen nur mit dieser Variablen — die Release-Kette
#: (nachweise, Stufe `qualitaetstor`) setzt sie verpflichtend.
OOM_LIVE_VARIABLE = "SCANVIEW_OOM_LIVE"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get(OOM_LIVE_VARIABLE) == "1":
        return
    ueberspringen = pytest.mark.skip(
        reason=f"oom_live: endet mit Result=oom-kill (KDE-Meldung); läuft nur mit "
               f"{OOM_LIVE_VARIABLE}=1, das die Release-Kette setzt")
    for item in items:
        if item.get_closest_marker("oom_live") is not None:
            item.add_marker(ueberspringen)


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
