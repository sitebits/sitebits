"""Wochenscan: alle unterstützten Versionen gegen eine frische Schwachstellen-DB, fail-closed.
trivy ist eine Attrappe; Manifest, Signatur und Git-Commit sind echt (Wegwerf-Schlüssel)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import signierhilfe
from test_archiv import _manifest
from test_betrieb import _schluessel

from scanview_nachweise import cli, osv, scan, wochenscan
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.manifest import kanonisch
from scanview_nachweise.scan import DbStand, ScanErgebnis, Treffer
from scanview_nachweise.werkzeuge import Werkzeug

DB_ZEIT = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
JETZT = DB_ZEIT + timedelta(hours=2)
LOCK_ALT = b"lock alt\n"
LOCK_NEU = b"lock neu\n"
SERVER = "pkg:pypi/demo@1.0.0"
BEGRUENDUNG = "Der verwundbare Pfad wird im Server nirgends aufgerufen (geprüft 2026-10-01)"
TRIVY = Werkzeug("trivy", "aquasec/trivy@sha256:" + "1" * 64, "0.74.0", None)


def _sha(daten: bytes) -> str:
    return "sha256:" + hashlib.sha256(daten).hexdigest()


class Welt:
    """Archiv (signiertes Release 0.1.0 samt SBOMs), Repo mit Release-Commit, DB-Cache, VEX."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        key, self.signers = _schluessel(tmp / "schluessel")
        self.key = key
        self.repo = signierhilfe.repo(tmp / "repo", key)
        for pfad in ("web/package-lock.json", "server/uv.lock", "mcp-server/uv.lock",
                     "import/uv.lock"):
            (self.repo / pfad).parent.mkdir(exist_ok=True)
            (self.repo / pfad).write_bytes(LOCK_ALT)
        signierhilfe.git(self.repo, "add", "-A")
        signierhilfe.git(self.repo, "commit", "-q", "--no-gpg-sign", "-m", "release")
        self.commit = signierhilfe.git(self.repo, "rev-parse", "HEAD")
        self.archiv = tmp / "archiv"
        (self.archiv / "betrieb").mkdir(parents=True)
        self.schreibe_unterstuetzt(["0.1.0"])
        self.release("0.1.0")
        self.cache = tmp / "cache"
        self.vex = tmp / "vex.openvex.json"
        self.schreibe_vex([])
        self.treffer: list[Treffer] = []
        self.gescannt: list[str] = []
        self.aufrufe: list[tuple[list[Path], Path, Path]] = []

    def schreibe_unterstuetzt(self, versionen: list[str]) -> None:
        liste = ", ".join(f'"{v}"' for v in versionen)
        (self.archiv / "betrieb" / "unterstuetzt.toml").write_text(
            f'versionen = [{liste}]\nstand = {{L = "0.1.0", S = "nicht ausgewertet"}}\n'
            "aktualisiert = 2026-10-01\n", encoding="utf-8")

    def release(self, version: str) -> Path:
        ordner = self.archiv / "releases" / version
        (ordner / "sbom").mkdir(parents=True)
        daten = _manifest(version)
        daten["quellcommit"] = self.commit
        for name in ("web", "server", "mcp"):
            roh = f'{{"sbom":"{name}"}}'.encode()
            (ordner / "sbom" / f"scanview-{name}-{version}.cdx.json").write_bytes(roh)
            daten["sboms"][f"scanview-{name}-{version}.cdx.json"] = hashlib.sha256(  # type: ignore[index]
                roh).hexdigest()
        self.manifest = ordner / "manifest.json"
        self.manifest.write_bytes(kanonisch(daten))
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(self.key), "-n",
                        "scanview-release", str(self.manifest)], check=True,
                       stdin=subprocess.DEVNULL, capture_output=True)
        return ordner

    def schreibe_vex(self, aussagen: list[dict[str, Any]]) -> None:
        self.vex.write_text(json.dumps({
            "@context": "https://openvex.dev/ns/v0.2.0", "@id": "urn:test:vex", "author": "T",
            "timestamp": "2026-10-01T00:00:00Z", "version": 1, "statements": aussagen}))

    def db(self, zeit: datetime = DB_ZEIT) -> None:
        self.stand = DbStand(zeit, "0" * 64, "UpdatedAt")

    def lauf(self, ausgabe: str = "aus", **kw: Any) -> wochenscan.Wochenergebnis:
        return wochenscan.wochenscan(
            self.archiv, self.repo, self.vex, kw.pop("signers", self.signers), self.cache,
            {"trivy": TRIVY}, self.tmp / ausgabe, bekannt=kw.pop("bekannt", set()),
            jetzt=kw.pop("jetzt", JETZT))


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Welt:
    w = Welt(tmp_path)
    w.db()

    def scanne_alle(sboms: dict[str, Path], cache: Path, vex: Path, werkzeuge: object,
                    *, uhr: datetime, osv: object = None) -> ScanErgebnis:
        # Die Attrappe prüft die DB-Frische wie das echte `scanne_alle`.
        if uhr - w.stand.zeit > scan.DB_HOECHSTALTER:
            raise NachweisFehler("db-alter", "trivy-DB zu alt")
        w.gescannt += sorted(sboms)
        w.aufrufe.append((list(sboms.values()), cache, vex))
        return ScanErgebnis(w.stand, w.treffer,
                            {n: {"sbom": p.name, "treffer": {}} for n, p in sboms.items()})

    monkeypatch.setattr(scan, "scanne_alle", scanne_alle)
    return w


