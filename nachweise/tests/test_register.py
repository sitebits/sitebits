"""Register laden und validieren: Kontrollmatrix und Anwendbarkeit als TOML,
fail-closed."""

import ast
import re
import subprocess
from pathlib import Path

import pytest
from test_gate import Laeufer

from scanview_nachweise.cli import main
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.positivliste import Positivliste, lies_positivliste
from scanview_nachweise.register import (
    Kontrolle,
    lade_anwendbarkeit,
    lade_anwendbarkeit_register,
    lade_matrix,
)

WURZEL = Path(__file__).resolve().parents[2]
ECHTE_MATRIX = WURZEL / "docs" / "compliance" / "kontrollmatrix.toml"
ECHTE_ANWENDBARKEIT = WURZEL / "docs" / "compliance" / "anwendbarkeit.toml"


def _zeile(**abweichung: object) -> str:
    """Gültige Matrixzeile als TOML; `abweichung` überschreibt Felder (None = weglassen)."""
    felder: dict[str, object] = {
        "id": "K-TEST-1",
        "quelle": "Testquelle 1",
        "titel": "Testkontrolle",
        "art": "pflicht",
        "anwendbar": True,
        "begruendung": "",
        "pruefhandlung": "Test ausführen",
        "sollwert": "grün",
        "release_voraussetzung": True,
        "iso": ["8.8"],
        "oeffentlich": True,
        "nachweis_schluessel": "scan",
    }
    felder.update(abweichung)
    zeilen = ["[[kontrolle]]"]
    for name, wert in felder.items():
        if wert is None:
            continue
        if isinstance(wert, bool):
            zeilen.append(f"{name} = {'true' if wert else 'false'}")
        elif isinstance(wert, list):
            zeilen.append(f"{name} = [" + ", ".join(f'"{w}"' for w in wert) + "]")
        elif isinstance(wert, str):
            zeilen.append(f'{name} = "{wert}"')
        else:
            zeilen.append(f"{name} = {wert}")
    return "\n".join(zeilen) + "\n"


def _matrix(tmp_path: Path, *zeilen: str) -> Path:
    p = tmp_path / "m.toml"
    p.write_text("\n".join(zeilen), encoding="utf-8")
    return p


# --- Validierung der Matrix -----------------------------------------------------------------------


def test_release_voraussetzung_braucht_nachweisschluessel(tmp_path):
    p = tmp_path / "m.toml"
    p.write_text('''[[kontrolle]]
id = "K-CRA-I-A"
quelle = "CRA Anhang I Teil I (2) a"
titel = "Keine bekannten ausnutzbaren Schwachstellen"
art = "pflicht"
anwendbar = true
begruendung = ""
pruefhandlung = "trivy-Scan der SBOM gegen DB ≤ 24 h"
sollwert = "0 Befunde ≥ hoch ohne VEX fixed/not_affected"
release_voraussetzung = true
iso = ["8.8"]
oeffentlich = true
''', encoding="utf-8")
    with pytest.raises(NachweisFehler, match="nachweis_schluessel"):
        lade_matrix(p)


def test_gueltige_zeile_wird_geladen(tmp_path):
    k = lade_matrix(_matrix(tmp_path, _zeile()))
    assert k == [
        Kontrolle(
            id="K-TEST-1", quelle="Testquelle 1", titel="Testkontrolle", art="pflicht",
            anwendbar=True, begruendung="", pruefhandlung="Test ausführen", sollwert="grün",
            release_voraussetzung=True, iso=("8.8",), oeffentlich=True, nachweis_schluessel="scan",
        )
    ]


