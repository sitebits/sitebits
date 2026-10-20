import logging
import signal
from pathlib import Path
from typing import Any

import pytest
from pakete import lege_paket

from scanview_server import cli


def test_serve_registriert_sighup_der_katalog_neu_laedt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGHUP muss `app.state.katalog.laden()` auslösen (systemd ExecReload).

    Ruft den registrierten Handler direkt auf statt ein echtes Signal an den Testprozess zu
    schicken (fragil/plattformabhängig) — `uvicorn.run` wird dafür durch eine No-Op-Funktion
    ersetzt, die nur die App einfängt, statt wirklich einen Server zu starten.
    """
    (tmp_path / "d").mkdir()
    (tmp_path / "s").mkdir()
    monkeypatch.setenv("SCANVIEW_DATA", str(tmp_path / "d"))
    monkeypatch.setenv("SCANVIEW_STATE", str(tmp_path / "s"))
    monkeypatch.setenv("SCANVIEW_ORIGIN", "https://x.test/")
    monkeypatch.setenv("SCANVIEW_RP_ID", "x.test")

    apps: list[Any] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: apps.append(app))

    alter_handler = signal.getsignal(signal.SIGHUP)
    try:
        assert cli.main(["serve"]) == 0
        app = apps[0]
        assert app.state.katalog.get("kirche") is None
        lege_paket(tmp_path / "d", "kirche")
        handler = signal.getsignal(signal.SIGHUP)
        handler(signal.SIGHUP, None)
        assert app.state.katalog.get("kirche") is not None
    finally:
        signal.signal(signal.SIGHUP, alter_handler)


def test_sighup_ladefehler_wird_geloggt_alter_katalog_bleibt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Der SIGHUP-Handler darf nicht aus dem Signal-Handler herausplatzen, wenn
    `Katalog.laden()` scheitert (z. B. ein Mount-Hänger unter der Datenwurzel) — Fehler loggen,
    alten Katalog unverändert lassen."""
    (tmp_path / "d").mkdir()
    (tmp_path / "s").mkdir()
    lege_paket(tmp_path / "d", "kirche")  # schon beim Start vorhanden
    monkeypatch.setenv("SCANVIEW_DATA", str(tmp_path / "d"))
    monkeypatch.setenv("SCANVIEW_STATE", str(tmp_path / "s"))
    monkeypatch.setenv("SCANVIEW_ORIGIN", "https://x.test/")
    monkeypatch.setenv("SCANVIEW_RP_ID", "x.test")

    apps: list[Any] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: apps.append(app))

    alter_handler = signal.getsignal(signal.SIGHUP)
    try:
        assert cli.main(["serve"]) == 0
        app = apps[0]
        assert app.state.katalog.get("kirche") is not None  # beim Start erfolgreich geladen

        def kaputt(self: cli.Katalog) -> None:
            raise OSError("Datenwurzel nicht lesbar")

        # Erst NACH main() kaputt machen — create_app() ruft laden() selbst schon beim Start auf.
        monkeypatch.setattr(cli.Katalog, "laden", kaputt)
        caplog.set_level(logging.ERROR, "scanview.cli")
        handler = signal.getsignal(signal.SIGHUP)
        handler(signal.SIGHUP, None)  # darf nicht werfen

        assert app.state.katalog.get("kirche") is not None  # alter Stand unverändert
        fehlerzeilen = [r for r in caplog.records if r.name == "scanview.cli"]
        assert len(fehlerzeilen) == 1
        assert fehlerzeilen[0].levelno == logging.ERROR
    finally:
        signal.signal(signal.SIGHUP, alter_handler)


