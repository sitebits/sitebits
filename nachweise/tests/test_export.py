"""Öffentlicher Export: Git-Blobs eines Commits, Positivliste mit `**`,
Hash-Manifest, bestätigter Release-Diff, Freigaben für Binärdateien, Begriffs- und
Geheimnisprüfung, Baubarkeit im Wegwerf-Abzug. Getestet gegen kleine Test-Repos; npm/uv/docker
werden über den austauschbaren Befehlsläufer von `export` ersetzt, gitleaks läuft zusätzlich
echt (Marker `docker`).

Alle Begriffe und Geheimnisse hier sind synthetisch und werden zur Laufzeit zusammengesetzt:
diese Datei liegt selbst im Export und wird von denselben Prüfungen gelesen."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from scanview_nachweise import cli, export
from scanview_nachweise.export import (
    Blob,
    Exportmanifest,
    Treffer,
    diff_hash,
    diff_manifeste,
    exportiere,
    git_blobs,
    glob_regex,
    kandidaten,
    lade_begriffe,
    lade_freigaben,
    lade_manifest,
    lies_positivliste,
    pruefe_baubarkeit,
    pruefe_begriffe,
    pruefe_binaer,
    pruefe_geheimnisse,
    schreibe_export,
)
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.positivliste import Positivliste
from scanview_nachweise.werkzeuge import lade_werkzeuge

REPO_WURZEL = Path(__file__).resolve().parents[2]
KOMMA_BILD = "web/public/a,b c.jpg"
# Synthetischer Begriff, bewusst nicht in der echten Begriffsliste.
BEGRIFF = "geheim" + "kunde"
# Echte JPEG-Signatur, damit die Datei auch inhaltlich binär ist.
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4


def _git(wurzel: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=wurzel, check=True, capture_output=True,
                          text=True).stdout


def _schreibe(wurzel: Path, dateien: dict[str, str | bytes]) -> None:
    for relativ, inhalt in dateien.items():
        pfad = wurzel / relativ
        pfad.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(inhalt, bytes):
            pfad.write_bytes(inhalt)
        else:
            pfad.write_text(inhalt, encoding="utf-8")


def _commit(wurzel: Path, nachricht: str = "stand") -> str:
    _git(wurzel, "add", "-A")
    _git(wurzel, "commit", "-q", "-m", nachricht)
    return _git(wurzel, "rev-parse", "HEAD").strip()


def _sha(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


POSITIVLISTE = """\
# Testliste
LICENSE
SECURITY.md
web/**
!web/e2e/**
server/**
mcp-server/**
import/**
nachweise/**
veroeffentlichung/positivliste.txt
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    wurzel = tmp_path / "repo"
    wurzel.mkdir()
    _git(wurzel, "init", "-q", "-b", "main")
    _git(wurzel, "config", "user.email", "test@example.invalid")
    _git(wurzel, "config", "user.name", "Test")
    _git(wurzel, "config", "commit.gpgsign", "false")
    _schreibe(wurzel, {
        "LICENSE": "EUPL\n",
        "SECURITY.md": "# Sicherheit\n\nMeldungen an security@example.org\n",
        "privat.md": "nicht öffentlich\n",
        "web/package.json": json.dumps({"name": "w", "allowScripts": {"esbuild": False,
                                                                      "bau-hilfe": True}}),
        "web/src/main.ts": "export {}\n",
        "web/e2e/instanz.spec.ts": "// spricht echte Instanzen an\n",
        "web/größen und, komma.ts": "export const x = 1;\n",
        "server/pyproject.toml": "[project]\nname='s'\n",
        "server/uv.lock": "version = 1\n",
        "server/run.sh": "#!/bin/sh\n",
        "mcp-server/pyproject.toml": "[project]\nname='m'\n",
        "mcp-server/uv.lock": "version = 1\n",
        "nachweise/pyproject.toml": "[project]\nname='n'\n",
        "nachweise/uv.lock": "version = 1\n",
        "import/pyproject.toml": "[project]\nname='i'\n",
        "import/uv.lock": "version = 1\n",
        "veroeffentlichung/positivliste.txt": POSITIVLISTE,
        "veroeffentlichung/freigaben.txt": "# keine Freigaben\n",
        ".gitleaks.toml": "title = 't'\n[extend]\nuseDefault = true\n",
    })
    (wurzel / "server/run.sh").chmod(0o755)
    _commit(wurzel)
    return wurzel


# --- git_blobs ---------------------------------------------------------------------------------

def test_git_blobs_liest_commit_nicht_arbeitsbaum(repo: Path) -> None:
    commit = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "LICENSE").write_text("im Arbeitsbaum geändert\n", encoding="utf-8")
    (repo / "neu.txt").write_text("ungetrackt\n", encoding="utf-8")
    blobs = {b.pfad: b for b in git_blobs(repo, commit)}
    assert "neu.txt" not in blobs
    assert "web/größen und, komma.ts" in blobs  # -z: Umlaut, Leerzeichen, Komma unverändert
    assert blobs["server/run.sh"].modus == "100755"
    lizenz = blobs["LICENSE"]
    assert (lizenz.modus, lizenz.typ) == ("100644", "blob")
    assert lizenz.hash == _git(repo, "rev-parse", f"{commit}:LICENSE").strip()


def test_git_blobs_unbekannter_commit(repo: Path) -> None:
    with pytest.raises(NachweisFehler) as e:
        git_blobs(repo, "0" * 40)
    assert e.value.regel == "export-commit"


def test_symlink_im_kandidatensatz_bricht_ab(repo: Path) -> None:
    (repo / "web/link.ts").symlink_to("src/main.ts")
    (repo / "privat-link").symlink_to("privat.md")
    commit = _commit(repo)
    blobs = git_blobs(repo, commit)
    # Außerhalb der Positivliste stört ein Symlink nicht …
    kandidaten(blobs, ["LICENSE"])
    # … im Kandidatensatz bricht er ab.
    with pytest.raises(NachweisFehler) as e:
        kandidaten(blobs, ["web/**"])
    assert e.value.regel == "export-symlink"


def test_submodul_im_kandidatensatz_bricht_ab(repo: Path) -> None:
    fremd = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{fremd},server/vendor")
    _git(repo, "commit", "-q", "-m", "submodul")
    blobs = git_blobs(repo, _git(repo, "rev-parse", "HEAD").strip())
    assert any(b.typ == "commit" for b in blobs)
    with pytest.raises(NachweisFehler) as e:
        kandidaten(blobs, ["server/**"])
    assert e.value.regel == "export-submodul"


# --- Positivliste ------------------------------------------------------------------------------

@pytest.mark.parametrize(("muster", "treffer", "kein_treffer"), [
    ("web/**", ["web/a.ts", "web/x/y/z.ts"], ["web", "webx/a.ts", "a/web/b.ts"]),
    ("**", ["a", "a/b/c"], []),
    ("**/README.md", ["README.md", "a/README.md", "a/b/README.md"], ["a/README.mdx"]),
    ("web/**/test.ts", ["web/test.ts", "web/a/b/test.ts"], ["web/atest.ts"]),
    ("web/*.ts", ["web/a.ts"], ["web/a/b.ts", "web/a.tsx"]),
    ("web/scripts/screenshot*", ["web/scripts/screenshot.mjs", "web/scripts/screenshot-a.mjs"],
     ["web/scripts/bau.mjs", "web/scripts/screenshot/x.mjs"]),
    ("docs/public/", ["docs/public/a.md", "docs/public/x/b.md"], ["docs/publicx.md"]),
    ("a?c.txt", ["abc.txt"], ["a/c.txt", "ac.txt"]),
    ("datei (1)+.txt", ["datei (1)+.txt"], ["datei 1.txt"]),
])
def test_glob_regex(muster: str, treffer: list[str], kein_treffer: list[str]) -> None:
    regex = glob_regex(muster)
    for pfad in treffer:
        assert regex.fullmatch(pfad), f"{muster} soll {pfad} treffen"
    for pfad in kein_treffer:
        assert not regex.fullmatch(pfad), f"{muster} soll {pfad} nicht treffen"