def test_fehler_hat_regel_matrix(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        lade_matrix(_matrix(tmp_path, _zeile(), _zeile()))
    assert e.value.regel == "matrix"


def test_doppelte_id(tmp_path):
    with pytest.raises(NachweisFehler, match="K-TEST-1"):
        lade_matrix(_matrix(tmp_path, _zeile(), _zeile(titel="anders")))


@pytest.mark.parametrize("id_", ["k-test", "K-", "X-TEST", "K-TEST 1", "K-TÄST", "K-TEST-1\n"])
def test_ungueltige_id(tmp_path, id_):
    with pytest.raises(NachweisFehler, match="ID"):
        lade_matrix(_matrix(tmp_path, _zeile(id=id_.replace("\n", "\\n"))))


def test_fehlende_begruendung_bei_nicht_anwendbar(tmp_path):
    with pytest.raises(NachweisFehler, match="Begründung"):
        lade_matrix(_matrix(tmp_path, _zeile(anwendbar=False, begruendung="zu kurz")))


def test_nicht_anwendbar_mit_begruendung_ok(tmp_path):
    k = lade_matrix(_matrix(tmp_path, _zeile(
        anwendbar=False, begruendung="Keine Funkschnittstelle im Produkt vorhanden.")))
    assert k[0].anwendbar is False


@pytest.mark.parametrize("feld", ["quelle", "titel", "pruefhandlung", "sollwert"])
def test_pflichtfeld_leer(tmp_path, feld):
    with pytest.raises(NachweisFehler, match=feld):
        lade_matrix(_matrix(tmp_path, _zeile(**{feld: "   "})))


@pytest.mark.parametrize("feld", ["id", "quelle", "titel", "art", "anwendbar", "pruefhandlung",
                                  "sollwert", "release_voraussetzung", "iso", "oeffentlich"])
def test_pflichtfeld_fehlt(tmp_path, feld):
    with pytest.raises(NachweisFehler, match=feld):
        lade_matrix(_matrix(tmp_path, _zeile(**{feld: None})))


def test_oeffentlich_zeile_fehlt_heisst_aufgefuehrt(tmp_path):
    assert lade_matrix(_matrix(tmp_path, _zeile()))[0].oeffentlich_zeile is True


def test_oeffentlich_zeile_false_wird_geladen(tmp_path):
    k = lade_matrix(_matrix(tmp_path, _zeile(oeffentlich=False, oeffentlich_zeile=False)))
    assert k[0].oeffentlich_zeile is False


def test_oeffentlich_zeile_false_nur_bei_nicht_oeffentlich(tmp_path):
    with pytest.raises(NachweisFehler, match="oeffentlich_zeile"):
        lade_matrix(_matrix(tmp_path, _zeile(oeffentlich=True, oeffentlich_zeile=False)))


def test_oeffentlich_zeile_falscher_typ(tmp_path):
    with pytest.raises(NachweisFehler, match="oeffentlich_zeile"):
        lade_matrix(_matrix(tmp_path, _zeile(oeffentlich=False, oeffentlich_zeile="nein")))


def test_unbekanntes_feld_wird_abgelehnt(tmp_path):
    # Tippfehler dürfen nicht still zu None werden (fail-closed).
    with pytest.raises(NachweisFehler, match="nachweisschluessel"):
        lade_matrix(_matrix(tmp_path, _zeile(nachweis_schluessel=None,
                                             release_voraussetzung=False,
                                             nachweisschluessel="scan")))


def test_falsche_art(tmp_path):
    with pytest.raises(NachweisFehler, match="art"):
        lade_matrix(_matrix(tmp_path, _zeile(art="optional")))


def test_falscher_typ_bool(tmp_path):
    with pytest.raises(NachweisFehler, match="anwendbar"):
        lade_matrix(_matrix(tmp_path, _zeile(anwendbar="ja")))


def test_falscher_typ_iso(tmp_path):
    p = _matrix(tmp_path, _zeile(iso=None) + "iso = \"8.8\"\n")
    with pytest.raises(NachweisFehler, match="iso"):
        lade_matrix(p)


def test_ohne_release_voraussetzung_darf_schluessel_fehlen(tmp_path):
    k = lade_matrix(_matrix(tmp_path, _zeile(release_voraussetzung=False,
                                             nachweis_schluessel=None)))
    assert k[0].nachweis_schluessel is None
    assert k[0].schluessel_teile == ()


def test_mehrere_nachweisschluessel(tmp_path):
    k = lade_matrix(_matrix(tmp_path, _zeile(nachweis_schluessel="lizenzen+scan")))
    assert k[0].schluessel_teile == ("lizenzen", "scan")


@pytest.mark.parametrize("schluessel", ["", "Scan", "scan+", "doku:", "scan lizenzen", "+scan"])
def test_ungueltiger_nachweisschluessel(tmp_path, schluessel):
    with pytest.raises(NachweisFehler, match="nachweis_schluessel"):
        lade_matrix(_matrix(tmp_path, _zeile(nachweis_schluessel=schluessel)))


def test_leere_matrix(tmp_path):
    with pytest.raises(NachweisFehler, match="keine"):
        lade_matrix(_matrix(tmp_path, "# leer\n"))


def test_datei_fehlt(tmp_path):
    with pytest.raises(NachweisFehler, match="fehlt"):
        lade_matrix(tmp_path / "gibtsnicht.toml")


def test_kaputtes_toml(tmp_path):
    with pytest.raises(NachweisFehler, match="TOML"):
        lade_matrix(_matrix(tmp_path, "[[kontrolle]\nid = "))


# --- Anwendbarkeit -------------------------------------------------------------------------------

ANWENDBARKEIT_OK = '''entschieden_am = "2026-09-27"
status = "ungeklärt — handeln, als gälte Art. 14"

[[modell]]
name = "Plattform"
produktgrenze = "Server"
rechtsperson = "UG"
seit = "27.09.2026"
einordnung = "ungeklärt"
offene_fragen = ["Rolle?"]
'''


def test_anwendbarkeit_laden(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text(ANWENDBARKEIT_OK, encoding="utf-8")
    modelle = lade_anwendbarkeit(p)
    assert [m.name for m in modelle] == ["Plattform"]
    assert modelle[0].offene_fragen == ("Rolle?",)
    reg = lade_anwendbarkeit_register(p)
    assert reg.status == "ungeklärt — handeln, als gälte Art. 14"
    assert reg.entschieden_am == "2026-09-27"


@pytest.mark.parametrize("kaputt", [
    ANWENDBARKEIT_OK.replace('status = "ungeklärt — handeln, als gälte Art. 14"\n', ""),
    ANWENDBARKEIT_OK.replace('"2026-09-27"', '"27.09.2026"'),
    ANWENDBARKEIT_OK.replace('produktgrenze = "Server"', 'produktgrenze = ""'),
    ANWENDBARKEIT_OK.replace('rechtsperson = "UG"\n', ""),
    ANWENDBARKEIT_OK.replace('name = "Plattform"', 'name = "Plattform"\nextra = 1'),
    ANWENDBARKEIT_OK.split("[[modell]]")[0],
    ANWENDBARKEIT_OK + ANWENDBARKEIT_OK.split("\n\n", 1)[1],  # doppelter Modellname
])
def test_anwendbarkeit_ungueltig(tmp_path, kaputt):
    p = tmp_path / "a.toml"
    p.write_text(kaputt, encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        lade_anwendbarkeit(p)
    assert e.value.regel == "anwendbarkeit"


# --- CLI -----------------------------------------------------------------------------------------


BEFUNDE_OK = '''[[befund]]
id = "B-T-1"
quelle = "T-1 (Testbericht)"
titel = "GEHEIMER-TITEL"
schwere = "niedrig"
kategorie = "haertung"
status = "offen"
staende = ["abc1234"]
entdeckt = 2026-09-27
'''

NEGATIVTESTS_OK = '''[[kategorie]]
id = "NT-RECHTE"
titel = "Rechteprüfung je Rolle"
bezug = ["K-TEST-1", "B-T-1"]
instanz = false
tests = ["server/tests/test_a.py::test_a"]
'''


def _wurzel_mit_registern(tmp_path: Path, matrix: str, befunde: str = BEFUNDE_OK) -> Path:
    c = tmp_path / "docs" / "compliance"
    c.mkdir(parents=True)
    (c / "kontrollmatrix.toml").write_text(matrix, encoding="utf-8")
    (c / "anwendbarkeit.toml").write_text(ANWENDBARKEIT_OK, encoding="utf-8")
    (c / "befunde.toml").write_text(befunde, encoding="utf-8")
    (c / "negativtests.toml").write_text(NEGATIVTESTS_OK, encoding="utf-8")
    (tmp_path / "server" / "tests").mkdir(parents=True)
    (tmp_path / "server" / "tests" / "test_a.py").write_text("def test_a():\n    pass\n",
                                                            encoding="utf-8")
    return tmp_path


def test_cli_pruefen_ok(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile())
    assert main(["register", "pruefen", "--wurzel", str(w)]) == 0
    aus = capsys.readouterr().out
    assert "1 Kontrollen" in aus
    assert "1 Befunde" in aus and "1 Negativtest-Kategorien" in aus


def test_cli_pruefen_kaputte_matrix_exit_2(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile(nachweis_schluessel=None))
    assert main(["register", "pruefen", "--wurzel", str(w)]) == 2
    assert capsys.readouterr().err.startswith("NACHWEIS-FEHLER matrix:")


def test_cli_pruefen_ohne_register_exit_2(tmp_path, capsys):
    assert main(["register", "pruefen", "--wurzel", str(tmp_path)]) == 2
    assert "NACHWEIS-FEHLER" in capsys.readouterr().err


def test_cli_darstellen_schreibt_sechs_dateien_deterministisch(tmp_path):
    w = _wurzel_mit_registern(tmp_path, _zeile() + _zeile(id="K-TEST-2", oeffentlich=False,
                                                          pruefhandlung="GEHEIM-HANDLUNG"))
    ziele = [w / "docs/compliance/anwendbarkeit.md", w / "docs/compliance/kontrollmatrix.md",
             w / "docs/public/kontrollmatrix.md", w / "docs/compliance/befunde.md",
             w / "docs/public/befunde.md", w / "docs/public/anwendbarkeit.md"]
    assert main(["register", "darstellen", "--wurzel", str(w)]) == 0
    erst = [z.read_bytes() for z in ziele]
    assert main(["register", "darstellen", "--wurzel", str(w)]) == 0
    assert [z.read_bytes() for z in ziele] == erst
    assert b"GEHEIM-HANDLUNG" in erst[1]
    assert b"GEHEIM-HANDLUNG" not in erst[2]
    assert b"GEHEIMER-TITEL" in erst[3]
    assert b"GEHEIMER-TITEL" not in erst[4]


def test_cli_darstellen_pruefen_gleich_exit_0(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile())
    assert main(["register", "darstellen", "--wurzel", str(w)]) == 0
    capsys.readouterr()
    assert main(["register", "darstellen", "--pruefen", "--wurzel", str(w)]) == 0
    assert "übereinstimmend" in capsys.readouterr().out


def test_cli_darstellen_pruefen_meldet_abweichung_und_schreibt_nichts(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile())
    assert main(["register", "darstellen", "--wurzel", str(w)]) == 0
    veraendert = w / "docs/public/kontrollmatrix.md"
    veraendert.write_text("von Hand geändert\n", encoding="utf-8")
    (w / "docs/compliance/befunde.md").unlink()
    capsys.readouterr()
    assert main(["register", "darstellen", "--pruefen", "--wurzel", str(w)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("NACHWEIS-FEHLER register-darstellung:")
    assert "docs/public/kontrollmatrix.md" in err and "docs/compliance/befunde.md" in err
    assert veraendert.read_text(encoding="utf-8") == "von Hand geändert\n"
    assert not (w / "docs/compliance/befunde.md").exists()


def test_cli_darstellen_pruefen_ohne_md_exit_2(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile())
    assert main(["register", "darstellen", "--pruefen", "--wurzel", str(w)]) == 2
    assert not (w / "docs/compliance/kontrollmatrix.md").exists()


def test_cli_darstellen_kaputte_befunde_schreibt_nichts(tmp_path):
    w = _wurzel_mit_registern(tmp_path, _zeile(), befunde=BEFUNDE_OK.replace("offen", "behoben"))
    assert main(["register", "darstellen", "--wurzel", str(w)]) == 2
    assert not (w / "docs/compliance/kontrollmatrix.md").exists()


def test_cli_pruefen_unbekannter_bezug_exit_2(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile(), befunde=BEFUNDE_OK.replace("B-T-1", "B-T-2"))
    assert main(["register", "pruefen", "--wurzel", str(w)]) == 2
    assert "B-T-1" in capsys.readouterr().err


def test_cli_pruefen_unbekannter_test_exit_2(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile())
    (w / "server" / "tests" / "test_a.py").write_text("def test_b():\n    pass\n",
                                                      encoding="utf-8")
    assert main(["register", "pruefen", "--wurzel", str(w)]) == 2
    assert "server/tests/test_a.py::test_a" in capsys.readouterr().err


# --- CLI gate ------------------------------------------------------------------------------------

BLOCKIEREND = BEFUNDE_OK.replace('schwere = "niedrig"', 'schwere = "mittel"').replace(
    'kategorie = "haertung"', 'kategorie = "auth"')


def test_cli_gate_befunde_gruen(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile())
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "2026-09-27"]) == 0
    assert "0 blockierend" in capsys.readouterr().out


def test_cli_gate_befunde_blockiert_exit_1(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile(), befunde=BLOCKIEREND)
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "2026-09-27"]) == 1
    aus = capsys.readouterr().out
    assert "1 blockierend" in aus and "B-T-1: mittel/auth offen" in aus


def test_cli_gate_befunde_heute_wirkt(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile(), befunde=BEFUNDE_OK.replace(
        'schwere = "niedrig"', 'schwere = "mittel"'))
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "2026-11-26"]) == 0
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "2026-11-27"]) == 1
    assert "seit 61 Tagen" in capsys.readouterr().out