def test_einladen_druckt_link_mit_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "s").mkdir()
    monkeypatch.setenv("SCANVIEW_DATA", str(tmp_path / "d"))
    monkeypatch.setenv("SCANVIEW_STATE", str(tmp_path / "s"))
    monkeypatch.setenv("SCANVIEW_ORIGIN", "https://x.test/")
    monkeypatch.setenv("SCANVIEW_RP_ID", "x.test")
    assert cli.main(
        ["einladen", "--name", "Tina", "--email", "t@b.de", "--projekt", "kirche=betrachter"],
    ) == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert out.startswith("https://x.test/einladung#t=")
    assert cli.main(["nutzer"]) == 0


def _umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "d").mkdir(exist_ok=True)
    (tmp_path / "s").mkdir(exist_ok=True)
    monkeypatch.setenv("SCANVIEW_DATA", str(tmp_path / "d"))
    monkeypatch.setenv("SCANVIEW_STATE", str(tmp_path / "s"))
    monkeypatch.setenv("SCANVIEW_ORIGIN", "https://x.test/")
    monkeypatch.setenv("SCANVIEW_RP_ID", "x.test")


def test_einladen_lehnt_ungueltige_projekt_id_ab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _umgebung(tmp_path, monkeypatch)
    for pid in ("../x", "Kirche", "-x", "a" * 65, "a b"):
        assert cli.main(
            ["einladen", "--name", "T", "--email", "t@b.de", f"--projekt={pid}=betrachter"],
        ) == 2
        assert "ungültig" in capsys.readouterr().err


