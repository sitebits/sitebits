"""SAST (Semgrep mit vendiertem Regelsatz, Bandit): Schwere-Abbildung, Fingerabdruck, Gate gegen
Befundregister und Ausnahmen, Abdeckung, Regelsatz-Lock und die gehärteten Container-Läufe."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import sast
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.register import Befund, lade_befunde
from scanview_nachweise.werkzeuge import Werkzeug, gefilterte_umgebung, git_befehl

WURZEL = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "sast"
ECHTE_REGELN = WURZEL / "nachweise" / sast.REGELN
#: Der vendierte Regelsatz darf nicht weiterverbreitet werden und fehlt im öffentlichen Export.
_REGELN_DA = pytest.mark.skipif(not ECHTE_REGELN.is_dir(),
                                reason="vendierter Semgrep-Regelsatz liegt nur im privaten Repo "
                                       "(Lizenz erlaubt keine Weiterverbreitung)")

MODUL = "import subprocess\n\n    subprocess.run(befehl, check=True)\n"
SEITE = 'const text = "a" + wert;\nwindow.location.href = ziel;\n'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(git_befehl("-c", "user.name=t", "-c", "user.email=t@t.invalid",
                              "-c", "commit.gpgsign=false", *args, repo=repo),
                   check=True, capture_output=True, env=gefilterte_umgebung())


def _worktree(tmp_path: Path, modul: str = MODUL, seite: str = SEITE) -> Path:
    wt = tmp_path / "wt"
    for ziel in sast.ZIELE:
        (wt / ziel).mkdir(parents=True)
    (wt / "server/src/paket").mkdir()
    (wt / "server/src/paket/modul.py").write_text(modul, encoding="utf-8")
    (wt / "web/src/seite.ts").write_text(seite, encoding="utf-8")
    _git(wt, "init", "-q")
    _git(wt, "add", ".")
    return wt


def _semgrep_daten() -> dict[str, Any]:
    return json.loads((FIXTURES / "semgrep-ausgabe.json").read_text(encoding="utf-8"))


def _bandit_daten() -> dict[str, Any]:
    return json.loads((FIXTURES / "bandit-ausgabe.json").read_text(encoding="utf-8"))


def _befund(fingerabdruck: str | None, **abweichung: Any) -> Befund:
    felder: dict[str, Any] = dict(
        id="B-SAST-SEMGREP-" + (fingerabdruck or "0" * 16).upper(), quelle="SAST",
        titel="Test", schwere="mittel", kategorie="sonstiges", status="offen",
        staende=["abc1234"], fix_commit=None, retest=None, akzeptanz_begruendung=None,
        frist=None, entdeckt=date(2026, 9, 29), fingerabdruck=fingerabdruck)
    felder.update(abweichung)
    return Befund(**felder)


def _fund(schwere: str, *, regel: str = "B404", pfad: str = "server/src/a.py",
          werkzeug: str = "bandit", fingerabdruck: str = "a" * 16) -> sast.Fund:
    return sast.Fund(werkzeug=werkzeug, regel=regel, pfad=pfad, zeile=1,  # type: ignore[arg-type]
                     schwere=schwere, fingerabdruck=fingerabdruck,  # type: ignore[arg-type]
                     text="x")


# --- Schwere ------------------------------------------------------------------------------------

@pytest.mark.parametrize(("roh", "soll"), [("ERROR", "hoch"), ("WARNING", "mittel"),
                                           ("INFO", "niedrig"), ("CRITICAL", "hoch"),
                                           ("", "hoch")])
def test_semgrep_schwere(roh, soll):
    assert sast.semgrep_schwere(roh) == soll


@pytest.mark.parametrize(("schwere", "konfidenz", "soll"), [
    ("HIGH", "HIGH", "hoch"), ("MEDIUM", "HIGH", "mittel"), ("LOW", "MEDIUM", "niedrig"),
    ("HIGH", "LOW", "mittel"), ("MEDIUM", "LOW", "niedrig"), ("LOW", "LOW", "info"),
    ("UNDEFINED", "HIGH", "hoch"), ("MEDIUM", "UNDEFINED", "mittel")])
def test_bandit_schwere(schwere, konfidenz, soll):
    assert sast.bandit_schwere(schwere, konfidenz) == soll


# --- Auswertung und Fingerabdruck ---------------------------------------------------------------

def test_semgrep_auswertung(tmp_path):
    wt = _worktree(tmp_path)
    funde, gescannt = sast.werte_semgrep_aus(_semgrep_daten(), wt)
    assert [(f.regel, f.pfad, f.zeile, f.schwere) for f in funde] == [
        ("python.lang.security.audit.dangerous-subprocess-use-audit",
         "server/src/paket/modul.py", 3, "hoch"),
        ("javascript.browser.security.js-open-redirect", "web/src/seite.ts", 2, "mittel"),
        ("javascript.lang.security.audit.unsafe-formatstring", "web/src/seite.ts", 1,
         "niedrig")]
    assert all(f.werkzeug == "semgrep" and len(f.fingerabdruck) == 16 for f in funde)
    assert "server/src/paket/modul.py" in gescannt and "web/src/seite.ts" in gescannt


def test_bandit_auswertung(tmp_path):
    wt = _worktree(tmp_path)
    funde, gescannt = sast.werte_bandit_aus(_bandit_daten(), wt)
    assert [(f.regel, f.pfad, f.zeile, f.schwere) for f in funde] == [
        ("B404", "server/src/paket/modul.py", 1, "niedrig"),
        ("B310", "server/src/paket/modul.py", 3, "mittel")]
    assert gescannt == {"server/src/paket/modul.py"}


def test_fingerabdruck_formel(tmp_path):
    wt = _worktree(tmp_path)
    fund = sast.werte_bandit_aus(_bandit_daten(), wt)[0][1]
    erwartet = hashlib.sha256(
        b"bandit|B310|server/src/paket/modul.py|subprocess.run(befehl, check=True)"
    ).hexdigest()[:16]
    assert fund.fingerabdruck == erwartet


def test_fingerabdruck_stabil_bei_verschobener_zeile(tmp_path):
    vorher = sast.werte_semgrep_aus(_semgrep_daten(), _worktree(tmp_path / "a"))[0][0]
    daten = _semgrep_daten()
    daten["results"][0]["start"]["line"] = 5
    daten["results"][0]["end"]["line"] = 5
    verschoben = sast.werte_semgrep_aus(daten, _worktree(tmp_path / "b", modul="\n\n" + MODUL))
    assert verschoben[0][0].zeile == 5
    assert verschoben[0][0].fingerabdruck == vorher.fingerabdruck
    # Nur Einrückung/Leerraum geändert: gleich (normalisiert).
    anders_eingerueckt = sast.werte_semgrep_aus(_semgrep_daten(), _worktree(
        tmp_path / "c", modul=MODUL.replace("    subprocess.run(befehl, check=True)",
                                            "\tsubprocess.run(befehl,  check=True)  ")))
    assert anders_eingerueckt[0][0].fingerabdruck == vorher.fingerabdruck


def test_fingerabdruck_anders_bei_geaenderter_zeile(tmp_path):
    vorher = sast.werte_semgrep_aus(_semgrep_daten(), _worktree(tmp_path / "a"))[0][0]
    geaendert = sast.werte_semgrep_aus(_semgrep_daten(), _worktree(
        tmp_path / "b", modul=MODUL.replace("befehl", "anderer_befehl")))[0][0]
    assert geaendert.fingerabdruck != vorher.fingerabdruck


MEHRZEILIG = "x = 1\nrows = conn.execute(\n    \"SELECT * FROM t WHERE a=?\", (a,),\n).fetchall()\n"


def _mehrzeilig(tmp_path: Path, modul: str) -> sast.Fund:
    daten = _semgrep_daten()
    daten["results"][0]["start"]["line"] = 2
    daten["results"][0]["end"]["line"] = 4
    return sast.werte_semgrep_aus(daten, _worktree(tmp_path, modul=modul))[0][0]


def test_fingerabdruck_ueber_den_ganzen_fundbereich(tmp_path):
    """Die gemeldete Spanne zählt, nicht nur ihre erste Zeile: eine Änderung allein an einer
    Folgezeile (hier der SQL-Text) ergibt einen neuen Fingerabdruck."""
    vorher = _mehrzeilig(tmp_path / "a", MEHRZEILIG)
    geaendert = _mehrzeilig(tmp_path / "b", MEHRZEILIG.replace("a=?", "a=" + "'x'"))
    assert geaendert.fingerabdruck != vorher.fingerabdruck
    verschoben = _mehrzeilig(tmp_path / "c", MEHRZEILIG)
    assert verschoben.fingerabdruck == vorher.fingerabdruck


def test_bandit_fingerabdruck_ueber_line_range(tmp_path):
    daten = _bandit_daten()
    daten["results"][1]["line_number"] = 2
    daten["results"][1]["line_range"] = [2, 3, 4]
    a = sast.werte_bandit_aus(daten, _worktree(tmp_path / "a", modul=MEHRZEILIG))[0][1]
    b = sast.werte_bandit_aus(daten, _worktree(
        tmp_path / "b", modul=MEHRZEILIG.replace("a=?", "b=?")))[0][1]
    assert a.fingerabdruck != b.fingerabdruck and a.zeile == 2


def test_zeilen_nur_an_zeilenumbruch_getrennt(tmp_path):
    # Seitenvorschub/U+2028 in einer Zeile verschieben die Zeilennummern der Werkzeuge nicht.
    modul = "a = '\f'\nb = 1\n    subprocess.run(befehl, check=True)\n"
    fund = sast.werte_semgrep_aus(_semgrep_daten(), _worktree(tmp_path, modul=modul))[0][0]
    assert fund.fingerabdruck == sast.fingerabdruck(
        "semgrep", fund.regel, fund.pfad, "    subprocess.run(befehl, check=True)")


def test_fundzeile_ausserhalb_der_datei(tmp_path):
    daten = _semgrep_daten()
    daten["results"][0]["start"]["line"] = 99
    daten["results"][0]["end"]["line"] = 99
    with pytest.raises(NachweisFehler) as e:
        sast.werte_semgrep_aus(daten, _worktree(tmp_path))
    assert e.value.regel == "sast-ausgabe"


def test_umgekehrter_bereich_ist_fehler(tmp_path):
    daten = _semgrep_daten()
    daten["results"][0]["end"]["line"] = 1
    with pytest.raises(NachweisFehler) as e:
        sast.werte_semgrep_aus(daten, _worktree(tmp_path))
    assert e.value.regel == "sast-ausgabe"


def test_pfad_ausserhalb_des_worktrees(tmp_path):
    daten = _semgrep_daten()
    daten["results"][0]["path"] = "/etc/passwd"
    with pytest.raises(NachweisFehler) as e:
        sast.werte_semgrep_aus(daten, _worktree(tmp_path))
    assert e.value.regel == "sast-ausgabe"


def test_semgrep_fehler_bricht_ab(tmp_path):
    daten = _semgrep_daten()
    daten["errors"] = [{"type": "Timeout", "message": "rule timed out"}]
    with pytest.raises(NachweisFehler) as e:
        sast.werte_semgrep_aus(daten, _worktree(tmp_path))
    assert e.value.regel == "sast-werkzeug" and "Timeout" in e.value.text


def test_bandit_fehler_bricht_ab(tmp_path):
    daten = _bandit_daten()
    daten["errors"] = [{"filename": "/quelle/server/src/x.py", "reason": "syntax error"}]
    with pytest.raises(NachweisFehler) as e:
        sast.werte_bandit_aus(daten, _worktree(tmp_path))
    assert e.value.regel == "sast-werkzeug"


# --- Abdeckung ----------------------------------------------------------------------------------

def test_quelldateien_aus_git(tmp_path):
    wt = _worktree(tmp_path)
    (wt / "server/src/ungetrackt.py").write_text("x = 1\n", encoding="utf-8")
    assert sast.quelldateien(wt, sast.ZIELE, (".py",)) == {"server/src/paket/modul.py"}
    assert sast.quelldateien(wt, sast.ZIELE, (".py", ".ts")) == {
        "server/src/paket/modul.py", "web/src/seite.ts"}


def test_ziele_umfassen_host_skripte():
    # release_pruefen.py u. a. laufen auf den Hosts — gehören zum gescannten Umfang.
    assert "deploy/platform" in sast.ZIELE


def test_quelldateien_ohne_tests_unter_einem_ziel(tmp_path):
    wt = _worktree(tmp_path)
    (wt / "deploy/platform/test").mkdir(parents=True)
    (wt / "deploy/platform/release_pruefen.py").write_text("x = 1\n", encoding="utf-8")
    (wt / "deploy/platform/test/probe.py").write_text("x = 1\n", encoding="utf-8")
    (wt / "server/src/tests").mkdir()
    (wt / "server/src/tests/test_x.py").write_text("x = 1\n", encoding="utf-8")
    _git(wt, "add", ".")
    dateien = sast.quelldateien(wt, sast.ZIELE, (".py",))
    assert "deploy/platform/release_pruefen.py" in dateien
    assert not {"deploy/platform/test/probe.py", "server/src/tests/test_x.py"} & dateien
    # Ein Testordner als Ziel selbst (Fixtures der Negativkontrolle) bleibt vollständig.
    assert sast.quelldateien(wt, ("deploy/platform/test",), (".py",)) == {
        "deploy/platform/test/probe.py"}


def test_abdeckung_null_dateien_ist_fehler():
    with pytest.raises(NachweisFehler) as e:
        sast.pruefe_abdeckung("semgrep", set(), {"server/src/a.py"})
    assert e.value.regel == "sast-abdeckung" and "server/src/a.py" in e.value.text


def test_abdeckung_verlangt_jede_datei_nicht_nur_die_anzahl():
    # Genug gescannte Dateien, aber eine Quelldatei fehlt → Fehler (Anzahl allein reicht nicht).
    with pytest.raises(NachweisFehler, match=r"server/src/b\.py"):
        sast.pruefe_abdeckung("bandit", {"server/src/a.py", "x.pyc", "y.pyc"},
                              {"server/src/a.py", "server/src/b.py"})
    assert sast.pruefe_abdeckung("bandit", {"server/src/a.py", "x.pyc"},
                                 {"server/src/a.py"}) == 1


def test_abdeckung_ohne_quelldateien_ist_fehler():
    with pytest.raises(NachweisFehler) as e:
        sast.pruefe_abdeckung("semgrep", {"a.py"}, set())
    assert e.value.regel == "sast-abdeckung"


# --- Regelsatz ------------------------------------------------------------------------------------

def _regelsatz(tmp_path: Path) -> Path:
    nachweise = tmp_path / "nachweise"
    regeln = nachweise / sast.REGELN
    (regeln / "python").mkdir(parents=True)
    (regeln / "LICENSE").write_text("Lizenz\n", encoding="utf-8")
    (regeln / "python" / "a.yaml").write_text("rules: []\n", encoding="utf-8")
    sast.schreibe_regellock(nachweise, sast.Regelquelle(
        url="https://example.invalid/regeln", commit="0" * 40, lizenz="Testlizenz",
        lizenz_url="https://example.invalid/lizenz", weiterverbreitung=False,
        datum=date(2026, 9, 29), auswahl="Test"))
    return nachweise


def test_regelsatz_unveraendert(tmp_path):
    nachweise = _regelsatz(tmp_path)
    quelle = sast.pruefe_regelsatz(nachweise)
    assert quelle.lizenz == "Testlizenz" and quelle.weiterverbreitung is False


@pytest.mark.parametrize("aenderung", ["geaendert", "zusaetzlich", "fehlend"])
def test_regelsatz_abweichung(tmp_path, aenderung):
    nachweise = _regelsatz(tmp_path)
    regeln = nachweise / sast.REGELN
    if aenderung == "geaendert":
        (regeln / "python" / "a.yaml").write_text("rules: [x]\n", encoding="utf-8")
    elif aenderung == "zusaetzlich":
        (regeln / "python" / "b.yaml").write_text("rules: []\n", encoding="utf-8")
    else:
        (regeln / "python" / "a.yaml").unlink()
    with pytest.raises(NachweisFehler) as e:
        sast.pruefe_regelsatz(nachweise)
    assert e.value.regel == "sast-regeln" and "python/" in e.value.text


def test_regelsatz_symlink_verboten(tmp_path):
    nachweise = _regelsatz(tmp_path)
    (nachweise / sast.REGELN / "python" / "a.yaml").unlink()
    (nachweise / sast.REGELN / "python" / "a.yaml").symlink_to(tmp_path)
    with pytest.raises(NachweisFehler) as e:
        sast.pruefe_regelsatz(nachweise)
    assert e.value.regel == "sast-regeln"


def test_regelsatz_lock_fehlt(tmp_path):
    nachweise = _regelsatz(tmp_path)
    (nachweise / sast.REGELN_LOCK).unlink()
    with pytest.raises(NachweisFehler) as e:
        sast.pruefe_regelsatz(nachweise)
    assert e.value.regel == "sast-regeln"


@_REGELN_DA
def test_echter_regelsatz_passt_zum_lock():
    quelle = sast.pruefe_regelsatz(WURZEL / "nachweise")
    assert quelle.url == "https://github.com/semgrep/semgrep-rules"
    assert quelle.lizenz == "Semgrep Rules License v1.0"
    assert quelle.weiterverbreitung is False
    assert (ECHTE_REGELN / "LICENSE").is_file()


# --- Ausnahmen ----------------------------------------------------------------------------------

_REGEL_AUSNAHME = '''
[[ausnahme]]
werkzeug = "bandit"
regel = "B404"
pfade = ["server/src/**"]
schwere = "niedrig"
begruendung = "Import von subprocess allein ist kein Befund; jeder Aufruf ist einzeln bewertet"
datum = 2026-09-29
'''


def _ausnahmen(tmp_path: Path, text: str) -> list[sast.Ausnahme]:
    pfad = tmp_path / "sast-ausnahmen.toml"
    pfad.write_text(text, encoding="utf-8")
    return sast.lade_ausnahmen(pfad)


def test_ausnahmen_laden(tmp_path):
    [a] = _ausnahmen(tmp_path, _REGEL_AUSNAHME)
    assert (a.werkzeug, a.regel, a.pfade, a.schwere, a.fingerabdruck) == (
        "bandit", "B404", ("server/src/**",), "niedrig", None)


def test_ausnahme_per_fingerabdruck(tmp_path):
    [a] = _ausnahmen(tmp_path, '[[ausnahme]]\nwerkzeug = "semgrep"\nfingerabdruck = "'
                     + "b" * 16 + '"\nschwere = "info"\nbegruendung = "'
                     + "Begründung lang genug für die Regel" + '"\ndatum = 2026-09-29\n')
    assert a.fingerabdruck == "b" * 16 and a.regel is None


@pytest.mark.parametrize("schwere", ["mittel", "hoch"])
def test_regel_ausnahme_fuer_mittel_ist_ladefehler(tmp_path, schwere):
    with pytest.raises(NachweisFehler) as e:
        _ausnahmen(tmp_path, _REGEL_AUSNAHME.replace('"niedrig"', f'"{schwere}"'))
    assert e.value.regel == "sast-ausnahmen"


@pytest.mark.parametrize(("alt", "neu"), [
    ('begruendung = "Import von subprocess allein ist kein Befund; jeder Aufruf ist einzeln '
     'bewertet"', 'begruendung = "zu kurz"'),
    ("datum = 2026-09-29", 'datum = "2026-09-29"'),
    ('pfade = ["server/src/**"]', "pfade = []"),
    ('pfade = ["server/src/**"]', 'pfade = ["server/[ab]/**"]'),
    ('regel = "B404"\n', 'regel = "B404"\nfingerabdruck = "' + "c" * 16 + '"\n'),
    ('regel = "B404"\n', ""),
    ('werkzeug = "bandit"', 'werkzeug = "flake8"'),
    ("datum = 2026-09-29", "datum = 2026-09-29\nunbekannt = 1"),
])
def test_ausnahme_ungueltig(tmp_path, alt, neu):
    with pytest.raises(NachweisFehler) as e:
        _ausnahmen(tmp_path, _REGEL_AUSNAHME.replace(alt, neu))
    assert e.value.regel == "sast-ausnahmen"


def test_ausnahme_fingerabdruck_format(tmp_path):
    with pytest.raises(NachweisFehler):
        _ausnahmen(tmp_path, '[[ausnahme]]\nwerkzeug = "semgrep"\nfingerabdruck = "XYZ"\n'
                   'schwere = "info"\nbegruendung = "Begründung lang genug für die Regel"\n'
                   "datum = 2026-09-29\n")


def test_leere_ausnahmedatei_ist_erlaubt(tmp_path):
    assert _ausnahmen(tmp_path, "# keine\n") == []


# --- Gate ---------------------------------------------------------------------------------------

def test_mittlerer_fund_ohne_befund_blockiert():
    blockiert = sast.sast_gate([_fund("mittel")], [], [])
    assert len(blockiert) == 1 and "Befundregister" in blockiert[0]


@pytest.mark.parametrize("schwere", ["mittel", "hoch"])
def test_fund_mit_befund_gleichen_fingerabdrucks_frei(schwere):
    assert sast.sast_gate([_fund(schwere)], [_befund("a" * 16)], []) == []


def test_fund_mit_befund_anderen_fingerabdrucks_blockiert():
    assert sast.sast_gate([_fund("hoch")], [_befund("b" * 16), _befund(None)], []) != []


def test_mehrere_funde_mit_gleichem_fingerabdruck_ein_befund():
    assert sast.sast_gate([_fund("hoch"), _fund("hoch")], [_befund("a" * 16)], []) == []


def test_niedriger_fund_ohne_ausnahme_blockiert():
    blockiert = sast.sast_gate([_fund("niedrig")], [], [])
    assert len(blockiert) == 1 and "Ausnahme" in blockiert[0]


def test_niedriger_fund_mit_regel_ausnahme_frei(tmp_path):
    ausnahmen = _ausnahmen(tmp_path, _REGEL_AUSNAHME)
    assert sast.sast_gate([_fund("niedrig"), _fund("info")], [], ausnahmen) == []


def test_regel_ausnahme_gilt_nur_fuer_ihren_pfad_und_ihr_werkzeug(tmp_path):
    ausnahmen = _ausnahmen(tmp_path, _REGEL_AUSNAHME)
    assert sast.sast_gate([_fund("niedrig", pfad="web/src/a.py")], [], ausnahmen) != []
    assert sast.sast_gate([_fund("niedrig", werkzeug="semgrep")], [], ausnahmen) != []
    assert sast.sast_gate([_fund("niedrig", regel="B603")], [], ausnahmen) != []


def test_ausnahme_hebt_mittleren_fund_nie_auf(tmp_path):
    ausnahmen = _ausnahmen(tmp_path, _REGEL_AUSNAHME)
    fp = _ausnahmen(tmp_path, '[[ausnahme]]\nwerkzeug = "bandit"\nfingerabdruck = "' + "a" * 16
                    + '"\nschwere = "niedrig"\nbegruendung = "Begründung lang genug für die '
                    'Regel"\ndatum = 2026-09-29\n')
    assert sast.sast_gate([_fund("mittel")], [], [*ausnahmen, *fp]) != []


def test_niedriger_fund_ueber_ausnahmeschwere_blockiert(tmp_path):
    ausnahmen = _ausnahmen(tmp_path, _REGEL_AUSNAHME.replace('"niedrig"', '"info"'))
    assert sast.sast_gate([_fund("info")], [], ausnahmen) == []
    assert sast.sast_gate([_fund("niedrig")], [], ausnahmen) != []


def test_niedriger_fund_mit_befund_frei():
    # Wer einen niedrigen Fund als Befund führt, braucht keine zusätzliche Ausnahme.
    assert sast.sast_gate([_fund("niedrig")], [_befund("a" * 16)], []) == []


def test_ausnahme_ohne_fund_nur_meldung(tmp_path):
    ausnahmen = _ausnahmen(tmp_path, _REGEL_AUSNAHME)
    assert sast.sast_gate([], [], ausnahmen) == []
    meldungen = sast.ungenutzte_ausnahmen([], ausnahmen)
    assert len(meldungen) == 1 and "B404" in meldungen[0]
    assert sast.ungenutzte_ausnahmen([_fund("niedrig")], ausnahmen) == []


# --- Befundregister: Feld fingerabdruck ---------------------------------------------------------

_BEFUND = '''
[[befund]]
id = "{id}"
quelle = "SAST Semgrep (Regel python.lang.security.audit.dangerous-subprocess-use-audit)"
titel = "Test"
schwere = "hoch"
kategorie = "sonstiges"
status = "offen"
staende = ["abc1234"]
entdeckt = 2026-09-29
{fp}
'''


def _lade_befund(tmp_path: Path, id_: str, fp: str) -> list[Befund]:
    pfad = tmp_path / "befunde.toml"
    pfad.write_text(_BEFUND.format(id=id_, fp=fp), encoding="utf-8")
    return lade_befunde(pfad)


def test_befund_mit_fingerabdruck(tmp_path):
    [b] = _lade_befund(tmp_path, "B-SAST-SEMGREP-0123456789ABCDEF",
                       'fingerabdruck = "0123456789abcdef"')
    assert b.fingerabdruck == "0123456789abcdef"


def test_befund_ohne_fingerabdruck_bleibt_gueltig(tmp_path):
    [b] = _lade_befund(tmp_path, "B-WB-X1", "")
    assert b.fingerabdruck is None


@pytest.mark.parametrize(("id_", "fp"), [
    ("B-SAST-SEMGREP-0123456789ABCDEF", ""),
    ("B-SAST-SEMGREP-0123456789ABCDEF", 'fingerabdruck = "fedcba9876543210"'),
    ("B-SAST-FLAKE8-0123456789ABCDEF", 'fingerabdruck = "0123456789abcdef"'),
    ("B-WB-X1", 'fingerabdruck = "0123456789abcdef"'),
    ("B-SAST-BANDIT-0123456789ABCDEF", 'fingerabdruck = "0123456789ABCDEF"'),
    ("B-SAST-BANDIT-0123", 'fingerabdruck = "0123"'),
])
def test_befund_fingerabdruck_ungueltig(tmp_path, id_, fp):
    with pytest.raises(NachweisFehler) as e:
        _lade_befund(tmp_path, id_, fp)
    assert e.value.regel == "befunde"


def test_befund_id_aus_fund():
    fund = _fund("hoch", werkzeug="semgrep", fingerabdruck="0123456789abcdef")
    assert sast.befund_id(fund) == "B-SAST-SEMGREP-0123456789ABCDEF"


# --- Container-Läufe (Befehl mitgeschnitten, Ausgabe aus Fixture) --------------------------------

def _werkzeuge(tmp_path: Path) -> dict[str, Werkzeug]:
    return {"semgrep": Werkzeug("semgrep", "semgrep/semgrep@sha256:" + "1" * 64, "1.0", None),
            "bandit": Werkzeug("bandit", "ghcr.io/astral-sh/uv@sha256:" + "2" * 64, "1.0",
                               "werkzeuge/bandit")}


class _Laeufer:
    def __init__(self, daten: dict[str, Any]) -> None:
        self.daten = daten
        self.befehle: list[list[str]] = []

    def __call__(self, befehl: list[str], regel: str, **_kw: Any) -> Any:
        self.befehle.append(befehl)
        mounts: dict[str, str] = {}
        for i, a in enumerate(befehl):
            if a == "-v":
                host, ziel = befehl[i + 1].split(":")[:2]
                mounts[ziel] = host
        aus = next(a for a in befehl if a.startswith("/aus/"))
        (Path(mounts["/aus"]) / aus.removeprefix("/aus/")).write_text(
            json.dumps(self.daten), encoding="utf-8")


def _mit_regelsatz(wt: Path) -> None:
    ziel = wt / "nachweise"
    quelle = _regelsatz(wt.parent / "regelsatz")
    shutil.copytree(quelle, ziel, dirs_exist_ok=True)


def test_semgrep_lauf_gehaertet_und_offline(tmp_path):
    wt = _worktree(tmp_path)
    _mit_regelsatz(wt)
    laeufer = _Laeufer(_semgrep_daten())
    ausgabe = tmp_path / "berichte" / "sast-semgrep.json"
    ausgabe.parent.mkdir()
    funde, anzahl = sast.semgrep_laufen(wt, _werkzeuge(tmp_path), ausgabe, laeufer=laeufer)
    assert anzahl == 2 and len(funde) == 3 and ausgabe.is_file()
    [befehl] = laeufer.befehle
    assert befehl[:6] == ["docker", "run", "--rm", "--pull", "never", "--network"]
    assert "none" in befehl and "--read-only" in befehl
    for pflicht in ("--metrics", "--disable-version-check", "--disable-nosem",
                    "--no-git-ignore", "--json"):
        assert pflicht in befehl
    assert befehl[befehl.index("--metrics") + 1] == "off"
    assert befehl[befehl.index("--timeout") + 1] == str(sast.SEMGREP_ZEITLIMIT_S)
    assert befehl[befehl.index("--timeout-threshold") + 1] == "0"
    # Schreibziel ist ein eigener, frischer Ordner — nie der Berichtsordner mit den Berichten
    # früherer Stufen.
    assert not any(a.startswith(f"{ausgabe.parent}:") for a in befehl)
    assert f"{wt / 'nachweise' / sast.REGELN}:/regeln:ro" in befehl
    assert f"{wt}:/quelle:ro" in befehl
    # Jede versionierte Quelldatei einzeln (sonst griffen die Standard-Ausschlüsse des Werkzeugs).
    assert [a for a in befehl if a.startswith("/quelle/")] == [
        "/quelle/server/src/paket/modul.py", "/quelle/web/src/seite.ts"]


def test_semgrep_lauf_prueft_regelsatz_vorher(tmp_path):
    wt = _worktree(tmp_path)
    _mit_regelsatz(wt)
    (wt / "nachweise" / sast.REGELN / "python" / "a.yaml").write_text("x", encoding="utf-8")
    laeufer = _Laeufer(_semgrep_daten())
    with pytest.raises(NachweisFehler) as e:
        sast.semgrep_laufen(wt, _werkzeuge(tmp_path), tmp_path / "s.json", laeufer=laeufer)
    assert e.value.regel == "sast-regeln" and laeufer.befehle == []


def test_semgrep_lauf_abdeckung(tmp_path):
    wt = _worktree(tmp_path)
    _mit_regelsatz(wt)
    daten = _semgrep_daten()
    daten["paths"]["scanned"] = []
    with pytest.raises(NachweisFehler) as e:
        sast.semgrep_laufen(wt, _werkzeuge(tmp_path), tmp_path / "s.json",
                            laeufer=_Laeufer(daten))
    assert e.value.regel == "sast-abdeckung"


def test_ausgabe_wird_nie_ueberschrieben(tmp_path):
    wt = _worktree(tmp_path)
    _mit_regelsatz(wt)
    ausgabe = tmp_path / "s.json"
    ausgabe.write_text("{}", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        sast.semgrep_laufen(wt, _werkzeuge(tmp_path), ausgabe, laeufer=_Laeufer({}))
    assert e.value.regel == "sast-ausgabe"


def test_fehlendes_ziel_ist_fehler(tmp_path):
    wt = _worktree(tmp_path)
    _mit_regelsatz(wt)
    shutil.rmtree(wt / "mcp-server")
    with pytest.raises(NachweisFehler) as e:
        sast.semgrep_laufen(wt, _werkzeuge(tmp_path), tmp_path / "s.json",
                            laeufer=_Laeufer(_semgrep_daten()))
    assert e.value.regel == "sast-abdeckung" and "mcp-server/src" in e.value.text


def _bandit_installiert(wt: Path, w: Werkzeug) -> None:
    ordner = wt / "nachweise" / "werkzeuge" / "bandit"
    ordner.mkdir(parents=True)
    (ordner / "uv.lock").write_text("lock\n", encoding="utf-8")
    venv = ordner / ".venv"
    venv.mkdir()
    (venv / sast.MARKE).write_text(json.dumps(sast.bandit_marke(w, ordner), sort_keys=True),
                                   encoding="utf-8")


def test_bandit_lauf_gehaertet(tmp_path):
    wt = _worktree(tmp_path)
    w = _werkzeuge(tmp_path)
    _bandit_installiert(wt, w["bandit"])
    laeufer = _Laeufer(_bandit_daten())
    ausgabe = tmp_path / "b.json"
    funde, anzahl = sast.bandit_laufen(wt, w, ausgabe, laeufer=laeufer)
    assert anzahl == 1 and len(funde) == 2
    [befehl] = laeufer.befehle
    assert "none" in befehl and "--read-only" in befehl
    for pflicht in ("--exit-zero", "--ignore-nosec"):
        assert pflicht in befehl
    assert [a for a in befehl if a.startswith("/quelle/")] == ["/quelle/server/src/paket/modul.py"]
    assert befehl[befehl.index("-f") + 1] == "json"
    assert not any(a.startswith(f"{tmp_path}:") for a in befehl) and ausgabe.is_file()
    assert f"{wt / 'nachweise' / 'werkzeuge' / 'bandit'}:/werkzeug:ro" in befehl
    assert "/werkzeug/.venv/bin/bandit" in befehl


def test_bandit_ohne_installation_ist_fehler(tmp_path):
    wt = _worktree(tmp_path)
    with pytest.raises(NachweisFehler) as e:
        sast.bandit_laufen(wt, _werkzeuge(tmp_path), tmp_path / "b.json",
                           laeufer=_Laeufer(_bandit_daten()))
    assert e.value.regel == "sast-werkzeug"


def test_bandit_mit_fremder_marke_ist_fehler(tmp_path):
    wt = _worktree(tmp_path)
    w = _werkzeuge(tmp_path)
    _bandit_installiert(wt, w["bandit"])
    (wt / "nachweise/werkzeuge/bandit/uv.lock").write_text("anderes lock\n", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        sast.bandit_laufen(wt, w, tmp_path / "b.json", laeufer=_Laeufer(_bandit_daten()))
    assert e.value.regel == "sast-werkzeug"


def test_bandit_bereitstellen_ist_protokollierter_netzschritt(tmp_path):
    nachweise = tmp_path / "nachweise"
    ordner = nachweise / "werkzeuge" / "bandit"
    ordner.mkdir(parents=True)
    (ordner / "uv.lock").write_text("lock\n", encoding="utf-8")
    befehle: list[list[str]] = []

    def laeufer(befehl: list[str], regel: str, **_kw: Any) -> None:
        befehle.append(befehl)
        (ordner / ".venv").mkdir()

    w = _werkzeuge(tmp_path)
    schritte = sast.bandit_bereitstellen(nachweise, w, laeufer=laeufer)
    assert len(schritte) == 1 and schritte[0].befehl[:3] == ["uv", "sync", "--locked"]
    assert "--network" not in befehle[0]  # Netzschritt: Standardnetz
    assert (ordner / ".venv" / sast.MARKE).is_file()
    # Zweiter Aufruf: Marke passt, kein Netz.
    assert sast.bandit_bereitstellen(nachweise, w, laeufer=laeufer) == []
    assert len(befehle) == 1


# --- Echte Werkzeuge ----------------------------------------------------------------------------

def _docker_da() -> bool:
    return shutil.which("docker") is not None


@_REGELN_DA
@pytest.mark.docker
def test_echte_fixtures_haben_funde_ab_mittel_je_werkzeug(tmp_path):
    """Die Fixtures der Negativkontrolle lösen mit dem vendierten Regelsatz und gepinntem Bandit
    je mindestens einen Fund ≥ mittel aus (sonst bewiese die Kontrolle nichts)."""
    if not _docker_da():
        pytest.fail("docker fehlt — die Negativkontrolle braucht echte Container")
    from scanview_nachweise.werkzeuge import lade_werkzeuge
    werkzeuge = lade_werkzeuge(WURZEL / "nachweise" / "werkzeuge.json")
    sast.bandit_bereitstellen(WURZEL / "nachweise", werkzeuge)
    ziele = (sast.FIXTURE_ORDNER,)
    semgrep, _ = sast.semgrep_laufen(WURZEL, werkzeuge, tmp_path / "s.json", ziele=ziele)
    bandit, _ = sast.bandit_laufen(WURZEL, werkzeuge, tmp_path / "b.json", ziele=ziele)
    for funde in (semgrep, bandit):
        assert any(f.schwere in ("hoch", "mittel") for f in funde), funde
    assert {f.pfad.rsplit(".", 1)[1] for f in semgrep} == {"py", "ts"}


# --- CLI `pruefen sast` -------------------------------------------------------------------------

def _cli_ergebnis(blockiert: list[str]) -> sast.Ergebnis:
    bericht = {"zaehlprobe": {"semgrep": {"hoch": 1, "mittel": 0, "niedrig": 0, "info": 0},
                              "bandit": {"hoch": 0, "mittel": 0, "niedrig": 2, "info": 0}},
               "abdeckung": {"semgrep": 3, "bandit": 2}, "ausnahmen_ohne_fund": ["alt"],
               "blockierend": blockiert, "funde": []}
    return sast.Ergebnis(bericht, blockiert, [])


@pytest.mark.parametrize(("blockiert", "code"), [([], 0), (["B-SAST-X: rot"], 1)])
def test_cli_pruefen_sast(tmp_path, monkeypatch, capsys, blockiert, code):
    from scanview_nachweise import cli
    gesehen: dict[str, Path] = {}

    def pruefen(worktree: Path, werkzeuge: dict[str, Werkzeug], berichte: Path) -> sast.Ergebnis:
        gesehen["worktree"], gesehen["berichte"] = worktree, berichte
        return _cli_ergebnis(blockiert)

    monkeypatch.setattr(sast, "pruefen", pruefen)
    arbeit = tmp_path / "arbeit"
    assert cli.main(["pruefen", "sast", "--wurzel", str(WURZEL), "--arbeitsordner",
                     str(arbeit)]) == code
    aus = capsys.readouterr().out
    assert gesehen == {"worktree": WURZEL, "berichte": arbeit}
    assert "semgrep: hoch 1" in aus and "bandit: " in aus and "HINWEIS alt" in aus
    assert json.loads((arbeit / "sast.json").read_text("utf-8"))["blockierend"] == blockiert
    assert ("B-SAST-X: rot" in aus) is bool(blockiert)


def test_fehlende_werkzeugausgabe_ist_fehler(tmp_path):
    wt = _worktree(tmp_path)
    _mit_regelsatz(wt)
    with pytest.raises(NachweisFehler) as e:
        sast.semgrep_laufen(wt, _werkzeuge(tmp_path), tmp_path / "s.json",
                            laeufer=lambda befehl, regel, **kw: None)
    assert e.value.regel == "sast-ausgabe"
