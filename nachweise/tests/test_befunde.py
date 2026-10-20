"""Befundregister und Negativtest-Register laden: TOML, fail-closed, dazu die
Querprüfungen (Verweise auf Tests und Instanzprüfungen, Bezüge auf Befunde/Kontrollen) und die
echten Register im Repo."""

from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path

import pytest

from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.register import (
    Befund,
    lade_befunde,
    lade_matrix,
    lade_negativtests,
    pruefe_bezuege,
    pruefe_verweise,
)

WURZEL = Path(__file__).resolve().parents[2]
COMPLIANCE = WURZEL / "docs" / "compliance"
ECHTE_BEFUNDE = COMPLIANCE / "befunde.toml"
ECHTE_NEGATIVTESTS = COMPLIANCE / "negativtests.toml"


def _befund(**abweichung: object) -> str:
    """Gültiger Befund als TOML; `abweichung` überschreibt Felder (None = weglassen). Datumswerte
    werden als TOML-Datum geschrieben, Texte in Anführungszeichen."""
    felder: dict[str, object] = {
        "id": "B-WB-M1",
        "quelle": "audit-code-whitebox M1",
        "titel": "Kontosperre umgehbar",
        "schwere": "mittel",
        "kategorie": "auth",
        "status": "behoben",
        "staende": ["a546418"],
        "entdeckt": date(2026, 9, 27),
        "fix_commit": "3ee2ca6",
        "retest": "server/tests/test_sicherheit.py::test_m1_x",
    }
    felder.update(abweichung)
    zeilen = ["[[befund]]"]
    for name, wert in felder.items():
        if wert is None:
            continue
        if isinstance(wert, date):
            zeilen.append(f"{name} = {wert.isoformat()}")
        elif isinstance(wert, list):
            zeilen.append(f"{name} = [" + ", ".join(f'"{w}"' for w in wert) + "]")
        elif isinstance(wert, str):
            zeilen.append(f'{name} = "{wert}"')
        else:
            zeilen.append(f"{name} = {wert}")
    return "\n".join(zeilen) + "\n"


def _datei(tmp_path: Path, *teile: str, name: str = "b.toml") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(teile), encoding="utf-8")
    return p


AKZEPTIERT = dict(status="akzeptiert", fix_commit=None, retest=None,
                  akzeptanz_begruendung="Ohne CAPTCHA nicht lösbar, Passkey als Ausweg.",
                  frist=date(2026, 12, 31), freigabe="Andre")


# --- lade_befunde ---------------------------------------------------------------------------------


BEGR = "Betrifft nur die Laufzeit-Images des Import-Werkzeugs"
BEGR_PLATZHALTER = BEGR


def test_artefakte_optional_und_geladen(tmp_path):
    ohne = lade_befunde(_datei(tmp_path, _befund()))[0]
    assert ohne.artefakte == ()
    mit = lade_befunde(_datei(tmp_path, _befund(artefakte=["import"],
                                                artefakte_begruendung=BEGR), name="m.toml"))[0]
    assert mit.artefakte == ("import",) and mit.artefakte_begruendung == BEGR


@pytest.mark.parametrize("abweichung", [dict(artefakte=["import"]),
                                        dict(artefakte=["import"], artefakte_begruendung="kurz"),
                                        dict(artefakte_begruendung=BEGR_PLATZHALTER)])
def test_artefakte_nur_mit_begruendung(tmp_path, abweichung):
    with pytest.raises(NachweisFehler) as e:
        lade_befunde(_datei(tmp_path, _befund(**abweichung)))
    assert e.value.regel == "befunde" and "artefakte" in e.value.text


@pytest.mark.parametrize("artefakte", [["pdal"], [], ["import", "import"], ["Web"]])
def test_artefakte_unbekannt_leer_oder_doppelt(tmp_path, artefakte):
    with pytest.raises(NachweisFehler) as e:
        lade_befunde(_datei(tmp_path, _befund(artefakte=artefakte, artefakte_begruendung=BEGR)))
    assert e.value.regel == "befunde" and "artefakte" in e.value.text