def _treffer(id_: str = "CVE-2099-0001", schwere: str = "hoch", artefakt: str = "server"
             ) -> Treffer:
    return Treffer(id_, SERVER, "1.0.0", schwere, None, artefakt=artefakt, bom_ref=SERVER)


def _aussage(lock: bytes) -> dict[str, Any]:
    return {"vulnerability": {"name": "CVE-2099-0001"}, "products": [{"@id": SERVER}],
            "status": "not_affected", "justification": "vulnerable_code_not_in_execute_path",
            "impact_statement": BEGRUENDUNG, "timestamp": "2026-09-30T00:00:00Z",
            "scanview": {"lock_hash": _sha(lock), "lockdatei": "server/uv.lock",
                         "pruefer": "Andre"}}


# --- Eingaben ------------------------------------------------------------------------------------

def test_ohne_unterstuetzte_version_fehler(welt):
    (welt.archiv / "betrieb" / "unterstuetzt.toml").unlink()
    with pytest.raises(NachweisFehler) as e:
        welt.lauf()
    assert e.value.regel == "wochenscan-versionen"
    welt.schreibe_unterstuetzt([])
    with pytest.raises(NachweisFehler) as e:
        welt.lauf()
    assert e.value.regel == "wochenscan-versionen"


def test_fremder_anker_fehler(welt, tmp_path):
    _, fremd = _schluessel(tmp_path / "fremd")
    with pytest.raises(NachweisFehler) as e:
        welt.lauf(signers=fremd)
    assert e.value.regel == "signatur"


def test_archivierte_anker_kopie_zaehlt_nicht(welt, tmp_path):
    # Eine neben dem Manifest liegende Kopie darf den übergebenen Anker nie ersetzen.
    _, fremd = _schluessel(tmp_path / "fremd")
    (welt.archiv / "releases" / "0.1.0" / "allowed_signers").write_bytes(welt.signers.read_bytes())
    with pytest.raises(NachweisFehler) as e:
        welt.lauf(signers=fremd)
    assert e.value.regel == "signatur"


def test_sbom_hash_veraendert_fehler(welt):
    sbom = welt.archiv / "releases" / "0.1.0" / "sbom" / "scanview-web-0.1.0.cdx.json"
    sbom.write_bytes(b'{"sbom":"verfaelscht"}')
    with pytest.raises(NachweisFehler) as e:
        welt.lauf()
    assert e.value.regel == "wochenscan-sbom"
    assert welt.gescannt == []


def test_sbom_fehlt_fehler(welt):
    (welt.archiv / "releases" / "0.1.0" / "sbom" / "scanview-mcp-0.1.0.cdx.json").unlink()
    with pytest.raises(NachweisFehler) as e:
        welt.lauf()
    assert e.value.regel == "wochenscan-sbom"


