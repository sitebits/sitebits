"""Signierte Commits: Prüfung der Git-SSH-Signaturen gegen den Vertrauensanker im
Nachweis-Archiv.

Der Anker liegt **im Archiv**, nie im geprüften Quellbaum — sonst könnte ein Commit die Liste
der Schlüssel mitbringen, gegen die er selbst geprüft wird. Gelesen wird er aus den Git-Objekten
von `refs/heads/main` des Archivs (nicht aus dem Arbeitsbaum):

- `vertrauensanker/commit-signers` — `allowed_signers` im Format von `ssh-keygen` (Namensraum
  `git`); ein neuer Schlüssel ist eine neue Zeile.
- `vertrauensanker/commit-revoked` — Sperrdatei (`gpg.ssh.revocationFile`): öffentliche
  Schlüssel, die nicht mehr gelten, unabhängig von der Commit-Zeit.
- `vertrauensanker/commit-signatur.toml` — `stichtag_commit` (voller Hash), `altstaende`
  (Zweigspitzen, die zum Stichtag schon existierten) und `zweige` (Release-Zweige).

Geprüft werden der Stichtag-Commit und jeder Commit, der vom Release-Commit aus erreichbar ist,
aber weder vom Stichtag noch von einem Altstand (`git rev-list <release> --not <stichtag>
<altstaende…>`). Alte, unsignierte Historie ist so ausgenommen; jeder Commit, der nach dem
Einfrieren auf **irgendeinem** Zweig entsteht und in das Release gelangt, wird geprüft. Gültig ist
allein der Git-Status `G`.

Was die Signatur belegt: Der Commit entstand mit einem Schlüssel aus dem Anker (auf der
Workstation) — nicht, dass ein Mensch ihn durchgesehen hat. Git bindet den Signierer nicht an die
Committer-Adresse; maßgeblich ist der Schlüssel."""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import re
import subprocess
import tempfile
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .fehler import NachweisFehler
from .gitobjekte import Objektleser
from .werkzeuge import ausfuehren, gefilterte_umgebung, git_befehl

ANKER_ORDNER = "vertrauensanker"
ANKER_REF = "refs/heads/main"
#: Genesis des Vertrauensankers: der erste Archiv-Commit mit `vertrauensanker/` (59b95d9,
#: 2026-09-28). Fest im Produkt-Repo — damit selbst durch die Commit-Signatur geschützt. Jeder
#: spätere Archiv-Commit muss mit einem Schlüssel aus dem Anker seines Elternstands signiert sein.
#: Ein neuer Genesis (etwa nach Kompromittierung) ist eine Änderung dieser Zeile.
ANKER_GENESIS = "59b95d9daa7e420438971945dc49c8ac3e1c0ae6"
SIGNERS_DATEI = "commit-signers"
WIDERRUF_DATEI = "commit-revoked"
STICHTAG_DATEI = "commit-signatur.toml"
REGEL = "commit-signatur"
REGEL_ANKER = "commit-signatur-anker"
#: Feste Prüfprogramme: weder PATH noch Repo- oder Nutzerkonfiguration wählen den Prüfer. SSH
#: prüft ssh-keygen (derselbe Pfad wie deploy/platform/release_pruefen.py); OpenPGP und X.509
#: sind nie gültig (Programm, das immer scheitert).
SSH_KEYGEN = "/usr/bin/ssh-keygen"
_NIE = "/usr/bin/false"
_SSH_SIGNATUR = b"gpgsig -----BEGIN SSH SIGNATURE-----"

_COMMIT = re.compile(r"[0-9a-f]{40}")
_SCHLUESSEL = re.compile(
    r"(?:^|\s)(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com|"
    r"sk-ecdsa-sha2-nistp256@openssh\.com)\s+([A-Za-z0-9+/]+={0,2})(?:\s|$)")
#: Bedeutung der Git-Statuscodes (`%G?`); nur `G` gilt.
_STATUS = {
    "G": "gültig",
    "B": "Signatur ungültig oder Schlüssel widerrufen",
    "U": "unbekannter Schlüssel (nicht im Vertrauensanker)",
    "X": "Signatur abgelaufen",
    "Y": "Schlüssel abgelaufen",
    "R": "Schlüssel widerrufen",
    "E": "Signatur nicht prüfbar (unbekannter Schlüssel oder Prüfprogramm fehlt)",
    "N": "unsigniert",
}
#: Höchstzahl der einzeln genannten Commits in einer Fehlermeldung.
_MELDUNG_HOECHSTENS = 20


@dataclass(frozen=True)
class Anker:
    """Vertrauensanker. `signers`/`widerrufen` sind Dateien außerhalb des Archiv-Arbeitsbaums
    (aus den Git-Objekten geschrieben); `commit` ist der Archiv-Commit, aus dem sie stammen
    (None bei einem Anker aus Dateien)."""

    signers: Path
    widerrufen: Path
    stichtag: str
    altstaende: tuple[str, ...]
    zweige: tuple[str, ...]
    fingerprints: frozenset[str]
    signers_sha256: str
    commit: str | None = None


def _anker_fehler(text: str) -> NachweisFehler:
    return NachweisFehler(REGEL_ANKER, text)


def _git(repo: Path, *args: str, regel: str = REGEL) -> str:
    return ausfuehren(git_befehl(*args, repo=repo), regel).stdout