def test_cli_gate_befunde_ungueltiges_datum_exit_2(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile())
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "27.09.2026"]) == 2
    assert "NACHWEIS-FEHLER" in capsys.readouterr().err


@pytest.mark.parametrize("code, exit_", [(0, 0), (1, 1), (4, 1)])
def test_cli_gate_negativtests(tmp_path, monkeypatch, capsys, code, exit_):
    from scanview_nachweise import gate

    w = _wurzel_mit_registern(tmp_path, _zeile())
    monkeypatch.setattr(gate, "uv_pytest", Laeufer({"server": code}))
    assert main(["gate", "negativtests", "--wurzel", str(w)]) == exit_
    aus = capsys.readouterr().out
    # Die Zahl kommt aus der JUnit-XML (tatsächlich gelaufene Fälle), nicht aus dem Register.
    assert ("NT-RECHTE: grün (1 Tests gelaufen)" if code == 0 else "NT-RECHTE: rot") in aus


def _git_repo_mit_commit(w: Path) -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", *args],
                              cwd=w, capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "x")
    return git("rev-parse", "--short=7", "HEAD")


FIX = BEFUNDE_OK.replace('status = "offen"', 'status = "behoben"') + (
    'retest = "server/tests/test_a.py::test_a"\nfix_commit = "{sha}"\n')


