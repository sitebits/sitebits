"""Läufer: führt einen Auftrag isoliert aus (R-E-F-2 Ersatzweg `systemd-run`).

Jeder Lauf ist eine eigene transiente systemd-Unit `scanview-auftrag-<id>` mit festen Grenzen:
kein Netz, kein Unix-Socket (sonst erreicht der Job über den Bus des User-Managers
`systemd-run` und startet eine Unit ohne Grenzen — lokal nachgewiesen 21.10.), Dateisystem nur
lesend bis auf den Arbeitsordner, RAM-, Zeit- und Prozessgrenze. Die Argumentliste ist fest;
die Shell kommt nie vor.

Der Erfolg eines Laufs steht nie im Exit-Code von `systemd-run` (1 bei Zeitlimit, OOM, Startfehler
und bei „nicht erfüllt“ des Prüfers): ausgewertet wird die Zusammenfassung, die `systemd-run
--wait` nach dem Ende der Unit schreibt („Finished with result: …“, „Main processes terminated
with: code=…, status=…“). Die Ausgabe des Jobs selbst geht in eine Datei im Arbeitsordner und
kann diese Zeilen nicht fälschen.

Welche Properties im User-Manager wirken, steht in docs/public/sicherheitsarchitektur.md
(Abschnitt „Import-Worker und Auswertungsjobs“, gemessen mit systemd 262); `selbsttest` prüft
Netz, DNS, Unix-Socket, Schreiben außerhalb und RAM-Grenze bei jedem Worker-Start.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

Ausgang = Literal["ok", "exit", "speicher", "zeit", "start", "abgebrochen"]
UNIT_PRAEFIX = "scanview-auftrag-"
_UNIT_RE = re.compile(r"^scanview-auftrag-[0-9a-z-]{1,64}$")
_ERGEBNIS_RE = re.compile(r"^\s*Finished with result: ([a-z-]+)\s*$", re.MULTILINE)
_HAUPT_RE = re.compile(
    r"^\s*Main processes terminated with: code=([a-z]+), status=(\d+)", re.MULTILINE,
)
# Puffer über dem Zeitlimit der Unit, bevor der Worker systemd-run selbst abbricht.
_ZEIT_PUFFER_S = 120


@dataclass(frozen=True)
class Grenzen:
    ram_mb: int
    zeit_s: int
    prozesse: int = 64
    # Größte Datei, die der Job schreiben darf (LimitFSIZE) — Plattenbremse je Datei.
    datei_mb: int = 1024


@dataclass(frozen=True)
class Lauf:
    """`argv[0]` ist ein absoluter Pfad; `arbeit` ist der einzige beschreibbare Ordner."""

    name: str
    argv: Sequence[str]
    arbeit: Path
    grenzen: Grenzen
    protokoll: Path
    umgebung: Mapping[str, str] = field(default_factory=dict)
    # Wurzeln, die der Job leer sieht (Pakete und Berichte anderer Projekte) …
    verbergen: tuple[Path, ...] = ()
    # … und darin wieder sichtbar, nur lesend (das eigene Paket).
    lesen: tuple[Path, ...] = ()
    # Nur für die RAM-Probe des Selbsttests: `OOMPolicy=continue`, damit ein OOM-Kill eines
    # Kindprozesses die Unit nicht als `oom-kill` beendet. Echte Aufträge: nie gesetzt.
    oom_fortsetzen: bool = False


@dataclass(frozen=True)
class Ergebnis:
    ausgang: Ausgang
    status: int | None
    dauer_s: float
    roh: str = ""


class Laeufer(Protocol):
    def ausfuehren(self, lauf: Lauf) -> Ergebnis: ...

    def stoppen(self, name: str) -> None: ...


def deutung(ausgabe: str, rc: int) -> tuple[Ausgang, int | None]:
    """Zusammenfassung von `systemd-run --wait` → Ausgang. Die letzte Zeile zählt."""
    treffer = _ERGEBNIS_RE.findall(ausgabe)
    haupt = _HAUPT_RE.findall(ausgabe)
    status = int(haupt[-1][1]) if haupt and haupt[-1][0] == "exited" else None
    if not treffer:
        return "start", None
    ergebnis = treffer[-1]
    if ergebnis == "oom-kill":
        return "speicher", None
    if ergebnis == "timeout":
        return "zeit", None
    if ergebnis in ("success", "exit-code") and status is not None:
        return ("ok" if status == 0 else "exit"), status
    return "abgebrochen", status


@dataclass
class SystemdLaeufer:
    """`systemd-run --user` (lokal, Dienstnutzer mit Linger) oder System-Manager."""

    benutzer: bool = True
    systemd_run: str = "/usr/bin/systemd-run"
    systemctl: str = "/usr/bin/systemctl"
    # Zusätzlich unsichtbar (InaccessiblePaths), z. B. Zustandsordner des Webdienstes.
    unsichtbar: tuple[Path, ...] = ()
    # ProtectHome: „read-only“ am Arbeitsplatz (Import-Umgebung liegt unter /home),
    # „tmpfs“ auf dem Server (Import-Umgebung unter /opt).
    home: Literal["read-only", "tmpfs", "yes"] = "read-only"

    def _sperrpfade(self) -> list[Path]:
        # Sockets des Systems und der User-Manager (Bus, resolved, Docker …): zusätzlich zur
        # Adressfamilien-Sperre auch unsichtbar.
        return [Path("/run/dbus"), Path("/run/systemd"), Path("/run/user"), *self.unsichtbar]

    def befehl(self, lauf: Lauf) -> list[str]:
        if not _UNIT_RE.fullmatch(lauf.name):
            raise ValueError("Unit-Name ungültig")
        if not lauf.argv or not Path(lauf.argv[0]).is_absolute():
            raise ValueError("argv[0] muss ein absoluter Pfad sein")
        g = lauf.grenzen
        props = [
            f"MemoryMax={g.ram_mb}M", "MemorySwapMax=0",
            f"RuntimeMaxSec={g.zeit_s}", f"TasksMax={g.prozesse}",
            # PrivatePIDs macht den Job zu PID 1 seines Namensraums: SIGTERM ohne eigenen
            # Handler ignoriert der Kernel, erst SIGKILL nach TimeoutStopSec beendet ihn.
            # Fest statt Vorgabe des Managers (Server: 90 s).
            "TimeoutStopSec=10",
            f"LimitFSIZE={g.datei_mb}M", "LimitCORE=0",
            "PrivateNetwork=yes",
            # Positivliste: nur IP (im leeren Netz-Namespace nutzlos) — kein AF_UNIX (Bus,
            # Namensauflösung über systemd-resolved), kein Netlink, kein Packet.
            "RestrictAddressFamilies=AF_INET", "RestrictAddressFamilies=AF_INET6",
            "ProtectSystem=strict", f"ProtectHome={self.home}",
            *(f"TemporaryFileSystem={p}:ro" for p in lauf.verbergen),
            *(f"BindReadOnlyPaths={p}" for p in lauf.lesen),
            f"BindPaths={lauf.arbeit}", f"ReadWritePaths={lauf.arbeit}",
            "NoNewPrivileges=yes", "PrivateDevices=yes", "PrivateIPC=yes", "PrivatePIDs=yes",
            "RestrictNamespaces=yes", "LockPersonality=yes", "RestrictRealtime=yes",
            "RestrictSUIDSGID=yes", "SystemCallArchitectures=native",
            "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
            "ProtectControlGroups=yes", "ProtectClock=yes", "ProtectHostname=yes",
            "UMask=0027",
            f"StandardOutput=truncate:{lauf.protokoll}",
            f"StandardError=append:{lauf.protokoll}",
        ]
        props += [f"InaccessiblePaths=-{p}" for p in self._sperrpfade()]
        if lauf.oom_fortsetzen:
            props.append("OOMPolicy=continue")
        cmd = [self.systemd_run]
        if self.benutzer:
            cmd.append("--user")
        cmd += ["--wait", "--collect", "--service-type=exec", f"--unit={lauf.name}",
                f"--working-directory={lauf.arbeit}"]
        for p in props:
            cmd += ["-p", p]
        umgebung = {"LANG": "C.UTF-8", "HOME": str(lauf.arbeit), "TMPDIR": str(lauf.arbeit),
                    **lauf.umgebung}
        for k, v in umgebung.items():
            cmd += ["-E", f"{k}={v}"]
        return [*cmd, "--", *lauf.argv]

    def ausfuehren(self, lauf: Lauf) -> Ergebnis:
        t0 = time.monotonic()
        try:
            r = subprocess.run(
                self.befehl(lauf), capture_output=True, text=True, check=False,
                timeout=lauf.grenzen.zeit_s + _ZEIT_PUFFER_S,
                # systemd-run übersetzt seine Zusammenfassung nicht; sicher ist sicher.
                env={**os.environ, "LC_ALL": "C"},
            )
        except subprocess.TimeoutExpired:
            self.stoppen(lauf.name)
            return Ergebnis("zeit", None, time.monotonic() - t0)
        roh = (r.stdout or "") + (r.stderr or "")
        ausgang, status = deutung(roh, r.returncode)
        return Ergebnis(ausgang, status, time.monotonic() - t0, roh[-2000:])

    def stoppen(self, name: str) -> None:
        if not _UNIT_RE.fullmatch(name):
            raise ValueError("Unit-Name ungültig")
        cmd = [self.systemctl, *(["--user"] if self.benutzer else []), "stop", f"{name}.service"]
        subprocess.run(cmd, capture_output=True, check=False, timeout=60)
        cmd = [self.systemctl, *(["--user"] if self.benutzer else []), "reset-failed",
               f"{name}.service"]
        subprocess.run(cmd, capture_output=True, check=False, timeout=60)


@dataclass
class TestLaeufer:
    """Für Unit-Tests: ruft statt eines Prozesses eine Funktion mit dem Lauf auf."""

    aktion: Callable[[Lauf], Ergebnis]
    gestoppt: list[str] = field(default_factory=list)
    laeufe: list[Lauf] = field(default_factory=list)

    def ausfuehren(self, lauf: Lauf) -> Ergebnis:
        self.laeufe.append(lauf)
        return self.aktion(lauf)

    def stoppen(self, name: str) -> None:
        self.gestoppt.append(name)