def fingerprints(signers_roh: bytes, wo: str) -> frozenset[str]:
    """SHA256-Fingerprints (`SHA256:…` wie `ssh-keygen -l`) aller Schlüssel in einer
    allowed_signers-Datei. Eine Zeile ohne erkennbaren Schlüssel ist ein Fehler, ebenso eine
    Datei ohne Schlüssel."""
    try:
        text = signers_roh.decode("utf-8")
    except UnicodeDecodeError:
        raise _anker_fehler(f"{wo}: nicht UTF-8") from None
    ergebnis: set[str] = set()
    for nr, zeile in enumerate(text.splitlines(), 1):
        if not zeile.strip() or zeile.lstrip().startswith("#"):
            continue
        treffer = _SCHLUESSEL.search(zeile)
        try:
            roh = base64.b64decode(treffer.group(2), validate=True) if treffer else b""
        except binascii.Error:
            roh = b""
        if not roh:
            raise _anker_fehler(f"{wo}: Zeile {nr} ohne lesbaren Schlüssel")
        digest = base64.b64encode(hashlib.sha256(roh).digest()).decode().rstrip("=")
        ergebnis.add(f"SHA256:{digest}")
    if not ergebnis:
        raise _anker_fehler(f"{wo}: kein Schlüssel eingetragen")
    return frozenset(ergebnis)


def anker_aus_dateien(signers: Path, widerrufen: Path, *, stichtag: str,
                      altstaende: tuple[str, ...] = (), zweige: tuple[str, ...],
                      commit: str | None = None) -> Anker:
    """Anker aus vorhandenen Dateien (z. B. für Negativkontrollen und Tests)."""
    try:
        roh = signers.read_bytes()
    except OSError as e:
        raise _anker_fehler(f"{signers}: nicht lesbar ({e.strerror})") from None
    return Anker(signers=signers, widerrufen=widerrufen, stichtag=stichtag,
                 altstaende=tuple(altstaende), zweige=tuple(zweige),
                 fingerprints=fingerprints(roh, str(signers)),
                 signers_sha256=hashlib.sha256(roh).hexdigest(), commit=commit)


def _aufloesen(leser: Objektleser, rev: str, was: str, regel: str = REGEL) -> str:
    """`rev` zu einem vollen Commit-Hash; Optionen (`-…`) und Nicht-Commits werden abgelehnt.
    Jedes gelesene Objekt (auch ein Tag auf dem Weg) ist hashgeprüft."""
    if not isinstance(rev, str) or not rev or rev.startswith("-"):
        raise NachweisFehler(regel, f"{was} „{rev}“ ist kein Commit")
    try:
        oid = _git(leser.repo, "rev-parse", "--verify", "-q", "--end-of-options", rev).strip()
    except NachweisFehler:
        raise NachweisFehler(regel, f"{was} „{rev[:40]}“ ist kein Commit in {leser.repo}"
                             ) from None
    for _ in range(8):  # Tag auf Tag … höchstens einige Stufen
        try:
            art = leser.art(oid)
        except NachweisFehler as e:
            if "fehlt" not in e.text:
                raise
            raise NachweisFehler(regel, f"{was} „{rev[:40]}“ fehlt in {leser.repo}") from None
        if art == "commit":
            return oid
        if art != "tag":
            break
        kopf = leser.lies(oid, "tag").split(b"\n", 1)[0].decode("ascii", "replace")
        if not kopf.startswith("object "):
            break
        oid = kopf[7:]
    raise NachweisFehler(regel, f"{was} „{rev[:40]}“ ist kein Commit in {leser.repo}")


def _pruefe_dateien(anker: Anker) -> tuple[Path, Path]:
    # Fehlt die Signers-Datei, meldet git für jeden Schlüssel „U“; fehlt die Sperrdatei, nur eine
    # Warnung — und der Widerruf wirkte still nicht. Beides ist ein Ankerfehler.
    for pfad, was in ((anker.signers, "allowed_signers"), (anker.widerrufen, "Sperrdatei")):
        if not pfad.is_file():
            raise _anker_fehler(f"{pfad}: {was} des Vertrauensankers fehlt")
    _pruefe_sperrzeilen(anker.widerrufen)
    return anker.signers.resolve(), anker.widerrufen.resolve()


_SPERRZEILE = re.compile(r"[ \t]*(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|"
                         r"sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)"
                         r"[ \t]+[A-Za-z0-9+/]+={0,2}(?:[ \t].*)?", re.ASCII)


def _pruefe_sperrzeilen(widerrufen: Path) -> None:
    """Klartext-Sperrdatei mindestens so streng wie ssh-keygen: Zeilen nur an `\n` getrennt (ein
    `\r` am Zeilenende zählt nicht), am Zeilenanfang nur Leerzeichen und Tab übersprungen; jede
    Zeile außer Leer- und Kommentarzeilen ist `<typ> <base64>` mit optionalem Kommentar. Eine
    Zeile mit Principal oder Option (z. B. aus allowed_signers kopiert) oder mit anderem
    Leerraum am Anfang ließe ssh-keygen jede Signatur ablehnen — das Archiv wäre gesperrt.
    Eine binäre KRL prüft ssh-keygen selbst."""
    _pruefe_sperrinhalt(widerrufen.read_bytes(), str(widerrufen))


def _pruefe_sperrinhalt(roh: bytes, wo: str) -> None:
    """`_pruefe_sperrzeilen` für einen Inhalt; `wo` benennt ihn in der Meldung."""
    if roh.startswith(b"SSHKRL\n\0"):
        return
    try:
        text = roh.decode("utf-8")
    except UnicodeDecodeError:
        raise _anker_fehler(f"{wo}: Sperrdatei weder UTF-8 noch KRL") from None
    for nr, zeile in enumerate(text.split("\n"), 1):
        zeile = zeile.removesuffix("\r")
        if zeile.strip(" \t") and not zeile.lstrip(" \t").startswith("#") \
                and not _SPERRZEILE.fullmatch(zeile):
            raise _anker_fehler(f"{wo}: Zeile {nr} ist kein öffentlicher Schlüssel im "
                                "ssh-Format (ohne Principal, ohne Optionen)")