def test_cli_gate_befunde_prueft_fix_commit_im_repo(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile())
    sha = _git_repo_mit_commit(w)
    (w / "docs/compliance/befunde.toml").write_text(FIX.format(sha=sha), encoding="utf-8")
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "2026-09-27"]) == 0
    (w / "docs/compliance/befunde.toml").write_text(FIX.format(sha="0123abc"), encoding="utf-8")
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "2026-09-27"]) == 2
    assert "NACHWEIS-FEHLER befunde:" in capsys.readouterr().err


def test_fix_commit_muss_im_gepruften_stand_enthalten_sein(tmp_path, capsys):
    """Ein Fix auf einem anderen Zweig existiert zwar im Repo, ist aber nicht im
    geprüften Stand (HEAD) enthalten — kein „behoben“."""
    w = _wurzel_mit_registern(tmp_path, _zeile())
    _git_repo_mit_commit(w)

    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", *args],
                              cwd=w, capture_output=True, text=True, check=True).stdout.strip()
    haupt = git("rev-parse", "--abbrev-ref", "HEAD")
    git("checkout", "-q", "-b", "anderer-zweig")
    git("commit", "-q", "--allow-empty", "-m", "Fix nur hier")
    fremd = git("rev-parse", "--short=7", "HEAD")
    git("checkout", "-q", haupt)
    (w / "docs/compliance/befunde.toml").write_text(FIX.format(sha=fremd), encoding="utf-8")
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "2026-09-27"]) == 2
    fehler = capsys.readouterr().err
    assert "nicht im geprüften Stand" in fehler and fremd in fehler


