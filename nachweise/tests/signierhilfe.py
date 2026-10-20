"""Testhilfen: Git-Repos mit SSH-signierten Commits und ein Nachweis-Archiv mit Vertrauensanker
(`vertrauensanker/commit-signers`, `commit-revoked`, `commit-signatur.toml`). Signiert wird nur
mit Wegwerf-Schlüsseln in tmp_path, nie mit dem echten Commit-Schlüssel."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scanview_nachweise import signaturen
from scanview_nachweise.signaturen import Anker, anker_aus_dateien

EMAIL = "test@example.invalid"
RELEASE_ZWEIGE = ("main", "feat/nachweise")


def git(repo: Path, *args: str, **env: str) -> str:
    # Ohne ssh-agent wie die Kette: signiert wird allein mit der Schlüsseldatei.
    umgebung = {k: v for k, v in os.environ.items() if k != "SSH_AUTH_SOCK"} | env
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, env=umgebung).stdout.strip()


def schluessel(ordner: Path, name: str = "commit_ed25519") -> tuple[Path, str]:
    """Wegwerf-Schlüssel; Rückgabe (privater Schlüssel, allowed_signers-Zeile für `git`)."""
    ordner.mkdir(parents=True, exist_ok=True)
    key = ordner / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    typ, wert = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    return key, f'{EMAIL} namespaces="git" {typ} {wert} test\n'


def fingerprint(key: Path) -> str:
    return subprocess.run(["ssh-keygen", "-l", "-f", str(key.with_suffix(".pub"))], check=True,
                          capture_output=True, text=True).stdout.split()[1]


def repo(ordner: Path, key: Path, *, zweig: str = "main") -> Path:
    """Leeres Repo mit SSH-Signatur-Konfiguration (Schlüsseldatei, kein Agent)."""
    ordner.mkdir(parents=True)
    git(ordner, "init", "-q", "-b", zweig)
    git(ordner, "config", "user.name", "Test")
    git(ordner, "config", "user.email", EMAIL)
    git(ordner, "config", "gpg.format", "ssh")
    git(ordner, "config", "user.signingkey", str(key))
    git(ordner, "config", "commit.gpgsign", "false")
    git(ordner, "config", "tag.gpgsign", "false")
    return ordner


def commit(r: Path, name: str, *, signiert: bool, key: Path | None = None) -> str:
    """Commit mit Datei `name`; signiert mit dem Repo-Schlüssel oder `key`, sonst ausdrücklich
    unsigniert. Rückgabe: voller Hash."""
    (r / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    git(r, "add", f"{name}.txt")
    zusatz = ["-c", f"user.signingkey={key}"] if key else []
    git(r, *zusatz, "commit", "-q", "-S" if signiert else "--no-gpg-sign", "-m", name)
    return git(r, "rev-parse", "HEAD")


def toml(stichtag: str, altstaende: tuple[str, ...] = (),
         zweige: tuple[str, ...] = RELEASE_ZWEIGE) -> str:
    def liste(werte: tuple[str, ...]) -> str:
        return "[" + ", ".join(f'"{w}"' for w in werte) + "]"
    return (f'stichtag_commit = "{stichtag}"\naltstaende = {liste(altstaende)}\n'
            f"zweige = {liste(zweige)}\n")


def anker(archiv: Path, signers_zeilen: str, stichtag: str, altstaende: tuple[str, ...] = (),
          zweige: tuple[str, ...] = RELEASE_ZWEIGE, widerrufen: str = "") -> None:
    """Vertrauensanker ins Archiv schreiben und signiert committen. Der erste Anker-Commit eines
    Archivs wird dessen Genesis (`signaturen.ANKER_GENESIS`, von tests/conftest.py nach jedem
    Test zurückgesetzt)."""
    ordner = archiv / "vertrauensanker"
    neu = not ordner.exists()
    ordner.mkdir(exist_ok=True)
    (ordner / "commit-signers").write_text(signers_zeilen, encoding="utf-8")
    (ordner / "commit-revoked").write_text(widerrufen, encoding="utf-8")
    (ordner / "commit-signatur.toml").write_text(toml(stichtag, altstaende, zweige),
                                                 encoding="utf-8")
    git(archiv, "add", "vertrauensanker")
    git(archiv, "commit", "-q", "-S", "-m", "vertrauensanker")
    if neu:
        signaturen.ANKER_GENESIS = git(archiv, "rev-parse", "HEAD")


def signiertes_archiv(ordner: Path) -> Path:
    """Archiv-Repo mit Commit-Schlüssel, Startcommit und Anker (Stichtag beliebig)."""
    key, zeile = schluessel(ordner.parent / f"{ordner.name}-schluessel")
    a = repo(ordner, key)
    commit(a, "start", signiert=True)
    anker(a, zeile, "a" * 40)
    return a


def anker_dateien(ordner: Path, zeile: str, stichtag: str = "a" * 40,
                  altstaende: tuple[str, ...] = (), widerrufen: str = "") -> Anker:
    """Anker aus Dateien (ohne Archiv) — für Prüfungen gegen ein Test-Repo."""
    ordner.mkdir(parents=True, exist_ok=True)
    signers = ordner / "commit-signers"
    signers.write_text(zeile, encoding="utf-8")
    revoked = ordner / "commit-revoked"
    revoked.write_text(widerrufen, encoding="utf-8")
    return anker_aus_dateien(signers, revoked, stichtag=stichtag, altstaende=altstaende,
                             zweige=RELEASE_ZWEIGE)


def objekt_ueberschreiben(repo: Path, oid: str, typ: str, inhalt: bytes) -> None:
    """Loses Objekt `oid` durch anderen Inhalt ersetzen (ID bleibt, Hash passt nicht mehr) —
    wie ein Angreifer mit Schreibzugriff auf den Objektspeicher. Gepackte Objekte vorher lose."""
    import zlib
    pfad = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / \
        "objects" / oid[:2] / oid[2:]
    assert pfad.exists(), "Objekt nicht lose"
    pfad.chmod(0o644)
    pfad.write_bytes(zlib.compress(f"{typ} {len(inhalt)}\0".encode() + inhalt))