def pruef_konfiguration(signers: Path, widerrufen: Path) -> list[str]:
    """Die festen `-c`-Werte jeder Signaturprüfung: Anker-Dateien, Prüfprogramme (SSH nur
    `/usr/bin/ssh-keygen`, OpenPGP/X.509 nie gültig) und keine Signaturausgabe im Log."""
    werte = {"gpg.ssh.allowedSignersFile": str(signers),
             "gpg.ssh.revocationFile": str(widerrufen),
             "gpg.ssh.program": SSH_KEYGEN, "gpg.program": _NIE,
             "gpg.openpgp.program": _NIE, "gpg.x509.program": _NIE,
             "log.showSignature": "false"}
    return [teil for k, v in werte.items() for teil in ("-c", f"{k}={v}")]


def _git_konfig(repo: Path, schluessel: str) -> str:
    """Wert eines git-Konfigurationsschlüssels, wie ihn ein Commit über `git_befehl` sieht;
    nicht gesetzt → leer."""
    try:
        r = subprocess.run(git_befehl("config", "--get", schluessel, repo=repo),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           check=False, stdin=subprocess.DEVNULL, env=gefilterte_umgebung())
    except OSError as e:
        raise NachweisFehler(REGEL, f"git nicht ausführbar: {e.strerror}") from None
    return r.stdout.strip() if r.returncode == 0 else ""


_PROBE = b"scanview-archiv: Probesignatur vor dem Commit\n"


def _schluesseldatei(repo: Path, wert: str) -> Path:
    """`user.signingkey` als Datei, so aufgelöst wie git: `~` erweitert, relativ zur
    Repo-Wurzel. Literale (`key::…`, `ssh-…`) signiert git über den Agenten — die Kette reicht
    `SSH_AUTH_SOCK` bewusst nicht durch, also wäre der Commit nicht signierbar."""
    if wert.startswith(("key::", "ssh-")):
        raise NachweisFehler(REGEL, "user.signingkey ist ein Schlüssel-Literal — die Kette "
                                    "signiert nur mit einer Schlüsseldatei (kein Agent)")
    pfad = Path(wert).expanduser()
    return pfad if pfad.is_absolute() else repo / pfad


def pruefe_signierschluessel(repo: Path, anker: Anker) -> str:
    """Vor einem signierten Commit ins Archiv eine echte Probe: mit dem Schlüssel aus
    `user.signingkey` (`gpg.format` ssh) so signieren, wie git es tut (`ssh-keygen -Y sign -n git`,
    gefilterte Umgebung), und die Probe wie `pruefe_commit` gegen Anker und Sperrdatei prüfen.
    Die Probe läuft ohne Terminal: Ein Schlüssel mit Passphrase scheitert sofort, statt zweimal
    zu fragen (Probe, dann git) — die Kette braucht einen unverschlüsselten Schlüssel. Scheitert
    eins, entstünde ein Commit, der nicht signiert werden kann oder den die Prüfung
    danach ablehnt (und der als Elternstand jedes weitere Schreiben sperrt). Komfortprüfung vor
    dem Commit — das Tor bleibt `pruefe_commit` danach. Rückgabe: Fingerprint."""
    format_ = _git_konfig(repo, "gpg.format")
    if format_ != "ssh":
        raise NachweisFehler(REGEL, f"{repo}: gpg.format ist „{format_ or 'openpgp'}“ — Archiv-"
                                    "Commits brauchen ssh")
    wert = _git_konfig(repo, "user.signingkey")
    if not wert:
        raise NachweisFehler(REGEL, f"{repo}: user.signingkey nicht gesetzt — kein "
                                    "Signierschlüssel für den Archiv-Commit")
    schluessel = _schluesseldatei(repo, wert)
    signers, widerrufen = _pruefe_dateien(anker)
    with tempfile.TemporaryDirectory(prefix="scanview-probe-") as ordner:
        probe = Path(ordner) / "probe"
        probe.write_bytes(_PROBE)
        r = _ssh_keygen(["-Y", "sign", "-n", "git", "-f", str(schluessel), str(probe)], None)
        sig = probe.with_name("probe.sig")
        if r.returncode != 0 or not sig.is_file():
            raise NachweisFehler(REGEL, f"{repo}: mit user.signingkey nicht signierbar — die "
                                        "Kette signiert ohne Agent und ohne Terminal, ein "
                                        "Schlüssel mit Passphrase scheitert hier: "
                                        + r.stderr.decode("utf-8", "replace").strip()[-200:])
        gefunden = _ssh_keygen(["-Y", "find-principals", "-f", str(signers), "-s", str(sig)],
                               None)
        principals = [z for z in gefunden.stdout.decode("utf-8", "replace").splitlines() if z]
        if gefunden.returncode != 0 or not principals:
            raise NachweisFehler(REGEL, "Signierschlüssel steht nicht im Vertrauensanker — kein "
                                        "Commit, das Archiv bleibt unverändert")
        fingerprints_: set[str] = set()
        for principal in principals:
            v = _ssh_keygen(["-Y", "verify", "-n", "git", "-f", str(signers), "-I", principal,
                             "-s", str(sig), "-r", str(widerrufen)], _PROBE)
            ausgabe = (v.stdout + v.stderr).decode("utf-8", "replace")
            treffer = _FINGERPRINT.search(ausgabe)
            if v.returncode != 0 or not treffer:
                raise NachweisFehler(REGEL, "Probesignatur von ssh-keygen gegen Anker/Sperrdatei "
                                            "abgelehnt — kein Commit, das Archiv bleibt "
                                            f"unverändert: {ausgabe.strip()[-200:]}")
            fingerprints_.add(treffer.group(1))
    if len(fingerprints_) != 1 or not fingerprints_ <= anker.fingerprints:
        raise NachweisFehler(REGEL, "Signierschlüssel steht nicht eindeutig im Vertrauensanker")
    return fingerprints_.pop()


