"""Testhelfer Auswertungen: App mit jobs.db und Berichtsablage, Projekte „ost“ und „kirche“,
Konten je Rolle, API-Tokens, und ein Worker mit Test-Läufer, der statt des Prüfers eine
synthetische Ausgabe schreibt. Nur synthetische Daten."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from nutzer import person
from pakete import lege_paket

from scanview_server import apitoken, auftraege, db
from scanview_server.config import Settings
from scanview_server.web import create_app
from scanview_worker.laeufer import Ergebnis, Lauf, TestLaeufer
from scanview_worker.worker import Einstellungen, Worker

BASIS = "/api/v1/projekte/ost/auswertungen"
PRUEFEN = {"typ": "auswerten_pruefen", "optionen": {"profil": "bestandserfassung"}}
KONTEN = {"leitung": "projektleitung", "bear": "bearbeiter", "rueck": "rueckmelder",
          "betr": "betrachter"}
ERGEBNIS = {
    "katalog": {"titel": "Bestandserfassung", "datei": "bestandserfassung.toml", "sha256": "ab"},
    "gesamt": {"urteil": "nicht erfüllt",
               "zaehlung": {"erfüllt": 1, "nicht erfüllt": 1, "nicht prüfbar": 0},
               "entschieden": 2},
    "zeilen": [{"id": "L01", "urteil": "erfüllt"}, {"id": "L09", "urteil": "nicht erfüllt"}],
}


def pruefer_schreibt(lauf: Lauf, ergebnis: Any = ERGEBNIS, status: int = 1) -> Ergebnis:
    """Was `scanview-pruefen` in `--aus` hinterlässt (Exit 1 = mindestens eine Zeile nicht
    erfüllt, ein gültiger Bericht)."""
    aus = Path(lauf.argv[lauf.argv.index("--aus") + 1])
    (aus / "ergebnis.json").write_text(json.dumps(ergebnis, ensure_ascii=False))
    (aus / "bericht.html").write_text("<!doctype html><title>Bericht</title><p>synthetisch")
    (aus / "L09-karte.png").write_bytes(b"\x89PNG\r\n")
    (aus / "lauf.json").write_text("{}")
    return Ergebnis("exit" if status else "ok", status, 0.1)


@dataclass
class Welt:
    client: TestClient
    tmp: Path
    c: dict[str, TestClient] = field(default_factory=dict)
    id: dict[str, int] = field(default_factory=dict)

    @property
    def settings(self) -> Settings:
        s: Settings = self.client.app.state.settings  # type: ignore[attr-defined]
        return s

    def conn(self) -> sqlite3.Connection:
        return db.connect(self.settings.db_path)

    def jobs(self) -> sqlite3.Connection:
        assert self.settings.jobs_db is not None
        return auftraege.verbinden(self.settings.jobs_db)

    def token(self, wer: str, umfang: str = "schreiben", *, agent: bool = False) -> TestClient:
        conn = self.conn()
        try:
            roh, _ = apitoken.erzeugen(conn, self.id[wer], f"{wer}-{umfang}-{agent}", umfang,
                                       agent=agent)
        finally:
            conn.close()
        return TestClient(self.client.app, base_url=ORIGIN,
                          headers={"Authorization": f"Bearer {roh}"})

    def post(self, wer: str, pfad: str, koerper: Any = None) -> httpx.Response:
        return self.c[wer].post(pfad, json=koerper, headers=csrf())

    def delete(self, wer: str, pfad: str) -> httpx.Response:
        return self.c[wer].delete(pfad, headers=csrf())

    def starten(self, wer: str = "leitung", koerper: Any = None) -> str:
        r = self.post(wer, BASIS, PRUEFEN if koerper is None else koerper)
        assert r.status_code == 202, r.text
        aid: str = r.json()["id"]
        return aid

    def einstellungen(self, reserve: int = 0) -> Einstellungen:
        assert self.settings.berichte_dir is not None and self.settings.jobs_db is not None
        return Einstellungen(jobs_db=self.settings.jobs_db, berichte=self.settings.berichte_dir,
                             data_dir=self.settings.data_dir, import_bin=self.tmp / "import-bin",
                             reserve_bytes=reserve)

    def worker(self, aktion: Callable[[Lauf], Ergebnis] = pruefer_schreibt,
               reserve: int = 0) -> tuple[Worker, TestLaeufer]:
        laeufer = TestLaeufer(aktion)
        return Worker(self.einstellungen(reserve), laeufer), laeufer

    def rechnen(self, aktion: Callable[[Lauf], Ergebnis] = pruefer_schreibt) -> TestLaeufer:
        w, laeufer = self.worker(aktion)
        w.start()
        w.schleife(einmal=True)
        w.conn.close()
        return laeufer


def baue_welt(tmp_path: Path, *, jobs: bool = True) -> Iterator[Welt]:
    data, state = tmp_path / "data", tmp_path / "state"
    data.mkdir()
    state.mkdir()
    s = Settings(
        data_dir=data, state_dir=state, origin=ORIGIN, rp_id="scanview.test", rp_name="scanview",
        jobs_db=tmp_path / "jobs" / "jobs.db" if jobs else None,
        berichte_dir=tmp_path / "berichte" if jobs else None,
    )
    if jobs:
        (tmp_path / "jobs").mkdir()
    lege_paket(data, "ost")
    lege_paket(data, "kirche")
    with TestClient(create_app(s), base_url=ORIGIN) as client:
        w = Welt(client, tmp_path)
        for schluessel, rolle in KONTEN.items():
            n, c = person(client, f"{schluessel}@example.test", grants={"ost": rolle},
                          name=f"Name {schluessel}")
            w.c[schluessel], w.id[schluessel] = c, n.id
        n, c = person(client, "fremd@example.test", grants={"kirche": "projektleitung"})
        w.c["fremd"], w.id["fremd"] = c, n.id
        n, c = person(client, "admin@example.test", admin=True)
        w.c["admin"], w.id["admin"] = c, n.id
        w.c["anonym"] = TestClient(client.app, base_url=ORIGIN)
        yield w


@pytest.fixture
def welt(tmp_path: Path) -> Iterator[Welt]:
    yield from baue_welt(tmp_path)


@pytest.fixture
def welt_ohne_jobs(tmp_path: Path) -> Iterator[Welt]:
    yield from baue_welt(tmp_path, jobs=False)