def test_zugaenge_verwalten_per_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _umgebung(tmp_path, monkeypatch)
    from scanview_server import db, konten

    assert cli.main(["einladen", "--name", "T", "--email", "t@b.de",
                     "--projekt", "p1=betrachter", "--projekt", "p2=betrachter"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1].split("#t=", 1)[1]
    # Offene Einladung ohne Konto widerrufen (Hauptfall).
    assert cli.main(["widerrufen", "--email", "t@b.de"]) == 0
    assert "1 offene Einladung(en)" in capsys.readouterr().out
    conn = db.connect(tmp_path / "s" / "scanview.db")
    assert konten.einladung_pruefen(conn, token) is None

    assert cli.main(["einladen", "--name", "T", "--email", "t@b.de",
                     "--projekt", "p1=betrachter", "--projekt", "p2=betrachter"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1].split("#t=", 1)[1]
    n = konten.einladung_einloesen(conn, token, lambda _c, _u: None)
    sitzung = konten.sitzung_anlegen(conn, n.id)

    assert cli.main(["entziehen", "--email", "t@b.de", "--projekt", "p1"]) == 0
    assert konten.rolle(conn, n, "p1") is None and konten.rolle(conn, n, "p2") == "betrachter"
    assert cli.main(["entziehen", "--email", "t@b.de", "--alle"]) == 0
    assert konten.rolle(conn, n, "p2") is None
    assert cli.main(["entziehen", "--email", "t@b.de", "--projekt", "../p1"]) == 2

    assert cli.main(["sperren", "--email", "T@B.de"]) == 0
    assert konten.sitzung_nutzer(conn, sitzung) is None
    assert cli.main(["entsperren", "--email", "t@b.de"]) == 0
    sitzung = konten.sitzung_anlegen(conn, n.id)
    assert konten.sitzung_nutzer(conn, sitzung) is not None
    assert cli.main(["abmelden-ueberall", "--email", "t@b.de"]) == 0
    assert konten.sitzung_nutzer(conn, sitzung) is None
    conn.close()
    capsys.readouterr()

    for cmd in ("widerrufen", "sperren", "entsperren", "abmelden-ueberall"):
        assert cli.main([cmd, "--email", "niemand@b.de"]) == 2
        assert "niemand@b.de" in capsys.readouterr().err
    assert cli.main(["entziehen", "--email", "niemand@b.de", "--alle"]) == 2
    with pytest.raises(SystemExit):  # --projekt und --alle schließen sich aus
        cli.main(["entziehen", "--email", "t@b.de", "--alle", "--projekt", "p1"])


def test_cli_protokolliert_und_nutzer_zeigt_sperrstatus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _umgebung(tmp_path, monkeypatch)
    from scanview_server import db, konten, protokoll

    assert cli.main(["einladen", "--name", "T", "--email", "t@b.de",
                     "--projekt", "p1=betrachter"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1].split("#t=", 1)[1]
    conn = db.connect(tmp_path / "s" / "scanview.db")
    konten.einladung_einloesen(conn, token, lambda _c, _u: None)
    assert cli.main(["sperren", "--email", "t@b.de"]) == 0
    assert cli.main(["entziehen", "--email", "t@b.de", "--alle"]) == 0
    capsys.readouterr()
    assert cli.main(["nutzer"]) == 0
    zeile = capsys.readouterr().out.strip().splitlines()[-1]
    assert zeile.split(" | ")[3] == "gesperrt"
    assert cli.main(["entsperren", "--email", "t@b.de"]) == 0
    capsys.readouterr()
    assert cli.main(["nutzer"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].split(" | ")[3] == "aktiv"
    cli_arten = [e.art for e in protokoll.lesen(conn) if e.kanal == "cli"]
    assert cli_arten == ["konto.entsperrt", "recht.entzogen", "konto.gesperrt",
                         "einladung.angelegt"]
    conn.close()


def test_anonymisieren_per_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _umgebung(tmp_path, monkeypatch)
    from scanview_server import db, konten, protokoll

    assert cli.main(["einladen", "--name", "Anna Muster", "--email", "anna@b.de",
                     "--projekt", "p1=bearbeiter"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1].split("#t=", 1)[1]
    conn = db.connect(tmp_path / "s" / "scanview.db")
    n = konten.einladung_einloesen(conn, token, lambda _c, _u: None)
    assert cli.main(["anonymisieren", "--email", "Anna@B.de"]) == 0
    out = capsys.readouterr().out
    assert f"Konto {n.id} anonymisiert" in out and "anna@b.de" not in out.lower()
    assert konten.konto_id(conn, "anna@b.de") is None
    assert cli.main(["nutzer"]) == 0
    zeile = capsys.readouterr().out.strip().splitlines()[-1]
    assert f"Gelöschtes Konto {n.id}" in zeile and "gesperrt" in zeile
    assert next(e.art for e in protokoll.lesen(conn) if e.kanal == "cli") == "konto.anonymisiert"
    conn.close()
    assert cli.main(["anonymisieren", "--email", "anna@b.de"]) == 2
    assert "anna@b.de" in capsys.readouterr().err


def test_cli_anonymisiertes_konto_endgueltig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _umgebung(tmp_path, monkeypatch)
    from scanview_server import db, konten

    assert cli.main(["einladen", "--name", "T", "--email", "t@b.de"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1].split("#t=", 1)[1]
    conn = db.connect(tmp_path / "s" / "scanview.db")
    n = konten.einladung_einloesen(conn, token, lambda _c, _u: None)
    conn.close()
    assert cli.main(["anonymisieren", "--email", "t@b.de"]) == 0
    capsys.readouterr()
    ersatz = f"geloescht-{n.id}@invalid"
    for argv in (["entsperren", "--email", ersatz], ["anonymisieren", "--email", ersatz],
                 ["einladen", "--name", "X", "--email", ersatz, "--zuruecksetzen"]):
        assert cli.main(argv) == 2, argv
        assert "Konto wurde anonymisiert" in capsys.readouterr().err


def test_einladen_projektleitung_und_unbekannte_rolle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _umgebung(tmp_path, monkeypatch)
    assert cli.main(["einladen", "--name", "L", "--email", "l@x.de",
                     "--projekt", "ost=projektleitung"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith(
        "https://x.test/einladung#t=")
    assert cli.main(["einladen", "--name", "L", "--email", "l@x.de",
                     "--projekt", "ost=chef"]) == 2
    assert "betrachter, rueckmelder, bearbeiter, projektleitung" in capsys.readouterr().err