def pruefe_commit(repo: Path, commit: str, anker: Anker, *,
                  leser: Objektleser | None = None) -> str:
    """Signatur eines Commits gegen den Anker (allowed_signers, Namensraum `git`, Sperrdatei).
    Nur Status `G` gilt; Rückgabe ist der Fingerprint des Schlüssels. Sonst
    `NachweisFehler("commit-signatur", "<commit[:12]>: <Status> (<Bedeutung>)")`. Das
    Commit-Objekt ist hashgeprüft (Inhalt passt zur ID)."""
    if leser is None:
        with Objektleser(repo, REGEL) as eigener:
            return pruefe_commit(repo, commit, anker, leser=eigener)
    signers, widerrufen = _pruefe_dateien(anker)
    voll = _aufloesen(leser, commit, "Commit")
    kopf = leser.lies(voll, "commit").split(b"\n\n", 1)[0]
    art_ok = any(z.startswith(_SSH_SIGNATUR) for z in kopf.split(b"\n"))
    status, fingerprint, signierer = _git_status(repo, voll, signers, widerrufen)
    if status != "G":
        bedeutung = _STATUS.get(status, "unbekannter Status")
        raise NachweisFehler(REGEL, f"{voll[:12]}: {status or '?'} ({bedeutung})"
                                    + (f", Schlüssel {fingerprint}" if fingerprint else ""))
    if not fingerprint.startswith("SHA256:") or not signierer:
        raise NachweisFehler(REGEL, f"{voll[:12]}: gültig gemeldet, aber ohne Fingerprint oder "
                                    "Signierer")
    if fingerprint not in anker.fingerprints:
        raise NachweisFehler(REGEL, f"{voll[:12]}: Schlüssel {fingerprint} nicht im "
                                    "Vertrauensanker")
    if not art_ok:
        raise NachweisFehler(REGEL, f"{voll[:12]}: gültig gemeldet, aber keine SSH-Signatur im "
                                    "Commit-Objekt")
    # Zweite, eigene Prüfung über genau die hashgeprüften Bytes (git log liest das Objekt selbst
    # noch einmal): beide müssen denselben Schlüssel als gültig melden.
    eigen = _ssh_pruefung(leser.lies(voll, "commit"), voll, signers, widerrufen)
    if eigen != fingerprint:
        raise NachweisFehler(REGEL, f"{voll[:12]}: ssh-keygen meldet Schlüssel {eigen}, git "
                                    f"{fingerprint}")
    return fingerprint


def _git_status(repo: Path, voll: str, signers: Path, widerrufen: Path) -> tuple[str, str, str]:
    """`git log %G?/%GF/%GS` mit der festen Prüfkonfiguration."""
    aus = _git(repo, *pruef_konfiguration(signers, widerrufen),
               "log", "-1", "--no-show-signature", "--format=%G?%x00%GF%x00%GS", voll)
    teile = aus.rstrip("\n").split("\x00")
    if len(teile) != 3:
        raise NachweisFehler(REGEL, f"{voll[:12]}: Signaturstatus nicht lesbar")
    return teile[0], teile[1], teile[2]


_FINGERPRINT = re.compile(r"key (SHA256:[A-Za-z0-9+/]+)")


def _signatur_teile(roh: bytes, voll: str) -> tuple[bytes, bytes, str]:
    """Aus einem Commit-Objekt: signierter Inhalt (Objekt ohne den `gpgsig`-Kopf samt
    Fortsetzungszeilen, wie git ihn prüft), Signatur und Prüfzeit (Committer-Zeit, UTC,
    `YYYYMMDDHHMMSSZ`). Genau ein `gpgsig`-Kopf; `gpgsig-sha256` wird abgelehnt."""
    kopf, trenner, rest = roh.partition(b"\n\n")
    payload: list[bytes] = []
    signatur: list[bytes] = []
    anzahl = 0
    in_signatur = False
    zeit: int | None = None
    for zeile in kopf.split(b"\n"):
        if in_signatur and zeile.startswith(b" "):
            signatur.append(zeile[1:])
            continue
        in_signatur = False
        if zeile.startswith(b"gpgsig "):
            anzahl += 1
            in_signatur = True
            signatur.append(zeile[7:])
            continue
        if zeile.startswith(b"gpgsig-sha256 "):
            raise NachweisFehler(REGEL, f"{voll[:12]}: gpgsig-sha256 nicht unterstützt")
        if zeile.startswith(b"committer "):
            try:
                zeit = int(zeile.rsplit(b" ", 2)[1])
            except (IndexError, ValueError):
                zeit = None
        payload.append(zeile)
    if anzahl != 1 or zeit is None:
        raise NachweisFehler(REGEL, f"{voll[:12]}: nicht genau eine Signatur oder keine "
                                    "Committer-Zeit im Commit-Objekt")
    pruefzeit = datetime.fromtimestamp(zeit, UTC).strftime("%Y%m%d%H%M%SZ")
    return b"\n".join(payload) + trenner + rest, b"\n".join(signatur) + b"\n", pruefzeit


