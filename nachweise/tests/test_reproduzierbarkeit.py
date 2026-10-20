"""Build-Rückverfolgbarkeit messen: `vergleiche_builds` rein auf Testverzeichnissen,
`web_build_messen` mit injizierter Bau-Funktion (kein echter npm-Lauf) gegen ein kleines
Test-Repo, dazu die CLI-Anbindung `messen web-build`."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from scanview_nachweise import reproduzierbarkeit
from scanview_nachweise.cli import main
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.reproduzierbarkeit import Vergleich, vergleiche_builds, web_build_messen


def _schreibe(wurzel: Path, dateien: dict[str, str]) -> None:
    for relativ, inhalt in dateien.items():
        pfad = wurzel / relativ
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(inhalt, encoding="utf-8")


# --- vergleiche_builds --------------------------------------------------------------------------


def test_gleicher_baum_ist_gleich(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _schreibe(a, {"index.html": "<html/>", "assets/app.js": "console.log(1)"})
    _schreibe(b, {"index.html": "<html/>", "assets/app.js": "console.log(1)"})
    assert vergleiche_builds(a, b) == Vergleich(gleich=True, abweichend=[])


def test_geaenderte_datei_ist_abweichend(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _schreibe(a, {"assets/app.js": "console.log(1)"})
    _schreibe(b, {"assets/app.js": "console.log(2)"})
    v = vergleiche_builds(a, b)
    assert v.gleich is False
    assert v.abweichend == ["unterschiedlich: assets/app.js"]


def test_zusaetzliche_datei_ist_abweichend(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _schreibe(a, {"index.html": "x", "nur-a.txt": "x"})
    _schreibe(b, {"index.html": "x", "nur-b.txt": "y"})
    v = vergleiche_builds(a, b)
    assert v.gleich is False
    assert v.abweichend == ["nur in a: nur-a.txt", "nur in b: nur-b.txt"]


def test_verschachtelter_pfad_wird_verglichen(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _schreibe(a, {"assets/wasm/tief/verschachtelt.wasm": "abc"})
    _schreibe(b, {"assets/wasm/tief/verschachtelt.wasm": "abc"})
    assert vergleiche_builds(a, b).gleich is True


def test_fehlendes_verzeichnis_ist_nachweisfehler(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    with pytest.raises(NachweisFehler) as e:
        vergleiche_builds(a, tmp_path / "fehlt")
    assert e.value.regel == "build-vergleich"


def test_symlink_gleiches_ziel_ist_gleich(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _schreibe(a, {"index.html": "x"})
    _schreibe(b, {"index.html": "x"})
    # Das Ziel muss nicht existieren — verglichen wird der Linktext, nicht das, worauf er zeigt
    # (Symlinks werden nicht verfolgt).
    (a / "verweis.txt").symlink_to("./nicht-vorhanden")
    (b / "verweis.txt").symlink_to("./nicht-vorhanden")
    assert vergleiche_builds(a, b) == Vergleich(gleich=True, abweichend=[])


def test_symlink_unterschiedliches_ziel_ist_abweichend(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _schreibe(a, {"index.html": "x"})
    _schreibe(b, {"index.html": "x"})
    (a / "verweis.txt").symlink_to("./ziel-a")
    (b / "verweis.txt").symlink_to("./ziel-b")
    v = vergleiche_builds(a, b)
    assert v.gleich is False
    assert v.abweichend == ["unterschiedliches Linkziel: verweis.txt"]


def test_symlink_nur_in_einem_baum_ist_abweichend(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _schreibe(a, {"index.html": "x"})
    _schreibe(b, {"index.html": "x"})
    ziel_ausserhalb = tmp_path / "ausserhalb.txt"
    ziel_ausserhalb.write_text("geheim", encoding="utf-8")
    (a / "verweis.txt").symlink_to(ziel_ausserhalb)
    v = vergleiche_builds(a, b)
    assert v.gleich is False
    assert v.abweichend == ["nur in a: verweis.txt"]
    # Das Ziel wird nicht verfolgt: der Inhalt von ausserhalb.txt landet nicht im Vergleich.
    assert "geheim" not in json.dumps(v.abweichend)


# --- web_build_messen -----------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    wurzel = tmp_path / "repo"
    wurzel.mkdir()
    _git(wurzel, "init", "-q", "-b", "main")
    _schreibe(wurzel, {"web/package.json": "{}", "README.md": "x"})
    _git(wurzel, "add", "-A")
    _git(wurzel, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-q", "-m", "initial")
    yield wurzel
    # Falls ein Test einen Worktree stehen lässt, hier zusätzlich aufräumen statt einen Fehler
    # in einem späteren Test zu verursachen.
    subprocess.run(["git", "worktree", "prune"], cwd=wurzel, capture_output=True, check=False)


def _bau_gleich(worktree: Path) -> None:
    _schreibe(worktree, {"web/dist/index.html": "<html/>", "web/dist/app.js": "x"})


def _bau_ungleich_fabrik() -> reproduzierbarkeit.BauFunktion:
    zaehler = {"n": 0}

    def _bauen(worktree: Path) -> None:
        zaehler["n"] += 1
        _schreibe(worktree, {"web/dist/index.html": "<html/>",
                             "web/dist/app.js": f"x{zaehler['n']}"})
    return _bauen


def _worktree_pfade(repo: Path) -> list[str]:
    r = _git(repo, "worktree", "list", "--porcelain")
    return [z.removeprefix("worktree ") for z in r.stdout.splitlines() if z.startswith("worktree ")]


def test_web_build_messen_gleich_schreibt_json_und_raeumt_auf(repo: Path, tmp_path: Path) -> None:
    arbeitsordner = tmp_path / "arbeit"
    v = web_build_messen(repo, arbeitsordner=arbeitsordner, bauen=_bau_gleich)
    assert v == Vergleich(gleich=True, abweichend=[])

    ausgabe = json.loads((arbeitsordner / "build-vergleich.json").read_text(encoding="utf-8"))
    assert ausgabe["gleich"] is True
    assert ausgabe["abweichend"] == []
    assert ausgabe["commit"]
    assert ausgabe["baum_web"]

    # Worktrees sind wieder weg …
    assert not (arbeitsordner / "a").exists()
    assert not (arbeitsordner / "b").exists()
    pfade = _worktree_pfade(repo)
    assert str(arbeitsordner / "a") not in pfade
    assert str(arbeitsordner / "b") not in pfade
    # … aber die Bau-Ergebnisse bleiben zur Nachprüfung erhalten.
    assert (arbeitsordner / "dist-a" / "index.html").is_file()
    assert (arbeitsordner / "dist-b" / "index.html").is_file()


def test_web_build_messen_abweichend(repo: Path, tmp_path: Path) -> None:
    arbeitsordner = tmp_path / "arbeit"
    v = web_build_messen(repo, arbeitsordner=arbeitsordner, bauen=_bau_ungleich_fabrik())
    assert v.gleich is False
    assert v.abweichend == ["unterschiedlich: app.js"]


def test_web_build_messen_bau_schlaegt_fehl_raeumt_trotzdem_auf(repo: Path,
                                                                tmp_path: Path) -> None:
    arbeitsordner = tmp_path / "arbeit"

    def _kaputt(worktree: Path) -> None:
        raise RuntimeError("bau kaputt")

    with pytest.raises(RuntimeError, match="bau kaputt"):
        web_build_messen(repo, arbeitsordner=arbeitsordner, bauen=_kaputt)
    assert not (arbeitsordner / "a").exists()
    assert str(arbeitsordner / "a") not in _worktree_pfade(repo)


def test_web_build_messen_leerer_dist_ist_nachweisfehler(repo: Path, tmp_path: Path) -> None:
    arbeitsordner = tmp_path / "arbeit"
    with pytest.raises(NachweisFehler) as e:
        web_build_messen(repo, arbeitsordner=arbeitsordner, bauen=lambda _worktree: None)
    assert e.value.regel == "build-messen"
    assert not (arbeitsordner / "a").exists()


def test_web_build_messen_dist_nur_mit_leerem_unterordner_ist_nachweisfehler(
    repo: Path, tmp_path: Path,
) -> None:
    # dist/ existiert und `rglob("*")` liefert etwas (den Unterordner selbst) — die Prüfung muss
    # trotzdem NachweisFehler werfen, weil keine einzige Datei drinsteckt.
    def _nur_leerer_unterordner(worktree: Path) -> None:
        (worktree / "web" / "dist" / "leer").mkdir(parents=True)

    arbeitsordner = tmp_path / "arbeit"
    with pytest.raises(NachweisFehler) as e:
        web_build_messen(repo, arbeitsordner=arbeitsordner, bauen=_nur_leerer_unterordner)
    assert e.value.regel == "build-messen"
    assert "fehlt oder ist leer" in e.value.text


def test_web_build_messen_vorhandener_zielordner_ist_nachweisfehler(repo: Path,
                                                                    tmp_path: Path) -> None:
    arbeitsordner = tmp_path / "arbeit"
    (arbeitsordner / "a").mkdir(parents=True)
    with pytest.raises(NachweisFehler, match="existiert schon"):
        web_build_messen(repo, arbeitsordner=arbeitsordner, bauen=_bau_gleich)
    assert str(arbeitsordner / "b") not in _worktree_pfade(repo)


def test_web_build_messen_aufraeumen_fehlgeschlagen_wird_gemeldet(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path,
) -> None:
    # Injizierter fehlschlagender Remove statt eines echten Git-Fehlers: `_worktree_entfernen`
    # meldet für jeden Worktree „nicht entfernt“.
    monkeypatch.setattr(reproduzierbarkeit, "_worktree_entfernen", lambda _wurzel, _pfad: False)
    arbeitsordner = tmp_path / "arbeit"
    with pytest.raises(NachweisFehler) as e:
        web_build_messen(repo, arbeitsordner=arbeitsordner, bauen=_bau_gleich)
    assert e.value.regel == "messen-aufraeumen"
    # Der Vergleich selbst ist gelungen und steht — trotz des Aufräum-Fehlers — vollständig in
    # der JSON, nur der Exit-Code zeigt fail-closed den Aufräum-Fehler an.
    ausgabe = json.loads((arbeitsordner / "build-vergleich.json").read_text(encoding="utf-8"))
    assert ausgabe["gleich"] is True
    assert sorted(ausgabe["aufraeumen_fehlgeschlagen"]) == sorted(
        str(arbeitsordner / n) for n in ("a", "b"))


def test_web_build_messen_urspruengliche_ausnahme_hat_vorrang_vor_aufraeumfehler(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path,
) -> None:
    # Bau UND Aufräumen schlagen fehl: die Bau-Ausnahme muss durchkommen, nicht maskiert durch
    # einen Aufräum-Fehler aus dem finally-Block.
    monkeypatch.setattr(reproduzierbarkeit, "_worktree_entfernen", lambda _wurzel, _pfad: False)
    arbeitsordner = tmp_path / "arbeit"

    def _kaputt(worktree: Path) -> None:
        raise RuntimeError("bau kaputt")

    with pytest.raises(RuntimeError, match="bau kaputt"):
        web_build_messen(repo, arbeitsordner=arbeitsordner, bauen=_kaputt)


def test_web_build_messen_aufraeumen_erfolgreich_ohne_meldung(repo: Path, tmp_path: Path) -> None:
    arbeitsordner = tmp_path / "arbeit"
    web_build_messen(repo, arbeitsordner=arbeitsordner, bauen=_bau_gleich)
    ausgabe = json.loads((arbeitsordner / "build-vergleich.json").read_text(encoding="utf-8"))
    assert ausgabe["aufraeumen_fehlgeschlagen"] == []


def test_web_build_messen_eigene_ausgabe(repo: Path, tmp_path: Path) -> None:
    ausgabe = tmp_path / "irgendwo" / "ergebnis.json"
    web_build_messen(repo, arbeitsordner=tmp_path / "arbeit", ausgabe=ausgabe, bauen=_bau_gleich)
    assert ausgabe.is_file()
    assert not (tmp_path / "arbeit" / "build-vergleich.json").exists()


# --- CLI --------------------------------------------------------------------------------------


def test_cli_messen_web_build(monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path,
                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(reproduzierbarkeit, "npm_build", _bau_gleich)
    arbeitsordner = tmp_path / "arbeit"
    exit_code = main(["messen", "web-build", "--wurzel", str(repo),
                      "--arbeitsordner", str(arbeitsordner)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "gleich" in out
    assert (arbeitsordner / "build-vergleich.json").is_file()


def test_cli_messen_web_build_abweichend_bleibt_exit_0(monkeypatch: pytest.MonkeyPatch,
                                                        repo: Path, tmp_path: Path) -> None:
    monkeypatch.setattr(reproduzierbarkeit, "npm_build", _bau_ungleich_fabrik())
    arbeitsordner = tmp_path / "arbeit"
    exit_code = main(["messen", "web-build", "--wurzel", str(repo),
                      "--arbeitsordner", str(arbeitsordner)])
    # Kein Gate: eine Abweichung meldet sich in der Ausgabe, blockiert aber nicht.
    assert exit_code == 0


def test_ausfuehren_mit_cwd(tmp_path: Path) -> None:
    from scanview_nachweise.werkzeuge import ausfuehren
    r = ausfuehren([sys.executable, "-c", "import os; print(os.getcwd())"], "probe", cwd=tmp_path)
    assert r.stdout.strip() == str(tmp_path)