def test_cli_gate_befunde_fix_commit_ohne_git_repo_exit_2(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile(),
                              befunde=FIX.format(sha="0123abc"))
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "2026-09-27"]) == 2
    assert "0123abc" in capsys.readouterr().err


def test_cli_gate_befunde_prueft_retest(tmp_path, capsys):
    w = _wurzel_mit_registern(tmp_path, _zeile(), befunde=BEFUNDE_OK.replace(
        'status = "offen"', 'status = "offen"\nretest = "server/tests/test_a.py::test_weg"'))
    assert main(["gate", "befunde", "--wurzel", str(w), "--heute", "2026-09-27"]) == 2
    assert "test_weg" in capsys.readouterr().err


def test_cli_darstellen_kaputt_schreibt_nichts(tmp_path):
    w = _wurzel_mit_registern(tmp_path, _zeile(nachweis_schluessel=None))
    assert main(["register", "darstellen", "--wurzel", str(w)]) == 2
    assert not (w / "docs/compliance/kontrollmatrix.md").exists()
    assert not (w / "docs/public").exists()


# --- Echte Register (im privaten Repo; im öffentlichen Export nicht enthalten) -------------------

_echt = pytest.mark.skipif(
    not ECHTE_MATRIX.exists(),
    reason="Compliance-Register liegen nur im privaten Repo (nicht Teil des öffentlichen Exports)",
)

# Erwartete Zuordnung: ID → (Nachweisschlüssel, Release-Voraussetzung), wörtlich.
SOLL = {
    "K-CRA-I-A": ("scan", True),
    "K-CRA-I-B": ("doku:sicherheitsarchitektur", True),
    "K-CRA-I-C": ("doku:support", True),
    "K-CRA-I-D": ("negativtests", True),
    "K-CRA-I-E": ("doku:sicherheitsarchitektur", True),
    "K-CRA-I-F": ("manifest+doku:betrieb", True),
    "K-CRA-I-G": ("doku:sicherheitsarchitektur", True),
    "K-CRA-I-H": ("negativtests", True),
    "K-CRA-I-I": ("doku:sicherheitsarchitektur", False),
    "K-CRA-I-J": ("doku:sicherheitsarchitektur", True),
    "K-CRA-I-K": ("negativtests", False),
    "K-CRA-I-L": ("doku:betrieb", False),
    "K-CRA-I-M": ("doku:betrieb", False),
    "K-CRA-II-1": ("sbom", True),
    "K-CRA-II-2": ("doku:support", True),
    "K-CRA-II-3": ("negativtests", True),
    "K-CRA-II-4": ("changelog", True),
    "K-CRA-II-5": ("doku:security", True),
    "K-CRA-II-6": ("doku:security", True),
    "K-CRA-II-7": ("manifest", True),
    "K-CRA-II-8": ("doku:support", True),
    "K-CRA-13-5": ("lizenzen+scan", True),
    "K-CRA-13-8": ("doku:support", True),
    "K-CRA-14": ("vault:meldeplaybook", True),
    "K-CRA-ANH-II": ("doku:readme+doku:betrieb", True),
    "K-CRA-ANH-VII": ("archiv", True),
    "K-CRA-MODUL-A": ("anwendbarkeit", False),
    "K-TR2-FORMAT": ("sbom", True),
    "K-TR2-FELDER": ("sbom", True),
    "K-TR2-TIEFE": ("sbom", True),
    "K-TR3-SECTXT": ("betrieb:securitytxt", False),
    "K-AKTIV-SAST": ("sast", True),
    "K-AKTIV-FUZZING": ("fuzzing", True),
    "K-OSSF-STATIC-ANALYSIS": ("manifest+sast", True),
}
OSSF = ["REPORT-PROCESS", "REPORT-PRIVATE", "RESPONSE-14", "VULNS-60", "STATIC-ANALYSIS",
        "TEST-POLICY", "TESTS-ADDED", "DELIVERY-MITM", "RELEASE-NOTES-VULNS"]


@_echt
def test_echte_matrix_enthaelt_soll_zeilen():
    k = {x.id: x for x in lade_matrix(ECHTE_MATRIX)}
    for id_, (schluessel, rv) in SOLL.items():
        assert id_ in k, id_
        assert (k[id_].nachweis_schluessel, k[id_].release_voraussetzung) == (schluessel, rv), id_
    for id_ in ["K-TR3-FORMULAR", "K-TR3-CSAF"]:
        assert k[id_].anwendbar and not k[id_].release_voraussetzung, id_
        assert "offen bis Inverkehrbringen" in k[id_].sollwert, id_
    for name in OSSF:
        z = k[f"K-OSSF-{name}"]
        assert z.art == "freiwillig", z.id
        assert z.release_voraussetzung is (name != "TESTS-ADDED"), z.id
    for id_ in ["K-CRA-14", "K-TR3-SECTXT", "K-TR3-FORMULAR", "K-TR3-CSAF"]:
        assert k[id_].oeffentlich, id_
    # ISO-Zuordnung, stichprobenhaft geprüft.
    assert "8.8" in k["K-CRA-I-A"].iso
    assert {"5.21", "8.9"} <= set(k["K-CRA-II-1"].iso)
    assert "8.26" in k["K-CRA-I-D"].iso
    for z in k.values():
        if z.nachweis_schluessel == "negativtests":
            assert "8.29" in z.iso, z.id


