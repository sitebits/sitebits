"""Gemeinsame Test-Einstellungen."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from werkzeugpflicht import PRAEFIX

from scanview_nachweise import signaturen


@pytest.fixture(autouse=True)
def _anker_genesis_zuruecksetzen(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`signierhilfe.anker` setzt den Genesis-Commit des jeweiligen Test-Archivs als
    `signaturen.ANKER_GENESIS`; nach jedem Test gilt wieder der echte Wert."""
    monkeypatch.setattr(signaturen, "ANKER_GENESIS", signaturen.ANKER_GENESIS)
    yield


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """Auslassungen wegen fehlender Werkzeuge nicht im Skip-Zähler untergehen lassen."""
    gruende: dict[str, int] = {}
    for bericht in terminalreporter.stats.get("skipped", []):
        grund = bericht.longrepr[2] if isinstance(bericht.longrepr, tuple) else ""
        if PRAEFIX in grund:
            grund = grund.removeprefix("Skipped: ")
            gruende[grund] = gruende.get(grund, 0) + 1
    if gruende:
        terminalreporter.section("WARNUNG: Prüfungen ausgelassen, Werkzeug fehlt", yellow=True)
        for grund, anzahl in gruende.items():
            terminalreporter.write_line(f"{anzahl} Test(s): {grund}")
        terminalreporter.write_line(
            "Das Qualitätstor ist so unvollständig; mit SCANVIEW_QUALITAETSTOR=1 wird das ein "
            "Fehlschlag.")