@pytest.mark.parametrize("muster", ["web/[ab].ts", "/web/**", "", "web/***"])
def test_glob_regex_ungueltig(muster: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        glob_regex(muster)
    assert e.value.regel == "export-positivliste"


def test_lies_positivliste_kommentare_und_leerzeilen() -> None:
    assert lies_positivliste("# Kopf\n\nweb/**\n  !web/e2e/**  # E2E\n") == [
        "web/**", "!web/e2e/**"]


def test_kandidaten_negation_und_letzte_regel_gewinnt(repo: Path) -> None:
    blobs = git_blobs(repo, "HEAD")
    pfade = [b.pfad for b in kandidaten(blobs, lies_positivliste(POSITIVLISTE))]
    assert "web/src/main.ts" in pfade
    assert "web/e2e/instanz.spec.ts" not in pfade
    assert "privat.md" not in pfade
    assert ".gitleaks.toml" not in pfade
    # Spätere Zeile hebt eine frühere Negation wieder auf (wie .gitignore).
    wieder = kandidaten(blobs, ["web/**", "!web/e2e/**", "web/e2e/instanz.spec.ts"])
    assert "web/e2e/instanz.spec.ts" in [b.pfad for b in wieder]
    assert pfade == sorted(pfade)


@pytest.mark.parametrize("notiz", [
    "HANDOFF.md", "HANDOFF-PLATTFORM.md", "INBOX.md", "web/INBOX-alt.md",
    "docs/superpowers/plans/x.md", ".superpowers/sdd/a.md", "web/.superpowers/b.md",
    "nachweise/stand.ledger.md", "server/x.ledger",
    # Groß/klein egal, „superpowers“ als Pfadteil überall.
    "handoff.md", "web/Inbox.txt", "docs/x.LEDGER.md", "web/superpowers/a.md",
    "notizen/SuperPowers/b.md", "a/.SUPERPOWERS/c",
])
def test_arbeitsnotizen_nie_oeffentlich(repo: Path, notiz: str) -> None:
    _schreibe(repo, {notiz: "Arbeitsnotiz\n"})
    blobs = git_blobs(repo, _commit(repo))
    with pytest.raises(NachweisFehler) as e:
        kandidaten(blobs, ["**"])
    assert e.value.regel == "export-arbeitsnotiz"
    assert notiz in e.value.text
    # Nicht erfasst → kein Fehler (die Regel greift nur, wenn die Liste sie aufnehmen würde).
    kandidaten(blobs, ["LICENSE"])


# --- schreibe_export / Manifest ----------------------------------------------------------------

def test_schreibe_export_inhalt_hash_und_modus(repo: Path, tmp_path: Path) -> None:
    blobs = kandidaten(git_blobs(repo, "HEAD"), lies_positivliste(POSITIVLISTE))
    ziel = tmp_path / "export"
    m = schreibe_export(repo, "HEAD", blobs, ziel)
    pfade = [e[0] for e in m.eintraege]
    assert pfade == sorted(pfade)
    assert "privat.md" not in pfade and not (ziel / "privat.md").exists()
    assert not (ziel / "web/e2e").exists()
    eintraege = m.hashes()
    inhalt = (ziel / "web/größen und, komma.ts").read_bytes()
    assert inhalt == b"export const x = 1;\n"
    assert eintraege["web/größen und, komma.ts"] == _sha(inhalt)
    assert (ziel / "server/run.sh").stat().st_mode & 0o111
    assert not (ziel / "LICENSE").stat().st_mode & 0o111
    modi = {e[0]: e[2] for e in m.eintraege}
    assert (modi["server/run.sh"], modi["LICENSE"]) == ("100755", "100644")
    # Nur Dateien des Manifests liegen im Ziel.
    auf_platte = sorted(str(p.relative_to(ziel)) for p in ziel.rglob("*") if p.is_file())
    assert auf_platte == pfade


def test_schreibe_export_ziel_muss_leer_sein(repo: Path, tmp_path: Path) -> None:
    blobs = kandidaten(git_blobs(repo, "HEAD"), ["LICENSE"])
    ziel = tmp_path / "export"
    ziel.mkdir()
    (ziel / "alt.txt").write_text("x", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        schreibe_export(repo, "HEAD", blobs, ziel)
    assert e.value.regel == "export-ziel"
    # Leeres, vorhandenes Verzeichnis ist erlaubt.
    leer = tmp_path / "leer"
    leer.mkdir()
    assert schreibe_export(repo, "HEAD", blobs, leer).eintraege[0][0] == "LICENSE"


def test_manifest_json_rundreise(tmp_path: Path) -> None:
    m = Exportmanifest([("a b,c.txt", "1" * 64, "100644"), ("z.txt", "2" * 64, "100755")])
    pfad = tmp_path / "m.json"
    pfad.write_text(m.als_json(), encoding="utf-8")
    assert lade_manifest(pfad) == m


def test_lade_manifest_ungueltig(tmp_path: Path) -> None:
    pfad = tmp_path / "m.json"
    for eintrag in ({"pfad": "a", "sha256": "kein-hash", "modus": "100644"},
                    {"pfad": "a", "sha256": "1" * 64},
                    {"pfad": "a", "sha256": "1" * 64, "modus": "120000"}):
        pfad.write_text(json.dumps({"dateien": [eintrag]}), encoding="utf-8")
        with pytest.raises(NachweisFehler) as e:
            lade_manifest(pfad)
        assert e.value.regel == "export-manifest"


# --- Diff --------------------------------------------------------------------------------------

def test_diff_manifeste_und_hash() -> None:
    n, x = "100644", "100755"
    alt = Exportmanifest([("a", "1" * 64, n), ("b", "2" * 64, n), ("c", "3" * 64, n),
                          ("e", "7" * 64, n)])
    neu = Exportmanifest([("a", "1" * 64, n), ("b", "4" * 64, n), ("d", "5" * 64, x),
                          ("e", "7" * 64, x)])
    d = diff_manifeste(alt, neu)
    assert d.neu == [("d", "5" * 64, x)]
    # Nur der Modus geändert (ausführbar geworden) zählt ebenfalls als Änderung.
    assert d.geaendert == [("b", "2" * 64, "4" * 64, n, n), ("e", "7" * 64, "7" * 64, n, x)]
    assert d.entfernt == [("c", "3" * 64, n)]
    assert any("e" in z and "100644→100755" in z for z in d.zeilen())
    assert re.fullmatch(r"[0-9a-f]{64}", diff_hash(d))
    assert diff_hash(d) == diff_hash(diff_manifeste(alt, neu))
    # Ein anderer Inhalt derselben Datei ergibt einen anderen Diff-Hash.
    anders = Exportmanifest([("a", "1" * 64, n), ("b", "6" * 64, n), ("d", "5" * 64, x),
                             ("e", "7" * 64, x)])
    assert diff_hash(diff_manifeste(alt, anders)) != diff_hash(d)
    nur_modus = Exportmanifest([("a", "1" * 64, n), ("b", "4" * 64, n), ("d", "5" * 64, n),
                                ("e", "7" * 64, x)])
    assert diff_hash(diff_manifeste(alt, nur_modus)) != diff_hash(d)


def test_diff_ohne_vorgaenger_alles_neu() -> None:
    neu = Exportmanifest([("a", "1" * 64, "100644")])
    d = diff_manifeste(None, neu)
    assert d.neu == [("a", "1" * 64, "100644")] and not d.geaendert and not d.entfernt


# --- Binärdateien / Freigaben ------------------------------------------------------------------

def _mit_bild(repo: Path, tmp_path: Path, bild: bytes = JPEG) -> tuple[Path, Exportmanifest]:
    _schreibe(repo, {KOMMA_BILD: bild})
    commit = _commit(repo)
    ziel = tmp_path / "export"
    blobs = kandidaten(git_blobs(repo, commit), ["web/**"])
    return ziel, schreibe_export(repo, commit, blobs, ziel)


def test_bild_ohne_freigabe_abgelehnt_und_korrekt_gehasht(repo: Path, tmp_path: Path) -> None:
    ziel, m = _mit_bild(repo, tmp_path)
    assert m.hashes()[KOMMA_BILD] == _sha(JPEG)
    with pytest.raises(NachweisFehler) as e:
        pruefe_binaer(ziel, m, {})
    assert e.value.regel == "export-binaer"
    assert KOMMA_BILD in e.value.text
    pruefe_binaer(ziel, m, {KOMMA_BILD: _sha(JPEG)})


def test_ersetzte_freigegebene_datei_abgelehnt(repo: Path, tmp_path: Path) -> None:
    ziel, m = _mit_bild(repo, tmp_path, JPEG + b"ersetzt")
    with pytest.raises(NachweisFehler) as e:
        pruefe_binaer(ziel, m, {KOMMA_BILD: _sha(JPEG)})
    assert e.value.regel == "export-binaer"


def test_bildendung_auch_bei_textinhalt_und_grosse_textdatei(repo: Path,
                                                             tmp_path: Path) -> None:
    _schreibe(repo, {"web/icon.svg": "<svg/>\n", "web/gross.txt": "x" * (1024 * 1024 + 1),
                     "web/binaer.dat": b"\x00\xff\xfe"})
    commit = _commit(repo)
    ziel = tmp_path / "export"
    m = schreibe_export(repo, commit, kandidaten(git_blobs(repo, commit), ["web/**"]), ziel)
    with pytest.raises(NachweisFehler) as e:
        pruefe_binaer(ziel, m, {})
    for pfad in ("web/icon.svg", "web/gross.txt", "web/binaer.dat"):
        assert pfad in e.value.text
    assert "web/src/main.ts" not in e.value.text


def test_lade_freigaben(tmp_path: Path) -> None:
    datei = tmp_path / "freigaben.txt"
    datei.write_text(f"# Kopf\n\n{'a' * 64}  {KOMMA_BILD}  # Logo, im Repo gezeichnet\n",
                     encoding="utf-8")
    assert lade_freigaben(datei.read_text(encoding="utf-8")) == {KOMMA_BILD: "a" * 64}


@pytest.mark.parametrize("zeile", [
    f"{'a' * 64}  web/logo.png",                       # ohne Begründung
    f"{'a' * 64}  web/logo.png  #   ",                 # leere Begründung
    "abc  web/logo.png  # zu kurz",                    # kein SHA-256
    f"{'a' * 64} web/logo.png  # ein Leerzeichen",     # Trennung nicht zwei Leerzeichen
])
def test_lade_freigaben_ungueltig(zeile: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        lade_freigaben(zeile + "\n")
    assert e.value.regel == "export-freigaben"


def test_lade_freigaben_doppelter_pfad() -> None:
    zeile = f"{'a' * 64}  web/logo.png  # Logo\n"
    with pytest.raises(NachweisFehler):
        lade_freigaben(zeile + zeile.replace("a", "b", 64))


# --- Begriffe ----------------------------------------------------------------------------------

def test_begriff_im_inhalt_und_im_dateinamen(tmp_path: Path) -> None:
    _schreibe(tmp_path, {
        "web/a.ts": f"// Paket von {BEGRIFF.upper()} GmbH\n",
        f"web/{BEGRIFF}-daten/b.ts": "export {}\n",
        "web/c.ts": f"const x{BEGRIFF} = 1; // mitten im Wort: kein Treffer\n",
        "web/d.ts": f"const projekt{BEGRIFF} = 1; // klein an klein: kein Wortanfang\n",
    })
    treffer = pruefe_begriffe(tmp_path, [BEGRIFF])
    assert Treffer("web/a.ts", 1, BEGRIFF) in treffer
    assert Treffer(f"web/{BEGRIFF}-daten/b.ts", 0, BEGRIFF) in treffer
    assert all(t.pfad not in ("web/c.ts", "web/d.ts") for t in treffer)


@pytest.mark.parametrize("zeile", [
    "const ORT_S001 = 1;".replace("ORT", "ORTSNAME"),   # Großschreibung + Unterstrich danach
    "paket = 'kunde_ortsname'",                        # Unterstrich davor
    "const neuOrtsnameDaten = 1;",                     # camelCase: klein→groß ist Wortgrenze
    "die ortsnamer Kirche",                            # Begriff als Wortanfang (Ableitung)
    "ORTSNAME",
    "(Ortsname)",
    "Station S001Ortsname",                            # Ziffer→Buchstabe ist Wortgrenze
    "paket v2ortsname",
])
def test_begriffe_in_bezeichnern(tmp_path: Path, zeile: str) -> None:
    """Grenzen sind nur Buchstaben/Ziffern: `_` trennt, ein Wechsel klein→groß (camelCase) trennt,
    und ein Begriff trifft auch als Anfang eines längeren Worts (z. B. Ableitungen)."""
    begriff = "orts" + "name"
    _schreibe(tmp_path, {"a.ts": zeile + "\n"})
    assert [t.zeile for t in pruefe_begriffe(tmp_path, [begriff])] == [1], zeile


def test_begriffe_in_kodierten_daten_kein_treffer(tmp_path: Path) -> None:
    """Base64-Werte (z. B. `integrity` in package-lock.json) sind kodierte Daten, kein Text: ein
    zufälliges „/Ortsname…“ darin ist kein Leck. Kurze Bezeichner bleiben geprüft."""
    begriff = "orts" + "name"
    import base64
    echt = base64.b64encode(hashlib.sha512(b"x").digest()).decode()  # 88 Zeichen inkl. ==
    kodiert = "sha512-" + echt[:20] + "/Ortsname" + echt[29:]
    assert len(kodiert) == len("sha512-") + 88
    bild = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(52))).decode()
    daten_uri = "data:image/png;base64," + bild[:20] + "/Ortsname" + bild[29:]
    _schreibe(tmp_path, {"a.json": f'"integrity": "{kodiert}"\n',
                         "c.css": f"a {{ background: url({daten_uri}); }}\n",
                         "b.ts": "const x = 'a/OrtsnameDaten';\n"})
    assert [t.pfad for t in pruefe_begriffe(tmp_path, [begriff])] == ["b.ts"]


def test_eingebettete_base64_nutzlast_wird_dekodiert_geprueft(tmp_path: Path) -> None:
    """Vite bettet Worker als `atob("<base64>")` ein, Daten-URIs tragen `base64,<…>`. Zufällige
    Begriffsstücke im Base64-Text sind kein Leck — aber der dekodierte Inhalt wird geprüft, damit
    ein eingebetteter Worker keinen blinden Fleck bildet."""
    import base64
    begriff = "orts" + "name"
    sauber = base64.b64encode(b"var x = 1; // " + b"a" * 60).decode()
    rauschen = sauber[:20] + "/Ortsname" + sauber[29:]  # Stück im Base64-Text, dekodiert Müll
    leck = base64.b64encode(f"const pfad = '/data/{begriff}/';".encode()).decode()
    _schreibe(tmp_path, {
        "a.js": f'new Worker(URL.createObjectURL(new Blob([atob("{rauschen}")])));\n',
        "b.js": f'new Worker(URL.createObjectURL(new Blob([atob("{leck}")])));\n',
        "c.css": f"a {{ background: url(data:text/plain;base64,{leck}); }}\n",
    })
    treffer = pruefe_begriffe(tmp_path, [begriff])
    assert sorted((t.pfad, t.zeile) for t in treffer) == [("b.js", 1), ("c.css", 1)]


def test_wasm_literal_wird_dekodiert_geprueft(tmp_path: Path) -> None:
    """Spark übergibt sein Wasm-Modul als String-Literal (`f("AGFzbQ…")`, Base64 von `\\0asm`).
    Zufällige Begriffsstücke im Base64-Text sind kein Leck, der dekodierte Inhalt wird geprüft;
    andere lange Literale bleiben Text."""
    import base64
    begriff = "orts" + "name"
    sauber = base64.b64encode(b"\0asm\1\0\0\0" + b"\x01" * 60).decode()
    rauschen = sauber[:20] + "/Ortsname" + sauber[29:]
    leck = base64.b64encode(b"\0asm\1\0\0\0" + f"/data/{begriff}/".encode()).decode()
    _schreibe(tmp_path, {
        "a.js": f'var m = f("{rauschen}");\n',
        "b.js": f'var m = f("{leck}");\n',
        "c.js": 'var t = "Ortsname";\n',
    })
    treffer = pruefe_begriffe(tmp_path, [begriff])
    assert sorted((t.pfad, t.zeile) for t in treffer) == [("b.js", 1), ("c.js", 1)]


def test_bauergebnis_freigabe_nur_bei_gleichem_hash_und_anzahl(tmp_path: Path) -> None:
    """Geprüfte Zufallstreffer im gebündelten Fremdcode: freigegeben nur für genau diese Datei,
    diesen SHA-256 und diese Trefferzahl — jede Abweichung lässt das Tor wieder greifen."""
    import hashlib
    begriff = "qx" + "z"
    _schreibe(tmp_path, {"a.js": f"var {begriff}1 = 1, {begriff}2 = 2;\n", "b.js": "var ok;\n"})
    sha = hashlib.sha256((tmp_path / "a.js").read_bytes()).hexdigest()
    gemeldet: list[str] = []
    export._begriffe_gate(tmp_path, [begriff], "im Test", {"a.js": (sha, 2)}, gemeldet.append)
    assert gemeldet == ["Bauergebnis-Freigabe angewandt: a.js (2 geprüfte Zufallstreffer)"]
    for falsch in ({"a.js": (sha, 1)}, {"a.js": ("0" * 64, 2)}, {}):
        with pytest.raises(NachweisFehler) as e:
            export._begriffe_gate(tmp_path, [begriff], "im Test", falsch, lambda _: None)
        assert e.value.regel == "export-begriff"


def test_bau_freigabe_mit_praefix_im_tarball(tmp_path: Path) -> None:
    """Im ausgelieferten Web-Tarball liegt dieselbe Datei unter `web/dist/`."""
    import hashlib
    begriff = "qx" + "z"
    _schreibe(tmp_path, {"web/dist/assets/a.js": f"var {begriff}1;\n"})
    sha = hashlib.sha256((tmp_path / "web/dist/assets/a.js").read_bytes()).hexdigest()
    treffer = pruefe_begriffe(tmp_path, [begriff])
    frei = {"assets/a.js": (sha, 1)}
    assert export.ohne_bau_freigaben(treffer, tmp_path, frei, "web/dist/", lambda _: None) == []
    assert export.ohne_bau_freigaben(treffer, tmp_path, frei, "", lambda _: None) == treffer


def test_bau_freigaben_format() -> None:
    sha = "a" * 64
    assert export.lade_bau_freigaben(f"# Kopf\n{sha}  assets/x-12345678.js  3  # Grund\n") == {
        "assets/x-12345678.js": (sha, 3)}
    for kaputt in (f"{sha}  assets/x.js  # ohne Anzahl", f"{sha}  assets/x.js  0  # null",
                   f"{sha}  assets/x.js  2  #", f"{sha}  a.js  1  # g\n{sha}  a.js  1  # g"):
        with pytest.raises(NachweisFehler):
            export.lade_bau_freigaben(kaputt)


def test_verschachtelte_nutzlast_rekursiv(tmp_path: Path) -> None:
    """Das echte Bauergebnis bettet in einem atob-Worker einen weiteren atob-Worker ein; der
    Begriff steht hier nur in der zweiten Ebene."""
    import base64

    def b64(text: str) -> str:
        return base64.b64encode(text.encode()).decode()

    innen = b64("const pfad = '/data/" + "orts" + "name/';")
    aussen = b64(f'var w = () => new Worker(URL.createObjectURL(new Blob([atob("{innen}")])));')
    _schreibe(tmp_path, {"a.js": f'x(atob("{aussen}"));\n'})
    assert [(t.pfad, t.zeile) for t in pruefe_begriffe(tmp_path, ["orts" + "name"])] == [
        ("a.js", 1)]


def test_verschachtelung_zu_tief_bricht_ab(tmp_path: Path) -> None:
    import base64
    inhalt = "harmlos"
    for _ in range(6):
        inhalt = 'atob("' + base64.b64encode(inhalt.encode()).decode() + '")'
    _schreibe(tmp_path, {"a.js": inhalt + "\n"})
    with pytest.raises(NachweisFehler) as e:
        pruefe_begriffe(tmp_path, ["orts" + "name"])
    assert e.value.regel == "export-kodiert-unlesbar"
    assert "a.js:1" in e.value.text


def test_hash_token_nur_in_echter_laenge(tmp_path: Path) -> None:
    """Nur SRI-Hashes echter Länge gelten als kodiert: `sha256-<Begriff>Key` ist Text."""
    _schreibe(tmp_path, {"a.ts": "const k = 'sha256-" + "orts" + "nameKey';\n"})
    assert [t.zeile for t in pruefe_begriffe(tmp_path, ["orts" + "name"])] == [1]


def test_nutzlast_ohne_auffuellung_und_unlesbar(tmp_path: Path) -> None:
    import base64
    ohne_gleich = base64.b64encode(b"/data/" + b"orts" + b"name/x").decode().rstrip("=")
    _schreibe(tmp_path, {"a.js": f'atob("{ohne_gleich}")\n'})
    assert [t.zeile for t in pruefe_begriffe(tmp_path, ["orts" + "name"])] == [1]
    kaputt = tmp_path / "kaputt"
    _schreibe(kaputt, {"b.js": 'atob("QUJDRE")\n'.replace("QUJDRE", "QUJDREVGR")})
    with pytest.raises(NachweisFehler) as e:
        pruefe_begriffe(kaputt, ["orts" + "name"])
    assert e.value.regel == "export-kodiert-unlesbar"
    assert "b.js:1" in e.value.text


@pytest.mark.parametrize("zeile", [
    "data/Ortsname2024/Scans/Station001/Panorama/cube",
    "/api/v1/projekte/Ortsname2/stationen/S001/panoramaBilder",
    "const kundeOrtsnameV2StationPoseMatrixDataCache = 1;",
    "sha512 steht hier nicht davor: aB3/OrtsnameXyZ0123456789abcdefghijklmnopqrstuvw",
])
def test_lange_pfade_und_bezeichner_werden_geprueft(tmp_path: Path, zeile: str) -> None:
    """Lange Pfade, URLs und Bezeichner sind Text, keine kodierten Daten: nur echte Hash-Token
    (`sha512-…` wie npm-integrity) und `base64,`-Nutzlasten von Daten-URIs werden übersprungen."""
    _schreibe(tmp_path, {"a.ts": zeile + "\n"})
    assert [t.zeile for t in pruefe_begriffe(tmp_path, ["orts" + "name"])] == [1], zeile


@pytest.mark.parametrize("zeile", [
    "const vorortsname = 1;",     # mitten im Wort (klein)
    "VORORTSNAME",                # mitten im Wort (groß)
])
def test_begriffe_mitten_im_wort_kein_treffer(tmp_path: Path, zeile: str) -> None:
    _schreibe(tmp_path, {"a.ts": zeile + "\n"})
    assert pruefe_begriffe(tmp_path, ["orts" + "name"]) == []


def test_begriffe_mit_satzzeichen_am_rand(tmp_path: Path) -> None:
    _schreibe(tmp_path, {
        "a.conf": "von 10.9.8.12 abgelehnt\n",
        "b.conf": "host.intern.example.test\n",
        "c.conf": "110.9.8.1 und x10.9.8.1\n",
        "d.bin": b"\x00\xff db.intern.example.test \x00",
    })
    treffer = pruefe_begriffe(tmp_path, ["10.9.8.", ".intern.example.test"])
    assert {t.pfad for t in treffer} == {"a.conf", "b.conf", "d.bin"}


def test_lade_begriffe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    datei = tmp_path / "begriffe.txt"
    datei.write_text(f"# Kommentar\n\n{BEGRIFF}\n  Zweiter Begriff  # Notiz\n",
                     encoding="utf-8")
    monkeypatch.setenv("SCANVIEW_BEGRIFFE", str(datei))
    assert lade_begriffe() == [BEGRIFF, "Zweiter Begriff"]


def test_lade_begriffe_fehlt_nie_still(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCANVIEW_BEGRIFFE", raising=False)
    with pytest.raises(NachweisFehler) as e:
        lade_begriffe()
    assert e.value.regel == "export-begriffe"
    monkeypatch.setenv("SCANVIEW_BEGRIFFE", str(tmp_path / "fehlt.txt"))
    with pytest.raises(NachweisFehler):
        lade_begriffe()
    leer = tmp_path / "leer.txt"
    leer.write_text("# nur Kommentar\n", encoding="utf-8")
    monkeypatch.setenv("SCANVIEW_BEGRIFFE", str(leer))
    with pytest.raises(NachweisFehler):
        lade_begriffe()


# --- Geheimnisse (gitleaks) --------------------------------------------------------------------

class _Laeufer:
    """Ersetzt `export._lauf`: protokolliert jeden Befehl; git läuft echt, alles andere
    (docker, npm, uv) liefert leere Ausgabe und legt bei `npm run build` ein `dist/` an."""

    def __init__(self) -> None:
        self.befehle: list[tuple[list[str], Path | None]] = []
        self.umgebungen: list[dict[str, str] | None] = []
        self._echt = export._lauf

    def __call__(self, befehl: list[str], regel: str, *, cwd: Path | None = None,
                 umgebung: dict[str, str] | None = None) -> bytes:
        self.befehle.append((list(befehl), cwd))
        self.umgebungen.append(umgebung)
        if befehl[0] == "git":
            return self._echt(befehl, regel, cwd=cwd, umgebung=umgebung)
        if befehl[:3] == ["npm", "run", "build"] and cwd is not None:
            (cwd / "dist").mkdir()
            (cwd / "dist" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        return b""


def _bericht_ordner(befehl: list[str]) -> Path:
    mount = next(a for a in befehl if a.endswith(":/bericht:rw"))
    return Path(mount.removesuffix(":/bericht:rw"))


def _gitleaks_sauber(befehl: list[str]) -> int:
    """Ersetzt `export._gitleaks_lauf`: kein Fund (Exit 0, leerer Bericht)."""
    (_bericht_ordner(befehl) / export.GITLEAKS_BERICHT).write_text("[]", encoding="utf-8")
    return 0


def _gitleaks_mit(code: int, bericht: str | None) -> Callable[[list[str]], int]:
    def lauf(befehl: list[str]) -> int:
        if bericht is not None:
            (_bericht_ordner(befehl) / export.GITLEAKS_BERICHT).write_text(bericht, "utf-8")
        return code
    return lauf


def test_pruefe_geheimnisse_befehl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    befehle: list[list[str]] = []

    def lauf(b: list[str]) -> int:
        befehle.append(b)
        return _gitleaks_sauber(b)
    monkeypatch.setattr(export, "_gitleaks_lauf", lauf)
    konfig = tmp_path / "gitleaks.toml"
    konfig.write_text("title = 't'\n", encoding="utf-8")
    ziel = tmp_path / "export"
    ziel.mkdir()
    werkzeuge = lade_werkzeuge(REPO_WURZEL / "nachweise" / "werkzeuge.json")
    pruefe_geheimnisse(ziel, werkzeuge, konfig)
    [befehl] = befehle
    assert befehl[:2] == ["docker", "run"]
    assert "--network" in befehl and befehl[befehl.index("--network") + 1] == "none"
    assert werkzeuge["gitleaks"].image in befehl
    assert f"{ziel}:/export:ro" in befehl and f"{konfig}:/konfig/gitleaks.toml:ro" in befehl
    rest = befehl[befehl.index(werkzeuge["gitleaks"].image) + 1:]
    assert rest[:2] == ["dir", "/export"]
    assert "--config" in rest and "/konfig/gitleaks.toml" in rest
    assert "--redact" in rest
    assert rest[rest.index("--exit-code") + 1] == str(export.GITLEAKS_FUND)
    assert "--report-path" in rest


_FUND = json.dumps([{"RuleID": "scanview-api-token", "File": "/export/web/k.ts",
                     "Secret": "REDACTED"}])


@pytest.mark.parametrize(("code", "bericht", "regel"), [
    (export.GITLEAKS_FUND, _FUND, "export-geheimnis"),
    (0, "[]", None),
    (export.GITLEAKS_FUND, None, "export-geheimnis-werkzeug"),   # Fund ohne Bericht
    (export.GITLEAKS_FUND, "[]", "export-geheimnis-werkzeug"),   # Exit und Bericht uneins
    (0, _FUND, "export-geheimnis-werkzeug"),
    (0, None, "export-geheimnis-werkzeug"),
    (0, "kein json", "export-geheimnis-werkzeug"),
], ids=["fund", "sauber", "fund-ohne-bericht", "fund-leerer-bericht", "sauber-mit-funden",
        "sauber-ohne-bericht", "bericht-kaputt"])
def test_gitleaks_fund_und_werkzeugfehler_getrennt(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch,
                                                    code: int, bericht: str | None,
                                                    regel: str | None) -> None:
    """Nur ein bestätigter Fund ist `export-geheimnis`; alles Unauswertbare ist ein
    Werkzeugfehler."""
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_mit(code, bericht))
    ziel = tmp_path / "export"
    ziel.mkdir()
    werkzeuge = lade_werkzeuge(REPO_WURZEL / "nachweise" / "werkzeuge.json")
    if regel is None:
        pruefe_geheimnisse(ziel, werkzeuge, tmp_path)
        return
    with pytest.raises(NachweisFehler) as e:
        pruefe_geheimnisse(ziel, werkzeuge, tmp_path)
    assert e.value.regel == regel
    if regel == "export-geheimnis":
        assert "scanview-api-token" in e.value.text and "web/k.ts" in e.value.text


def test_gitleaks_docker_fehler_ist_werkzeugfehler(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Docker-Fehler (Exit 125, Image fehlt) ist kein Fund."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text("#!/bin/sh\necho 'Unable to find image' >&2\nexit 125\n",
                                 encoding="utf-8")
    (stub / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}:/usr/bin:/bin")
    ziel = tmp_path / "export"
    ziel.mkdir()
    werkzeuge = lade_werkzeuge(REPO_WURZEL / "nachweise" / "werkzeuge.json")
    with pytest.raises(NachweisFehler) as e:
        pruefe_geheimnisse(ziel, werkzeuge, tmp_path)
    assert e.value.regel == "export-geheimnis-werkzeug" and "125" in e.value.text


def _docker_da() -> bool:
    if shutil.which("docker") is None:
        return False
    image = lade_werkzeuge(REPO_WURZEL / "nachweise" / "werkzeuge.json")["gitleaks"].image
    ergebnis = subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                              check=False)
    return ergebnis.returncode == 0


@pytest.mark.docker
@pytest.mark.skipif(not _docker_da(), reason="docker oder gepinntes gitleaks-Image fehlt")
@pytest.mark.skipif(not (REPO_WURZEL / ".gitleaks.toml").exists(),
                    reason="gitleaks-Konfiguration liegt nur im privaten Repo (der Export "
                           "übergibt sie per --config)")
def test_gitleaks_findet_testsecret(tmp_path: Path) -> None:
    werkzeuge = lade_werkzeuge(REPO_WURZEL / "nachweise" / "werkzeuge.json")
    konfig = REPO_WURZEL / ".gitleaks.toml"
    sauber = tmp_path / "sauber"
    _schreibe(sauber, {"web/a.ts": "export const x = 1;\n"})
    pruefe_geheimnisse(sauber, werkzeuge, konfig)
    befallen = tmp_path / "befallen"
    # Zur Laufzeit zusammengesetzt: ein Literal in dieser Datei fände gitleaks im Export.
    token = "sv" + "_" + "Q7x" * 14 + "k"
    assert len(token) == 46
    _schreibe(befallen, {"web/konfig.ts": f"const t = '{token}';\n"})
    with pytest.raises(NachweisFehler) as e:
        pruefe_geheimnisse(befallen, werkzeuge, konfig)
    assert e.value.regel == "export-geheimnis"
    assert token not in e.value.text  # --redact


@pytest.mark.docker
@pytest.mark.skipif(not _docker_da(), reason="docker oder gepinntes gitleaks-Image fehlt")
@pytest.mark.skipif(not (REPO_WURZEL / ".gitleaks.toml").exists(),
                    reason="gitleaks-Konfiguration liegt nur im privaten Repo (der Export "
                           "übergibt sie per --config)")
def test_gitleaks_platzhalter_frei_echtes_token_daneben_gefunden(tmp_path: Path) -> None:
    """Die Wert-Freigabe des Test-Platzhalters (mcp-server/tests/test_datenschutz.py) gibt genau
    diesen Wert frei — nicht die Datei, nicht die Regel: ein echtes `sv_`-Token in derselben
    Datei wird weiter gefunden."""
    werkzeuge = lade_werkzeuge(REPO_WURZEL / "nachweise" / "werkzeuge.json")
    konfig = REPO_WURZEL / ".gitleaks.toml"
    platzhalter = "sv" + "_" + "datenschutz-test_0123456789abcdefghijklmnop"
    assert len(platzhalter) == 46
    datei = "mcp-server/tests/test_datenschutz.py"
    frei = tmp_path / "frei"
    _schreibe(frei, {datei: f'TOKEN = "{platzhalter}"\n'})
    pruefe_geheimnisse(frei, werkzeuge, konfig)
    befallen = tmp_path / "befallen"
    echt = "sv" + "_" + "Q7x" * 14 + "k"
    _schreibe(befallen, {datei: f'TOKEN = "{platzhalter}"\nANDERES = "{echt}"\n'})
    with pytest.raises(NachweisFehler) as e:
        pruefe_geheimnisse(befallen, werkzeuge, konfig)
    assert e.value.regel == "export-geheimnis"


# --- Baubarkeit --------------------------------------------------------------------------------

def _baubarer_export(tmp_path: Path) -> Path:
    ziel = tmp_path / "export"
    _schreibe(ziel, {
        "web/package.json": json.dumps({"allowScripts": {"esbuild": False, "bau-hilfe": True}}),
        "server/pyproject.toml": "", "server/uv.lock": "",
        "mcp-server/pyproject.toml": "", "mcp-server/uv.lock": "",
        "nachweise/pyproject.toml": "", "nachweise/uv.lock": "",
        "import/pyproject.toml": "", "import/uv.lock": "",
    })
    return ziel


def test_baubarkeit_befehle_im_abzug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    laeufer = _Laeufer()
    monkeypatch.setattr(export, "_lauf", laeufer)
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    ziel = _baubarer_export(tmp_path)
    vorher = sorted(p.relative_to(ziel) for p in ziel.rglob("*"))
    arbeit = tmp_path / "arbeit"
    ergebnis = pruefe_baubarkeit(ziel, arbeit)
    bau = arbeit / "bau"
    erwartet = [
        (["npm", "ci", "--ignore-scripts"], bau / "web"),
        (["npm", "rebuild", "bau-hilfe", "--ignore-scripts=false"], bau / "web"),
        (["npm", "run", "typecheck"], bau / "web"),
        (["npm", "run", "lint"], bau / "web"),
        (["npm", "test"], bau / "web"),
        (["npm", "run", "build"], bau / "web"),
        (["uv", "sync", "--locked"], bau / "server"),
        (["uv", "run", "pytest", "-q"], bau / "server"),
        (["uv", "sync", "--locked"], bau / "mcp-server"),
        (["uv", "run", "pytest", "-q"], bau / "mcp-server"),
        (["uv", "sync", "--locked"], bau / "nachweise"),
        (["uv", "run", "pytest", "-q"], bau / "nachweise"),
        (["uv", "sync", "--locked"], bau / "import"),
        (["uv", "run", "pytest", "-q", "-m", "not docker and not data"], bau / "import"),
    ]
    assert laeufer.befehle == erwartet
    # Das Exportziel bleibt unberührt (kein node_modules/.venv/dist im späteren Commit).
    assert sorted(p.relative_to(ziel) for p in ziel.rglob("*")) == vorher
    assert ergebnis.web_dist == bau / "web" / "dist"
    assert [(s.befehl, s.cwd) for s in ergebnis.netzschritte] == [
        (["npm", "ci", "--ignore-scripts"], "web"),
        (["npm", "rebuild", "bau-hilfe", "--ignore-scripts=false"], "web"),
        (["uv", "sync", "--locked"], "server"),
        (["uv", "sync", "--locked"], "mcp-server"),
        (["uv", "sync", "--locked"], "nachweise"),
        (["uv", "sync", "--locked"], "import"),
    ]
    # Eigene, schmale Umgebung: keine privaten Variablen, kein Python-Download.
    for umgebung in laeufer.umgebungen:
        assert umgebung is not None
        assert umgebung["UV_PYTHON_DOWNLOADS"] == "never"
        # Keine Nutzer-Konfiguration von uv, aber [tool.uv] im pyproject gilt weiter
        # (UV_NO_CONFIG würde auch exclude-newer/no-build-package des Projekts abschalten).
        assert "UV_NO_CONFIG" not in umgebung
        assert umgebung["XDG_CONFIG_HOME"] == str(arbeit / "konfig")
        # Eigenes HOME im Arbeitsordner (kein ~/.netrc, ~/.gitconfig, keine echten Caches);
        # Caches ausdrücklich im Arbeitsordner.
        assert umgebung["HOME"] == str(arbeit / "home")
        assert umgebung["NPM_CONFIG_CACHE"] == str(arbeit / "npm-cache")
        assert umgebung["UV_CACHE_DIR"] == str(arbeit / "uv-cache")
        assert umgebung["XDG_CACHE_HOME"] == str(arbeit / "cache")
        assert umgebung["XDG_DATA_HOME"] == str(arbeit / "daten")
        # Aus dem echten HOME nur PATH und die (schreibgeschützt genutzten) uv-Pythons.
        # (Der Arbeitsordner selbst darf unter HOME liegen, etwa mit TMPDIR auf der Platte.)
        echt = {k for k, w in umgebung.items()
                if w.startswith(str(Path.home())) and not w.startswith(str(arbeit))}
        assert echt <= {"PATH", "UV_PYTHON_INSTALL_DIR"}
        assert (arbeit / "konfig").is_dir() and not any((arbeit / "konfig").iterdir())
        assert "SCANVIEW_BEGRIFFE" not in umgebung


def test_baubarkeit_fehlende_komponente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "_lauf", _Laeufer())
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    ziel = _baubarer_export(tmp_path)
    (ziel / "mcp-server/uv.lock").unlink()
    with pytest.raises(NachweisFehler) as e:
        pruefe_baubarkeit(ziel, tmp_path / "arbeit")
    assert e.value.regel == "export-baubarkeit"


