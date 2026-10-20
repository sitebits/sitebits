"""Build-Rückverfolgbarkeit messen: ist `web/` reproduzierbar baubar?

`vergleiche_builds` vergleicht zwei Verzeichnisbäume dateiweise per SHA-256 (Reihenfolge auf der
Platte und Zeitstempel sind egal, nur der Inhalt zählt). Symlinks werden nicht verfolgt — ihr
Linkziel als Text zählt als Kennwert, ein Symlink nur in einem der beiden Bäume oder mit
unterschiedlichem Ziel ist eine Abweichung wie eine fehlende oder geänderte Datei.
`web_build_messen` baut dafür `web/`
zweimal unabhängig voneinander — je ein frischer `git worktree add --detach` von HEAD,
`npm ci --ignore-scripts && npm run build` darin — und vergleicht die beiden `dist`-Bäume.

Kein Gate: Das Ergebnis (gleich/abweichend) ist eine Messung für
`docs/compliance/werkzeuge.md` §Build, keine Release-Voraussetzung — `web_build_messen` liefert
den Befund einfach zurück, ohne selbst zu entscheiden, ob das schlimm ist. Der Bau läuft auf dem
Host statt im gehärteten Container: `npm ci` braucht Netz für den Abhängigkeits-Download, das ist
nur für Bau-Stufen vorgesehen, und dieser Vergleich ist ohnehin
keine Freigabekette."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .fehler import NachweisFehler
from .werkzeuge import ausfuehren, gefilterte_umgebung, git_befehl

#: Pfad des Web-Baus relativ zur Worktree-Wurzel.
_DIST = Path("web/dist")


@dataclass(frozen=True)
class Vergleich:
    """Ergebnis eines Baubaum-Vergleichs. `abweichend` benennt jede Abweichung als Klartext
    (`nur in a: <pfad>`, `nur in b: <pfad>`, `unterschiedlich: <pfad>`,
    `unterschiedliches Linkziel: <pfad>`), sortiert nach Pfad."""

    gleich: bool
    abweichend: list[str]


# Ein Eintrag im Dateibaum ist entweder eine Datei (Kennwert: SHA-256 des Inhalts) oder ein
# Symlink (Kennwert: das Linkziel als Text, ungelöst). Beide Arten teilen sich einen Pfad-Raum,
# damit „Datei hier, Symlink dort“ ebenfalls als Abweichung auffällt statt stillschweigend eine
# der beiden Seiten zu verlieren.
_Art = str  # "datei" | "symlink"


def _dateibaum(wurzel: Path) -> dict[str, tuple[_Art, str]]:
    """Relativer Pfad (POSIX-Trenner, damit der Vergleich plattformunabhängig bleibt) → (Art,
    Kennwert), für jede Datei und jeden Symlink unter `wurzel`. Symlinks werden nicht verfolgt
    (weder als Datei noch als Verzeichnis betreten, ihr Ziel wird nicht gelesen) — verglichen wird
    nur der Linktext selbst (`os.readlink`); ein Bau-Baum mit einem Symlink nach außerhalb soll
    den Vergleich nicht verlassen."""
    if not wurzel.is_dir():
        raise NachweisFehler("build-vergleich", f"{wurzel}: kein Verzeichnis")
    ergebnis: dict[str, tuple[_Art, str]] = {}
    for pfad in wurzel.rglob("*"):
        relativ = pfad.relative_to(wurzel).as_posix()
        if pfad.is_symlink():
            ergebnis[relativ] = ("symlink", os.readlink(pfad))
        elif pfad.is_file():
            ergebnis[relativ] = ("datei", hashlib.sha256(pfad.read_bytes()).hexdigest())
    return ergebnis


def vergleiche_builds(a: Path, b: Path) -> Vergleich:
    """Vergleicht zwei Verzeichnisbäume dateiweise per SHA-256 (Symlinks per Linkziel, nicht
    verfolgt): `gleich`, wenn beide exakt dieselben relativen Pfade mit derselben Art und
    demselben Kennwert enthalten. Fehlt `a` oder `b`, bricht der Vergleich mit
    `NachweisFehler("build-vergleich", …)` ab, statt zwei leere Bäume als „gleich“ zu werten."""
    baum_a, baum_b = _dateibaum(a), _dateibaum(b)
    abweichend: list[str] = []
    for pfad in sorted(set(baum_a) | set(baum_b)):
        eintrag_a, eintrag_b = baum_a.get(pfad), baum_b.get(pfad)
        if eintrag_a is None:
            abweichend.append(f"nur in b: {pfad}")
        elif eintrag_b is None:
            abweichend.append(f"nur in a: {pfad}")
        elif eintrag_a[0] != eintrag_b[0]:
            abweichend.append(f"unterschiedliche Art ({eintrag_a[0]} statt {eintrag_b[0]}): "
                              f"{pfad}")
        elif eintrag_a[0] == "symlink":
            if eintrag_a[1] != eintrag_b[1]:
                abweichend.append(f"unterschiedliches Linkziel: {pfad}")
        elif eintrag_a[1] != eintrag_b[1]:
            abweichend.append(f"unterschiedlich: {pfad}")
    return Vergleich(gleich=not abweichend, abweichend=abweichend)


#: Bau-Schritt für einen Worktree: bekommt dessen Wurzel, wirft bei Fehler (üblicherweise
#: `NachweisFehler`). Injizierbar, damit `web_build_messen` ohne echten `npm`-Lauf testbar ist.
BauFunktion = Callable[[Path], None]


def npm_build(worktree: Path) -> None:
    """Standard-Bauschritt: `npm ci --ignore-scripts` (Netz für den Abhängigkeits-Download gegen
    `package-lock.json`) und danach `npm run build`, beides in `<worktree>/web`."""
    web = worktree / "web"
    if not web.is_dir():
        raise NachweisFehler("build-messen", f"{web}: kein web/-Verzeichnis im Worktree")
    ausfuehren(["npm", "ci", "--ignore-scripts"], "build-messen", cwd=web)
    ausfuehren(["npm", "run", "build"], "build-messen", cwd=web)


def _worktree_entfernen(wurzel: Path, pfad: Path) -> bool:
    """`git worktree remove --force`; True bei Erfolg. Wirft nie: ein Fehler hier darf im
    `finally`-Block von `web_build_messen` keine schon laufende Ausnahme verschlucken. Ein
    Fehlschlag geht sofort auf stderr, der Aufrufer entscheidet, was zusätzlich damit passiert
    (bei `web_build_messen`: in `aufraeumen_fehlgeschlagen` sammeln und ins JSON schreiben)."""
    r = subprocess.run(git_befehl("worktree", "remove", "--force", str(pfad)), cwd=wurzel,
                       capture_output=True, text=True, check=False,
                       env=gefilterte_umgebung())
    if r.returncode != 0:
        print(f"build-messen: Aufräumen von {pfad} fehlgeschlagen: {r.stderr.strip()}",
             file=sys.stderr)
        return False
    return True


def web_build_messen(
    wurzel: Path,
    *,
    arbeitsordner: Path,
    ausgabe: Path | None = None,
    bauen: BauFunktion | None = None,
) -> Vergleich:
    """Baut `web/` zweimal unabhängig voneinander — je ein frischer, unabhängiger
    `git worktree add --detach <arbeitsordner>/{a,b} HEAD` — und vergleicht die beiden
    `web/dist`-Bäume (`vergleiche_builds`). `bauen` ersetzt den Bauschritt für Tests, Standard ist
    `npm_build` (später gebunden, damit ein Patch auf das Modulattribut auch über die CLI wirkt).

    `arbeitsordner` nimmt die beiden Worktrees sowie Kopien der beiden `dist`-Bäume (`dist-a`,
    `dist-b`) auf, die auch nach dem Aufräumen der Worktrees erhalten bleiben, damit eine
    Abweichung im Nachhinein untersucht werden kann. `ausgabe` (Standard
    `<arbeitsordner>/build-vergleich.json`) bekommt das Ergebnis als JSON (`gleich`, `abweichend`,
    `aufraeumen_fehlgeschlagen`, der gemessene Commit und der Baum-Hash von `web/`).

    Fail-closed: ein schon vorhandener Worktree-Zielordner, ein fehlendes `web/`-Verzeichnis oder
    ein fehlender/leerer `dist`-Baum (mindestens eine Datei, ein leerer Unterordner zählt nicht)
    nach dem Bau brechen mit `NachweisFehler` ab. Beide Worktrees werden in jedem Fall wieder
    entfernt (`git worktree remove --force`), auch wenn der Bau fehlschlägt — ein Fehler beim
    Aufräumen wird gesammelt, statt eine schon laufende Ausnahme im `finally`-Block zu
    verschlucken. War der Bau selbst erfolgreich, aber das Aufräumen nicht, steht das Ergebnis
    trotzdem vollständig im JSON (inkl. `aufraeumen_fehlgeschlagen`); anschließend, nachdem die
    Datei geschrieben ist, wirft die Funktion `NachweisFehler("messen-aufraeumen", …)`, damit ein
    nicht entfernter Worktree nicht mit Exit 0 durchgeht."""
    regel = "build-messen"
    bau_schritt = bauen if bauen is not None else npm_build
    arbeitsordner.mkdir(parents=True, exist_ok=True)
    worktrees = {"a": arbeitsordner / "a", "b": arbeitsordner / "b"}
    for pfad in worktrees.values():
        if pfad.exists():
            raise NachweisFehler(regel, f"{pfad}: existiert schon — Arbeitsordner nicht "
                                        "wiederverwenden")

    angelegt: list[Path] = []
    aufraeumen_fehlgeschlagen: list[str] = []
    vergleich: Vergleich | None = None
    try:
        dists: dict[str, Path] = {}
        for name, pfad in worktrees.items():
            ausfuehren(git_befehl("worktree", "add", "--detach", str(pfad), "HEAD"), regel,
                      cwd=wurzel)
            angelegt.append(pfad)
            bau_schritt(pfad)
            dist = pfad / _DIST
            if not dist.is_dir() or not any(p.is_file() for p in dist.rglob("*")):
                raise NachweisFehler(regel, f"{dist}: fehlt oder ist leer nach dem Bau")
            dists[name] = dist
        vergleich = vergleiche_builds(dists["a"], dists["b"])
        for name, dist in dists.items():
            shutil.copytree(dist, arbeitsordner / f"dist-{name}")
    finally:
        for pfad in angelegt:
            if not _worktree_entfernen(wurzel, pfad):
                aufraeumen_fehlgeschlagen.append(str(pfad))
    # Ab hier ist keine Ausnahme mehr unterwegs (sonst hätte das obige try/finally sie schon nach
    # außen durchgereicht) — vergleich ist also gesetzt.
    assert vergleich is not None

    kommit = ausfuehren(git_befehl("rev-parse", "HEAD"), regel, cwd=wurzel).stdout.strip()
    baum_web = ausfuehren(git_befehl("rev-parse", "HEAD:web"), regel, cwd=wurzel).stdout.strip()
    ziel_datei = ausgabe if ausgabe is not None else arbeitsordner / "build-vergleich.json"
    ziel_datei.parent.mkdir(parents=True, exist_ok=True)
    ziel_datei.write_text(json.dumps({
        "gleich": vergleich.gleich,
        "abweichend": vergleich.abweichend,
        "aufraeumen_fehlgeschlagen": aufraeumen_fehlgeschlagen,
        "commit": kommit,
        "baum_web": baum_web,
        "erzeugt_am": datetime.now(UTC).isoformat(timespec="seconds"),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if aufraeumen_fehlgeschlagen:
        # Erst geschrieben, dann geworfen: das Ergebnis bleibt nachvollziehbar, aber Exit 0 wäre
        # falsch — ein stehen gelassener Worktree ist ein echter Fehlerzustand.
        raise NachweisFehler("messen-aufraeumen",
                             "Worktree(s) nicht entfernt: " + ", ".join(aufraeumen_fehlgeschlagen))
    return vergleich
