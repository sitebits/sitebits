"""Git-Export der Bearbeitungen.

Je Projekt ein Repo unter `<git_dir>/<projekt>/` mit `meta.json`, `pois/<id>.json`,
`messungen/<id>.json`, `faeden/<poi>/<schluessel>.json` (Befund-Faden: alle Einträge, auch interne
und zurückgezogene — Betreiber-Export, docs/public/betrieb.md) und einem einmal angelegten
`README.md`. Quelle ist allein das
Bearbeitungsprotokoll (`ereignisse`): je Inhaltsereignis genau ein Commit, Dateiinhalt = `nachher`
des Ereignisses (bei `…geaendert` ist das schon der voll zusammengeführte Stand, siehe
inhalte.aendern), bei `…geloescht` verschwindet die Datei. Wie weit ein Projekt exportiert ist,
steht in `git_export_stand`; zusätzlich trägt jeder Commit `Ereignis: <id>` — scheitert das
Fortschreiben des Stands nach einem Commit, setzt der nächste Lauf an diesem Commit an statt ihn zu
wiederholen. Punktwolken/Pakete kommen nie ins Git.

Autor (E-G1): pseudonym `Nutzer <id> <nutzer-<id>@scanview.invalid>`, der Kontoname nur bei
`users.git_name_zeigen = 1` zum Zeitpunkt des Exports; die E-Mail bleibt immer die erfundene.
Committer ist `scanview <scanview@scanview.invalid>`.

Sicherheit: Pfade entstehen nur aus Projekt-IDs, die `PROJECT_ID_RE` erfüllen,
und Objekt-IDs, die `inhalte.ID_RE` erfüllen; zusätzlich muss der aufgelöste Pfad unter dem Repo
liegen. Ein Ereignis mit fremder ID wird übersprungen und gemeldet, der Stand rückt trotzdem vor.
Git läuft nur als Argumentliste (nie über eine Shell), mit minimaler Umgebung (`_git_env`), ohne
Hooks und ohne Signieren. Die Remote-URL wird nie in `.git/config` gespeichert und erscheint in
Meldungen nur ohne Benutzer/Passwort.
"""

from __future__ import annotations

import fcntl
import functools
import json
import logging
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .config import Settings
from .faden import schluessel_gueltig
from .inhalte import ART, ID_RE
from .katalog import PROJECT_ID_RE, Katalog

log = logging.getLogger("scanview.gitexport")

COMMITTER = ("scanview", "scanview@scanview.invalid")
ZWEIG = "main"
# poi → pois, messung → messungen (aus inhalte.ART: Ordner und Ereignisarten laufen nie auseinander)
_ORDNER: dict[str, str] = {art: tabelle for tabelle, art in ART.items()}
# G7: Vorschlags-Ereignisse (vorgeschlagen, vorschlag_geaendert, verworfen …) fehlen bewusst; erst
# `angenommen` schreibt die dann gültige Datei.
_AKTIONEN = ("angelegt", "wiederhergestellt", "geaendert", "geloescht", "angenommen")
META = "meta.geaendert"
# Befund-Faden: objekt_id `<poi>/<schluessel>`, Dateiinhalt = Eintrag laut `nachher` (ohne Namen).
FADEN_ARTEN = ("eintrag.angelegt", "eintrag.zurueckgezogen")
_EREIGNIS_ZEILE = re.compile(r"^Ereignis: (\d+)$", re.MULTILINE)
_STEUER = re.compile(r"[\x00-\x1f\x7f]")
_GIT_OPTIONEN = (
    # Keine Hooks, kein Signieren, keine im Hintergrund abgekoppelte Wartung.
    "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
    "-c", "gc.autoDetach=false",
)
UEBERSPRUNGEN = "übersprungen:"
REMOTE_UNGUELTIG = (
    "SCANVIEW_GIT_REMOTE muss den Platzhalter {projekt} enthalten (und keine anderen)."
)


class GitFehler(Exception):
    """Git-Aufruf gescheitert. Die Meldung enthält nie Zugangsdaten aus der Remote-URL."""