def test_baubarkeit_ohne_dist_bricht_ab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def ohne_dist(befehl: list[str], regel: str, *, cwd: Path | None = None,
                  umgebung: dict[str, str] | None = None) -> bytes:
        return b""
    monkeypatch.setattr(export, "_lauf", ohne_dist)
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    with pytest.raises(NachweisFehler) as e:
        pruefe_baubarkeit(_baubarer_export(tmp_path), tmp_path / "arbeit")
    assert e.value.regel == "export-baubarkeit"


# --- exportiere (Ablauf, Bestätigung, kein Push) -----------------------------------------------

def _begriffsdatei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    datei = tmp_path / "begriffe.txt"
    datei.write_text(f"{BEGRIFF}\n", encoding="utf-8")
    monkeypatch.setenv("SCANVIEW_BEGRIFFE", str(datei))
    return lade_begriffe()


def _exportiere(repo: Path, tmp_path: Path, name: str, **kw: object) -> export.Exportergebnis:
    werkzeuge = lade_werkzeuge(REPO_WURZEL / "nachweise" / "werkzeuge.json")
    return exportiere(repo, "HEAD", tmp_path / name, begriffe=[BEGRIFF], werkzeuge=werkzeuge,
                      arbeitsordner=tmp_path / f"arbeit-{name}", melde=lambda _z: None,
                      **kw)  # type: ignore[arg-type]