@_echt
def test_echte_anwendbarkeit_gueltig():
    reg = lade_anwendbarkeit_register(ECHTE_ANWENDBARKEIT)
    assert reg.status == "ungeklärt — handeln, als gälte Art. 14"
    assert len(reg.modelle) == 3
    assert sum(len(m.offene_fragen) for m in reg.modelle) >= 5


@_echt
def test_echte_matrix_geplante_interne_haertungen_nicht_oeffentlich():
    """Eine geplante, nicht öffentliche Härtung legt schon mit Kennung und Titel eine Lücke offen:
    sie erscheint in der öffentlichen Fassung gar nicht. Die Prüfung nennt bewusst keine
    Kennungen — diese Testdatei wird selbst veröffentlicht."""
    kontrollen = lade_matrix(ECHTE_MATRIX)
    geplant_intern = [k for k in kontrollen
                      if k.id.startswith("K-GEPLANT-") and not k.oeffentlich]
    assert geplant_intern
    assert all(not k.oeffentlich_zeile for k in geplant_intern)
    oeffentlich = (WURZEL / "docs" / "public" / "kontrollmatrix.md").read_text(encoding="utf-8")
    for k in kontrollen:
        if not k.oeffentlich_zeile:
            assert f"| {k.id} |" not in oeffentlich
            assert k.titel not in oeffentlich


#: Kennungen privater Arbeitsdokumente und Review-/Planungsrunden — nie in öffentlicher Doku:
#: Spec-Abschnitte, Rulings, Codex-Funde, Re-Review- und Gesamt-Review-Punkte (frühere
#: Fix-Runden), Fix-Runden- und „Review Focus“-Nummern selbst, sowie Plan-Task-Verweise
#: („Task 12b“ — nur mit Ziffer, sonst träfe es das englische Wort „Task“ in Fließtext).
#: Nur `R<1-2 Ziffern>` als bloße Zahl, nicht `P<1-2 Ziffern>`: Lizenzkategorien (`P10`, `P12`,
#: `P14`, `F10` in `nachweise/lizenzen.toml`) und Messpunkt-Bezeichner (`P1`, `P2`, … in
#: Vorschau-/CSV-Code) sehen gleich aus wie eine private Kennung, sind aber echte, im Projekt
#: selbst erklärte Fachbegriffe (wie Befund-IDs `B-…` und Kontroll-IDs `K-…`, die wegen des
#: vorangestellten Bindestrichs ohnehin nicht matchen). Der private Plan-Vorsatz „P24-“ bleibt
#: über die eigene Alternative erfasst; „Codex P1-7“, „Re-Review R3-1“ & Co. bleiben trotz
#: Bindestrich-Suffix erfasst, weil hier das auslösende Wort selbst (nicht die bloße Zahl) zählt.
#: Review-Fundkürzel (`C`/`I`/`M`/`N`, ein bis zwei Ziffern, Doppelpunkt, optional mit
#: „Review“ davor) zählen nur am Anfang eines Kommentars bzw. Docstrings — dort stehen sie als
#: Präfix; im Fließtext oder ohne Kommentarzeichen (Messpunkte, Lizenzkategorien) nicht.
#: Ein `R` mit Ziffer, das allein ein Zeichenketten-Literal bildet (`'R2'`, Knopfnamen von
#: Controllern), ist ein Gerätebegriff; im Fließtext („gemäß R3“) bleibt es erfasst.
_INTERN = re.compile(r"Spec §|\bSpec\b|Ruling|\bP24-"
                     r"|(?<![-\w'\"])R\d{1,2}(?![-\w])|(?<![-\w])R\d{1,2}(?![-\w'\"])"
                     r"|\bCodex\b|\bRe-Review\b|\bGesamt-Review\b|\bFix-Runde\b"
                     r"|\bReview Focus\b|\bTask \d{1,3}[a-z]?\b"
                     r'|^\s*(?:"""|#:?|//|/\*\*?|\*)\s*(?:Review )?[CIMN]\d{1,2}:')

#: Controller-Knöpfe (L1/R1, L2/R2, R3 …) sind Gerätebegriffe: Ein `R` mit Ziffer zählt in
#: einer Zeile mit eindeutigem Controller-Vokabular (Knopf-Gegenstücke, Symbole, „Controller“)
#: nicht als private Kennung. Jeder andere Treffer derselben Zeile zählt weiter.
_CONTROLLER = re.compile(r"\b(?:LT|RT|LB|RB|LS|RS|L[123])\b|Controller|Gamepad|DualSense|✕|△|○|□")


