from scanview_nachweise import cli
from scanview_nachweise.cli import main
from scanview_nachweise.fehler import NachweisFehler


def test_unbekannter_befehl_exit_2(capsys):
    assert main(["gibtsnicht"]) == 2


def test_fehler_format():
    assert str(NachweisFehler("sbom-abdeckung", "3 Dateien ohne Komponente")) == \
        "NACHWEIS-FEHLER sbom-abdeckung: 3 Dateien ohne Komponente"


def test_version_befehl(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "scanview-nachweise 0.11.0"


def test_handler_fehler_exit_2(monkeypatch, capsys):
    # Interface-Vorgabe: jeder Handler, der NachweisFehler wirft, meldet sich auf
    # stderr und liefert Exit 2. Noch hat kein echter Unterbefehl diesen Pfad, darum hier über
    # `version` erzwungen (set_defaults bindet _version erst beim main()-Aufruf, der Patch wirkt).
    def _boom(_args: object) -> int:
        raise NachweisFehler("x", "y")

    monkeypatch.setattr(cli, "_version", _boom)
    assert main(["version"]) == 2
    assert capsys.readouterr().err.strip() == "NACHWEIS-FEHLER x: y"


def test_stichtag_ist_das_utc_datum(monkeypatch):
    """`--heute` fehlt: kurz nach Mitternacht Ortszeit (UTC+2) ist es in UTC noch der Vortag —
    DAST-, Scan- und Gate-Stichtag rechnen wie Release und Updates in UTC."""
    from datetime import UTC, date, datetime

    class Uhr(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return datetime(2026, 9, 30, 22, 30, tzinfo=UTC)

    monkeypatch.setattr(cli, "datetime", Uhr)
    assert cli._heute(cli.argparse.Namespace(heute=None)) == date(2026, 9, 30)
    assert cli._heute(cli.argparse.Namespace(heute="2026-10-05")) == date(2026, 10, 5)


def test_dast_freigabe_heisst_betreiber_freigabe(capsys):
    """Der Schalter nennt die Rolle, keine Person (öffentliche Doku und Hilfetext)."""
    assert main(["pruefen", "dast", "--help"]) == 0
    hilfe = capsys.readouterr().out
    assert "--betreiber-freigabe" in hilfe and "durch den Betreiber" in " ".join(hilfe.split())
