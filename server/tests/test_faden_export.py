"""Befund-Faden in CSV, JSON, Mängelliste (XLSX und Druck) und Git-Export."""

from __future__ import annotations

import dataclasses
import io
import json
import sqlite3
import zipfile
from collections.abc import Iterator

import pytest
from fadenwelt import BASIS, FADEN, INTERN, OEFFENTLICH, Welt, welt  # noqa: F401  (Fixture)
from test_gitexport import git

from scanview_server import db, faden, gitexport, inhalte, projektverwaltung
from scanview_server.config import Settings


def _zustaendig_und_frist(welt: Welt) -> None:  # noqa: F811 — Fixture
    z = {"gewerk_id": welt.gewerk["Dach"], "nutzer_id": welt.id["rueck"]}
    assert welt.post("leitung", FADEN, {"art": "zustaendig", "wert": z,
                                        "version": 1}).status_code == 201
    assert welt.post("leitung", FADEN, {"art": "frist", "wert": "2026-11-30",
                                        "version": 2}).status_code == 201
    assert welt.post("bear", FADEN, {"art": "status", "wert": "in_arbeit",
                                     "version": 3}).status_code == 201


def test_csv_zusatzspalten(welt: Welt) -> None:  # noqa: F811 — Fixture
    _zustaendig_und_frist(welt)
    zeilen = welt.c["bear"].get(BASIS + "/export.csv").text.removeprefix("﻿").split("\r\n")
    kopf = zeilen[0].split(";")
    assert kopf[-3:] == ["Status", "Zuständig", "Frist"]
    assert zeilen[1].split(";")[-3:] == ["in Arbeit", "DA / Name rueck", "2026-11-30"]


def test_csv_ohne_schalter_unveraendert(welt: Welt) -> None:  # noqa: F811 — Fixture
    welt.sql("UPDATE projekte SET befund_faeden=0")
    kopf = welt.c["bear"].get(BASIS + "/export.csv").text.removeprefix("﻿").split("\r\n")[0]
    assert kopf.split(";")[-1] == "Hinweis"  # letzte Basisspalte, keine Faden-Spalten


def test_json_export_stand_und_faden(welt: Welt) -> None:  # noqa: F811 — Fixture
    _zustaendig_und_frist(welt)
    poi = welt.c["bear"].get(BASIS + "/export.json").json()["pois"][0]
    assert poi["status"] == "in_arbeit" and poi["frist"] == "2026-11-30"
    assert poi["zustaendig"] == {"gewerk_id": welt.gewerk["Dach"], "nutzer_id": welt.id["rueck"]}
    arten = [e["art"] for e in poi["faden"]]
    assert arten == ["kommentar", "zustaendig", "frist", "status"]
    assert poi["faden"][0]["text"] == OEFFENTLICH
    bh = welt.c["bh"].get(BASIS + "/export.json").json()["pois"][0]
    assert bh["faden"][0]["text"] == INTERN
    welt.sql("UPDATE projekte SET befund_faeden=0")
    aus = welt.c["bear"].get(BASIS + "/export.json").json()["pois"][0]
    assert not {"status", "zustaendig", "frist", "faden"} & set(aus)


def _blatt(inhalt: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(inhalt)) as z:
        return z.read("xl/worksheets/sheet1.xml").decode()


def test_maengelliste_zusatzspalten(welt: Welt) -> None:  # noqa: F811 — Fixture
    _zustaendig_und_frist(welt)
    blatt = _blatt(welt.c["bear"].get(BASIS + "/maengelliste.xlsx").content)
    for text in ("Status", "Zuständig", "Frist", "in Arbeit", "DA / Name rueck", "2026-11-30"):
        assert f">{text}<" in blatt
    assert 'ref="A4:O5"' in blatt and 'r="O4"' in blatt
    druck = welt.c["bear"].get(BASIS + "/maengelliste").json()["pois"][0]
    assert druck["stand"]["status"] == "in_arbeit"
    welt.sql("UPDATE projekte SET befund_faeden=0")
    blatt = _blatt(welt.c["bear"].get(BASIS + "/maengelliste.xlsx").content)
    assert 'ref="A4:L5"' in blatt and ">Status<" not in blatt
    assert "stand" not in welt.c["bear"].get(BASIS + "/maengelliste").json()["pois"][0]


@pytest.fixture
def an(settings: Settings) -> Settings:
    return dataclasses.replace(settings, git_export=True)


@pytest.fixture
def conn(an: Settings) -> Iterator[sqlite3.Connection]:
    c = db.connect(an.db_path)
    db.migrate(c)
    c.execute("INSERT INTO users (id, email, name, created_at) VALUES (1, 'a@example.test',"
              " 'A', 0)")
    projektverwaltung.einrichten(c, "ost", "bauprojekt", von=1, now=0)
    yield c
    c.close()


def test_git_export_faeden_alles(an: Settings, conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "ost", "P-1", {"title": "T", "position": [0, 0, 0]},
                    nutzer_id=1, revision=1, now=1000)
    admin = faden.Sicht(nutzer=1, rolle="admin", gewerk=None, bauherrenseite=True, intern=True)
    k, _ = faden.anlegen(conn, "ost", "P-1", admin, art="kommentar", text="Intern", intern=True,
                         now=2000)
    st, _ = faden.anlegen(conn, "ost", "P-1", admin, art="status", wert="in_arbeit", version=1,
                          now=3000)
    faden.zurueckziehen(conn, "ost", "P-1", k["schluessel"], admin, now=4000)
    assert gitexport.exportieren(conn, an) == {"ost": 4}
    repo = an.git_dir / "ost"
    a, b = k["schluessel"], st["schluessel"]
    datei = json.loads((repo / "faeden" / "P-1" / f"{a}.json").read_text())
    assert datei["intern"] is True and datei["text"] is None and datei["zurueckgezogen_am"] == 4000
    assert datei["schluessel"] == a and "id" not in datei
    assert sorted(p.name for p in (repo / "faeden" / "P-1").iterdir()) == sorted(
        [f"{a}.json", f"{b}.json"])
    alt = git(repo, "show", f"HEAD~1:faeden/P-1/{a}.json")
    assert json.loads(alt)["text"] == "Intern"     # Betreiber-Export: Verlauf enthält alles
    assert "Name" not in alt and '"erstellt_von": 1' in alt
    assert git(repo, "log", "--reverse", "--format=%s").splitlines() == [
        "poi.angelegt P-1", f"eintrag.angelegt P-1/{a}", f"eintrag.angelegt P-1/{b}",
        f"eintrag.zurueckgezogen P-1/{a}"]


@pytest.mark.parametrize("objekt", ["P-1/x", "../P-1/0123456789abcdef", "P-1/1",
                                    "P-1/0123456789abcdef/1", "P-1", "P-1/0123456789ABCDEF",
                                    "P 1/0123456789abcdef"])
def test_git_export_unsichere_eintrag_id_uebersprungen(an: Settings, conn: sqlite3.Connection,
                                                       objekt: str) -> None:
    conn.execute("INSERT INTO ereignisse (zeit, kanal, project_id, art, objekt_id, nachher)"
                 " VALUES (1, 'cli', 'ost', 'eintrag.angelegt', ?, '{}')", (objekt,))
    ergebnis = gitexport.exportieren(conn, an)
    assert ergebnis["ost"] == 0 and any(k.startswith(gitexport.UEBERSPRUNGEN) for k in ergebnis)
    assert not (an.git_dir / "ost" / "faeden").exists()
