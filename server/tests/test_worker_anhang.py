"""F7: Worker bereinigt Bildanhänge isoliert (Test-Läufer statt Prozess).

Geprüft: feste Argumentliste, Budget 512 MB/60 s, Sichtbarkeit (nur die Eingabedatei lesend),
Anhänge vor anderen Aufträgen, eigene Grenze je Projekt, Ausgabe des Decoders als fremd geprüft
(nur bild.jpg + info.json, regulär, JPEG-Kopf, Maße), Ablehnungsmeldung ohne Pfade."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from scanview_server import auftraege as A
from scanview_worker import worker as W
from scanview_worker.laeufer import Ergebnis, Lauf, TestLaeufer

ANHANG = "ab" * 16


@pytest.fixture
def umgebung(tmp_path: Path) -> tuple[W.Einstellungen, sqlite3.Connection]:
    for d in ("data", "berichte", "import-bin", "anh/eingang"):
        (tmp_path / d).mkdir(parents=True)
    s = W.Einstellungen(jobs_db=tmp_path / "jobs.db", berichte=tmp_path / "berichte",
                        data_dir=tmp_path / "data", import_bin=tmp_path / "import-bin",
                        anhaenge=tmp_path / "anh")
    conn = A.verbinden(s.jobs_db)
    A.schema(conn)
    return s, conn


def _einreihen(conn: sqlite3.Connection, s: W.Einstellungen, anhang: str = ANHANG,
               inhalt: bytes = b"\xff\xd8\xff roh") -> str:
    assert s.anhaenge is not None
    (s.anhaenge / "eingang" / anhang).write_bytes(inhalt)
    return A.einreihen(conn, projekt="ost", typ="anhang_bereinigen", optionen={"anhang": anhang},
                       nutzer_id=1, kanal="web")


def _decoder(bild: bytes = b"\xff\xd8\xff neu", info: object = None,
             extra: dict[str, bytes] | None = None):
    def aktion(lauf: Lauf) -> Ergebnis:
        aus = Path(lauf.argv[-1])
        aus.mkdir(parents=True)
        (aus / "bild.jpg").write_bytes(bild)
        (aus / "info.json").write_text(json.dumps(info if info is not None
                                                  else {"breite": 60, "hoehe": 30}))
        for n, b in (extra or {}).items():
            (aus / n).write_bytes(b)
        return Ergebnis("ok", 0, 0.1)
    return aktion


def _status(conn: sqlite3.Connection, aid: str) -> sqlite3.Row:
    r: sqlite3.Row = conn.execute("SELECT * FROM auftraege WHERE id=?", (aid,)).fetchone()
    return r


def test_argumente_budget_sichtbarkeit_und_uebernahme(umgebung) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    aid = _einreihen(conn, s)
    tl = TestLaeufer(_decoder())
    w = W.Worker(s, tl)
    assert w.schritt()
    [lauf] = tl.laeufe
    assert s.anhaenge is not None
    ein = s.anhaenge / "eingang" / ANHANG
    assert list(lauf.argv) == [str(s.import_bin / "scanview-import"), "anhang-bereinigen",
                               str(ein), str(lauf.arbeit / "aus")]
    assert (lauf.grenzen.ram_mb, lauf.grenzen.zeit_s) == (512, 60)
    # TasksMax=8: Bibliotheken dürfen keine Thread-Pools je Kern starten (sonst hängt der Import).
    assert dict(lauf.umgebung) == {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                                   "MKL_NUM_THREADS": "1"}
    assert lauf.lesen == (ein,)
    assert s.anhaenge in lauf.verbergen and s.data_dir in lauf.verbergen
    r = _status(conn, aid)
    assert r["status"] == "fertig" and json.loads(r["zaehlung"]) == {"breite": 60, "hoehe": 30}
    ziel = s.anhaenge / "geprueft" / ANHANG
    assert sorted(p.name for p in ziel.iterdir()) == ["bild.jpg", "info.json"]
    assert not lauf.arbeit.exists()


def test_anhaenge_vor_anderen_auftraegen(umgebung) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    A.einreihen(conn, projekt="ost", typ="auswerten_pruefen",
                optionen={"profil": "bestandserfassung"}, nutzer_id=1, kanal="web")
    anh = _einreihen(conn, s)
    assert A.naechster(conn).id == anh  # type: ignore[union-attr]


def test_eigene_grenze_je_projekt(umgebung) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    for _ in range(A.OFFEN_JE_PROJEKT_MAX):
        A.einreihen(conn, projekt="ost", typ="auswerten_pruefen",
                    optionen={"profil": "bestandserfassung"}, nutzer_id=1, kanal="web")
    # Volle Auswertungen blockieren keine Fotos …
    for i in range(A.ANHAENGE_OFFEN_JE_PROJEKT_MAX):
        _einreihen(conn, s, anhang=f"{i:032x}")
    # … aber Fotos haben ihre eigene Grenze.
    with pytest.raises(A.ZuViele):
        _einreihen(conn, s, anhang="f" * 32)


@pytest.mark.parametrize("anhang", ["../" + "a" * 29, "A" * 32, "a" * 31])
def test_optionen_nur_anhang_id(anhang: str) -> None:
    with pytest.raises(A.AuftragUngueltig):
        A.pruefe("anhang_bereinigen", {"anhang": anhang}, erlaubt=A.ANHAENGE)
    with pytest.raises(A.AuftragUngueltig):
        A.pruefe("anhang_bereinigen", {"anhang": "a" * 32, "pfad": "/etc"}, erlaubt=A.ANHAENGE)


def test_nicht_ueber_auswertungen_oder_importe() -> None:
    for erlaubt in (A.AUSWERTUNGEN, A.IMPORTE):
        with pytest.raises(A.AuftragUngueltig):
            A.pruefe("anhang_bereinigen", {"anhang": "a" * 32}, erlaubt=erlaubt)


@pytest.mark.parametrize("decoder", [
    _decoder(bild=b"<svg/>"),
    _decoder(info={"breite": 0, "hoehe": 30}),
    _decoder(info={"breite": 60, "hoehe": 30, "gps": "52,13"}),
    _decoder(info={"breite": 60, "hoehe": 30, "aufnahme": "gestern"}),
    _decoder(extra={"bild.html": b"<html>"}),
])
def test_fremde_ausgabe_abgelehnt(umgebung, decoder) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    aid = _einreihen(conn, s)
    W.Worker(s, TestLaeufer(decoder)).schritt()
    r = _status(conn, aid)
    assert r["status"] == "fehlgeschlagen" and r["fehler"] == W.F_ANHANG_KAPUTT
    assert not (s.anhaenge / "geprueft" / ANHANG).exists()


def test_symlink_in_der_ausgabe_abgelehnt(umgebung, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    aid = _einreihen(conn, s)
    geheim = tmp_path / "geheim"
    geheim.write_bytes(b"\xff\xd8\xff geheim")

    def aktion(lauf: Lauf) -> Ergebnis:
        aus = Path(lauf.argv[-1])
        aus.mkdir(parents=True)
        os.symlink(geheim, aus / "bild.jpg")
        (aus / "info.json").write_text('{"breite": 1, "hoehe": 1}')
        return Ergebnis("ok", 0, 0.1)

    W.Worker(s, TestLaeufer(aktion)).schritt()
    assert _status(conn, aid)["status"] == "fehlgeschlagen"


def test_ablehnung_des_decoders_ohne_pfade(umgebung) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    aid = _einreihen(conn, s)

    def aktion(lauf: Lauf) -> Ergebnis:
        lauf.protokoll.write_text("Abgelehnt: Bild hat zu viele Pixel (höchstens 40 Megapixel).\n")
        return Ergebnis("exit", 2, 0.1)

    W.Worker(s, TestLaeufer(aktion)).schritt()
    r = _status(conn, aid)
    assert r["status"] == "fehlgeschlagen" and "40 Megapixel" in r["fehler"]


@pytest.mark.parametrize("ergebnis,text", [
    (Ergebnis("speicher", None, 1.0), "512 MB"),
    (Ergebnis("zeit", None, 1.0), "60 s"),
    (Ergebnis("exit", 1, 0.1), W.F_ANHANG_KAPUTT),
])
def test_grenzen(umgebung, ergebnis: Ergebnis, text: str) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    aid = _einreihen(conn, s)
    W.Worker(s, TestLaeufer(lambda _l: ergebnis)).schritt()
    r = _status(conn, aid)
    assert r["status"] == "fehlgeschlagen" and text in r["fehler"] and "/" not in r["fehler"]


def test_eingabe_symlink_oder_fehlend(umgebung, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    aid = _einreihen(conn, s)
    assert s.anhaenge is not None
    ein = s.anhaenge / "eingang" / ANHANG
    ein.unlink()
    ein.symlink_to(tmp_path / "data")
    tl = TestLaeufer(_decoder())
    W.Worker(s, tl).schritt()
    assert _status(conn, aid)["fehler"] == W.F_ANHANG_FEHLT and tl.laeufe == []


def test_ohne_anhaenge_ordner(umgebung) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    aid = _einreihen(conn, s)
    s2 = W.Einstellungen(**{**s.__dict__, "anhaenge": None})
    W.Worker(s2, TestLaeufer(_decoder())).schritt()
    assert _status(conn, aid)["fehler"] == W.F_ANHANG_AUS


def test_raeumen_nach_frist(umgebung) -> None:  # type: ignore[no-untyped-def]
    s, conn = umgebung
    _einreihen(conn, s)
    w = W.Worker(s, TestLaeufer(_decoder()))
    w.schritt()
    assert w.anhaenge_raeumen() == 0
    assert w.anhaenge_raeumen(jetzt=10**12) == 1
    assert s.anhaenge is not None and not (s.anhaenge / "geprueft" / ANHANG).exists()