def _intern(zeile: str) -> bool:
    for m in _INTERN.finditer(zeile):
        if re.fullmatch(r"R\d{1,2}", m.group()) and _CONTROLLER.search(zeile):
            continue
        return True
    return False


def test_oeffentliche_doku_ohne_interne_kennungen():
    treffer = [f"{p.relative_to(WURZEL)}:{nr}: {z.strip()[:80]}"
               for p in sorted((WURZEL / "docs" / "public").rglob("*.md"))
               for nr, z in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
               if _intern(z)]
    assert treffer == []


@pytest.mark.skipif(not (WURZEL / "docs" / "compliance" / "kontrollmatrix.toml").exists(),
                    reason="kontrollmatrix.toml liegt nur im privaten Repo (nicht im Export)")
def test_oeffentliche_matrixzeilen_ohne_interne_kennungen():
    """Auch die Quelle der öffentlichen Fassung (kontrollmatrix.toml, Zeilen mit
    oeffentlich = true) nennt keine privaten Dokumente."""
    from scanview_nachweise.register import lade_matrix
    for k in lade_matrix(WURZEL / "docs" / "compliance" / "kontrollmatrix.toml"):
        if k.oeffentlich:
            for feld in (k.quelle, k.titel, k.pruefhandlung, k.sollwert):
                assert not _intern(feld), (k.id, feld)


#: Diese Datei definiert und prüft `_INTERN` — nur die Definition (samt erklärendem Kommentar
#: davor) und die Gegenprobe brauchen die verbotenen Zeichenketten zwangsläufig als Literale;
#: der Rest der Datei wird wie jede andere geprüft.
_SELBST = Path(__file__).resolve()
_SELBST_BEREICHE = ("_INTERN", "test_interne_kennungen_muster_erkennt_verstoss")


def _selbst_ausgenommene_zeilen() -> set[int]:
    """Zeilennummern (1-basiert) der Ausnahmebereiche in dieser Datei: die Anweisung bzw.
    Funktion aus `_SELBST_BEREICHE` samt direkt davor stehendem Kommentarblock."""
    text = _SELBST.read_text(encoding="utf-8")
    zeilen = text.splitlines()
    ausgenommen: set[int] = set()
    for knoten in ast.parse(text).body:
        namen = ({t.id for t in knoten.targets if isinstance(t, ast.Name)}
                 if isinstance(knoten, ast.Assign) else
                 {knoten.name} if isinstance(knoten, ast.FunctionDef) else set())
        if not namen & set(_SELBST_BEREICHE):
            continue
        anfang = knoten.lineno
        while anfang > 1 and zeilen[anfang - 2].lstrip().startswith("#"):
            anfang -= 1
        ausgenommen.update(range(anfang, (knoten.end_lineno or knoten.lineno) + 1))
    return ausgenommen


def _exportierte_dateien() -> list[Path]:
    """Jede versionierte Datei, die die Positivliste des öffentlichen Exports aufnimmt
    (`veroeffentlichung/positivliste.txt`) — derselbe Maßstab wie `export.kandidaten` und
    `artefakte.baue_artefakte`."""
    positivliste = Positivliste(lies_positivliste(
        (WURZEL / "veroeffentlichung" / "positivliste.txt").read_text(encoding="utf-8")))
    versioniert = subprocess.run(["git", "ls-files"], cwd=WURZEL, check=True,
                                 capture_output=True, text=True).stdout.splitlines()
    return [WURZEL / p for p in versioniert if p and positivliste.nimmt_auf(p)]


@pytest.mark.skipif(not (WURZEL / ".git").exists(),
                    reason="braucht die Git-Verwaltung des privaten Repos (git ls-files)")
def test_exportierter_quellcode_ohne_interne_kennungen():
    """Zitate und Kennungen privater Arbeitsdokumente (Spezifikation, Entscheidungen) sind nicht
    nur in öffentlicher Doku verboten, sondern auch in Code-Kommentaren des öffentlich exportierten
    Baums (`nachweise/**`, `web/**`, `server/**`, `mcp-server/**`, `deploy/platform/**` laut
    Positivliste) — nicht nur `docs/public/*.md` wie
    `test_oeffentliche_doku_ohne_interne_kennungen`."""
    treffer: list[str] = []
    selbst = _selbst_ausgenommene_zeilen()
    for pfad in _exportierte_dateien():
        try:
            text = pfad.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # Binärdatei oder nicht lesbar — keine Textzeilen zu prüfen
        eigene = pfad.resolve() == _SELBST
        for nr, zeile in enumerate(text.splitlines(), 1):
            if eigene and nr in selbst:
                continue
            if _intern(zeile):
                treffer.append(f"{pfad.relative_to(WURZEL)}:{nr}: {zeile.strip()[:100]}")
    assert treffer == []


