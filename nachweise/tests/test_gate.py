"""Befund-Gate und Negativtest-Lauf: Release-Schwelle je Regel inkl.
Grenzfällen, Negativtest-Suite je Kategorie grün/rot, fail-closed."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.gate import (
    befund_gate,
    befunde_nicht_ausgeliefert,
    negativtests_ausfuehren,
    negativtests_details,
)
from scanview_nachweise.register import Befund

HEUTE = date(2026, 9, 27)


def befund(**abweichung: object) -> Befund:
    felder: dict[str, object] = dict(
        id="B-001", quelle="Testquelle", titel="Testbefund", schwere="niedrig",
        kategorie="sonstiges", status="offen", staende=("abc1234",), fix_commit=None,
        retest=None, akzeptanz_begruendung=None, frist=None, entdeckt=HEUTE,
    )
    felder.update(abweichung)
    return Befund(**felder)  # type: ignore[arg-type]


# --- befund_gate ----------------------------------------------------------------------------------


def test_mittel_auth_blockiert_sofort():
    b = befund(schwere="mittel", kategorie="auth", status="offen", entdeckt=date(2026, 9, 27))
    assert befund_gate([b], date(2026, 9, 27)) == ["B-001: mittel/auth offen"]


def test_mittel_sonstiges_blockiert_erst_nach_60_tagen():
    b = befund(schwere="mittel", kategorie="haertung", status="offen", entdeckt=date(2026, 7, 29))
    assert befund_gate([b], date(2026, 9, 27)) == []          # 60 Tage
    assert befund_gate([b], date(2026, 9, 28)) == ["B-001: mittel offen seit 61 Tagen"]


@pytest.mark.parametrize("kategorie", ["auth", "rechte", "trennung", "abfluss"])
def test_mittel_in_schutzkategorien_blockiert(kategorie):
    b = befund(schwere="mittel", kategorie=kategorie)
    assert befund_gate([b], HEUTE) == [f"B-001: mittel/{kategorie} offen"]


@pytest.mark.parametrize("kategorie", ["verfuegbarkeit", "lieferkette", "haertung", "sonstiges"])
def test_mittel_uebrige_kategorien_frisch_nicht_blockierend(kategorie):
    assert befund_gate([befund(schwere="mittel", kategorie=kategorie)], HEUTE) == []


@pytest.mark.parametrize("schwere", ["kritisch", "hoch"])
def test_hoch_und_kritisch_offen_blockieren(schwere):
    b = befund(schwere=schwere, kategorie="haertung")
    assert befund_gate([b], HEUTE) == [f"B-001: {schwere} offen"]


@pytest.mark.parametrize("schwere", ["niedrig", "info"])
def test_niedrig_und_info_blockieren_nie(schwere):
    b = befund(schwere=schwere, kategorie="auth", entdeckt=date(2020, 1, 1))
    assert befund_gate([b], HEUTE) == []


def test_mittel_schutzkategorie_und_alt_nur_eine_meldung():
    # Beide Regeln treffen: genau eine Zeile je Befund, die erste passende Regel gewinnt.
    b = befund(schwere="mittel", kategorie="rechte", entdeckt=date(2026, 1, 1))
    assert befund_gate([b], HEUTE) == ["B-001: mittel/rechte offen"]


@pytest.mark.parametrize("status", ["behoben", "kein-befund"])
def test_erledigte_befunde_blockieren_nicht(status):
    b = befund(schwere="kritisch", kategorie="auth", status=status, fix_commit="abc1234",
               retest="server/tests/test_x.py::test_y")
    assert befund_gate([b], HEUTE) == []


def test_akzeptiert_blockiert_erst_nach_fristablauf():
    b = befund(schwere="hoch", kategorie="auth", status="akzeptiert",
               akzeptanz_begruendung="Begründung mit ausreichend Länge", frist=HEUTE)
    assert befund_gate([b], HEUTE) == []                      # Frist heute: noch gültig
    assert befund_gate([b], date(2026, 9, 28)) == [
        "B-001: Akzeptanz abgelaufen am 2026-09-27"]


# --- Befunde je Artefakt --------------------------------------------------------------------------

AUSGELIEFERT = {"web", "server", "mcp"}


def test_nur_import_blockiert_ein_plattform_release_nicht_steht_aber_informativ_da():
    nur_import = befund(id="B-IMG", schwere="hoch", artefakte=("import",))
    assert befund_gate([nur_import], HEUTE, ausgeliefert=AUSGELIEFERT) == []
    assert befunde_nicht_ausgeliefert([nur_import], HEUTE, AUSGELIEFERT) == [
        "B-IMG: hoch offen (betrifft nur import — nicht ausgeliefert, informativ)"]


def test_ohne_artefakte_betrifft_alles_und_blockiert():
    b = befund(schwere="hoch")
    assert befund_gate([b], HEUTE, ausgeliefert=AUSGELIEFERT) == ["B-001: hoch offen"]
    assert befunde_nicht_ausgeliefert([b], HEUTE, AUSGELIEFERT) == []


def test_teilweise_ausgeliefert_blockiert():
    b = befund(schwere="hoch", artefakte=("import", "server"))
    assert befund_gate([b], HEUTE, ausgeliefert=AUSGELIEFERT) == ["B-001: hoch offen"]


def test_ohne_release_kontext_gilt_jeder_befund():
    # `gate befunde` ohne Release: strenger Standard, auch nur-Import-Befunde blockieren.
    assert befund_gate([befund(schwere="hoch", artefakte=("import",))], HEUTE) == [
        "B-001: hoch offen"]


def test_nicht_blockierende_nur_import_befunde_nicht_informativ_gelistet():
    b = befund(schwere="niedrig", artefakte=("import",))
    assert befunde_nicht_ausgeliefert([b], HEUTE, AUSGELIEFERT) == []


def test_reihenfolge_wie_im_register():
    bs = [befund(id="B-B", schwere="hoch"), befund(id="B-A", schwere="niedrig"),
          befund(id="B-C", schwere="mittel", kategorie="trennung")]
    assert befund_gate(bs, HEUTE) == ["B-B: hoch offen", "B-C: mittel/trennung offen"]


# --- negativtests_ausfuehren ----------------------------------------------------------------------

NEGATIV_OK = """
[[kategorie]]
id = "NT-EINS"
titel = "Erste Kategorie"
bezug = ["K-TEST-1"]
instanz = false
tests = ["server/tests/test_a.py::test_eins", "import/tests/test_b.py::test_zwei"]