@pytest.mark.skipif(not ECHTE_BEFUNDE.exists(), reason="Befundregister liegt nur im privaten Repo")
def test_echte_nur_import_befunde_markiert():
    befunde = {b.id: b for b in lade_befunde(ECHTE_BEFUNDE)}
    # Genau diese Befunde sind eingeschränkt — eine neue Einschränkung ist eine bewusste Änderung.
    # SAST-Funde in import/src (Importwerkzeug, nicht ausgeliefert) tragen ebenfalls „import“.
    sast_import = {b.id for b in befunde.values()
                   if b.id.startswith("B-SAST-") and "(import/src/" in b.quelle}
    assert sast_import == {"B-SAST-SEMGREP-F418401D449114A3",
                           "B-SAST-SEMGREP-F92C9932AB14305C",
                           "B-SAST-BANDIT-14789B265EC81EA8",
                           # Splat-Import (0.10): Kamera-XML und build-lod-Aufruf
                           "B-SAST-SEMGREP-FDA42991C858C489", "B-SAST-SEMGREP-FF5E75374A3F0D89",
                           "B-SAST-SEMGREP-BE6EE389B33C057A", "B-SAST-BANDIT-3849E6199464A0A6"}
    assert {b.id for b in befunde.values() if b.artefakte} == {
        "B-SCAN-IMG-PDAL", "B-SCAN-IMG-UNTWINE", "B-LK-IMPORT", "B-LK-UNTWINE", "B-SBOM-IMAGES",
        "B-BF-V1", "B-BD-R8-I1", *sast_import,
        "B-SAST-BANDIT-5686B4B81278B8ED", "B-SAST-BANDIT-8B67B85AA135B6D6",
        "B-SAST-BANDIT-56F8E6847EEB3AB3", "B-RL-N1"}
    for bid in ("B-SCAN-IMG-PDAL", "B-SCAN-IMG-UNTWINE", "B-LK-IMPORT", "B-LK-UNTWINE",
                "B-SBOM-IMAGES", *sast_import):
        assert befunde[bid].artefakte == ("import",), bid
        assert len(befunde[bid].artefakte_begruendung or "") >= 20, bid
    # Barrierefreiheit des Viewers: nur das Web-Bundle ist betroffen.
    assert befunde["B-BF-V1"].artefakte == ("web",)
    assert befunde["B-BF-V1"].schwere == "niedrig"
    # Rücksprung nach der Anmeldung: nur das Portal im Web-Bundle wertet `weiter=` aus.
    assert befunde["B-BD-R8-I1"].artefakte == ("web",)
    # Cache-Leerung beim Sitzungswechsel: nur der Plattform-Server setzt die Antworten.
    assert befunde["B-RL-N1"].artefakte == ("server",)
    for bid in ("B-WB-M4", "B-TE-M3"):
        assert befunde[bid].artefakte == (), bid


def test_gueltiger_befund(tmp_path):
    assert lade_befunde(_datei(tmp_path, _befund())) == [Befund(
        id="B-WB-M1", quelle="audit-code-whitebox M1", titel="Kontosperre umgehbar",
        schwere="mittel", kategorie="auth", status="behoben", staende=("a546418",),
        fix_commit="3ee2ca6", retest="server/tests/test_sicherheit.py::test_m1_x",
        akzeptanz_begruendung=None, frist=None, entdeckt=date(2026, 9, 27),
    )]