def test_selbstausnahme_nur_fuer_definition_und_gegenprobe():
    """Die Ausnahme dieser Datei deckt nur `_INTERN` (mit Kommentar) und die Gegenprobe — nicht
    etwa den Docstring des Schutztests oder andere Tests."""
    selbst = _selbst_ausgenommene_zeilen()
    zeilen = _SELBST.read_text(encoding="utf-8").splitlines()
    ausgenommen = "\n".join(zeilen[n - 1] for n in sorted(selbst))
    assert "_INTERN = re.compile(" in ausgenommen
    assert "def test_interne_kennungen_muster_erkennt_verstoss" in ausgenommen
    assert "def test_exportierter_quellcode_ohne_interne_kennungen" not in ausgenommen
    assert "def test_oeffentliche_doku_ohne_interne_kennungen" not in ausgenommen
    assert len(selbst) < len(zeilen) // 4


def test_interne_kennungen_muster_erkennt_verstoss():
    """Gegenprobe zu den beiden Tests oben: `_INTERN` schlägt tatsächlich an privaten Kennungen
    an und lässt echte Fachbezeichner (Befund-/Kontroll-IDs, CRA-Artikel) unangetastet — eine
    leere Trefferliste allein bewiese sonst nichts über die Güte des Musters."""
    for verstoss in ("Netzschritt (R5)", "Spec §13 Nr. 5", "Ruling P24-C1", "P24-P9",
                     "siehe Spec", "Codex P1-7", "Codex-Abschluss P1", "Re-Review R3-1",
                     "Re-Review F6", "Gesamt-Review I1", "Minor Gesamt-Review",
                     "Fix-Runde 2, Punkt 7", "Review Focus 4", "Task 12b", "Task 5",
                     '    """M3: inBundle', "    # I2: am letzten @", '"""Review C1: Zweig',
                     "  // M7: ly= mit", "#: N2: Docstring", "'gemäß R3'", "(R4)"):
        assert _INTERN.search(verstoss), verstoss
    for unbedenklich in ("Befund B-CX-R2-P1", "Kontrolle K-AKTIV-NPM-SIGNATUREN",
                        "CRA Art. 13 Abs. 5", "ISO 8.30", "R2024a",
                        "für den aktuellen Task (Datenvertrag) unschädlich", "Task-Liste",
                        "P1: -1,000 -2,000", "    # P2: Messpunkt", "x = M1: y",
                        "    # Lizenz P10: frei", "C14: Kohlenstoff im Fließtext",
                        "'L1', 'R1', 'L2', 'R2'", '["R3"]', "L2/R2 (LT/RT) zoomen",
                        "| L1 / LB · R1 / RB | Werkzeug |", "setzt ✕/A statt R2"):
        assert not _intern(unbedenklich), unbedenklich
    for verstoss in ("Spec § 4 mit L2/R2", "Ruling R3 beim Controller"):
        assert _intern(verstoss), verstoss


def test_cli_darstellen_betrieb_schreibt_betriebsnachweise_und_laesst_matrix_unberuehrt(tmp_path):
    import json
    w = _wurzel_mit_registern(tmp_path / "w", _zeile() + _zeile(
        id="K-BETRIEB-X", nachweis_schluessel="betrieb:dast", release_voraussetzung=False))
    archiv = tmp_path / "archiv"
    (archiv / "pruefungen" / "2026-10-01-dast-L").mkdir(parents=True)
    (archiv / "pruefungen" / "2026-10-01-dast-L" / "ergebnis.json").write_text(
        json.dumps({"gate": "blockiert"}), encoding="utf-8")
    assert main(["register", "darstellen", "--wurzel", str(w)]) == 0
    matrix = (w / "docs/compliance/kontrollmatrix.md").read_bytes()
    assert main(["register", "darstellen", "--betrieb", str(archiv), "--wurzel", str(w)]) == 0
    assert (w / "docs/compliance/kontrollmatrix.md").read_bytes() == matrix
    text = (w / "docs/compliance/betriebsnachweise.md").read_text(encoding="utf-8")
    assert "L: 2026-10-01 blockiert" in text
    # Release-Gate und --pruefen kennen die Archivstände nicht und bleiben übereinstimmend.
    assert main(["register", "darstellen", "--pruefen", "--wurzel", str(w)]) == 0


def test_cli_darstellen_pruefen_mit_betrieb_exit_2(tmp_path, capsys):
    """`--pruefen` vergleicht nur die Register-Fassungen; ein `--betrieb` dazu würde still
    ignoriert — deshalb Abbruch statt Schein-Prüfung."""
    w = _wurzel_mit_registern(tmp_path / "w", _zeile())
    (tmp_path / "archiv" / "pruefungen").mkdir(parents=True)
    assert main(["register", "darstellen", "--wurzel", str(w)]) == 0
    assert main(["register", "darstellen", "--pruefen", "--betrieb", str(tmp_path / "archiv"),
                 "--wurzel", str(w)]) == 2
    assert "--betrieb" in capsys.readouterr().err


def test_cli_darstellen_betrieb_ohne_archiv_exit_2(tmp_path):
    w = _wurzel_mit_registern(tmp_path / "w", _zeile())
    assert main(["register", "darstellen", "--betrieb", str(tmp_path / "fehlt"),
                 "--wurzel", str(w)]) == 2
    assert not (w / "docs/compliance/betriebsnachweise.md").exists()
