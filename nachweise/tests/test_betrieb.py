"""Update-Protokoll der Instanzen gegen die signierten Manifeste im Archiv auswerten und die
unterstützten Versionen festhalten."""

from __future__ import annotations

import hashlib
import subprocess
import tomllib
from datetime import date
from pathlib import Path

import pytest
import signierhilfe
from test_archiv import QUELLE, _manifest

from scanview_nachweise import archiv as archiv_modul
from scanview_nachweise import cli
from scanview_nachweise.betrieb import (
    aktueller_stand,
    lies_updates,
    pruefe_gegen_archiv,
    unterstuetzte_versionen,
)
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.manifest import kanonisch

FIXTURES = Path(__file__).parent / "fixtures" / "updates"
HEUTE = date(2026, 9, 28)


def _schluessel(ordner: Path, name: str = "k") -> tuple[Path, Path]:
    ordner.mkdir(parents=True, exist_ok=True)
    key = ordner / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "t", "-f", str(key)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    typ, wert = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    signers = ordner / f"{name}-allowed_signers"
    signers.write_text(f'release@scanview namespaces="scanview-release" {typ} {wert}\n',
                       encoding="utf-8")
    return key, signers


def _release(archiv: Path, version: str, key: Path) -> Path:
    ordner = archiv / "releases" / version
    ordner.mkdir(parents=True)
    manifest = ordner / "manifest.json"
    manifest.write_bytes(kanonisch(_manifest(version)))
    subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", "scanview-release",
                    str(manifest)], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return manifest


def _zeile(manifest: Path, version: str = "0.1.0", *, quellcommit: str = QUELLE,
           vertauscht: bool = False, ergebnis: str = "ok") -> str:
    daten = _manifest(version)
    paare = {a["name"]: a["sha256"] for a in daten["artefakte"]}  # type: ignore[attr-defined]
    if vertauscht:
        paare["web"], paare["server"] = paare["server"], paare["web"]
    sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    arte = ",".join(f"{n}:{h}" for n, h in paare.items())
    return (f"2026-09-28T15:07:08Z {version} {sha} {quellcommit} "
            f"sicherung=/var/backups/scanview/update-sicherungen/x.db artefakte={arte} "
            f"ergebnis={ergebnis}\n")


