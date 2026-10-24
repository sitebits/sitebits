"""Testhelfer Uploads und Importe: App mit eingeschaltetem Import, kleinem Upload-Ordner, Konten
(Admin, Projektleitung, Bearbeiter) und Tokens; Hochladen in Stücken wie der Browser. Nur
synthetische Daten."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from nutzer import person
from pakete import lege_paket

from scanview_server import apitoken, auftraege, db
from scanview_server import uploads as U
from scanview_server.config import Settings
from scanview_server.web import create_app
from scanview_worker.laeufer import Ergebnis, Lauf, TestLaeufer
from scanview_worker.worker import Einstellungen, Worker

STUECK = 1024
# Kleinste gültige Kopfbytes je Art.
GLB = b"glTF" + (2).to_bytes(4, "little") + (0).to_bytes(4, "little") + b"\0" * 2000
SPZ = b"\x1f\x8b\x08" + bytes(range(256)) * 10
PLY = b"ply\nformat binary_little_endian 1.0\n" + b"\1" * 3000


def digest(stueck: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(stueck).digest()).decode() + ":"


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

    @property
    def wurzel(self) -> Path:
        assert self.settings.uploads_dir is not None
        return self.settings.uploads_dir

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

    def anlegen(self, c: TestClient, dateien: dict[str, bytes] | list[dict[str, Any]],
                ) -> httpx.Response:
        liste = (dateien if isinstance(dateien, list)
                 else [{"pfad": p, "groesse": len(b)} for p, b in dateien.items()])
        return c.post("/api/v1/uploads", json={"dateien": liste}, headers=csrf())

    def stueck(self, c: TestClient, uid: str, nr: int, daten: bytes, a: int, *,
               gesamt: int | None = None, pruef: str | None = None) -> httpx.Response:
        teil = daten[a:a + STUECK]
        n = len(daten) if gesamt is None else gesamt
        return c.put(f"/api/v1/uploads/{uid}/dateien/{nr}", content=teil, headers={
            **csrf(), "Content-Range": f"bytes {a}-{a + len(teil) - 1}/{n}",
            "Content-Digest": pruef if pruef is not None else digest(teil)})

    def senden(self, c: TestClient, uid: str, nr: int, daten: bytes, *, ab: int = 0,
               bis: int | None = None) -> int:
        """Stücke ab `ab` senden, bis `bis` Bytes erreicht sind (Abbruch simulieren)."""
        a = ab
        ende = len(daten) if bis is None else bis
        while a < ende:
            r = self.stueck(c, uid, nr, daten, a)
            assert r.status_code == 200, r.text
            a = r.json()["stand"]
        return a

    def hochladen(self, c: TestClient, dateien: dict[str, bytes]) -> dict[str, Any]:
        r = self.anlegen(c, dateien)
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        for nr, daten in enumerate(dateien.values()):
            self.senden(c, uid, nr, daten)
        r = c.post(f"/api/v1/uploads/{uid}/abschliessen", json={}, headers=csrf())
        assert r.status_code == 200, r.text
        ergebnis: dict[str, Any] = r.json()
        return ergebnis


    def einstellungen(self) -> Einstellungen:
        s = self.settings
        assert s.jobs_db is not None and s.berichte_dir is not None
        return Einstellungen(jobs_db=s.jobs_db, berichte=s.berichte_dir, data_dir=s.data_dir,
                             import_bin=self.tmp / "import-bin", reserve_bytes=0,
                             uploads=self.wurzel, uebergabe=self.tmp / "uebergabe",
                             meldungen=self.tmp / "meldungen")

    def worker(self, aktion: Any, laeufer: TestLaeufer | None = None,
               ) -> tuple[Worker, TestLaeufer]:
        t = laeufer if laeufer is not None else TestLaeufer(aktion)
        return Worker(self.einstellungen(), t), t

    def import_starten(self, c: TestClient, dateien: dict[str, bytes], typ: str,
                       **optionen: str) -> str:
        u = self.hochladen(c, dateien)
        r = c.post("/api/v1/importe", json={"upload": u["id"], "typ": typ,
                                              "optionen": optionen}, headers=csrf())
        assert r.status_code == 202, r.text
        aid: str = r.json()["id"]
        return aid


def ziel_von(lauf: Lauf) -> Path:
    return Path(lauf.argv[3])


def baut_paket(lauf: Lauf) -> Ergebnis:
    """Was `neu-ohne-wolke` hinterlässt (bzw. `verify` mit Exit 0)."""
    if lauf.argv[1] == "neu-ohne-wolke":
        ziel = ziel_von(lauf)
        (ziel / "layers" / "splats").mkdir(parents=True)
        (ziel / "layers" / "splats" / "model.spz").write_bytes(b"\x1f\x8b\x08synthetisch")
        (ziel / "project.json").write_text(
            json.dumps({"schemaVersion": 2, "projectId": ziel.name}))
    return Ergebnis("ok", 0, 0.1)


def baue_welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, an: bool = True,
              quote: int = 1024 * 1024) -> Iterator[Welt]:
    monkeypatch.setattr(U, "STUECK", STUECK)
    data, state, jobs, up = (tmp_path / n for n in ("data", "state", "jobs", "uploads"))
    for d in (data, state, jobs, up, tmp_path / "uebergabe", tmp_path / "meldungen"):
        d.mkdir()
    s = Settings(
        data_dir=data, state_dir=state, origin=ORIGIN, rp_id="scanview.test", rp_name="scanview",
        jobs_db=jobs / "jobs.db", berichte_dir=tmp_path / "berichte",
    )
    if an:
        s = replace(s, import_enabled=True, uploads_dir=up, upload_quote_bytes=quote,
                    upload_reserve_bytes=0)
    lege_paket(data, "ost")
    with TestClient(create_app(s), base_url=ORIGIN) as client:
        w = Welt(client, tmp_path)
        for schluessel, admin, grants in (
                ("admin", True, {}), ("leitung", False, {"ost": "projektleitung"}),
                ("bear", False, {"ost": "bearbeiter"})):
            n, c = person(client, f"{schluessel}@example.test", admin=admin, grants=grants)
            w.c[schluessel], w.id[schluessel] = c, n.id
        w.c["anonym"] = TestClient(client.app, base_url=ORIGIN)
        yield w


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Welt]:
    yield from baue_welt(tmp_path, monkeypatch)


@pytest.fixture
def welt_aus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Welt]:
    yield from baue_welt(tmp_path, monkeypatch, an=False)