[[kategorie]]
id = "NT-ZWEI"
titel = "Zweite Kategorie"
bezug = ["K-TEST-1"]
instanz = false
tests = ["server/tests/test_a.py::test_drei"]

[[kategorie]]
id = "NT-INSTANZ"
titel = "Nur auf der Instanz"
bezug = ["K-TEST-1"]
instanz = true
tests = ["deploy/platform/test/caddy-pruefen.sh 5b"]
"""


def _repo(tmp_path: Path, negativ: str = NEGATIV_OK) -> Path:
    (tmp_path / "docs" / "compliance").mkdir(parents=True)
    (tmp_path / "docs" / "compliance" / "negativtests.toml").write_text(negativ, encoding="utf-8")
    (tmp_path / "server" / "tests").mkdir(parents=True)
    (tmp_path / "server" / "tests" / "test_a.py").write_text(
        "def test_eins():\n    pass\n\n\ndef test_drei():\n    pass\n", encoding="utf-8")
    (tmp_path / "import" / "tests").mkdir(parents=True)
    (tmp_path / "import" / "tests" / "test_b.py").write_text(
        "def test_zwei():\n    pass\n", encoding="utf-8")
    (tmp_path / "deploy" / "platform" / "test").mkdir(parents=True)
    (tmp_path / "deploy" / "platform" / "test" / "caddy-pruefen.sh").write_text(
        "#!/bin/bash\n# 5b X-Forwarded-Uri roh\n", encoding="utf-8")
    return tmp_path


def junit_schreiben(befehl: list[str], cwd: Path, *, weglassen: int = 0,
                    zusatz: str = "") -> None:
    """Schreibt wie pytest eine JUnit-XML an den Ort aus `--junitxml=…`: je Node-ID ein
    bestandener Testfall (die letzten `weglassen` fehlen), `zusatz` wird roh angehängt."""
    ziel = next(a.split("=", 1)[1] for a in befehl if a.startswith("--junitxml="))
    nodeids = [a for a in befehl if "::" in a]
    faelle = "".join(
        f'<testcase classname="{n.split("::")[0].removesuffix(".py").replace("/", ".")}" '
        f'name="{n.split("::")[1]}" time="0.0"/>'
        for n in nodeids[:len(nodeids) - weglassen])
    Path(ziel).write_text(f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{faelle}'
                          f"{zusatz}</testsuite></testsuites>", encoding="utf-8")


class Laeufer:
    """Ersatz für `uv run pytest`: merkt sich die Aufrufe, liefert je Teilprojekt einen
    Exit-Code und schreibt bei Exit 0 eine JUnit-XML mit je einem bestandenen Testfall."""

    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.codes = codes or {}
        self.aufrufe: list[tuple[list[str], Path]] = []

    def __call__(self, befehl: list[str], cwd: Path) -> int:
        self.aufrufe.append((befehl, cwd))
        code = self.codes.get(cwd.name, 0)
        if code == 0:
            junit_schreiben(befehl, cwd)
        return code


def _ohne_junit(befehl: list[str]) -> list[str]:
    return [a for a in befehl if not a.startswith("--junitxml=")]


def test_alle_gruen_und_instanz_uebersprungen(tmp_path):
    repo = _repo(tmp_path)
    laeufer = Laeufer()
    assert negativtests_ausfuehren(repo, ausfuehren=laeufer) == {"NT-EINS": True, "NT-ZWEI": True}
    befehle = [(_ohne_junit(b), c.relative_to(repo).as_posix()) for b, c in laeufer.aufrufe]
    assert befehle == [
        (["uv", "run", "--locked", "pytest", "-q", "tests/test_a.py::test_eins"], "server"),
        (["uv", "run", "--locked", "pytest", "-q", "tests/test_b.py::test_zwei"], "import"),
        (["uv", "run", "--locked", "pytest", "-q", "tests/test_a.py::test_drei"], "server"),
    ]
    assert all(sum(a.startswith("--junitxml=") for a in b) == 1 for b, _ in laeufer.aufrufe)


def test_details_zaehlen_ausgefuehrte_tests(tmp_path):
    ergebnis = negativtests_details(_repo(tmp_path), ausfuehren=Laeufer())
    assert {k: (e.gruen, e.ausgefuehrt) for k, e in ergebnis.items()} == {
        "NT-EINS": (True, 2), "NT-ZWEI": (True, 1)}


@pytest.mark.parametrize("zusatz, weglassen, grund", [
    ('<testcase classname="tests.test_a" name="test_x"><skipped message="s"/></testcase>', 0,
     "übersprungen"),
    ('<testcase classname="tests.test_a" name="test_x"><failure message="f"/></testcase>', 0,
     "fehlgeschlagen"),
    ('<testcase classname="tests.test_a" name="test_x"><error message="e"/></testcase>', 0,
     "fehlgeschlagen"),
    ("", 1, "nicht ausgeführt"),
])
def test_exit_0_aber_junit_zeigt_nicht_gelaufen_ist_rot(tmp_path, zusatz, weglassen, grund):
    repo = _repo(tmp_path)

    def laeufer(befehl: list[str], cwd: Path) -> int:
        junit_schreiben(befehl, cwd, zusatz=zusatz, weglassen=weglassen)
        return 0

    ergebnis = negativtests_details(repo, ausfuehren=laeufer)
    assert not ergebnis["NT-EINS"].gruen and grund in ergebnis["NT-EINS"].grund


def test_exit_0_ohne_junit_ist_rot(tmp_path):
    ergebnis = negativtests_details(_repo(tmp_path), ausfuehren=lambda befehl, cwd: 0)
    assert not ergebnis["NT-EINS"].gruen and "JUnit" in ergebnis["NT-EINS"].grund


# --- mit echtem pytest in einem kleinen Wegwerf-Projekt (nicht dem Server) ------------------------

TESTDATEI = """import pytest