def test_fehler_hat_regel_befunde(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        lade_befunde(_datei(tmp_path, _befund(), _befund()))
    assert e.value.regel == "befunde"
    assert "B-WB-M1" in e.value.text and "doppelt" in e.value.text


@pytest.mark.parametrize("feld", ["fix_commit", "retest"])
def test_behoben_braucht_fix_commit_und_retest(tmp_path, feld):
    with pytest.raises(NachweisFehler, match=feld):
        lade_befunde(_datei(tmp_path, _befund(**{feld: None})))


def test_akzeptiert_gueltig(tmp_path):
    b = lade_befunde(_datei(tmp_path, _befund(**AKZEPTIERT)))[0]
    assert b.status == "akzeptiert" and b.frist == date(2026, 12, 31)
    assert b.akzeptanz_begruendung is not None


@pytest.mark.parametrize("abweichung, fehlt", [
    ({"akzeptanz_begruendung": None}, "akzeptanz_begruendung"),
    ({"akzeptanz_begruendung": "zu kurz"}, "akzeptanz_begruendung"),
    ({"frist": None}, "frist"),
    ({"freigabe": None}, "freigabe"),
    ({"freigabe": "Claude"}, "freigabe"),
])
def test_akzeptiert_braucht_begruendung_frist_und_andres_freigabe(tmp_path, abweichung, fehlt):
    with pytest.raises(NachweisFehler, match=fehlt):
        lade_befunde(_datei(tmp_path, _befund(**{**AKZEPTIERT, **abweichung})))


@pytest.mark.parametrize("feld, wert", [
    ("akzeptanz_begruendung", "Begründung mit ausreichend Länge"),
    ("frist", date(2026, 12, 31)),
    ("freigabe", "Andre"),
])
def test_akzeptanzfelder_nur_bei_akzeptiert(tmp_path, feld, wert):
    with pytest.raises(NachweisFehler, match=feld):
        lade_befunde(_datei(tmp_path, _befund(status="offen", **{feld: wert})))


def test_offen_darf_fix_commit_ohne_retest_tragen(tmp_path):
    # z. B. Härtung eingespielt, aber noch ohne automatisierten Retest → bleibt offen.
    b = lade_befunde(_datei(tmp_path, _befund(status="offen", retest=None)))[0]
    assert b.fix_commit == "3ee2ca6" and b.retest is None


@pytest.mark.parametrize("feld, wert", [
    ("schwere", "unbekannt"), ("schwere", "Hoch"), ("kategorie", "netz"),
    ("status", "erledigt"),
])
def test_unbekannte_werte(tmp_path, feld, wert):
    with pytest.raises(NachweisFehler, match=feld):
        lade_befunde(_datei(tmp_path, _befund(**{feld: wert})))


@pytest.mark.parametrize("id_", ["WB-M1", "B-", "b-wb-m1", "B-WB M1", "B-WÄ"])
def test_ungueltige_id(tmp_path, id_):
    with pytest.raises(NachweisFehler, match="ID"):
        lade_befunde(_datei(tmp_path, _befund(id=id_)))


@pytest.mark.parametrize("fix", ["3ee2ca", "xyz1234", "3EE2CA6", "3ee2ca6 45775f2"])
def test_ungueltiger_fix_commit(tmp_path, fix):
    with pytest.raises(NachweisFehler, match="fix_commit"):
        lade_befunde(_datei(tmp_path, _befund(fix_commit=fix)))


def test_mehrere_fix_commits(tmp_path):
    b = lade_befunde(_datei(tmp_path, _befund(fix_commit="3ee2ca6+45775f2")))[0]
    assert b.fix_commit == "3ee2ca6+45775f2"


@pytest.mark.parametrize("retest", [
    "test_m1_x", "server/tests/test_x.py", "server/tests/test_x.py::",
    "deploy/platform/test/caddy-pruefen.sh", "/abs/tests/test_x.py::test_y",
    "server/../tests/test_x.py::test_y",
])
def test_ungueltiger_retest(tmp_path, retest):
    with pytest.raises(NachweisFehler, match="retest"):
        lade_befunde(_datei(tmp_path, _befund(retest=retest)))


@pytest.mark.parametrize("retest", [
    "server/tests/test_sicherheit.py::test_m1_x",
    "deploy/platform/test/caddy-pruefen.sh 3e",
    "web/tests/portal-api-zugaenge.test.ts::EINWILLIGUNG_TEXT (Teil E, N1)",
])
def test_gueltige_retest_formen(tmp_path, retest):
    assert lade_befunde(_datei(tmp_path, _befund(retest=retest)))[0].retest == retest


def test_entdeckt_muss_datum_sein(tmp_path):
    with pytest.raises(NachweisFehler, match="entdeckt"):
        lade_befunde(_datei(tmp_path, _befund(entdeckt="2026-09-27")))


def test_staende_nicht_leer(tmp_path):
    with pytest.raises(NachweisFehler, match="staende"):
        lade_befunde(_datei(tmp_path, _befund(staende=[])))


def test_unbekanntes_feld(tmp_path):
    with pytest.raises(NachweisFehler, match="fixcommit"):
        lade_befunde(_datei(tmp_path, _befund(fixcommit="3ee2ca6")))


def test_leeres_register(tmp_path):
    with pytest.raises(NachweisFehler, match="keine"):
        lade_befunde(_datei(tmp_path, "# leer\n"))


# --- lade_negativtests ----------------------------------------------------------------------------


def _kategorie(**abweichung: object) -> str:
    felder: dict[str, object] = {
        "id": "NT-RECHTE", "titel": "Rechteprüfung je Rolle", "bezug": ["K-CRA-I-D"],
        "instanz": False, "tests": ["server/tests/test_authz.py::test_admin_sieht_alles"],
    }
    felder.update(abweichung)
    zeilen = ["[[kategorie]]"]
    for name, wert in felder.items():
        if wert is None:
            continue
        if isinstance(wert, bool):
            zeilen.append(f"{name} = {'true' if wert else 'false'}")
        elif isinstance(wert, list):
            zeilen.append(f"{name} = [" + ", ".join(f'"{w}"' for w in wert) + "]")
        else:
            zeilen.append(f'{name} = "{wert}"')
    return "\n".join(zeilen) + "\n"


def test_negativtests_laden(tmp_path):
    k = lade_negativtests(_datei(tmp_path, _kategorie(), _kategorie(
        id="NT-FORWARD-AUTH", instanz=True, tests=["deploy/platform/test/caddy-pruefen.sh 6b"])))
    assert [x.id for x in k] == ["NT-RECHTE", "NT-FORWARD-AUTH"]
    assert k[0].tests == ("server/tests/test_authz.py::test_admin_sieht_alles",)
    assert k[1].instanz is True and k[0].bezug == ("K-CRA-I-D",)


@pytest.mark.parametrize("abweichung, text", [
    ({"tests": []}, "Kategorie NT-RECHTE ohne Test"),
    ({"id": "RECHTE"}, "ID"),
    ({"bezug": []}, "bezug"),
    ({"instanz": "nein"}, "instanz"),
    ({"extra": "x"}, "extra"),
    # Nicht-Instanz-Kategorien laufen per pytest; eine Instanzprüfung gehört dort nicht hin.
    ({"tests": ["deploy/platform/test/caddy-pruefen.sh 6b"]}, "pytest"),
    # Instanzkategorien verweisen nur auf Punkte von caddy-pruefen.sh.
    ({"instanz": True}, "caddy-pruefen"),
])
def test_negativtests_ungueltig(tmp_path, abweichung, text):
    with pytest.raises(NachweisFehler, match=text) as e:
        lade_negativtests(_datei(tmp_path, _kategorie(**abweichung)))
    assert e.value.regel == "negativtests"


def test_negativtests_doppelte_id(tmp_path):
    with pytest.raises(NachweisFehler, match="doppelt"):
        lade_negativtests(_datei(tmp_path, _kategorie(), _kategorie()))


# --- Querprüfungen --------------------------------------------------------------------------------


def test_pruefe_verweise(tmp_path):
    (tmp_path / "server" / "tests").mkdir(parents=True)
    (tmp_path / "server" / "tests" / "test_a.py").write_text(
        "async def test_a():\n    pass\n", encoding="utf-8")
    (tmp_path / "web" / "tests").mkdir(parents=True)
    (tmp_path / "web" / "tests" / "x.test.ts").write_text(
        "describe('Gruppe (N1)', () => {});\n", encoding="utf-8")
    pruefe_verweise(tmp_path, ["server/tests/test_a.py::test_a",
                               "web/tests/x.test.ts::Gruppe (N1)"], "t")
    for kaputt in ["server/tests/test_a.py::test_b", "server/tests/test_c.py::test_a",
                   "web/tests/x.test.ts::Gruppe", "deploy/platform/test/caddy-pruefen.sh 1"]:
        with pytest.raises(NachweisFehler, match=re.escape(kaputt)):
            pruefe_verweise(tmp_path, [kaputt], "t")


def test_pruefe_bezuege(tmp_path):
    befunde = lade_befunde(_datei(tmp_path, _befund()))
    kategorien = lade_negativtests(_datei(tmp_path, _kategorie(bezug=["K-CRA-I-D", "B-WB-M1"]),
                                          name="n.toml"))
    pruefe_bezuege(kategorien, befunde, {"K-CRA-I-D"})
    with pytest.raises(NachweisFehler, match="B-WB-M9"):
        pruefe_bezuege(lade_negativtests(_datei(tmp_path, _kategorie(bezug=["B-WB-M9"]),
                                                name="n2.toml")), befunde, {"K-CRA-I-D"})


# --- Echte Register (im privaten Repo; im öffentlichen Export nicht enthalten) -------------------

_echt = pytest.mark.skipif(
    not ECHTE_BEFUNDE.exists(),
    reason="Compliance-Register liegen nur im privaten Repo (nicht Teil des öffentlichen Exports)",
)

NT_KATEGORIEN = ["NT-RECHTE", "NT-TRENNUNG", "NT-PROJEKTLEITUNG", "NT-TOKEN", "NT-EINLADUNG",
                 "NT-PFADE", "NT-FORWARD-AUTH", "NT-DROSSEL", "NT-HEADER", "NT-FREISCHALTEN",
                 "NT-EINRICHTEN", "NT-LESEPROTOKOLL", "NT-FADEN", "NT-KI-VORSCHLAG"]


@_echt
def test_echte_negativtests_acht_kategorien_und_verweise_existieren():
    k = lade_negativtests(ECHTE_NEGATIVTESTS)
    assert [x.id for x in k] == NT_KATEGORIEN
    assert [x.id for x in k if x.instanz] == ["NT-FORWARD-AUTH"]
    pruefe_verweise(WURZEL, [t for x in k for t in x.tests], "negativtests")
    pruefe_bezuege(k, lade_befunde(ECHTE_BEFUNDE),
                   {x.id for x in lade_matrix(COMPLIANCE / "kontrollmatrix.toml")})


@_echt
def test_echte_befunde_retests_und_fix_commits_existieren():
    befunde = lade_befunde(ECHTE_BEFUNDE)
    pruefe_verweise(WURZEL, [b.retest for b in befunde if b.retest], "befunde")
    commits = {c for b in befunde if b.fix_commit for c in b.fix_commit.split("+")}
    for c in sorted(commits):
        r = subprocess.run(["git", "cat-file", "-e", f"{c}^{{commit}}"], cwd=WURZEL,
                           capture_output=True, check=False)
        assert r.returncode == 0, f"Fix-Commit {c} nicht im Repo"
        r = subprocess.run(["git", "merge-base", "--is-ancestor", c, "HEAD"], cwd=WURZEL,
                           capture_output=True, check=False)
        assert r.returncode == 0, f"Fix-Commit {c} nicht in HEAD enthalten"


@_echt
def test_echte_befunde_alle_kennungen_der_quellberichte_uebernommen():
    """Zählprobe: jede Kennung aus den Audit-/Review-Berichten steckt als ID oder
    als Alias in `quelle` im Register — kein Befund geht verloren."""
    befunde = lade_befunde(ECHTE_BEFUNDE)
    ids = {b.id for b in befunde}
    security = WURZEL / "docs" / "security"
    muster = {
        "2026-09-27-audit-code-whitebox.md": ("WB", [r"^### ([MN]\d+) —", r"^\| (I\d+) \|"]),
        "2026-09-27-audit-lab-blackbox.md": ("BB", [r"^#### ([MN]\d+) —",
                                                    r"^\| \*\*(I\d+)\*\* \|"]),
        "2026-09-27-review-teil-e.md": ("TE", [r"^\*\*([MN]\d+) —", r"^- \*\*(I\d+) —"]),
    }
    erwartet = {"WB": 22, "BB": 11, "TE": 19}
    for datei, (praefix, regexe) in muster.items():
        text = (security / datei).read_text(encoding="utf-8")
        kennungen = {m for r in regexe for m in re.findall(r, text, flags=re.M)}
        assert len(kennungen) == erwartet[praefix], (datei, sorted(kennungen))
        for k in kennungen:
            alias = f"{praefix}-{k}"
            assert f"B-{alias}" in ids or any(
                re.search(rf"\b{alias}\b", b.quelle) for b in befunde), alias
    # Die Fixwelle nennt nur Kennungen aus WB/BB — auch die müssen alle vorkommen.
    fix = (security / "2026-09-27-fixwelle.md").read_text(encoding="utf-8")
    for alias in set(re.findall(r"\b((?:WB|BB)-[MNI]\d+)\b", fix)):
        assert f"B-{alias}" in ids or any(
            re.search(rf"\b{alias}\b", b.quelle) for b in befunde), alias


@_echt
def test_jeder_freischalt_probetest_ist_negativtest():
    """Jeder Laufzeittest des Freischalt-Skripts (`@probe_da`) steht in
    NT-FREISCHALTEN — so blockiert ein Überspringen (fehlende Probe-Werkzeuge) das Release."""
    quelle = (WURZEL / "nachweise/tests/test_paket_freischalten.py").read_text(encoding="utf-8")
    probe = set(re.findall(r"^@probe_da\n(?:@.*\n)*def (\w+)\(", quelle, flags=re.M))
    assert len(probe) >= 22
    k = {x.id: x for x in lade_negativtests(ECHTE_NEGATIVTESTS)}["NT-FREISCHALTEN"]
    assert not k.instanz
    # Dazu die visudo-Prüfung der sudoers-Vorlage: übersprungen (visudo fehlt) ist im Release rot.
    assert {t.split("::")[1] for t in k.tests} == probe | {
        "test_sudoers_vorlage_syntaktisch_gueltig"}


@_echt
def test_jeder_einrichten_probetest_ist_negativtest():
    """Wie NT-FREISCHALTEN: die Laufzeittests von einrichten.sh dürfen im Release nicht
    übersprungen durchgehen."""
    quelle = (WURZEL / "nachweise/tests/test_einrichten_probe.py").read_text(encoding="utf-8")
    probe = set(re.findall(r"^@probe_da\n(?:@.*\n)*def (\w+)\(", quelle, flags=re.M))
    assert len(probe) >= 5
    k = {x.id: x for x in lade_negativtests(ECHTE_NEGATIVTESTS)}["NT-EINRICHTEN"]
    assert {t.split("::")[1] for t in k.tests} == probe