def _ssh_keygen(befehl: list[str], eingabe: bytes | None) -> subprocess.CompletedProcess[bytes]:
    # Eigene Sitzung ohne Controlling-Terminal: ssh-keygen kann nie über /dev/tty nach einer
    # Passphrase fragen (askpass scheitert ohne DISPLAY in der gefilterten Umgebung).
    try:
        return subprocess.run([SSH_KEYGEN, *befehl], input=eingabe, capture_output=True,
                              check=False, env=gefilterte_umgebung(), start_new_session=True,
                              stdin=None if eingabe is not None else subprocess.DEVNULL)
    except OSError as e:
        raise NachweisFehler(REGEL, f"ssh-keygen nicht ausführbar: {e.strerror}") from None


def _ssh_pruefung(roh: bytes, voll: str, signers: Path, widerrufen: Path) -> str:
    """`ssh-keygen -Y verify` (fester Pfad, Namensraum `git`, Sperrliste, Prüfzeit) über den
    signierten Inhalt der hashgeprüften Commit-Bytes; jeder gefundene Principal muss bestehen.
    Rückgabe: Fingerprint."""
    payload, signatur, pruefzeit = _signatur_teile(roh, voll)
    zeit = f"-Overify-time={pruefzeit}"
    with tempfile.TemporaryDirectory(prefix="scanview-sig-") as ordner:
        sig = Path(ordner) / "commit.sig"
        sig.write_bytes(signatur)
        gefunden = _ssh_keygen(["-Y", "find-principals", "-f", str(signers), "-s", str(sig),
                                zeit], None)
        principals = [p for p in gefunden.stdout.decode("utf-8", "replace").splitlines() if p]
        if gefunden.returncode != 0 or not principals:
            raise NachweisFehler(REGEL, f"{voll[:12]}: ssh-keygen findet keinen Schlüssel des "
                                        "Vertrauensankers für diese Signatur")
        fingerprints_: set[str] = set()
        for principal in principals:
            r = _ssh_keygen(["-Y", "verify", "-n", "git", "-f", str(signers), "-I", principal,
                             "-s", str(sig), "-r", str(widerrufen), zeit], payload)
            ausgabe = (r.stdout + r.stderr).decode("utf-8", "replace")
            treffer = _FINGERPRINT.search(ausgabe)
            if r.returncode != 0 or not treffer:
                raise NachweisFehler(REGEL, f"{voll[:12]}: ssh-keygen -Y verify ungültig "
                                            f"(Exit {r.returncode}): {ausgabe.strip()[-300:]}")
            fingerprints_.add(treffer.group(1))
    if len(fingerprints_) != 1:
        raise NachweisFehler(REGEL, f"{voll[:12]}: ssh-keygen meldet mehrdeutige Schlüssel")
    return fingerprints_.pop()


def _von(graph: dict[str, tuple[str, ...]], starts: list[str]) -> set[str]:
    """Von `starts` erreichbare Commits im (hashgeprüften) Graphen."""
    gesehen: set[str] = set()
    offen = list(starts)
    while offen:
        c = offen.pop()
        if c not in gesehen:
            gesehen.add(c)
            offen.extend(graph[c])
    return gesehen


def _altstaende(leser: Objektleser, altstaende: tuple[str, ...]) -> list[str]:
    """Jeder Altstand ist genau ein Commit-Objekt im Repo (kein Tag, kein Baum). Er nimmt die
    gesamte von ihm erreichbare Historie von der Prüfung aus, soweit sie nicht vom Stichtag
    abstammt — auch Commits, die nach dem Stichtag auf einem Altzweig entstanden sind. Deshalb
    ist jede Anker-Änderung signiert und wird in der Kette der Anker-Commits geprüft
    (`lade_anker`); dass ein Altstand nicht vom Stichtag abstammt, prüft
    `pruefe_seit_stichtag`."""
    ergebnis = []
    for alt in altstaende:
        voll = _aufloesen(leser, alt, "Altstand", REGEL_ANKER)
        if voll != alt:
            raise _anker_fehler(f"Altstand {alt[:12]} ist kein Commit-Objekt (Tag oder Baum?) — "
                                "eingetragen werden nur Commit-Hashes")
        ergebnis.append(voll)
    return ergebnis