def test_gut():
    pass


@pytest.mark.parametrize("n", [1, 2, 3])
def test_param(n):
    pass


def test_skip():
    pytest.skip("nicht hier")


@pytest.mark.xfail(reason="bekannt")
def test_xfail():
    assert False


def test_zweiter():
    pass
"""


def _mini_repo(tmp_path: Path, tests: list[str]) -> Path:
    liste = ", ".join(f'"proj/tests/test_mini.py::{t}"' for t in tests)
    (tmp_path / "docs" / "compliance").mkdir(parents=True)
    (tmp_path / "docs" / "compliance" / "negativtests.toml").write_text(
        f'[[kategorie]]\nid = "NT-MINI"\ntitel = "Mini"\nbezug = ["K-X"]\ninstanz = false\n'
        f"tests = [{liste}]\n", encoding="utf-8")
    (tmp_path / "proj" / "tests").mkdir(parents=True)
    (tmp_path / "proj" / "tests" / "test_mini.py").write_text(TESTDATEI, encoding="utf-8")
    return tmp_path


def _echter_laeufer(*zusatz: str) -> Callable[[list[str], Path], int]:
    """Wie `uv_pytest`, aber mit dem pytest dieses venv (kein uv-Projekt im Wegwerf-Ordner);
    `zusatz` simuliert eingeschleuste Optionen (addopts, PYTEST_ADDOPTS)."""
    def laeufer(befehl: list[str], cwd: Path) -> int:
        assert befehl[:4] == ["uv", "run", "--locked", "pytest"]
        umgebung = {k: v for k, v in os.environ.items()
                    if k not in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")}
        return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
                               *befehl[4:], *zusatz], cwd=cwd, env=umgebung,
                              capture_output=True, check=False).returncode
    return laeufer


def test_echt_bestanden_zaehlt_parametrisierte_faelle(tmp_path):
    repo = _mini_repo(tmp_path, ["test_gut", "test_param"])
    e = negativtests_details(repo, ausfuehren=_echter_laeufer())["NT-MINI"]
    assert (e.gruen, e.ausgefuehrt) == (True, 4)


@pytest.mark.parametrize("test", ["test_skip", "test_xfail"])
def test_echt_skip_und_xfail_sind_rot(tmp_path, test):
    repo = _mini_repo(tmp_path, ["test_gut", test])
    e = negativtests_details(repo, ausfuehren=_echter_laeufer())["NT-MINI"]
    assert not e.gruen and "übersprungen" in e.grund


def test_echt_abgewaehlt_ist_rot(tmp_path):
    # -k wählt test_zweiter ab: pytest endet mit Exit 0, gelaufen ist aber nur test_gut.
    repo = _mini_repo(tmp_path, ["test_gut", "test_zweiter"])
    e = negativtests_details(repo, ausfuehren=_echter_laeufer("-k", "test_gut"))["NT-MINI"]
    assert not e.gruen and "test_zweiter" in e.grund


def test_echt_null_tests_mit_exit_0_ist_rot(tmp_path):
    # --co sammelt nur: Exit 0, kein einziger Test gelaufen.
    repo = _mini_repo(tmp_path, ["test_gut"])
    e = negativtests_details(repo, ausfuehren=_echter_laeufer("--co"))["NT-MINI"]
    assert not e.gruen and e.ausgefuehrt == 0


@pytest.mark.parametrize("code", [1, 2, 4, 5])
def test_nicht_null_ist_rot(tmp_path, code):
    # 4 = Nutzungsfehler (z. B. unbekannte Node-ID), 5 = nichts gesammelt: beides rot.
    repo = _repo(tmp_path)
    ergebnis = negativtests_ausfuehren(repo, ausfuehren=Laeufer({"import": code}))
    assert ergebnis == {"NT-EINS": False, "NT-ZWEI": True}


def test_kategorie_ohne_test_ist_fehler(tmp_path):
    repo = _repo(tmp_path, NEGATIV_OK.replace(
        'tests = ["server/tests/test_a.py::test_drei"]', "tests = []"))
    with pytest.raises(NachweisFehler, match="Kategorie NT-ZWEI ohne Test"):
        negativtests_ausfuehren(repo, ausfuehren=Laeufer())


def test_unbekannter_test_ist_fehler_vor_jedem_lauf(tmp_path):
    repo = _repo(tmp_path, NEGATIV_OK.replace("test_drei", "test_gibtsnicht"))
    laeufer = Laeufer()
    with pytest.raises(NachweisFehler, match="test_gibtsnicht"):
        negativtests_ausfuehren(repo, ausfuehren=laeufer)
    assert laeufer.aufrufe == []


def test_unbekannter_caddy_punkt_ist_fehler(tmp_path):
    repo = _repo(tmp_path, NEGATIV_OK.replace("caddy-pruefen.sh 5b", "caddy-pruefen.sh 99z"))
    with pytest.raises(NachweisFehler, match="99z"):
        negativtests_ausfuehren(repo, ausfuehren=Laeufer())


def test_standard_laeufer_ohne_shell_und_ohne_fremdes_venv(tmp_path, monkeypatch):
    from scanview_nachweise import gate

    gesehen: dict[str, object] = {}

    def run(befehl, **kw):
        gesehen.update(befehl=befehl, **kw)
        return subprocess.CompletedProcess(befehl, 4)

    monkeypatch.setenv("VIRTUAL_ENV", "/irgendwo/.venv")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--co")
    monkeypatch.setenv("PYTEST_PLUGINS", "boese")
    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.uv_pytest(["uv", "run", "pytest"], tmp_path) == 4
    assert gesehen["befehl"] == ["uv", "run", "pytest"]
    assert gesehen.get("shell", False) is False
    assert gesehen["cwd"] == tmp_path
    for name in ("VIRTUAL_ENV", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        assert name not in gesehen["env"], name  # type: ignore[operator]


# --- Retests behobener Befunde --------------------------------------------------------------------

def test_retests_behobener_befunde_laufen_skip_ist_rot(tmp_path):
    """Jeder Python-Retest eines behobenen Befunds läuft wirklich (JUnit wie die Negativtests):
    übersprungen zählt als rot — auch der Retest von B-WB-M4 (Freischalt-Probe)."""
    from scanview_nachweise.gate import retests_details
    repo = _mini_repo(tmp_path, ["test_gut"])
    befunde = [befund(id="B-1", status="behoben", fix_commit="abc1234",
                      retest="proj/tests/test_mini.py::test_gut"),
               befund(id="B-2", status="behoben", fix_commit="abc1234",
                      retest="proj/tests/test_mini.py::test_param"),
               befund(id="B-3", status="offen", retest="proj/tests/test_mini.py::test_skip")]
    e = retests_details(repo, befunde, ausfuehren=_echter_laeufer())
    assert (e.gruen, e.ausgefuehrt) == (True, 4)
    befunde.append(befund(id="B-4", status="behoben", fix_commit="abc1234",
                          retest="proj/tests/test_mini.py::test_skip"))
    e = retests_details(repo, befunde, ausfuehren=_echter_laeufer())
    assert not e.gruen and "übersprungen" in e.grund


def test_retests_instanz_nicht_hier(tmp_path):
    from scanview_nachweise.gate import retest_ausserhalb, retests_details
    repo = _mini_repo(tmp_path, ["test_gut"])
    befunde = [befund(id="B-C", status="behoben", fix_commit="abc1234",
                      retest="deploy/platform/test/caddy-pruefen.sh 3e")]
    laeufer = Laeufer()
    e = retests_details(repo, befunde, ausfuehren=laeufer)
    assert e.gruen and e.ausgefuehrt == 0 and laeufer.aufrufe == []
    assert retest_ausserhalb(befunde) == {"B-C": "Instanz (Deploy-Smoke)"}


# --- Web-Retests und unbekannte Retest-Arten ------------------------------------------------------

WEB_RETEST = "web/tests/x.test.ts::sagt etwas"


def _vitest_laeufer(zustand: str) -> Callable[[list[str], Path], int]:
    """Ersetzt `npx vitest run … --reporter=junit`: schreibt eine JUnit-XML wie Vitest (Name mit
    describe-Präfix „… > “); `zustand` = bestanden | uebersprungen | fehlt | rot."""
    def laeufer(befehl: list[str], cwd: Path) -> int:
        assert befehl[:4] == ["npx", "--no-install", "vitest", "run"]
        assert "--reporter=junit" in befehl and cwd.name == "web"
        ziel = next(a.split("=", 1)[1] for a in befehl if a.startswith("--outputFile="))
        name = "Gruppe &gt; sagt etwas" if zustand != "fehlt" else "Gruppe &gt; anderes"
        innen = {"uebersprungen": "<skipped/>", "rot": "<failure message='x'/>"}.get(zustand, "")
        Path(ziel).write_text(
            '<?xml version="1.0"?><testsuites><testsuite name="vitest">'
            f'<testcase classname="tests/x.test.ts" name="{name}" time="0">{innen}</testcase>'
            "</testsuite></testsuites>", encoding="utf-8")
        return 1 if zustand == "rot" else 0
    return laeufer


def _web_repo(tmp_path: Path) -> Path:
    (tmp_path / "web" / "tests").mkdir(parents=True)
    (tmp_path / "web" / "tests" / "x.test.ts").write_text(
        "describe('Gruppe', () => { it('sagt etwas', () => {}) })\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(("zustand", "gruen"), [("bestanden", True), ("uebersprungen", False),
                                                ("fehlt", False), ("rot", False)])
def test_web_retest_laeuft_und_skip_ist_rot(tmp_path, zustand, gruen):
    from scanview_nachweise.gate import retests_details
    b = befund(id="B-W", status="behoben", fix_commit="abc1234", retest=WEB_RETEST)
    e = retests_details(_web_repo(tmp_path), [b], ausfuehren=_vitest_laeufer(zustand))
    assert e.gruen is gruen, e
    if gruen:
        assert e.ausgefuehrt == 1


@pytest.mark.parametrize("retest", ["mcp-server/tests/x.ts::y", "deploy/platform/test/x.sh::y"])
def test_unbekannte_retest_art_ist_rot(tmp_path, retest):
    """Ein behobener Retest außerhalb der bekannten Arten wird nicht still
    übergangen."""
    from scanview_nachweise.gate import retests_details
    datei = tmp_path / retest.split("::")[0]
    datei.parent.mkdir(parents=True)
    datei.write_text("y\n", encoding="utf-8")
    b = befund(id="B-X", status="behoben", fix_commit="abc1234", retest=retest)
    e = retests_details(tmp_path, [b], ausfuehren=Laeufer())
    assert not e.gruen and "unbekannte Retest-Art" in e.grund and "B-X" in e.grund
