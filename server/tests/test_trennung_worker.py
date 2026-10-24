"""Trennung Webdienst ↔ Import-Worker (R-E-F-1, Abnahme E-F 5).

- Der Webdienst (`scanview_server`) startet keine Prozesse: einzige Ausnahme bleibt `git` in
  `gitexport.py`. Jeder weitere Prozessstart oder die Zeichenketten `podman`/`docker` brechen den
  Test. Der Worker liegt deshalb in einem eigenen Paket (`scanview_worker`).
- Der Worker lädt vom Webdienst nur `auftraege` — keine Server-DB-, Sitzungs- oder Kontenmodule.
- Die Profilliste des Servers entspricht den mitgelieferten Profilen des Prüfers.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from scanview_server import auftraege

SRC = Path(__file__).resolve().parent.parent / "src"
SERVER = SRC / "scanview_server"
PROFILE = (Path(__file__).resolve().parents[2] / "import" / "src" / "scanview_import" / "pruefen"
           / "profile")

# Aufrufe, die einen Prozess starten oder ersetzen.
_OS_PROZESS = {"system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
               *(f"exec{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
               *(f"spawn{s}" for s in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe"))}
_MODULE = {"subprocess", "multiprocessing", "pty", "concurrent.futures.process"}


def _prozessstarts(datei: Path) -> list[str]:
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    treffer: list[str] = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            treffer += [a.name for a in knoten.names if a.name in _MODULE]
        elif isinstance(knoten, ast.ImportFrom):
            if knoten.module in _MODULE or (knoten.module == "os" and any(
                    a.name in _OS_PROZESS for a in knoten.names)):
                treffer.append(f"from {knoten.module}")
        elif isinstance(knoten, ast.Attribute):
            if (isinstance(knoten.value, ast.Name) and knoten.value.id == "os"
                    and knoten.attr in _OS_PROZESS):
                treffer.append(f"os.{knoten.attr}")
            if knoten.attr in ("create_subprocess_exec", "create_subprocess_shell"):
                treffer.append(knoten.attr)
    return treffer


def test_webdienst_startet_keine_prozesse_ausser_git() -> None:
    funde: dict[str, list[str]] = {}
    for datei in sorted(SERVER.rglob("*.py")):
        t = _prozessstarts(datei)
        text = datei.read_text(encoding="utf-8").lower()
        t += [w for w in ("podman", "docker") if w in text]
        if t:
            funde[datei.name] = t
    assert funde == {"gitexport.py": ["subprocess"]}


def test_gitexport_startet_nur_git() -> None:
    baum = ast.parse((SERVER / "gitexport.py").read_text(encoding="utf-8"))
    programme = set()
    for k in ast.walk(baum):
        if isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute) \
                and k.func.attr in ("run", "Popen", "check_output", "check_call", "call"):
            erstes = k.args[0] if k.args else None
            if isinstance(erstes, ast.List) and erstes.elts and isinstance(
                    erstes.elts[0], ast.Constant):
                programme.add(erstes.elts[0].value)
            else:
                programme.add(ast.unparse(erstes) if erstes is not None else "?")
    # eine einzige Aufrufstelle: `_git(…, git: str = "git")` → [git, *_GIT_OPTIONEN, *args]
    assert programme == {"[git, *_GIT_OPTIONEN, *args]"}, programme
    [funktion] = [k for k in ast.walk(baum)
                  if isinstance(k, ast.FunctionDef) and k.name == "_git"]
    standard = {a.arg: d for a, d in zip(funktion.args.kwonlyargs, funktion.args.kw_defaults,
                                         strict=True)}
    assert isinstance(standard["git"], ast.Constant) and standard["git"].value == "git"


def test_der_pruefer_findet_prozessstarts() -> None:
    """Gegenprobe: der Worker startet Prozesse, also muss der Prüfer dort anschlagen."""
    assert _prozessstarts(SRC / "scanview_worker" / "laeufer.py") == ["subprocess"]


def _module_nach_import(code: str) -> list[str]:
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                       check=True, timeout=60)
    return r.stdout.split()


def test_worker_laedt_vom_webdienst_nur_auftraege() -> None:
    geladen = _module_nach_import(
        "import sys, scanview_worker.worker\n"
        "print(*sorted(m for m in sys.modules if m.startswith('scanview_')))")
    assert [m for m in geladen if m.startswith("scanview_server")] == [
        "scanview_server", "scanview_server.auftraege"]
    fremd = _module_nach_import(
        "import sys, scanview_worker.worker\n"
        "print(*[m for m in ('fastapi', 'starlette', 'argon2', 'webauthn', 'uvicorn')"
        " if m in sys.modules])")
    assert fremd == []


def test_webdienst_laedt_den_worker_nicht() -> None:
    geladen = _module_nach_import(
        "import sys, pkgutil, importlib, scanview_server\n"
        "for m in pkgutil.walk_packages(scanview_server.__path__, 'scanview_server.'):\n"
        "    importlib.import_module(m.name)\n"
        "from scanview_server.config import Settings\n"
        "from scanview_server.web import create_app\n"
        "print(*sorted(m for m in sys.modules if m.startswith('scanview_worker')))")
    assert geladen == []


def test_profilliste_entspricht_den_profilen_des_pruefers() -> None:
    assert PROFILE.is_dir(), PROFILE
    assert sorted(auftraege.PROFILE) == sorted(p.stem for p in PROFILE.glob("*.toml"))
    assert auftraege.PROFIL_STANDARD in auftraege.PROFILE


def test_jeder_verfuegbare_typ_hat_ein_budget() -> None:
    assert set(auftraege.BUDGETS) == set(auftraege.VERFUEGBAR)
    assert auftraege.VERFUEGBAR <= set(auftraege.TYPEN)