def pruefe_seit_stichtag(repo: Path, anker: Anker, commit: str, *,
                         gesehen: set[str] | None = None) -> int:
    """Stichtag muss Vorfahre von `commit` sein; der Stichtag selbst und jeder Commit, der von
    `commit` aus erreichbar ist, aber weder vom Stichtag noch von einem Altstand, muss
    `pruefe_commit` bestehen. Die Menge wird aus den hashgeprüften Commit-Objekten selbst
    berechnet und muss mit `git rev-list <commit> --not <stichtag> <altstaende…>` übereinstimmen.
    Rückgabe: Anzahl der geprüften Commits; `gesehen` sammelt die Fingerprints. Alle Verstöße
    werden gesammelt gemeldet."""
    _pruefe_dateien(anker)
    # Ein flaches Repo kennt nicht alle Eltern: rev-list sähe dort weniger Commits.
    if _git(repo, "rev-parse", "--is-shallow-repository").strip() != "false":
        raise NachweisFehler(REGEL, f"{repo}: flaches Repository (shallow) — die Historie ist "
                                    "unvollständig, keine Prüfung möglich")
    with Objektleser(repo, REGEL) as leser:
        anfang = _aufloesen(leser, anker.stichtag, "Stichtag")
        ende = _aufloesen(leser, commit, "Release-Commit")
        alt = _altstaende(leser, anker.altstaende)
        graph = leser.erreichbar([ende, anfang, *alt])
        if anfang not in _von(graph, [ende]):
            raise NachweisFehler(REGEL, f"Stichtag {anfang[:12]} ist kein Vorfahre von "
                                        f"{ende[:12]}")
        for a in alt:
            if anfang in _von(graph, [a]):
                raise _anker_fehler(f"Altstand {a[:12]} stammt vom Stichtag {anfang[:12]} ab — "
                                    "er nähme Commits seit dem Stichtag von der Prüfung aus")
        eigen = _von(graph, [ende]) - _von(graph, [anfang, *alt])
        neu = _git(repo, "rev-list", ende, "--not", anfang, *alt).split()
        if set(neu) != eigen:
            raise NachweisFehler(REGEL, "git rev-list weicht von der aus den geprüften "
                                        f"Commit-Objekten berechneten Menge ab ({len(neu)} statt "
                                        f"{len(eigen)} Commits)")
        commits = [anfang, *neu]
        verstoesse: list[str] = []
        for c in commits:
            try:
                fp = pruefe_commit(repo, c, anker, leser=leser)
            except NachweisFehler as e:
                if e.regel != REGEL:
                    raise
                verstoesse.append(e.text)
                continue
            if gesehen is not None:
                gesehen.add(fp)
    if verstoesse:
        genannt = "; ".join(verstoesse[:_MELDUNG_HOECHSTENS])
        rest = len(verstoesse) - _MELDUNG_HOECHSTENS
        raise NachweisFehler(REGEL, f"{len(verstoesse)} von {len(commits)} Commits seit Stichtag "
                                    f"{anfang[:12]} ohne gültige Signatur: {genannt}"
                                    + (f" (und {rest} weitere)" if rest > 0 else ""))
    return len(commits)


def _datei_in(leser: Objektleser, commit: str, name: str) -> bytes | None:
    """Inhalt von `vertrauensanker/<name>` im Commit (None, wenn nicht vorhanden); nur als
    reguläre Datei (Modus 100644). Commit, Bäume und Blob sind hashgeprüft."""
    relativ = f"{ANKER_ORDNER}/{name}"
    treffer = leser.datei(commit, relativ)
    if treffer is None:
        return None
    modus, roh = treffer
    if modus != "100644":
        raise _anker_fehler(f"{relativ}: keine reguläre Datei (Modus {modus})")
    return roh


def _blob(leser: Objektleser, main: str, name: str) -> bytes:
    """Inhalt von `vertrauensanker/<name>` in `main` — und der Arbeitsbaum muss byte-gleich
    sein (auch bei skip-worktree/assume-unchanged, die `git status` blind machen)."""
    relativ = f"{ANKER_ORDNER}/{name}"
    roh = _datei_in(leser, main, name)
    if roh is None:
        raise _anker_fehler(f"{relativ} fehlt in {ANKER_REF} des Archivs")
    datei = leser.repo / relativ
    try:
        gleich = not datei.is_symlink() and datei.read_bytes() == roh
    except OSError:
        gleich = False
    if not gleich:
        raise _anker_fehler(f"{relativ}: Arbeitsbaum weicht vom committeten Anker ab "
                            "(nicht committete Änderung?)")
    return roh


# --- Kette der Anker-Commits ab dem Genesis ----------------------------------------------------

_VALID_BEFORE = re.compile(r'valid-before="[^"]*",')


@dataclass(frozen=True)
class _Stand:
    """Die Anker-Dateien eines Archiv-Commits (None = nicht vorhanden)."""

    signers: bytes | None
    widerruf: bytes | None
    toml: bytes | None


def _stand(leser: Objektleser, rev: str) -> _Stand:
    return _Stand(_datei_in(leser, rev, SIGNERS_DATEI), _datei_in(leser, rev, WIDERRUF_DATEI),
                  _datei_in(leser, rev, STICHTAG_DATEI))


def _zeilen(roh: bytes, wo: str) -> list[str]:
    try:
        return roh.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise _anker_fehler(f"{wo}: nicht UTF-8") from None


def _toml_felder(roh: bytes, wo: str) -> tuple[object, object, list[object]]:
    """stichtag_commit, zweige, altstaende (ältere Stände ohne altstaende: leer)."""
    try:
        daten = tomllib.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise _anker_fehler(f"{wo}: nicht lesbar ({e})") from None
    alt = daten.get("altstaende", [])
    liste = alt if isinstance(alt, list) else [alt]
    return daten.get("stichtag_commit"), daten.get("zweige"), liste