def test_ausgabe_existiert_fehler(welt):
    (welt.tmp / "aus").mkdir()
    with pytest.raises(NachweisFehler) as e:
        welt.lauf()
    assert e.value.regel == "wochenscan-ausgabe"


def test_db_25_stunden_alt_fehler(welt):
    with pytest.raises(NachweisFehler) as e:
        welt.lauf(jetzt=DB_ZEIT + timedelta(hours=25))
    assert e.value.regel == "db-alter"


# --- Ergebnis ------------------------------------------------------------------------------------

def test_sauber(welt):
    erg = welt.lauf()
    assert erg.versionen == ["0.1.0"] and erg.blockierend == {} and erg.neu == []
    assert welt.gescannt == ["mcp", "server", "web"]


def test_treffer_ohne_vex_blockiert_und_ist_neu(welt):
    welt.treffer = [_treffer()]
    erg = welt.lauf()
    assert list(erg.blockierend) == ["0.1.0"]
    assert erg.blockierend["0.1.0"][0].startswith("CVE-2099-0001 (hoch)")
    assert erg.neu == ["CVE-2099-0001"]


def test_bekannter_treffer_ist_nicht_neu(welt):
    welt.treffer = [_treffer()]
    erg = welt.lauf(bekannt={"CVE-2099-0001"})
    assert list(erg.blockierend) == ["0.1.0"] and erg.neu == []


def test_nicht_ausgelieferte_artefakte_blockieren_nicht(welt):
    welt.treffer = [_treffer(artefakt="import")]
    assert welt.lauf().blockierend == {}


def test_lock_hash_stammt_aus_dem_release_commit(welt):
    welt.schreibe_vex([_aussage(LOCK_ALT)])
    welt.treffer = [_treffer()]
    # HEAD ändert die Lockdatei; die Aussage gilt für den Hash des Release-Commits weiter.
    (welt.repo / "server" / "uv.lock").write_bytes(LOCK_NEU)
    signierhilfe.git(welt.repo, "commit", "-q", "--no-gpg-sign", "-am", "update")
    assert welt.lauf().blockierend == {}
    welt.schreibe_vex([_aussage(LOCK_NEU)])
    assert list(welt.lauf("aus2").blockierend) == ["0.1.0"]


def test_lock_hashes_im_commit_unbekannter_commit(welt):
    with pytest.raises(NachweisFehler) as e:
        wochenscan.lock_hashes_im_commit(welt.repo, "f" * 40)
    assert e.value.regel == "scan-lock"
    with pytest.raises(NachweisFehler):
        wochenscan.lock_hashes_im_commit(welt.repo, "--output=x")


# --- CLI -----------------------------------------------------------------------------------------

@pytest.fixture
def cli_args(welt, monkeypatch):
    monkeypatch.setattr(cli, "lade_werkzeuge", lambda p: {"trivy": TRIVY})
    monkeypatch.setattr(scan, "jetzt", lambda: JETZT)
    (welt.tmp / "bekannt.txt").write_text("# bekannt\nCVE-2099-0001\n")
    return ["wochenscan", "--archiv", str(welt.archiv), "--repo", str(welt.repo),
            "--vex", str(welt.vex), "--allowed-signers", str(welt.signers),
            "--cache", str(welt.cache), "--wurzel", str(welt.repo),
            "--ausgabe", str(welt.tmp / "cli-aus")]


def test_cli_exit_0_und_ergebnis_json(welt, cli_args):
    assert cli.main(cli_args) == 0
    daten = json.loads((welt.tmp / "cli-aus" / "ergebnis.json").read_text())
    assert daten["ergebnis"] == "grün" and daten["versionen"] == ["0.1.0"]
    assert daten["neu"] == [] and daten["db"]["feld"] == "UpdatedAt"


