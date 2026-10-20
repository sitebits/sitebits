"""G7: Exporte und Git-Export enthalten nur Gültiges."""

from __future__ import annotations

import dataclasses
import io
import json
import sqlite3
import zipfile
from collections.abc import Iterator

import pytest
from fadenwelt import BASIS, FADEN, POI, Welt, welt  # noqa: F401  (Fixture)
from test_gitexport import git

from scanview_server import db, faden, gitexport, inhalte, projektverwaltung
from scanview_server.config import Settings

GEHEIM = "KI-Fund-unbestaetigt"


def _xlsx_text(inhalt: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(inhalt)) as z:
        return "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())


def test_exporte_ohne_vorschlaege(welt: Welt) -> None:  # noqa: F811 — Fixture
    agent = welt.token("bear", "schreiben", agent=True)
    assert agent.post(BASIS + "/pois", json={"id": "P-KI", "daten": {**POI, "title": GEHEIM}}
                      ).status_code == 201
    assert agent.post(FADEN, json={"art": "frist", "wert": "2026-12-24"}).status_code == 202
    s = agent.post(FADEN, json={"art": "frist", "wert": "2026-12-25"}).json()["eintrag"]
    assert welt.post("bh", f"{FADEN}/{s['schluessel']}/verwerfen",
                     {"text": GEHEIM + "-verworfen"}).status_code == 201
    for wer in ("bh", "leitung"):
        for pfad in ("/export.json", "/export.csv", "/maengelliste", "/befunde"):
            r = welt.c[wer].get(BASIS + pfad)
            assert r.status_code == 200, (wer, pfad)
            assert GEHEIM not in r.text and "P-KI" not in r.text, (wer, pfad)
            assert "2026-12-24" not in r.text, (wer, pfad)
        r = welt.c[wer].get(BASIS + "/maengelliste.xlsx")
        assert r.status_code == 200
        assert GEHEIM not in _xlsx_text(r.content)
    poi = welt.c["bh"].get(BASIS + "/export.json").json()["pois"][0]
    assert all(e["art"] != "vorschlag" and e.get("bezug") is None for e in poi["faden"])


@pytest.fixture
def an(settings: Settings) -> Settings:
    return dataclasses.replace(settings, git_export=True)


@pytest.fixture
def conn(an: Settings) -> Iterator[sqlite3.Connection]:
    c = db.connect(an.db_path)
    db.migrate(c)
    for uid in (1, 2):
        c.execute("INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,0)",
                  (uid, f"u{uid}@example.test", f"U{uid}"))
    projektverwaltung.einrichten(c, "ost", "bauprojekt", von=1, now=0)
    yield c
    c.close()


def test_gitexport_erst_nach_annehmen(an: Settings, conn: sqlite3.Connection) -> None:
    roh = inhalte.pruefen("pois", {"title": "KI", "position": [0, 0, 0]})
    e = inhalte.anlegen(conn, "pois", "ost", "P-KI", roh, nutzer_id=1, revision=1, ki=True,
                        kanal="api", now=1000)
    e = inhalte.aendern(conn, "pois", "ost", "P-KI", {"title": "geprüft"}, e.version,
                        nutzer_id=2, now=1100)
    gitexport.exportieren(conn, an)
    repo = an.git_dir / "ost"
    assert not (repo / "pois" / "P-KI.json").exists()
    inhalte.annehmen(conn, "pois", "ost", "P-KI", e.version, nutzer_id=2, kanal="web",
                     token_name=None, now=1200)
    gitexport.exportieren(conn, an)
    assert json.loads((repo / "pois" / "P-KI.json").read_text())["title"] == "geprüft"
    log = git(repo, "log", "--format=%s")
    assert "poi.angenommen P-KI" in log and "vorgeschlagen" not in log
    assert "vorschlag_geaendert" not in log


def test_gitexport_verworfen_und_faden_vorschlag_nie(an: Settings,
                                                    conn: sqlite3.Connection) -> None:
    roh = inhalte.pruefen("pois", {"title": "Echt", "position": [0, 0, 0]})
    inhalte.anlegen(conn, "pois", "ost", "P-1", roh, nutzer_id=1, revision=1, now=1000)
    v = inhalte.anlegen(conn, "pois", "ost", "P-KI", roh, nutzer_id=1, revision=1, ki=True,
                        kanal="api", now=1001)
    inhalte.verwerfen(conn, "pois", "ost", "P-KI", v.version, nutzer_id=2, kanal="web",
                      token_name=None, now=1002)
    pruefer = faden.Sicht(nutzer=2, rolle="admin", gewerk=None, bauherrenseite=True,
                          intern=True)
    a, _ = faden.anlegen(conn, "ost", "P-1", pruefer, art="frist", wert="2026-11-01", ki=True,
                         kanal="api", now=1003)
    b, st = faden.anlegen(conn, "ost", "P-1", pruefer, art="frist", wert="2026-11-02",
                          ki=True, kanal="api", now=1004)
    faden.vorschlag_verwerfen(conn, "ost", "P-1", a["schluessel"], pruefer, kanal="web",
                              token_name=None, now=1005)
    neu, _ = faden.vorschlag_annehmen(conn, "ost", "P-1", b["schluessel"], pruefer,
                                      version=st["version"], kanal="web", token_name=None,
                                      now=1006)
    gitexport.exportieren(conn, an)
    repo = an.git_dir / "ost"
    assert not (repo / "pois" / "P-KI.json").exists()
    dateien = sorted(p.stem for p in (repo / "faeden" / "P-1").iterdir())
    assert dateien == [neu["schluessel"]]
    log = git(repo, "log", "--format=%s")
    assert "verworfen" not in log and "vorgeschlagen" not in log