def _nur_angehaengt(vorher: _Stand, nachher: _Stand, commit: str) -> None:
    """Anker-Dateien wachsen nur: keine Datei verschwindet; `commit-signers` nur neue Zeilen
    (eine vorhandene Zeile darf einzig `valid-before="…",` erhalten — reguläre Ablösung);
    `commit-revoked` nur neue Zeilen, und jede Zeile ist ein Schlüssel wie in
    `_pruefe_sperrzeilen` (sonst sperrte der Stand jede Signatur); in `commit-signatur.toml`
    bleiben Stichtag und Zweige, `altstaende` wächst nur."""
    wo = f"Archiv-Commit {commit[:12]}"
    paare = ((SIGNERS_DATEI, vorher.signers, nachher.signers),
             (WIDERRUF_DATEI, vorher.widerruf, nachher.widerruf),
             (STICHTAG_DATEI, vorher.toml, nachher.toml))
    for name, alt, neu in paare:
        if alt is not None and neu is None:
            raise _anker_fehler(f"{wo}: {ANKER_ORDNER}/{name} entfernt")
    if vorher.signers is not None and nachher.signers is not None:
        alt_z = _zeilen(vorher.signers, wo)
        neu_z = _zeilen(nachher.signers, wo)
        if len(neu_z) < len(alt_z) or not all(
                n == a or ("valid-before=" not in a and _VALID_BEFORE.sub("", n, count=1) == a)
                for a, n in zip(alt_z, neu_z, strict=False)):
            raise _anker_fehler(f"{wo}: {ANKER_ORDNER}/{SIGNERS_DATEI} geändert statt nur "
                                "angehängt (erlaubt: neue Zeilen, valid-before an alter Zeile)")
    if vorher.widerruf is not None and nachher.widerruf is not None:
        alt_z = _zeilen(vorher.widerruf, wo)
        if _zeilen(nachher.widerruf, wo)[:len(alt_z)] != alt_z:
            raise _anker_fehler(f"{wo}: {ANKER_ORDNER}/{WIDERRUF_DATEI} geändert statt nur "
                                "angehängt")
    if nachher.widerruf is not None:
        _pruefe_sperrinhalt(nachher.widerruf, f"{wo}: {ANKER_ORDNER}/{WIDERRUF_DATEI}")
    if vorher.toml is not None and nachher.toml is not None:
        s_alt, z_alt, a_alt = _toml_felder(vorher.toml, wo)
        s_neu, z_neu, a_neu = _toml_felder(nachher.toml, wo)
        if s_alt != s_neu or z_alt != z_neu or a_neu[:len(a_alt)] != a_alt:
            raise _anker_fehler(f"{wo}: {ANKER_ORDNER}/{STICHTAG_DATEI} geändert statt nur "
                                "angehängt (Stichtag und Zweige fest, altstaende nur wachsend; "
                                "ein neuer Stichtag braucht einen neuen Genesis)")


def _signiert_von(leser: Objektleser, commit: str, stand: _Stand, stand_commit: str,
                  ordner: Path, was: str) -> None:
    """`commit` ist mit einem Schlüssel aus `stand` (dem Anker von `stand_commit`) signiert
    (Sperrliste von `stand`)."""
    if stand.signers is None:
        raise _anker_fehler(f"{was} {commit[:12]}: kein {ANKER_ORDNER}/{SIGNERS_DATEI} im "
                            "maßgeblichen Anker")
    # Vor dem Schreiben in die Ablage: eine fehlerhafte Sperrzeile ist ein Fehler des
    # Ankerstands, nicht der Signatur, und die Meldung nennt dessen Commit statt der Ablage.
    _pruefe_sperrinhalt(stand.widerruf or b"", f"Ankerstand {stand_commit[:12]}: "
                                               f"{ANKER_ORDNER}/{WIDERRUF_DATEI}")
    ordner.mkdir(mode=0o700)
    anker = anker_aus_dateien(_schreibe(ordner, SIGNERS_DATEI, stand.signers),
                              _schreibe(ordner, WIDERRUF_DATEI, stand.widerruf or b""),
                              stichtag="0" * 40, zweige=("-",))
    try:
        pruefe_commit(leser.repo, commit, anker, leser=leser)
    except NachweisFehler as e:
        raise _anker_fehler(f"{was} {commit[:12]} nicht mit einem Schlüssel des vorigen Ankers "
                            f"signiert: {e.text}") from None


def _pruefe_ankerkette(leser: Objektleser, main: str, genesis: str) -> None:
    """Genesis (fest im Produkt) ist ein Commit, Vorfahre von `main` und mit einem Schlüssel aus
    seinem eigenen Anker signiert. Die Archiv-Historie ab dort ist linear; jeder spätere Commit —
    ob er den Anker berührt oder nicht — ist mit einem Schlüssel aus dem Anker **seines
    Elternstands** signiert und ändert die Anker-Dateien nur anhängend. Alle Objekte
    hashgeprüft; die Kette wird aus den Commit-Objekten selbst gebildet."""
    if not _COMMIT.fullmatch(genesis):
        raise _anker_fehler(f"Genesis „{genesis}“ ist kein voller Commit-Hash")
    try:
        art = leser.art(genesis)
    except NachweisFehler as e:
        if "fehlt" not in e.text:
            raise
        art = ""
    if art != "commit":
        raise _anker_fehler(f"Genesis {genesis[:12]} fehlt im Archiv (falsches Archiv?)")
    kette: list[str] = []
    c = main
    while c != genesis:
        eltern = leser.commit(c)[1]
        if len(eltern) > 1:
            raise _anker_fehler("Archiv-Historie ab dem Genesis nicht linear (Merge-Commit "
                                f"{c[:12]})")
        if not eltern:
            raise _anker_fehler(f"Genesis {genesis[:12]} ist kein Vorfahre von {ANKER_REF}")
        kette.append(c)
        c = eltern[0]
    kette.reverse()
    if kette != _git(leser.repo, "rev-list", "--reverse", f"{genesis}..{main}",
                     regel=REGEL_ANKER).split():
        raise _anker_fehler("git rev-list weicht von der aus den geprüften Commit-Objekten "
                            "gebildeten Archiv-Kette ab")
    stand = _stand(leser, genesis)
    with tempfile.TemporaryDirectory(prefix="scanview-ankerkette-") as tmp:
        _signiert_von(leser, genesis, stand, genesis, Path(tmp) / "0", "Genesis")
        vorgaenger = genesis
        for nr, c in enumerate(kette, 1):
            _signiert_von(leser, c, stand, vorgaenger, Path(tmp) / str(nr), "Archiv-Commit")
            neu = _stand(leser, c)
            _nur_angehaengt(stand, neu, c)
            stand, vorgaenger = neu, c