def test_cli_exit_1_bei_treffer(welt, cli_args, capsys):
    welt.treffer = [_treffer()]
    assert cli.main(cli_args) == 1
    daten = json.loads((welt.tmp / "cli-aus" / "ergebnis.json").read_text())
    assert daten["ergebnis"] == "blockiert" and daten["neu"] == ["CVE-2099-0001"]
    assert "BLOCKIERT" in capsys.readouterr().out


def test_cli_relative_pfade(welt, cli_args, monkeypatch):
    # Container-Mounts verlangen absolute Pfade; die Kommandozeile löst relative selbst auf.
    monkeypatch.chdir(welt.tmp)
    rel = [a if not a.startswith(str(welt.tmp)) else str(Path(a).relative_to(welt.tmp))
           for a in cli_args]
    assert not Path(rel[rel.index("--cache") + 1]).is_absolute()
    assert cli.main(rel) == 0
    assert (welt.tmp / "cli-aus" / "ergebnis.json").is_file()
    sboms, cache, vex = welt.aufrufe[0]
    assert sboms and all(p.is_absolute() for p in sboms)
    assert cache.is_absolute() and vex.is_absolute()


def test_cli_ergebnis_nennt_den_anker(welt, cli_args, capsys):
    assert cli.main(cli_args) == 0
    daten = json.loads((welt.tmp / "cli-aus" / "ergebnis.json").read_text())
    assert daten["anker"] == {"datei": str(welt.signers.resolve()),
                              "sha256": hashlib.sha256(welt.signers.read_bytes()).hexdigest()}
    assert str(welt.signers.resolve()) in capsys.readouterr().out


def test_cli_mittlere_treffer_stehen_in_der_ausgabe(welt, cli_args, capsys):
    welt.treffer = [_treffer("CVE-2099-0007", "mittel")]
    assert cli.main(cli_args) == 0
    assert "HINWEIS 0.1.0: CVE-2099-0007 (mittel, nicht blockierend)" in capsys.readouterr().out


def test_cli_schreibfehler_ist_exit_2(welt, cli_args, monkeypatch, capsys):
    original = Path.write_text

    def kaputt(self: Path, *a: Any, **k: Any) -> int:
        if self.name == "ergebnis.json":
            raise PermissionError(13, "Permission denied")
        return original(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", kaputt)
    assert cli.main(cli_args) == 2
    assert "wochenscan-ausgabe" in capsys.readouterr().err


def test_cli_bekannt_datei(welt, cli_args):
    welt.treffer = [_treffer()]
    assert cli.main([*cli_args, "--bekannt", str(welt.tmp / "bekannt.txt")]) == 1
    daten = json.loads((welt.tmp / "cli-aus" / "ergebnis.json").read_text())
    assert daten["neu"] == []


def test_cli_exit_2_bei_fehler(welt, cli_args, capsys):
    (welt.archiv / "betrieb" / "unterstuetzt.toml").unlink()
    assert cli.main(cli_args) == 2
    assert "wochenscan-versionen" in capsys.readouterr().err


def test_cli_db_aktualisieren_vor_dem_scan(welt, cli_args, monkeypatch):
    aufrufe: list[Path] = []
    monkeypatch.setattr(scan, "db_aktualisieren", lambda c, w: aufrufe.append(c) or welt.stand)
    osv_abfragen: list[list[osv.Abfrage]] = []
    monkeypatch.setattr(osv, "aktualisieren",
                        lambda a, c: osv_abfragen.append(list(a))
                        or osv.OsvStand(welt.stand.zeit, {}))
    assert cli.main([*cli_args, "--db-aktualisieren"]) == 0
    assert aufrufe == [welt.cache]
    # Der OSV-Netzschritt läuft mit (die Testwelt hat keine Eigenbauten: leere Abfrage).
    assert osv_abfragen == [[]]


def test_main_modul_startet(tmp_path):
    ergebnis = subprocess.run(
        ["python3", "-m", "scanview_nachweise", "version"], capture_output=True, text=True,
        cwd=tmp_path, env={"PYTHONPATH": str(Path(__file__).parents[1] / "src"),
                           "PATH": "/usr/bin"})
    assert ergebnis.returncode == 0 and ergebnis.stdout.strip()