def _baubar_machen(repo: Path) -> None:
    _schreibe(repo, {"web/package.json": json.dumps({"allowScripts": {}})})
    _commit(repo)


def test_exportiere_verlangt_bestaetigten_diff(repo: Path, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    laeufer = _Laeufer()
    monkeypatch.setattr(export, "_lauf", laeufer)
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    _baubar_machen(repo)
    with pytest.raises(NachweisFehler) as e:
        _exportiere(repo, tmp_path, "e1", vorheriges=None, bestaetigt=None)
    assert e.value.regel == "export-bestaetigung"
    wert = re.search(r"--bestaetigt ([0-9a-f]{64})", e.value.text)
    assert wert is not None
    # Ohne Bestätigung wurde nichts gebaut.
    assert not any(b[0][0] in ("npm", "uv") for b in laeufer.befehle)
    with pytest.raises(NachweisFehler) as e2:
        _exportiere(repo, tmp_path, "e2", vorheriges=None, bestaetigt="f" * 64)
    assert e2.value.regel == "export-bestaetigung"
    ergebnis = _exportiere(repo, tmp_path, "e3", vorheriges=None, bestaetigt=wert.group(1))
    assert ergebnis.diff_hash == wert.group(1)
    assert "privat.md" not in ergebnis.manifest.hashes()
    assert any(b[0][:2] == ["npm", "ci"] for b in laeufer.befehle)
    # Folge-Export ohne Änderung: leerer Diff, eigener Hash, ebenfalls zu bestätigen.
    leer = diff_manifeste(ergebnis.manifest, ergebnis.manifest)
    ergebnis2 = _exportiere(repo, tmp_path, "e4", vorheriges=ergebnis.manifest,
                            bestaetigt=diff_hash(leer))
    assert not (ergebnis2.diff.neu or ergebnis2.diff.geaendert or ergebnis2.diff.entfernt)


def test_exportiere_begriff_bricht_vor_bestaetigung_ab(repo: Path, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "_lauf", _Laeufer())
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    _schreibe(repo, {"web/src/kunde.ts": f"// für {BEGRIFF}\n"})
    _commit(repo)
    with pytest.raises(NachweisFehler) as e:
        _exportiere(repo, tmp_path, "e", vorheriges=None, bestaetigt=None)
    assert e.value.regel == "export-begriff"
    assert "web/src/kunde.ts:1" in e.value.text


def test_exportiere_nicht_freigegebenes_bild(repo: Path, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "_lauf", _Laeufer())
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    _schreibe(repo, {KOMMA_BILD: JPEG})
    _commit(repo)
    with pytest.raises(NachweisFehler) as e:
        _exportiere(repo, tmp_path, "e", vorheriges=None, bestaetigt=None)
    assert e.value.regel == "export-binaer"
    # Mit Freigabe (aus dem Commit gelesen) läuft der Export bis zur Bestätigung weiter.
    _schreibe(repo, {"veroeffentlichung/freigaben.txt":
                     f"{_sha(JPEG)}  {KOMMA_BILD}  # im Repo erstelltes Testbild\n"})
    _commit(repo)
    with pytest.raises(NachweisFehler) as e2:
        _exportiere(repo, tmp_path, "e2", vorheriges=None, bestaetigt=None)
    assert e2.value.regel == "export-bestaetigung"


def test_exportiere_liest_positivliste_aus_dem_commit(repo: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "_lauf", _Laeufer())
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    # Arbeitsbaum-Änderung an der Positivliste wirkt nicht (nur Git-Blobs des Commits zählen).
    (repo / "veroeffentlichung/positivliste.txt").write_text("**\n", encoding="utf-8")
    with pytest.raises(NachweisFehler) as e:
        _exportiere(repo, tmp_path, "e", vorheriges=None, bestaetigt=None)
    assert e.value.regel == "export-bestaetigung"
    assert not (tmp_path / "e" / "privat.md").exists()


def test_export_uebertraegt_nie(repo: Path, tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Export veröffentlicht nie selbst: kein Befehl enthält „push“, und der Quelltext von
    export.py kennt kein solches Git-Kommando."""
    laeufer = _Laeufer()
    monkeypatch.setattr(export, "_lauf", laeufer)
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    _baubar_machen(repo)
    with pytest.raises(NachweisFehler) as e:
        _exportiere(repo, tmp_path, "e1", vorheriges=None, bestaetigt=None)
    wert = re.search(r"--bestaetigt ([0-9a-f]{64})", e.value.text)
    assert wert is not None
    _exportiere(repo, tmp_path, "e2", vorheriges=None, bestaetigt=wert.group(1))
    assert laeufer.befehle
    for befehl, _ in laeufer.befehle:
        assert not any("push" in teil.lower() for teil in befehl), befehl
    quelltext = Path(export.__file__).read_text(encoding="utf-8")
    assert re.search(r"['\"]push['\"]", quelltext) is None


# --- CLI ---------------------------------------------------------------------------------------

def test_cli_export_zeigt_diff_und_verlangt_bestaetigung(
        repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(export, "_lauf", _Laeufer())
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    _baubar_machen(repo)
    _begriffsdatei(tmp_path, monkeypatch)
    werkzeuge = REPO_WURZEL / "nachweise" / "werkzeuge.json"
    argumente = ["export", "HEAD", "--repo", str(repo), "--werkzeuge", str(werkzeuge),
                 "--arbeitsordner", str(tmp_path / "arbeit1"), "--ziel", str(tmp_path / "z1")]
    assert cli.main(argumente) == 2
    ausgabe = capsys.readouterr()
    assert "NEU " in ausgabe.out and "web/src/main.ts" in ausgabe.out
    wert = re.search(r"--bestaetigt ([0-9a-f]{64})", ausgabe.err)
    assert wert is not None
    manifest = tmp_path / "exportmanifest.json"
    argumente = ["export", "HEAD", "--repo", str(repo), "--werkzeuge", str(werkzeuge),
                 "--arbeitsordner", str(tmp_path / "arbeit2"), "--ziel", str(tmp_path / "z2"),
                 "--bestaetigt", wert.group(1), "--manifest-aus", str(manifest)]
    assert cli.main(argumente) == 0
    assert "web/src/main.ts" in lade_manifest(manifest).hashes()
    # Zweiter Lauf gegen das gespeicherte Manifest: leerer Diff.
    argumente = ["export", "HEAD", "--repo", str(repo), "--werkzeuge", str(werkzeuge),
                 "--arbeitsordner", str(tmp_path / "arbeit3"), "--ziel", str(tmp_path / "z3"),
                 "--vorheriges", str(manifest)]
    assert cli.main(argumente) == 2
    ausgabe = capsys.readouterr()
    assert "keine Änderungen" in ausgabe.out


def test_cli_export_ohne_begriffsliste(repo: Path, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCANVIEW_BEGRIFFE", raising=False)
    assert cli.main(["export", "HEAD", "--repo", str(repo), "--ziel", str(tmp_path / "z")]) == 2
    assert not (tmp_path / "z").exists()


def test_blob_ist_unveraenderlich() -> None:
    b = Blob("100644", "blob", "a" * 40, "x")
    with pytest.raises(AttributeError):
        b.pfad = "y"  # type: ignore[misc]


# --- Private Pfadangaben -----------------------------------------------------------------------

# Zur Laufzeit zusammengesetzt: diese Datei liegt selbst im Export.
_PRIVAT = ["docs/" + "security/audit.md", "docs/" + "acceptance/slice0-1.md",
           "docs/" + "rulings-teil-e.md", "docs/" + "recht-lizenzen-lieferkette.md",
           "/" + "home/nutzer/daten"]


@pytest.mark.parametrize("verweis", _PRIVAT)
def test_private_pfade_werden_gefunden(tmp_path: Path, verweis: str) -> None:
    _schreibe(tmp_path, {"web/a.ts": "// ok\n", "server/b.py": f"# siehe {verweis}\n"})
    treffer = export.pruefe_private_pfade(tmp_path)
    assert [(t.pfad, t.zeile) for t in treffer] == [("server/b.py", 1)]


def test_private_pfade_unkritische_verweise(tmp_path: Path) -> None:
    _schreibe(tmp_path, {"a.md": "docs/public/release.md, docs/THIRD_PARTY.md, ~/.config, "
                                  "$HOME/.local/bin\n"})
    assert export.pruefe_private_pfade(tmp_path) == []


def test_exportiere_bricht_bei_privatem_pfad_ab(repo: Path, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "_lauf", _Laeufer())
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    _schreibe(repo, {"server/notiz.py": f"# vgl. {_PRIVAT[0]}\n"})
    _commit(repo)
    with pytest.raises(NachweisFehler) as e:
        _exportiere(repo, tmp_path, "e", vorheriges=None, bestaetigt=None)
    assert e.value.regel == "export-privater-pfad"
    assert "server/notiz.py:1" in e.value.text


def test_private_pfade_im_bauergebnis(tmp_path: Path) -> None:
    """Im Bauergebnis (gebündelter Fremdcode) gilt statt jedes Heimatverzeichnisses: die Heimat- und
    Arbeitsverzeichnisse des Bau-Rechners dürfen nicht darin stehen. Feste Zeichenketten von
    Fremdbibliotheken (Emscripten: Heimatverzeichnis „web_user“) sind kein Leck."""
    arbeit = tmp_path / "arbeit"
    muster = export.bauergebnis_muster(arbeit)
    _schreibe(tmp_path / "dist", {
        "a.js": 'var R={HOME:"/' + 'home/web_user"};\n',
        "b.js": f'//# sourceMappingURL={Path.home()}/x.js.map\n',
        "c.js": f'"{arbeit}/bau/web/src/main.ts"\n',
        "d.js": "// " + "docs/" + "security/x.md\n",
    })
    treffer = export.pruefe_private_pfade(tmp_path / "dist", muster)
    assert sorted(t.pfad for t in treffer) == ["b.js", "c.js", "d.js"]


# --- Meldeweg: Postfach-Marker in SECURITY.md --------------------------------------------------

MARKER = "<!-- " + "POSTFACH-OFFEN" + " -->"


def test_exportiere_verweigert_offenes_postfach(repo: Path, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Solange SECURITY.md den Marker trägt (Meldepostfach noch nicht eingerichtet), wird nichts
    zur Veröffentlichung freigegeben — vor Diff-Bestätigung und Bau."""
    laeufer = _Laeufer()
    monkeypatch.setattr(export, "_lauf", laeufer)
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    _schreibe(repo, {
        "veroeffentlichung/positivliste.txt": POSITIVLISTE + "SECURITY.md\n",
        "SECURITY.md": f"# Sicherheit\n\n{MARKER}\nMeldungen an security@example.org\n",
    })
    _commit(repo)
    with pytest.raises(NachweisFehler) as e:
        _exportiere(repo, tmp_path, "e", vorheriges=None, bestaetigt=None)
    assert e.value.regel == "export-postfach"
    assert "SECURITY.md:3" in e.value.text
    assert not any(b[0][0] in ("npm", "uv") for b in laeufer.befehle)

    # Marker entfernt → der Export läuft bis zur Bestätigung weiter.
    _schreibe(repo, {"SECURITY.md": "# Sicherheit\n\nMeldungen an security@example.org\n"})
    _commit(repo)
    with pytest.raises(NachweisFehler) as e2:
        _exportiere(repo, tmp_path, "e2", vorheriges=None, bestaetigt=None)
    assert e2.value.regel == "export-bestaetigung"


def test_postfach_marker_zaehlt_nur_in_security_md(tmp_path: Path) -> None:
    """Quelltext und Tests, die den Marker nennen (diese Datei, export.py), sperren nichts."""
    _schreibe(tmp_path, {"nachweise/tests/x.py": f"MARKER = '{MARKER}'\n"})
    _schreibe(tmp_path, {"SECURITY.md": "Kontakt: security@example.org\n"})
    export.pruefe_postfach(tmp_path)
    _schreibe(tmp_path, {"SECURITY.md": f"Kontakt\n{MARKER}\n"})
    with pytest.raises(NachweisFehler) as e:
        export.pruefe_postfach(tmp_path)
    assert e.value.regel == "export-postfach"


@pytest.mark.parametrize("postfach_vermerken", [False, True])
def test_fehlendes_security_md_sperrt(tmp_path: Path, postfach_vermerken: bool) -> None:
    """Fehlt SECURITY.md im Export (z. B. aus der Positivliste gefallen) oder ist sie
    unlesbar, gibt es keinen Meldeweg — Abbruch, im Release wie im eigenständigen Export."""
    with pytest.raises(NachweisFehler) as e:
        export.postfach_sperre(tmp_path)
    assert e.value.regel == "export-postfach" and "fehlt" in e.value.text
    with pytest.raises(NachweisFehler):
        export.pruefe_postfach(tmp_path)
    (tmp_path / "SECURITY.md").mkdir()
    with pytest.raises(NachweisFehler) as e:
        export.postfach_sperre(tmp_path)
    assert e.value.regel == "export-postfach"
    (tmp_path / "SECURITY.md").rmdir()
    (tmp_path / "SECURITY.md").write_bytes(b"\xff\xfe kaputt")
    with pytest.raises(NachweisFehler) as e:
        export.postfach_sperre(tmp_path)
    assert e.value.regel == "export-postfach"


def test_exportiere_ohne_security_md_bricht_ab(repo: Path, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export, "_lauf", _Laeufer())
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    _schreibe(repo, {"veroeffentlichung/positivliste.txt": POSITIVLISTE + "!SECURITY.md\n"})
    _commit(repo)
    for vermerken in (False, True):
        with pytest.raises(NachweisFehler) as e:
            _exportiere(repo, tmp_path, f"ohne-{vermerken}", vorheriges=None, bestaetigt=None,
                        postfach_vermerken=vermerken)
        assert e.value.regel == "export-postfach"


def test_release_export_vermerkt_offenes_postfach(repo: Path, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """In der Release-Kette sperrt der Marker die Veröffentlichung, nicht den lokalen
    Export — alle Prüfungen laufen, das Ergebnis trägt veroeffentlichbar = false mit Grund."""
    monkeypatch.setattr(export, "_lauf", _Laeufer())
    monkeypatch.setattr(export, "_gitleaks_lauf", _gitleaks_sauber)
    _schreibe(repo, {
        "veroeffentlichung/positivliste.txt": POSITIVLISTE + "SECURITY.md\n",
        "SECURITY.md": f"# Sicherheit\n\n{MARKER}\n",
        "web/package.json": json.dumps({"allowScripts": {}}),
    })
    _commit(repo)
    with pytest.raises(NachweisFehler) as e:
        _exportiere(repo, tmp_path, "e1", vorheriges=None, bestaetigt=None,
                    postfach_vermerken=True)
    assert e.value.regel == "export-bestaetigung"  # nicht export-postfach
    wert = re.search(r"--bestaetigt ([0-9a-f]{64})", e.value.text)
    assert wert is not None
    erg = _exportiere(repo, tmp_path, "e2", vorheriges=None, bestaetigt=wert.group(1),
                      postfach_vermerken=True)
    assert erg.manifest.veroeffentlichbar is False
    assert erg.manifest.sperrgrund is not None and "SECURITY.md:3" in erg.manifest.sperrgrund
    daten = json.loads(erg.manifest.als_json())
    assert daten["veroeffentlichbar"] is False and "SECURITY.md:3" in daten["sperrgrund"]
    # Standalone (Veröffentlichungsweg) verweigert weiterhin.
    with pytest.raises(NachweisFehler) as e3:
        _exportiere(repo, tmp_path, "e3", vorheriges=None, bestaetigt=wert.group(1))
    assert e3.value.regel == "export-postfach"


def test_exportmanifest_veroeffentlichbar_rundreise(tmp_path: Path) -> None:
    gesperrt = Exportmanifest([("a.txt", "0" * 64, "100644")], veroeffentlichbar=False,
                              sperrgrund="SECURITY.md:3 Marker")
    pfad = tmp_path / "m.json"
    pfad.write_text(gesperrt.als_json(), encoding="utf-8")
    assert lade_manifest(pfad) == gesperrt
    frei = Exportmanifest([("a.txt", "0" * 64, "100644")])
    pfad.write_text(frei.als_json(), encoding="utf-8")
    geladen = lade_manifest(pfad)
    assert geladen.veroeffentlichbar is True and geladen.sperrgrund is None
    # Älteres Manifest ohne Feld: veröffentlichbar (damals gab es die Sperre noch nicht).
    pfad.write_text(json.dumps({"dateien": [{"pfad": "a.txt", "sha256": "0" * 64,
                                             "modus": "100644"}]}), encoding="utf-8")
    assert lade_manifest(pfad).veroeffentlichbar is True
    for kaputt in ({"veroeffentlichbar": "nein"}, {"veroeffentlichbar": False},
                   {"veroeffentlichbar": True, "sperrgrund": "x"}):
        pfad.write_text(json.dumps({"dateien": [], **kaputt}), encoding="utf-8")
        with pytest.raises(NachweisFehler):
            lade_manifest(pfad)


def test_lauf_filtert_aufruferumgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    """Befehle des Exports erben `GIT_…`/`VITE_…`/`NODE_OPTIONS` nicht."""
    for name in ("GIT_DIR", "VITE_STANDARDPAKET", "NODE_OPTIONS"):
        monkeypatch.setenv(name, "eingeschleust")
    ausgabe = export._lauf(["env"], "probe").decode()
    namen = {z.split("=", 1)[0] for z in ausgabe.splitlines()}
    assert not namen & {"GIT_DIR", "VITE_STANDARDPAKET", "NODE_OPTIONS"}


@pytest.mark.skipif(not (REPO_WURZEL / "nachweise" / "sast" / "regeln.lock").is_file(),
                    reason="regeln.lock liegt nur im privaten Repo (nicht im Export)")
def test_vendierter_semgrep_regelsatz_nie_im_export() -> None:
    """Die Semgrep Rules License erlaubt keine Weiterverbreitung: kein Pfad unter
    `nachweise/sast/regeln/` (laut Lock, dazu künftige Dateien) wird öffentlich. Das Lock bleibt
    ebenfalls privat: es gehört zum Regelsatz und ist ohne ihn nicht prüfbar, und seine
    Prüfsummen hält der Geheimnis-Scan des Exports für Schlüssel."""
    from scanview_nachweise import sast
    positivliste = Positivliste(lies_positivliste(
        (REPO_WURZEL / "veroeffentlichung" / "positivliste.txt").read_text(encoding="utf-8")))
    _quelle, dateien = sast.lade_regellock(REPO_WURZEL / "nachweise" / sast.REGELN_LOCK)
    kandidaten = [f"nachweise/{sast.REGELN.as_posix()}/{p}" for p in dateien]
    kandidaten.append(f"nachweise/{sast.REGELN.as_posix()}/neu/regel.yaml")
    kandidaten.append(f"nachweise/{sast.REGELN_LOCK.as_posix()}")
    assert [p for p in kandidaten if positivliste.nimmt_auf(p)] == []


def test_renovate_konfiguration_ist_in_der_positivliste() -> None:
    """Die öffentliche Doku verweist auf `renovate.json`; ohne Aufnahme wäre der Verweis ein toter
    Link im Export."""
    positivliste = Positivliste(lies_positivliste(
        (REPO_WURZEL / "veroeffentlichung" / "positivliste.txt").read_text(encoding="utf-8")))
    assert positivliste.nimmt_auf("renovate.json")