def _schreibe(ablage: Path, name: str, inhalt: bytes) -> Path:
    pfad = ablage / name
    try:
        with pfad.open("xb") as f:
            f.write(inhalt)
        pfad.chmod(0o600)
    except OSError as e:
        raise _anker_fehler(f"{pfad}: nicht schreibbar ({e.strerror})") from None
    return pfad


def _liste(daten: dict[str, object], feld: str, *, leer_erlaubt: bool,
           hashes: bool) -> tuple[str, ...]:
    wert = daten[feld]
    if not isinstance(wert, list) or (not wert and not leer_erlaubt) or not all(
            isinstance(w, str) and w.strip() for w in wert) or len(set(wert)) != len(wert):
        raise _anker_fehler(f"{ANKER_ORDNER}/{STICHTAG_DATEI}: {feld} muss eine "
                            f"{'' if leer_erlaubt else 'nicht leere '}Liste eindeutiger Namen "
                            "sein")
    if hashes and not all(_COMMIT.fullmatch(w) for w in wert):
        raise _anker_fehler(f"{ANKER_ORDNER}/{STICHTAG_DATEI}: {feld} enthält keinen vollen "
                            "Commit-Hash")
    return tuple(wert)


def lade_anker(archiv: Path, ablage: Path | None = None, *,
               genesis: str | None = None) -> Anker:
    """Vertrauensanker aus `refs/heads/main` des Archivs (ausgecheckt muss `main` sein): die drei
    Dateien als reguläre, committete Dateien, Arbeitsbaum byte-gleich; `commit-signers` mit
    mindestens einem Schlüssel; `commit-signatur.toml` genau mit `stichtag_commit`,
    `altstaende` und `zweige`. Signers und Sperrdatei werden nach `ablage` geschrieben (neu,
    0600; Standard: frisches Temporärverzeichnis). Vorher die Kette der Archiv-Commits ab dem
    Genesis (`ANKER_GENESIS`, Tests: `genesis`), siehe `_pruefe_ankerkette`. Fehler →
    `commit-signatur-anker`."""
    if not (archiv / ".git").exists():
        raise _anker_fehler(f"{archiv}: kein Nachweis-Archiv (kein Git-Repo)")
    try:
        kopf = _git(archiv, "symbolic-ref", "-q", "HEAD", regel=REGEL_ANKER).strip()
    except NachweisFehler:
        kopf = "(losgelöst)"
    if kopf != ANKER_REF:
        raise _anker_fehler(f"{archiv}: ausgecheckt ist {kopf}, der Anker gilt nur aus "
                            f"{ANKER_REF} (main)")
    with Objektleser(archiv, REGEL_ANKER) as leser:
        main = _aufloesen(leser, ANKER_REF, "Archiv-main", REGEL_ANKER)
        _pruefe_ankerkette(leser, main, genesis or ANKER_GENESIS)
        signers_roh = _blob(leser, main, SIGNERS_DATEI)
        widerruf_roh = _blob(leser, main, WIDERRUF_DATEI)
        toml_roh = _blob(leser, main, STICHTAG_DATEI)
    try:
        daten = tomllib.loads(toml_roh.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise _anker_fehler(f"{ANKER_ORDNER}/{STICHTAG_DATEI}: nicht lesbar ({e})") from None
    felder = {"stichtag_commit", "altstaende", "zweige"}
    if set(daten) != felder:
        raise _anker_fehler(f"{ANKER_ORDNER}/{STICHTAG_DATEI}: genau {', '.join(sorted(felder))} "
                            f"erwartet, gefunden {', '.join(sorted(daten)) or '(nichts)'}")
    stichtag = daten["stichtag_commit"]
    if not isinstance(stichtag, str) or not _COMMIT.fullmatch(stichtag):
        raise _anker_fehler(f"{ANKER_ORDNER}/{STICHTAG_DATEI}: stichtag_commit ist kein voller "
                            "Commit-Hash")
    altstaende = _liste(daten, "altstaende", leer_erlaubt=True, hashes=True)
    zweige = _liste(daten, "zweige", leer_erlaubt=False, hashes=False)
    fps = fingerprints(signers_roh, f"{ANKER_ORDNER}/{SIGNERS_DATEI}")
    commit = _git(archiv, "log", "-1", "--format=%H", ANKER_REF, "--", ANKER_ORDNER,
                  regel=REGEL_ANKER).strip()
    if ablage is None:
        ablage = Path(tempfile.mkdtemp(prefix="scanview-anker-"))
    else:
        ablage.mkdir(mode=0o700, parents=True, exist_ok=True)
    return Anker(signers=_schreibe(ablage, SIGNERS_DATEI, signers_roh),
                 widerrufen=_schreibe(ablage, WIDERRUF_DATEI, widerruf_roh),
                 stichtag=stichtag, altstaende=altstaende, zweige=zweige, fingerprints=fps,
                 signers_sha256=hashlib.sha256(signers_roh).hexdigest(), commit=commit or None)


@contextlib.contextmanager
def geladener_anker(archiv: Path) -> Iterator[Anker]:
    """`lade_anker` mit einer Ablage, die nach dem Block wieder entfernt wird."""
    with tempfile.TemporaryDirectory(prefix="scanview-anker-") as ordner:
        yield lade_anker(archiv, Path(ordner) / "anker")
