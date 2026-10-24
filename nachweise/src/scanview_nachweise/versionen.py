"""Einheitliche Produktversion aller ausgelieferten Teilprojekte.

Ein Release trägt genau eine Version `X.Y.Z`. Sie steht in jedem ausgelieferten Teilprojekt
(Viewer/Portal `web`, Plattform-Server `server`, MCP-Server `mcp-server`, ab Manifest-Schema /3
das Import-Werkzeug `import`, dessen `__version__` aus den Paket-Metadaten kommt) und in der
Nachweiskette selbst (`nachweise`, dort zusätzlich die Konstante `_VERSION`, die
`scanview-nachweise version` ausgibt); die Release-Kette prüft beim Einfrieren, dass alle
übereinstimmen und der angeforderten Version entsprechen."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .fehler import NachweisFehler

REGEL = "versionen"
_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")

#: Die Version, die `scanview-nachweise version` ausgibt (Konstante `_VERSION` im CLI-Modul).
CLI_QUELLE = "nachweise/src/scanview_nachweise/cli.py#_VERSION"
_PYPROJECTS = ("server/pyproject.toml", "mcp-server/pyproject.toml", "import/pyproject.toml",
               "nachweise/pyproject.toml")

#: Quelle (Repo-relativ, bei der Sperrdatei mit Feld) → wo die Version steht.
TEILE = (
    "web/package.json",
    "web/package-lock.json",
    "web/package-lock.json#packages[\"\"]",
    *_PYPROJECTS,
    CLI_QUELLE,
)


def _lies(repo: Path, relativ: str) -> str:
    try:
        return (repo / relativ).read_text(encoding="utf-8")
    except OSError as e:
        raise NachweisFehler(REGEL, f"{relativ} nicht lesbar ({e.strerror})") from None


def _json(repo: Path, relativ: str) -> dict[str, Any]:
    try:
        daten = json.loads(_lies(repo, relativ))
    except json.JSONDecodeError as e:
        raise NachweisFehler(REGEL, f"{relativ} ist kein gültiges JSON ({e})") from None
    if not isinstance(daten, dict):
        raise NachweisFehler(REGEL, f"{relativ}: kein JSON-Objekt")
    return daten


def _pyproject(repo: Path, relativ: str) -> Any:
    try:
        daten = tomllib.loads(_lies(repo, relativ))
    except tomllib.TOMLDecodeError as e:
        raise NachweisFehler(REGEL, f"{relativ} ist kein gültiges TOML ({e})") from None
    projekt = daten.get("project")
    return projekt.get("version") if isinstance(projekt, dict) else None


def _cli_version(repo: Path) -> Any:
    """Der Wert der Zuweisung `_VERSION = "…"` auf Modulebene von `cli.py`, ohne das Modul zu
    importieren. Fehlt sie, gibt es sie mehrfach oder ist ihr Wert kein Text → `None`."""
    relativ = CLI_QUELLE.split("#", 1)[0]
    try:
        baum = ast.parse(_lies(repo, relativ), filename=relativ)
    except SyntaxError as e:
        raise NachweisFehler(REGEL, f"{relativ} ist kein gültiges Python ({e.msg})") from None
    werte: list[ast.expr | None] = [
        k.value
        for k in baum.body
        if isinstance(k, ast.Assign)
        and any(isinstance(z, ast.Name) and z.id == "_VERSION" for z in k.targets)
    ]
    werte += [
        k.value
        for k in baum.body
        if isinstance(k, ast.AnnAssign)
        and isinstance(k.target, ast.Name)
        and k.target.id == "_VERSION"
    ]
    if len(werte) != 1:
        return None
    (wert,) = werte
    return wert.value if isinstance(wert, ast.Constant) and isinstance(wert.value, str) else None


def versionen(repo: Path) -> dict[str, str]:
    """Die Version je Quelle aus `TEILE`. Fehlt eine Datei oder ein Versionsfeld → Abbruch."""
    paket = _json(repo, "web/package.json")
    sperre = _json(repo, "web/package-lock.json")
    pakete = sperre.get("packages")
    wurzel = pakete.get("") if isinstance(pakete, dict) else None
    roh: dict[str, Any] = {
        TEILE[0]: paket.get("version"),
        TEILE[1]: sperre.get("version"),
        TEILE[2]: wurzel.get("version") if isinstance(wurzel, dict) else None,
    }
    for relativ in _PYPROJECTS:
        roh[relativ] = _pyproject(repo, relativ)
    roh[CLI_QUELLE] = _cli_version(repo)
    fehlend = [q for q, v in roh.items() if not isinstance(v, str) or not v]
    if fehlend:
        raise NachweisFehler(REGEL, "Version fehlt in " + ", ".join(fehlend))
    return {q: str(v) for q, v in roh.items()}


def versionen_einheitlich(repo: Path) -> str:
    """Die gemeinsame Version `X.Y.Z` aller Quellen; weichen sie ab oder ist sie kein SemVer
    `X.Y.Z` → `NachweisFehler("versionen")` mit jeder Quelle und ihrem Wert."""
    gefunden = versionen(repo)
    werte = set(gefunden.values())
    if len(werte) != 1:
        raise NachweisFehler(REGEL, "Versionen nicht einheitlich: " + "; ".join(
            f"{q} = {v}" for q, v in gefunden.items()))
    (version,) = werte
    if not _SEMVER.fullmatch(version):
        raise NachweisFehler(REGEL, f"Version „{version}“ entspricht nicht X.Y.Z")
    return version
