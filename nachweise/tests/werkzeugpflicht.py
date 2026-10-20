"""Tests, die ein externes Werkzeug brauchen (gpg, Docker, gepinnte Images), dürfen nicht stumm
verschwinden. Fehlt das Werkzeug, wird mit einem unübersehbaren Grund übersprungen
(`conftest.py` wiederholt jede solche Auslassung am Ende des Laufs als Warnung); im
Qualitätstor — `SCANVIEW_QUALITAETSTOR=1` — ist ein fehlendes Werkzeug ein Fehlschlag."""

from __future__ import annotations

import os

import pytest

#: Beginn des Skip-Grundes; `conftest.pytest_terminal_summary` sucht danach.
PRAEFIX = "WERKZEUG FEHLT"
ENV_QUALITAETSTOR = "SCANVIEW_QUALITAETSTOR"


def verlange(werkzeug: str, vorhanden: bool, zweck: str) -> None:
    if vorhanden:
        return
    meldung = f"{PRAEFIX}: {werkzeug} — {zweck} wird NICHT geprüft"
    if os.environ.get(ENV_QUALITAETSTOR) == "1":
        pytest.fail(f"{meldung} (Qualitätstor verlangt {werkzeug})", pytrace=False)
    pytest.skip(meldung)