@pytest.fixture
def lager(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Archiv-Ordner (ohne Git) mit signiertem Release 0.1.0; Rückgabe (Archiv, Manifest,
    allowed_signers)."""
    key, signers = _schluessel(tmp_path / "schluessel")
    archiv = tmp_path / "archiv"
    return archiv, _release(archiv, "0.1.0", key), signers


def test_gueltige_zeile_ohne_abweichung(lager):
    archiv, manifest, signers = lager
    zeilen = lies_updates(_zeile(manifest))
    assert pruefe_gegen_archiv(zeilen, archiv, signers) == []
    assert aktueller_stand(zeilen) == "0.1.0"


def test_artefakt_hash_vertauscht(lager):
    archiv, manifest, signers = lager
    zeilen = lies_updates(_zeile(manifest, vertauscht=True))
    abweichungen = pruefe_gegen_archiv(zeilen, archiv, signers)
    assert len(abweichungen) == 2
    assert "Artefakt server" in abweichungen[0] and "Artefakt web" in abweichungen[1]


def test_quellcommit_weicht_ab(lager):
    archiv, manifest, signers = lager
    zeilen = lies_updates(_zeile(manifest, quellcommit="e" * 40))
    assert any("Quellcommit" in a for a in pruefe_gegen_archiv(zeilen, archiv, signers))


def test_manifest_hash_weicht_ab(lager):
    archiv, manifest, signers = lager
    text = _zeile(manifest).replace(hashlib.sha256(manifest.read_bytes()).hexdigest(), "0" * 64)
    abweichungen = pruefe_gegen_archiv(lies_updates(text), archiv, signers)
    assert any("Manifest" in a for a in abweichungen)


def test_version_nicht_im_archiv(lager):
    archiv, manifest, signers = lager
    text = _zeile(manifest).replace(" 0.1.0 ", " 9.9.9 ")
    abweichungen = pruefe_gegen_archiv(lies_updates(text), archiv, signers)
    assert any("9.9.9" in a and "Archiv" in a for a in abweichungen)


def test_manifest_signatur_falsch(lager, tmp_path):
    archiv, manifest, _signers = lager
    _fremd, fremde_signers = _schluessel(tmp_path / "fremd")
    with pytest.raises(NachweisFehler) as e:
        pruefe_gegen_archiv(lies_updates(_zeile(manifest)), archiv, fremde_signers)
    assert e.value.regel == "signatur"


def test_repo_modus_ist_abweichung(lager):
    archiv, _manifest_pfad, signers = lager
    text = (FIXTURES / "gemischt.log").read_text(encoding="utf-8")
    zeilen = lies_updates(text.splitlines()[4] + "\n")
    abweichungen = pruefe_gegen_archiv(zeilen, archiv, signers)
    assert abweichungen == ["Instanz läuft ohne geprüftes Release (2026-09-23T10:00:00Z)"]
    assert aktueller_stand(zeilen) is None


def test_abgelehnt_und_abgebrochen_sind_keine_abweichung(lager):
    archiv, _m, signers = lager
    zeilen = lies_updates("\n".join(
        (FIXTURES / "gemischt.log").read_text(encoding="utf-8").splitlines()[:2]) + "\n")
    assert [z.ergebnis for z in zeilen] == ["abgebrochen", "abgelehnt"]
    assert zeilen[1].grund == "signatur"
    assert pruefe_gegen_archiv(zeilen, archiv, signers) == []
    assert aktueller_stand(zeilen) is None


def test_gemischtes_protokoll_liest_alles(lager):
    zeilen = lies_updates((FIXTURES / "gemischt.log").read_text(encoding="utf-8"))
    assert [z.ergebnis for z in zeilen] == ["abgebrochen", "abgelehnt", "ok", "ok"]
    assert [z.version for z in zeilen] == ["0.0.9", "-", "0.1.0", "ungeprüft"]
    assert sorted(zeilen[2].artefakte) == ["mcp", "server", "web"]
    assert aktueller_stand(zeilen[:3]) == "0.1.0"
    assert aktueller_stand(zeilen) is None  # zuletzt lief die Instanz ohne geprüftes Release


@pytest.mark.parametrize("datei", ["kaputt.log", "ergebnis-unbekannt.log"])
def test_kaputte_zeile(datei):
    with pytest.raises(NachweisFehler) as e:
        lies_updates((FIXTURES / datei).read_text(encoding="utf-8"))
    assert e.value.regel == "updates-format"
    assert "Zeile 1" in e.value.text


def test_leeres_protokoll():
    assert lies_updates("") == []
    assert aktueller_stand([]) is None


def test_unterstuetzt_leer(tmp_path):
    (tmp_path / "releases").mkdir()
    with pytest.raises(NachweisFehler) as e:
        unterstuetzte_versionen(tmp_path, {"L": None, "S": None})
    assert e.value.regel == "unterstuetzt-leer"


def _versionen_anlegen(archiv: Path, *versionen: str) -> None:
    for v in versionen:
        (archiv / "releases" / v).mkdir(parents=True)
        (archiv / "releases" / v / "manifest.json").write_text("{}", encoding="utf-8")


def test_unterstuetzt_neueste_und_stand(tmp_path):
    _versionen_anlegen(tmp_path, "0.9.0", "0.10.0", "0.10.0-rc1", "0.2.0")
    assert unterstuetzte_versionen(tmp_path, {"L": "0.2.0", "S": None}) == ["0.10.0", "0.2.0"]
    assert unterstuetzte_versionen(tmp_path, {"L": "0.10.0", "S": "0.9.0"}) == ["0.10.0", "0.9.0"]


# --- CLI -------------------------------------------------------------------------------------


@pytest.fixture
def cli_archiv(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Signiertes Archiv-Repo (Vertrauensanker) mit committetem Release 0.1.0."""
    key, signers = _schluessel(tmp_path / "release-schluessel")
    archiv = signierhilfe.signiertes_archiv(tmp_path / "archiv")
    manifest = _release(archiv, "0.1.0", key)
    signierhilfe.git(archiv, "add", "releases")
    signierhilfe.git(archiv, "commit", "-q", "-S", "-m", "release")
    return archiv, manifest, signers


def _lauf(archiv: Path, signers: Path, protokoll: Path, instanz: str = "L") -> int:
    return cli.main(["betrieb", "updates", "--instanz", instanz, "--protokoll", str(protokoll),
                     "--archiv", str(archiv), "--allowed-signers", str(signers),
                     "--heute", HEUTE.isoformat()])


def test_cli_schreibt_pruefung_und_unterstuetzt(cli_archiv, tmp_path, capsys):
    archiv, manifest, signers = cli_archiv
    protokoll = tmp_path / "updates.log"
    protokoll.write_text(_zeile(manifest), encoding="utf-8")
    assert _lauf(archiv, signers, protokoll) == 0
    ziel = archiv / "pruefungen" / "2026-09-28-updates-L"
    assert (ziel / "updates.log").read_bytes() == protokoll.read_bytes()
    assert b'"abweichungen":[]' in (ziel / "ergebnis.json").read_bytes()
    toml = tomllib.loads((archiv / "betrieb" / "unterstuetzt.toml").read_text(encoding="utf-8"))
    assert toml == {"versionen": ["0.1.0"], "stand": {"L": "0.1.0", "S": "nicht ausgewertet"},
                    "aktualisiert": HEUTE}
    assert signierhilfe.git(archiv, "status", "--porcelain") == ""
    assert "nicht ausgewertet" in capsys.readouterr().out


def test_cli_abweichung_exit_1(cli_archiv, tmp_path):
    archiv, manifest, signers = cli_archiv
    protokoll = tmp_path / "updates.log"
    protokoll.write_text(_zeile(manifest, vertauscht=True), encoding="utf-8")
    assert _lauf(archiv, signers, protokoll) == 1
    assert (archiv / "pruefungen" / "2026-09-28-updates-L" / "ergebnis.json").is_file()


def test_cli_zweiter_lauf_behaelt_andere_instanz(cli_archiv, tmp_path):
    archiv, manifest, signers = cli_archiv
    protokoll = tmp_path / "updates.log"
    protokoll.write_text(_zeile(manifest), encoding="utf-8")
    assert _lauf(archiv, signers, protokoll, "S") == 0
    signierhilfe.git(archiv, "log", "-1")
    toml = tomllib.loads((archiv / "betrieb" / "unterstuetzt.toml").read_text(encoding="utf-8"))
    assert toml["stand"] == {"L": "nicht ausgewertet", "S": "0.1.0"}


def test_cli_zweiter_lauf_am_selben_tag(cli_archiv, tmp_path, capsys):
    """Ein zweiter Lauf am selben Tag bekommt die nächste Laufnummer; der erste Nachweis bleibt
    stehen, und eine unveränderte Betriebsdatei bricht den Lauf nicht ab."""
    archiv, manifest, signers = cli_archiv
    protokoll = tmp_path / "updates.log"
    protokoll.write_text(_zeile(manifest), encoding="utf-8")
    assert _lauf(archiv, signers, protokoll) == 0
    assert _lauf(archiv, signers, protokoll) == 0, capsys.readouterr().err
    namen = sorted(p.name for p in (archiv / "pruefungen").iterdir())
    assert len(namen) == 2 and namen[1].split("-updates-")[0].endswith(".2")
    assert signierhilfe.git(archiv, "status", "--porcelain") == ""


def test_cli_leeres_protokoll_und_leeres_archiv(tmp_path, capsys):
    archiv = signierhilfe.signiertes_archiv(tmp_path / "archiv")
    _key, signers = _schluessel(tmp_path / "s")
    protokoll = tmp_path / "updates.log"
    protokoll.write_text("", encoding="utf-8")
    assert _lauf(archiv, signers, protokoll) == 2
    assert "unterstuetzt-leer" in capsys.readouterr().err
    assert not (archiv / "pruefungen").exists()


@pytest.mark.parametrize("vorab", [("rc2", "rc10"), ("rc.2", "rc.10"), ("alpha.1", "alpha.1.1"),
                                   ("1", "alpha"), ("beta", "rc")])
def test_vorabversionen_werden_numerisch_verglichen(tmp_path, vorab):
    kleiner, groesser = vorab
    _versionen_anlegen(tmp_path, f"1.0.0-{groesser}", f"1.0.0-{kleiner}")
    assert unterstuetzte_versionen(tmp_path, {"L": None, "S": None}) == [f"1.0.0-{groesser}"]


def test_release_ist_neuer_als_seine_vorabversion(tmp_path):
    _versionen_anlegen(tmp_path, "1.0.0-rc10", "1.0.0")
    assert unterstuetzte_versionen(tmp_path, {"L": None, "S": None}) == ["1.0.0"]


def test_ordner_ohne_manifest_ist_keine_version(tmp_path):
    _versionen_anlegen(tmp_path, "0.1.0")
    (tmp_path / "releases" / "0.2.0").mkdir()
    assert unterstuetzte_versionen(tmp_path, {"L": None, "S": None}) == ["0.1.0"]
    (tmp_path / "releases" / "0.1.0" / "manifest.json").unlink()
    with pytest.raises(NachweisFehler) as e:
        unterstuetzte_versionen(tmp_path, {"L": None, "S": None})
    assert e.value.regel == "unterstuetzt-leer"


def test_cli_nicht_ausgewertet_ueberschreibt_vorhandenen_stand_nicht(cli_archiv, tmp_path):
    archiv, manifest, signers = cli_archiv
    protokoll = tmp_path / "updates.log"
    protokoll.write_text(_zeile(manifest), encoding="utf-8")
    assert _lauf(archiv, signers, protokoll) == 0
    leer = tmp_path / "leer.log"
    leer.write_text("", encoding="utf-8")
    assert cli.main(["betrieb", "updates", "--instanz", "L", "--protokoll", str(leer),
                     "--archiv", str(archiv), "--allowed-signers", str(signers),
                     "--heute", "2026-10-05"]) == 0
    toml = tomllib.loads((archiv / "betrieb" / "unterstuetzt.toml").read_text(encoding="utf-8"))
    assert toml["stand"] == {"L": "0.1.0", "S": "nicht ausgewertet"}
    assert toml["aktualisiert"] == date(2026, 10, 5)
    ergebnis = (archiv / "pruefungen" / "2026-10-05-updates-L" / "ergebnis.json").read_text(
        encoding="utf-8")
    assert "nicht ausgewertet" in ergebnis  # der Nachweis des Laufs sagt, was er ausgewertet hat


def test_schreibe_betrieb_unveraendert_macht_keinen_commit(tmp_path):
    archiv = signierhilfe.signiertes_archiv(tmp_path / "archiv")
    inhalt = b'versionen = ["0.1.0"]\n'
    ziel = archiv_modul.schreibe_betrieb(archiv, "unterstuetzt.toml", inhalt, "betrieb: erster")
    kopf = signierhilfe.git(archiv, "rev-parse", "HEAD")
    assert ziel.read_bytes() == inhalt
    assert archiv_modul.schreibe_betrieb(archiv, "unterstuetzt.toml", inhalt,
                                         "betrieb: zweiter") == ziel
    assert signierhilfe.git(archiv, "rev-parse", "HEAD") == kopf
    assert signierhilfe.git(archiv, "status", "--porcelain") == ""
    geaendert = b'versionen = ["0.2.0"]\n'
    archiv_modul.schreibe_betrieb(archiv, "unterstuetzt.toml", geaendert, "betrieb: dritter")
    assert signierhilfe.git(archiv, "rev-parse", "HEAD") != kopf
    assert ziel.read_bytes() == geaendert


def test_cli_repo_modus_ersetzt_den_bisherigen_stand(cli_archiv, tmp_path):
    """Läuft die Instanz zuletzt im Repo-Modus, ist das eine Auswertung: der frühere geprüfte
    Stand gilt nicht mehr und die Datei sagt „nicht ausgewertet“ statt 0.1.0."""
    archiv, manifest, signers = cli_archiv
    protokoll = tmp_path / "updates.log"
    protokoll.write_text(_zeile(manifest), encoding="utf-8")
    assert _lauf(archiv, signers, protokoll) == 0
    repo = tmp_path / "repo.log"
    repo.write_text("2026-10-05T10:00:00Z ungeprüft " + "0" * 64 + " " + "1" * 40
                    + " sicherung=/x.db artefakte=web:" + "2" * 64 + " ergebnis=ok\n",
                    encoding="utf-8")
    assert cli.main(["betrieb", "updates", "--instanz", "L", "--protokoll", str(repo),
                     "--archiv", str(archiv), "--allowed-signers", str(signers),
                     "--heute", "2026-10-05"]) == 1  # Repo-Modus ist eine Abweichung
    toml = tomllib.loads((archiv / "betrieb" / "unterstuetzt.toml").read_text(encoding="utf-8"))
    assert toml["stand"] == {"L": "nicht ausgewertet", "S": "nicht ausgewertet"}


def test_warnkennung_hinter_ok_zeile_wird_gelesen():
    """einrichten.sh hängt bei fehlenden Barrierefreiheits-Angaben `warnung=<kennung>` an die
    ok-Zeile; sie bricht die Auswertung nicht und ändert nichts an der Zeile."""
    zeile = ("2026-10-01T10:00:00Z 0.2.0 " + "1" * 64 + " " + "a" * 40
             + " sicherung=/x.db artefakte=web:" + "2" * 64 + " ergebnis=ok"
             + " warnung=barrierefreiheit-angaben-fehlen\n")
    [z] = lies_updates(zeile)
    assert z.ergebnis == "ok" and z.version == "0.2.0"
    with pytest.raises(NachweisFehler):
        lies_updates(zeile.replace("warnung=", "unbekannt="))


# --- Formattreue gegen einrichten.sh (alle HINWEIS-Varianten) ------------------------------------

EINRICHTEN = Path(__file__).parents[2] / "deploy" / "platform" / "einrichten.sh"
_KOPF = ("2026-10-01T10:00:00Z {version} {manifest} {commit} sicherung={sicherung} "
         "artefakte={artefakte} ergebnis={ergebnis}{hinweis}")
_OK = {"version": "0.2.0", "manifest": "1" * 64, "commit": "a" * 40, "sicherung": "/x.db",
       "artefakte": "web:" + "2" * 64}
_REPO = {"version": "ungeprüft", "manifest": "-", "commit": "a" * 40, "sicherung": "/x.db",
         "artefakte": "-"}
_LEER = {"version": "-", "manifest": "-", "commit": "-", "sicherung": "-", "artefakte": "-"}
_WARNUNG_REPO = " WARNUNG: kein geprüftes Release quelle=/srv/a b/scanview"


def test_einrichten_kennt_genau_diese_hinweise():
    """Bricht, sobald einrichten.sh einen neuen Hinweis schreibt — dann gehört er in den
    Formattest unten und in `lies_updates`."""
    import re
    text = EINRICHTEN.read_text(encoding="utf-8")
    assert "printf '%s %s %s %s sicherung=%s artefakte=%s ergebnis=%s%s\\n'" in text
    zuweisungen = sorted(set(re.findall(r'HINWEIS="([^"]*)"', text)))
    assert zuweisungen == sorted({
        " grund=$grund", " WARNUNG: kein geprüftes Release quelle=$REPO",
        "$HINWEIS ersteinrichtung=fortgesetzt", " grund=db-fehlt$HINWEIS",
        "$HINWEIS warnung=barrierefreiheit-angaben-fehlen",
        "$HINWEIS warnung=barrierefreiheit-nicht-pruefbar",
        "$HINWEIS warnung=impressum-datenschutz-fehlen"})


@pytest.mark.parametrize(("felder", "ergebnis", "hinweis", "zusaetze"), [
    (_OK, "ok", "", ()),
    (_OK, "ok", " warnung=barrierefreiheit-angaben-fehlen",
     ("warnung=barrierefreiheit-angaben-fehlen",)),
    (_OK, "ok", " ersteinrichtung=fortgesetzt", ("ersteinrichtung=fortgesetzt",)),
    (_OK, "ok", " ersteinrichtung=fortgesetzt warnung=barrierefreiheit-nicht-pruefbar",
     ("ersteinrichtung=fortgesetzt", "warnung=barrierefreiheit-nicht-pruefbar")),
    (_REPO, "ok", _WARNUNG_REPO, ("ohne geprüftes Release (quelle=/srv/a b/scanview)",)),
    (_REPO, "ok", _WARNUNG_REPO + " ersteinrichtung=fortgesetzt warnung=barrierefreiheit-"
     "angaben-fehlen", ("ohne geprüftes Release (quelle=/srv/a b/scanview)",
                        "ersteinrichtung=fortgesetzt", "warnung=barrierefreiheit-angaben-fehlen")),
    (_OK, "ok", " warnung=impressum-datenschutz-fehlen", ("warnung=impressum-datenschutz-fehlen",)),
    # Beide Warnungen in der Reihenfolge, in der einrichten.sh sie anhängt.
    (_OK, "ok", " warnung=barrierefreiheit-angaben-fehlen warnung=impressum-datenschutz-fehlen",
     ("warnung=barrierefreiheit-angaben-fehlen", "warnung=impressum-datenschutz-fehlen")),
    (_REPO, "ok", _WARNUNG_REPO + " ersteinrichtung=fortgesetzt warnung=barrierefreiheit-"
     "nicht-pruefbar warnung=impressum-datenschutz-fehlen",
     ("ohne geprüftes Release (quelle=/srv/a b/scanview)", "ersteinrichtung=fortgesetzt",
      "warnung=barrierefreiheit-nicht-pruefbar", "warnung=impressum-datenschutz-fehlen")),
    (_OK, "abgebrochen", "", ()),
    (_OK, "abgebrochen", " grund=db-fehlt", ()),
    (_REPO, "abgebrochen", " grund=db-fehlt" + _WARNUNG_REPO,
     ("ohne geprüftes Release (quelle=/srv/a b/scanview)",)),
    (_OK, "abgebrochen", " ersteinrichtung=fortgesetzt", ("ersteinrichtung=fortgesetzt",)),
    (_LEER, "abgelehnt", " grund=signatur", ()),
])
def test_alle_zeilen_aus_einrichten_werden_gelesen(felder, ergebnis, hinweis, zusaetze):
    zeile = _KOPF.format(**felder, ergebnis=ergebnis, hinweis=hinweis)
    [z] = lies_updates(zeile + "\n")
    assert z.ergebnis == ergebnis and z.version == felder["version"]
    assert z.grund == ("db-fehlt" if "db-fehlt" in hinweis else
                       "signatur" if ergebnis == "abgelehnt" else None)
    assert z.zusaetze == zusaetze


@pytest.mark.parametrize(("felder", "ergebnis", "hinweis", "meldung"), [
    (_OK, "ok", " grund=db-fehlt", "grund="),
    (_LEER, "abgelehnt", "", "ohne grund="),
    (_OK, "ok", _WARNUNG_REPO, "nur im Repo-Modus"),
    (_LEER, "abgelehnt", " grund=signatur ersteinrichtung=fortgesetzt", "abgelehnt"),
    (_OK, "ok", " warnung=Gross", "Protokollformat"),
    (_OK, "ok", " warnung=a ersteinrichtung=fortgesetzt", "Protokollformat"),
    (_OK, "ok", " warnung=a warnung=b ersteinrichtung=fortgesetzt", "Protokollformat"),
    (_OK, "ok", " warnung=a  warnung=b", "Protokollformat"),
    (_OK, "ok", " warnung=", "Protokollformat"),
    (_LEER, "abgelehnt", " grund=signatur warnung=impressum-datenschutz-fehlen", "abgelehnt"),
    (_OK, "ok", " warnung=impressum-datenschutz-fehlen warnung=impressum-datenschutz-fehlen",
     "doppelt"),
])
def test_unstimmige_zusaetze_werden_abgelehnt(felder, ergebnis, hinweis, meldung):
    with pytest.raises(NachweisFehler, match=meldung) as e:
        lies_updates(_KOPF.format(**felder, ergebnis=ergebnis, hinweis=hinweis) + "\n")
    assert e.value.regel == "updates-format"


def test_zusaetze_stehen_in_den_hinweisen():
    from scanview_nachweise.betrieb import informative_zeilen
    zeilen = lies_updates(
        _KOPF.format(**_OK, ergebnis="ok", hinweis="") + "\n"
        + _KOPF.format(**_OK, ergebnis="ok", hinweis=" warnung=barrierefreiheit-angaben-fehlen")
        + "\n" + _KOPF.format(**_OK, ergebnis="abgebrochen", hinweis=" grund=db-fehlt") + "\n")
    assert informative_zeilen(zeilen) == [
        "2026-10-01T10:00:00Z: ok, Version 0.2.0 — warnung=barrierefreiheit-angaben-fehlen",
        "2026-10-01T10:00:00Z: abgebrochen (grund=db-fehlt), Version 0.2.0"]