def _git_env(heim: Path) -> dict[str, str]:
    """Minimale Umgebung für jeden Git-Aufruf: nichts aus ~/.gitconfig, /etc/gitconfig oder
    GIT_*-Variablen des Aufrufers. `GIT_SSH_COMMAND` wird durchgereicht, wenn der Betrieb ihn
    setzt (Schlüsseldatei des Dienstes, README) — ssh nimmt sonst das passwd-Heimatverzeichnis
    (`/nonexistent`) und fände keinen Schlüssel."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(heim),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    ssh = os.environ.get("GIT_SSH_COMMAND")
    if ssh:
        env["GIT_SSH_COMMAND"] = ssh
    return env


def url_ohne_zugang(url: str) -> str:
    """Remote-URL ohne Benutzer/Passwort — für Meldungen und Protokoll."""
    if "://" in url:
        teile = urlsplit(url)
        host = teile.hostname or ""
        if teile.port is not None:
            host = f"{host}:{teile.port}"
        return urlunsplit((teile.scheme, host, teile.path, teile.query, teile.fragment))
    # scp-Form `nutzer@host:pfad`; ein lokaler Pfad hat kein ':' vor dem ersten '/'.
    kopf, doppelpunkt, rest = url.partition(":")
    if doppelpunkt and "/" not in kopf:
        return f"{kopf.rpartition('@')[2]}:{rest}"
    return url


def remote_fehler(vorlage: str) -> str | None:
    """Meldung, wenn die Remote-Vorlage unbrauchbar ist, sonst None. Ohne {projekt} landeten alle
    Projekte im selben Repo; andere Platzhalter ließen `format()` erst beim Push scheitern. Die
    Vorlage selbst erscheint nie in der Meldung (könnte Zugangsdaten enthalten)."""
    try:
        probe = vorlage.format(projekt="\0")
    except (KeyError, IndexError, ValueError):
        return REMOTE_UNGUELTIG
    return None if "{projekt}" in vorlage and "\0" in probe else REMOTE_UNGUELTIG


def _schwaerzen(text: str, url: str | None) -> str:
    if url:
        text = text.replace(url, url_ohne_zugang(url))
    # Auch von Git selbst umgeschriebene URLs (z. B. ohne Passwort, aber mit Benutzer).
    return re.sub(r"(://)[^/@\s]+@", r"\1", text)


def _git(
    repo: Path, heim: Path, *args: str, git: str = "git", eingabe: str | None = None,
    env: dict[str, str] | None = None, url: str | None = None, pruefen: bool = True,
) -> subprocess.CompletedProcess[str]:
    umgebung = _git_env(heim)
    if env:
        umgebung.update(env)
    try:
        return subprocess.run(
            [git, *_GIT_OPTIONEN, *args], check=pruefen, cwd=repo, env=umgebung,
            capture_output=True, text=True, input=eingabe,
        )
    except subprocess.CalledProcessError as e:
        # `from None`: die CalledProcessError trägt die Befehlszeile samt URL — nie in einen
        # Traceback durchreichen.
        meldung = _schwaerzen((e.stderr or e.stdout or "").strip(), url)
        raise GitFehler(f"git {args[0]} in {repo.name}: {meldung}") from None
    except OSError as e:
        raise GitFehler(f"git nicht ausführbar: {e.strerror}") from None


def _stand(conn: sqlite3.Connection, pid: str) -> int:
    row = conn.execute(
        "SELECT letzte_ereignis_id FROM git_export_stand WHERE project_id=?", (pid,),
    ).fetchone()
    return int(row[0]) if row else 0


def _stand_setzen(conn: sqlite3.Connection, pid: str, eid: int) -> None:
    conn.execute(
        "INSERT INTO git_export_stand (project_id, letzte_ereignis_id) VALUES (?,?)"
        " ON CONFLICT(project_id) DO UPDATE SET letzte_ereignis_id=excluded.letzte_ereignis_id",
        (pid, eid),
    )


def _inhaltsart(art: str) -> bool:
    if art == META or art in FADEN_ARTEN:
        return True
    typ, _, aktion = art.partition(".")
    return typ in _ORDNER and aktion in _AKTIONEN


def _zielpfad(repo: Path, art: str, objekt_id: Any) -> Path | str:
    """Datei eines Ereignisses im Repo — oder der Grund, warum es keinen sicheren Pfad gibt.

    Außer der ID-Prüfung: die Datei selbst darf kein Symlink sein (Schreiben ginge durch ihn
    hindurch auf eine andere Datei) und darf nicht unter `.git` auflösen (etwa über einen
    umgebogenen Ordner `pois` → `.git`); beides entsteht nur durch Eingriffe von außen."""
    if art in FADEN_ARTEN:
        poi, _, nr = objekt_id.partition("/") if isinstance(objekt_id, str) else ("", "", "")
        if not ID_RE.fullmatch(poi) or not schluessel_gueltig(nr):
            return "Objekt-ID ergibt keinen sicheren Pfad"
        ziel = repo / "faeden" / poi / f"{nr}.json"
    elif not isinstance(objekt_id, str) or not ID_RE.fullmatch(objekt_id):
        return "Objekt-ID ergibt keinen sicheren Pfad"
    elif art == META:
        ziel = repo / "meta.json"
    else:
        ziel = repo / _ORDNER[art.partition(".")[0]] / f"{objekt_id}.json"
    if ziel.is_symlink():
        return "Ziel ist ein Symlink"
    basis = repo.resolve()
    aufgeloest = ziel.resolve()
    if not aufgeloest.is_relative_to(basis):
        return "Ziel liegt außerhalb des Repos"
    if aufgeloest.is_relative_to(basis / ".git"):
        return "Ziel liegt unter .git"
    return ziel


def _json_datei(daten: Any) -> bytes:
    return (json.dumps(daten, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _anzeigename(roh: Any) -> str:
    """Kontoname als Git-Identität: ohne '<', '>' und Steuerzeichen (würden die Identität
    zerlegen), Leerraum zusammengefasst."""
    if not isinstance(roh, str):
        return ""
    return " ".join(_STEUER.sub(" ", roh.replace("<", "").replace(">", "")).split())


def _autor(conn: sqlite3.Connection, nutzer_id: int | None) -> tuple[str, str]:
    if nutzer_id is None:
        return COMMITTER
    email = f"nutzer-{nutzer_id}@scanview.invalid"
    row = conn.execute(
        "SELECT name, git_name_zeigen FROM users WHERE id=?", (nutzer_id,),
    ).fetchone()
    if row is not None and row["git_name_zeigen"] == 1:
        name = _anzeigename(row["name"])
        if name:
            return name, email
    return f"Nutzer {nutzer_id}", email


def _nachricht(e: sqlite3.Row) -> str:
    # Befund B-TE-M2: nur der Kanal, nie der Token-Name — ein frei gewählter Name
    # („Laptop Andre“) brächte sonst trotz E-G1 einen Personennamen in die unveränderliche und
    # womöglich gepushte Historie. Der Name bleibt im (internen) Protokoll, erreichbar über die
    # Ereignis-ID.
    return f"{e['art']} {e['objekt_id']}\n\nKanal: {e['kanal']}\nEreignis: {e['id']}\n"


def _readme(settings: Settings, pid: str) -> str:
    titel = pid
    try:
        katalog = Katalog(settings.data_dir)
        katalog.laden()
        karte = katalog.get(pid)
        if karte is not None and karte.titel != pid:
            titel = f"{karte.titel} ({pid})"
    except Exception:  # Titel ist Beiwerk; ohne lesbaren Katalog bleibt es bei der ID.
        log.debug("Katalogtitel für README von %s nicht lesbar", pid, exc_info=True)
    return (
        f"# siteBits – Bearbeitungen im Projekt {titel}\n\n"  # noqa: RUF001
        "Dieses Repository wird automatisch aus dem Bearbeitungsprotokoll der siteBits-Plattform\n"
        "erzeugt. Jeder Commit ist genau eine Bearbeitung (Anlegen, Ändern, Löschen,\n"
        "Wiederherstellen); Autorzeit ist der Zeitpunkt der Bearbeitung.\n\n"
        "- `meta.json` – Titel und Beschreibung des Projekts\n"  # noqa: RUF001
        "- `pois/<id>.json` – Markierungen (POIs)\n"  # noqa: RUF001
        "- `messungen/<id>.json` – Messungen\n"  # noqa: RUF001
        "- `faeden/<poi>/<schluessel>.json` – Befund-Fäden (alle Einträge, auch interne)\n"  # noqa: RUF001
        "- `genauigkeit.json` – Genauigkeitsnachweis je Punktwolke/Mesh des Pakets beim\n"  # noqa: RUF001
        "  Export der Bearbeitung; `pointSources[].layerId` einer Messung verweist auf `ebene`\n\n"
        "Autoren erscheinen pseudonym als „Nutzer <Nummer>“, mit Namen nur, wenn die Person das\n"
        "in ihrem Konto eingeschaltet hat. Punktwolken und Pakete sind nicht enthalten.\n"
        "Bitte hier nichts von Hand ändern — der nächste Export baut darauf auf.\n"
    )


def _katalog(settings: Settings) -> Katalog | None:
    try:
        katalog = Katalog(settings.data_dir)
        katalog.laden()
    except Exception:  # Beiwerk wie der README-Titel: der Export der Bearbeitungen geht vor.
        log.debug("Katalog für genauigkeit.json nicht lesbar", exc_info=True)
        return None
    return katalog


def _genauigkeit_datei(katalog: Katalog | None, pid: str) -> bytes | None:
    """Genauigkeit je Ebene (F15) aus dem Paket; ohne lesbaren Katalog bleibt die Datei stehen."""
    karte = katalog.get(pid) if katalog is not None else None
    if karte is None:
        return None
    return _json_datei({"ebenen": [g.als_json() for g in karte.genauigkeit]})


def _repo_bereit(settings: Settings, pid: str, git: str) -> Path:
    basis = settings.git_dir
    basis.mkdir(parents=True, exist_ok=True)
    repo = basis / pid
    if repo.is_symlink() or not repo.resolve().is_relative_to(basis.resolve()):
        raise GitFehler(f"Repo-Pfad für {pid} liegt nicht unter dem Git-Verzeichnis.")
    if not (repo / ".git").is_dir():
        repo.mkdir(exist_ok=True)
        _git(repo, basis, "init", "-q", "-b", ZWEIG, git=git)
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(_readme(settings, pid), encoding="utf-8")
    return repo


def _letztes_exportiertes(repo: Path, heim: Path, git: str) -> int:
    """`Ereignis: <id>` aus dem jüngsten Commit (0 ohne Repo oder ohne Commit)."""
    if not (repo / ".git").is_dir():
        return 0
    r = _git(repo, heim, "log", "-1", "--format=%B", git=git, pruefen=False)
    treffer = _EREIGNIS_ZEILE.findall(r.stdout) if r.returncode == 0 else []
    return int(treffer[-1]) if treffer else 0


def _projekte(conn: sqlite3.Connection, projekt: str | None) -> list[str]:
    if projekt is not None:
        return [projekt]
    rows = conn.execute(
        "SELECT DISTINCT project_id FROM ereignisse WHERE project_id IS NOT NULL"
        " ORDER BY project_id",
    ).fetchall()
    return [str(r[0]) for r in rows]


def _melden(ergebnis: dict[str, int], pid: str, e: sqlite3.Row, grund: str) -> None:
    log.warning("Git-Export %s: Ereignis %d (%s) übersprungen – %s",  # noqa: RUF001
                pid if PROJECT_ID_RE.fullmatch(pid) else "(ungültige Projekt-ID)",
                e["id"], e["art"], grund)
    ergebnis[f"{UEBERSPRUNGEN}{e['id']}"] = 1


def _projekt_exportieren(
    conn: sqlite3.Connection, settings: Settings, pid: str, ergebnis: dict[str, int], git: str,
    katalog: Callable[[], Katalog | None],
) -> Path | None:
    heim = settings.git_dir
    gueltig = PROJECT_ID_RE.fullmatch(pid) is not None
    repo = heim / (pid if gueltig else "-")  # ungültige ID wird nie ein Pfad
    stand = _stand(conn, pid)
    if gueltig:
        stand = max(stand, _letztes_exportiertes(repo, heim, git))
    ereignisse = conn.execute(
        "SELECT id, zeit, nutzer_id, kanal, art, objekt_id, nachher FROM ereignisse"
        " WHERE project_id=? AND id>? ORDER BY id", (pid, stand),
    ).fetchall()
    if gueltig:
        ergebnis[pid] = 0
    genauigkeit = _genauigkeit_datei(katalog(), pid) if gueltig and ereignisse else None
    for e in ereignisse:
        if not _inhaltsart(e["art"]):
            _stand_setzen(conn, pid, e["id"])  # Rechte-/sonstige Ereignisse: nie im Repo
            continue
        if not gueltig:
            _melden(ergebnis, pid, e, "Projekt-ID ergibt keinen sicheren Pfad")
            _stand_setzen(conn, pid, e["id"])
            continue
        ziel = _zielpfad(repo, e["art"], e["objekt_id"])
        geloescht = e["art"].endswith(".geloescht")
        if isinstance(ziel, str) or (not geloescht and e["nachher"] is None):
            _melden(ergebnis, pid, e, ziel if isinstance(ziel, str) else "ohne Inhalt")
            _stand_setzen(conn, pid, e["id"])
            continue
        inhalt = b""
        if not geloescht:
            try:
                inhalt = _json_datei(json.loads(e["nachher"]))
            except ValueError:  # nur bei Handeintrag denkbar; protokoll.ereignis schreibt JSON
                _melden(ergebnis, pid, e, "Inhalt ist kein JSON")
                _stand_setzen(conn, pid, e["id"])
                continue
        repo = _repo_bereit(settings, pid, git)
        if geloescht:
            ziel.unlink(missing_ok=True)
        else:
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.write_bytes(inhalt)
        # Geht mit der Bearbeitung in denselben Commit und ändert sich nur mit dem Paket.
        if genauigkeit is not None:
            (repo / "genauigkeit.json").write_bytes(genauigkeit)
        name, email = _autor(conn, e["nutzer_id"])
        datum = f"@{int(e['zeit'])} +0000"
        _git(repo, heim, "add", "-A", git=git)
        _git(repo, heim, "commit", "-q", "--allow-empty", "--no-verify", "--cleanup=verbatim",
             "-F", "-", git=git, eingabe=_nachricht(e), env={
                 "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": datum,
                 "GIT_COMMITTER_NAME": COMMITTER[0], "GIT_COMMITTER_EMAIL": COMMITTER[1],
                 "GIT_COMMITTER_DATE": datum,
             })
        # Nach jedem Commit, nicht am Ende: bricht ein späterer Git-Aufruf ab, wiederholt der
        # nächste Lauf keine schon geschriebenen Commits.
        _stand_setzen(conn, pid, e["id"])
        ergebnis[pid] += 1
    return repo if gueltig and (repo / ".git").is_dir() else None


def exportieren(
    conn: sqlite3.Connection, settings: Settings, *, projekt: str | None = None,
    push: bool = False, git: str = "git",
) -> dict[str, int]:
    """Exportiert neue Ereignisse in die Projekt-Repos → je Projekt Anzahl neuer Commits.

    Übersprungene Ereignisse (ID ergibt keinen sicheren Pfad) erscheinen zusätzlich als Schlüssel
    `übersprungen:<ereignis-id>` (Wert 1) — kollisionsfrei, weil `ü` in keiner Projekt-ID vorkommt.
    Mit `push` geht jedes Projekt-Repo an `settings.git_remote` (auch ohne neue Commits, damit ein
    früher gescheiterter Push nachgeholt wird). Git-Fehler → `GitFehler`.
    """
    if projekt is not None and not PROJECT_ID_RE.fullmatch(projekt):
        raise ValueError(f"Projekt-ID {projekt!r} ungültig.")
    if push and settings.git_remote is None:
        raise GitFehler("Push braucht SCANVIEW_GIT_REMOTE.")
    if push and settings.git_remote is not None:
        fehler = remote_fehler(settings.git_remote)
        if fehler is not None:  # vor der Sperre: kein Verzeichnis, keine Wirkung
            raise GitFehler(fehler)
    # Ein Lauf zur Zeit: ein Handaufruf parallel zum Timer würde dieselben Ereignisse sonst ein
    # zweites Mal committen (--allow-empty) und pushen. Der zweite Lauf wartet und findet danach
    # den fortgeschriebenen Stand vor.
    settings.git_dir.mkdir(parents=True, exist_ok=True)
    with open(settings.git_dir / ".sperre", "a") as sperre:
        fcntl.flock(sperre, fcntl.LOCK_EX)
        return _exportieren(conn, settings, projekt, push, git)


def _exportieren(
    conn: sqlite3.Connection, settings: Settings, projekt: str | None, push: bool, git: str,
) -> dict[str, int]:
    ergebnis: dict[str, int] = {}
    repos: list[tuple[str, Path]] = []
    # Katalog höchstens einmal je Lauf und nur, wenn ein Projekt neue Bearbeitungen hat.
    katalog = functools.cache(lambda: _katalog(settings))
    for pid in _projekte(conn, projekt):
        repo = _projekt_exportieren(conn, settings, pid, ergebnis, git, katalog)
        if repo is not None:
            repos.append((pid, repo))
    if push and settings.git_remote is not None:
        # Der erste gescheiterte Push bricht ab: die übrigen Projekte werden erst im nächsten Lauf
        # gepusht (ihre Commits sind lokal schon da, der Stand fortgeschrieben — nichts geht
        # verloren; jeder --push-Lauf pusht alle Repos, auch ohne neue Commits).
        for pid, repo in repos:
            url = settings.git_remote.format(projekt=pid)
            try:
                _git(repo, settings.git_dir, "push", "-q", "--", url,
                     f"HEAD:refs/heads/{ZWEIG}", git=git, url=url)
            except GitFehler as e:
                raise GitFehler(
                    f"Push von {pid} nach {url_ohne_zugang(url)} fehlgeschlagen: {e}",
                ) from None
    return ergebnis
