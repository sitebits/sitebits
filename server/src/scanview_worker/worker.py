"""`scanview-import-worker`: holt Aufträge aus `jobs.db` und rechnet sie isoliert.

Eigener Dienst unter eigenem Nutzer (R-E-F-1). Der Worker kennt nur `jobs.db`, den Paketordner
(nur lesend), die Import-Umgebung und den Berichtsordner — keine Server-Datenbank, keine
Sitzungen, keine Konten (Importgrenze per Test). Parallelität 1, FIFO.

Ablauf je Auswertung: Arbeitsordner `<berichte>/<projekt>/.arbeit-<auftrag>/` anlegen → Prüfer
im Läufer starten (feste Argumentliste, nur geprüfte IDs und Profilnamen) → aus `aus/` nur
erlaubte Dateien nach `<berichte>/<projekt>/<auftrag>/` übernehmen → Arbeitsordner entfernen.

Ablauf je Import (neues Projekt aus einem Upload): Arbeitsordner `<übergabe>/.arbeit-<auftrag>/`
→ `scanview-import neu-ohne-wolke` im Läufer (Upload nur lesend eingebunden, sonst nichts
sichtbar) → `scanview-import verify` in einem zweiten Lauf → Paketbaum prüfen (nur Ordner und
reguläre Dateien, ein Name je Datei, keine Gruppen-/Fremdschreibrechte) → per rename nach
`<übergabe>/<projekt>/`. Freischalten tut der Worker nie selbst: das root-Skript der Übergabe
legt das Paket über den Nutzer `scanview-uebergabe` in den Eingang und ruft das unveränderte
Freischalt-Skript; dessen Ergebnis liest der Worker als ein Wort aus dem Meldungsordner.

Neustartfest: Beim Start werden übrig gebliebene Units gestoppt, „laufend“ hängende Aufträge als
„abgebrochen“ markiert und alle Arbeitsordner geleert. Ein Selbsttest prüft vor der ersten
Arbeit, dass die Isolation greift (kein Netz, kein Unix-Socket, nichts außerhalb des
Arbeitsordners beschreibbar); schlägt er fehl, startet der Worker nicht.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import stat
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, Literal

from scanview_server import auftraege as A

from .laeufer import UNIT_PRAEFIX, Ergebnis, Grenzen, Laeufer, Lauf, SystemdLaeufer

log = logging.getLogger("scanview.worker")

ERLAUBTE_ENDUNGEN = (".json", ".html", ".png", ".csv", ".sha256")
_NAME_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
AUSGABE_MAX_BYTES = 256 * 1024 * 1024
AUSGABE_MAX_DATEIEN = 500
ERGEBNIS_MAX_BYTES = 32 * 1024 * 1024
ARBEIT_PRAEFIX = ".arbeit-"

# Meldungen für jobs.db: kurz, ohne Pfade (gehen über die API an Mensch und Agent).
F_PAKET = "Paket nicht gefunden oder nicht lesbar."
F_PLATTE = "Zu wenig freier Speicherplatz für den Bericht."
F_SPEICHER = "Speicherbudget überschritten ({mb} MB) – Auftrag beendet."  # noqa: RUF001
F_ZEIT = "Zeitlimit überschritten ({s} s) – Auftrag beendet."  # noqa: RUF001
F_EINGABE = "Der Prüfer meldet einen Katalog- oder Eingabefehler (Exit 2)."
F_ABBRUCH = "Der Prüfer ist abgebrochen (Exit {code})."
F_START = "Der isolierte Lauf ließ sich nicht starten."
F_AUSGABE = "Der Prüfer hat keinen gültigen Bericht geschrieben."
F_NEUSTART = "Worker neu gestartet – Auftrag abgebrochen."  # noqa: RUF001
F_BEENDET = "Worker wurde beendet – Auftrag abgebrochen."  # noqa: RUF001
F_PLATTE_IMPORT = "Zu wenig freier Speicherplatz für das neue Paket."
F_NICHT_EINGERICHTET = "Neue Projekte sind auf dieser Instanz nicht eingerichtet."
F_UPLOAD = "Upload nicht gefunden oder nicht lesbar."
F_VORHANDEN = "Ein Projekt mit dieser ID gibt es schon."
F_KONVERTIEREN = "Der Import ist abgebrochen (Exit {code})."
F_VERIFY = "Die Prüfung des neuen Pakets ist fehlgeschlagen."
F_PAKETBAUM = "Das neue Paket enthält unerlaubte Einträge."
F_ABGEBROCHEN = "Abgebrochen."
F_FREISCHALTEN_ZEIT = ("Freischalten nicht bestätigt (Zeitüberschreitung) – Projektliste prüfen; "  # noqa: RUF001
                       "das Paket kann noch erscheinen.")
# Meldewörter der Übergabe (Freischalt-Skript `pruefen` bzw. Schritte der Übergabe) → Text.
MELDUNGEN: dict[str, str] = {
    "OK": "freigeschaltet",
    "VORHANDEN": F_VORHANDEN,
    "RESTE": "Ein abgebrochenes Freischalten liegt noch auf dem Server – Betrieb benachrichtigen.",  # noqa: RUF001
    "KOPIE": "Das Paket ließ sich nicht in den Eingang übernehmen.",
    "FREISCHALTEN": "Das Freischalten ist fehlgeschlagen.",
}
F_MELDUNG = "Freischalten abgelehnt ({wort})."
F_ANHANG_AUS = "Anhänge sind auf dieser Instanz nicht eingerichtet."
F_ANHANG_FEHLT = "Anhang nicht gefunden oder nicht lesbar."
F_ANHANG_KAPUTT = "Das Bild ließ sich nicht verarbeiten."
ANHANG_EIN_MAX = 20 * 1024 * 1024
ANHANG_BILD_MAX = 32 * 1024 * 1024
ANHANG_AUSGABE = ("bild.jpg", "info.json")
ANHANG_EIN_THREAD = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
ANHANG_FRIST_S = 7 * 24 * 3600
_AUFNAHME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def anhang_ausgabe_pruefen(aus: Path) -> dict[str, Any]:
    """Ausgabe des Decoders ist fremd: genau `bild.jpg` (JPEG-Kopf, Größe) und `info.json`
    (Maße 1…8192, optional Aufnahmezeit), reguläre Dateien mit einem Namen. Wirft ValueError."""
    namen = sorted(p.name for p in aus.iterdir())
    if namen != sorted(ANHANG_AUSGABE):
        raise ValueError(f"unerwartete Ausgabe: {namen[:5]}")
    for name in ANHANG_AUSGABE:
        st = (aus / name).lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise ValueError("keine reguläre Datei")
    bild = aus / "bild.jpg"
    if not 0 < bild.stat().st_size <= ANHANG_BILD_MAX:
        raise ValueError("Bildgröße")
    with bild.open("rb") as f:
        if f.read(3) != b"\xff\xd8\xff":
            raise ValueError("kein JPEG")
    roh = (aus / "info.json").read_bytes()
    if len(roh) > 4096:
        raise ValueError("info.json zu groß")
    info = json.loads(roh)
    if not isinstance(info, dict) or set(info) - {"breite", "hoehe", "aufnahme"}:
        raise ValueError("info.json Form")
    for k in ("breite", "hoehe"):
        if type(info.get(k)) is not int or not 0 < info[k] <= 8192:
            raise ValueError("Maße")
    aufnahme = info.get("aufnahme")
    if aufnahme is not None and (not isinstance(aufnahme, str)
                                 or not _AUFNAHME_RE.fullmatch(aufnahme)):
        raise ValueError("Aufnahmezeit")
    return info
UEBERGABE_WARTEN_S = 15 * 60
PAKET_MAX_DATEIEN = 20_000
PAKET_MAX_BYTES = 200 * 1024**3
_ABGELEHNT = "Abgelehnt: "


_HOME: dict[str, Literal["read-only", "tmpfs", "yes"]] = {
    "read-only": "read-only", "tmpfs": "tmpfs", "yes": "yes"}


class Beenden(Exception):
    """SIGTERM/SIGINT: laufenden Auftrag abbrechen und sauber aufhören."""


class SelbsttestFehler(RuntimeError):
    pass


@dataclass(frozen=True)
class Einstellungen:
    jobs_db: Path
    berichte: Path
    data_dir: Path
    import_bin: Path
    reserve_bytes: int = 10 * 1024**3
    # Neue Projekte aus Uploads (alle drei oder keiner): Upload-Ordner (nur lesen), Übergabe
    # (Worker schreibt), Meldungen der Übergabe (nur lesen).
    uploads: Path | None = None
    uebergabe: Path | None = None
    meldungen: Path | None = None
    # Bildanhänge am Befund-Faden (F7): Wurzel mit `eingang/` (Webdienst schreibt, Worker liest)
    # und `geprueft/` (Worker schreibt, Webdienst übernimmt). None = Anhänge aus.
    anhaenge: Path | None = None

    @staticmethod
    def aus_umgebung(env: dict[str, str] | None = None) -> tuple[Einstellungen, SystemdLaeufer]:
        e = dict(os.environ if env is None else env)

        def pfad(name: str) -> Path:
            v = e.get(name, "").strip()
            if not v or not Path(v).is_absolute():
                raise SystemExit(f"Umgebungsvariable {name} fehlt oder ist kein absoluter Pfad.")
            return Path(v)

        roh_reserve = e.get("SCANVIEW_JOBS_RESERVE_GB", "10").strip()
        if not (roh_reserve.isascii() and roh_reserve.isdigit()):
            raise SystemExit("SCANVIEW_JOBS_RESERVE_GB muss eine ganze Zahl ab 0 sein.")
        art = e.get("SCANVIEW_JOBS_SYSTEMD", "user").strip()
        home = _HOME.get(e.get("SCANVIEW_JOBS_PROTECT_HOME", "read-only").strip())
        if art not in ("user", "system") or home is None:
            raise SystemExit("SCANVIEW_JOBS_SYSTEMD muss user|system, "
                             "SCANVIEW_JOBS_PROTECT_HOME read-only|tmpfs|yes sein.")
        unsichtbar = tuple(Path(p) for p in e.get("SCANVIEW_JOBS_UNSICHTBAR", "").split(":")
                           if p.strip())
        if not all(p.is_absolute() for p in unsichtbar):
            raise SystemExit("SCANVIEW_JOBS_UNSICHTBAR: nur absolute Pfade, mit ':' getrennt.")
        namen = ("SCANVIEW_UPLOADS", "SCANVIEW_UEBERGABE", "SCANVIEW_UEBERGABE_MELDUNGEN")
        gesetzt = [bool(e.get(n, "").strip()) for n in namen]
        if any(gesetzt) and not all(gesetzt):
            raise SystemExit(f"{', '.join(namen)} nur gemeinsam setzen.")
        imp = [pfad(n) for n in namen] if all(gesetzt) else [None, None, None]
        anh = pfad("SCANVIEW_ANHAENGE") if e.get("SCANVIEW_ANHAENGE", "").strip() else None
        s = Einstellungen(
            anhaenge=anh,
            jobs_db=pfad("SCANVIEW_JOBS_DB"), berichte=pfad("SCANVIEW_BERICHTE"),
            data_dir=pfad("SCANVIEW_DATA"), import_bin=pfad("SCANVIEW_IMPORT_BIN"),
            reserve_bytes=int(roh_reserve) * 1024**3,
            uploads=imp[0], uebergabe=imp[1], meldungen=imp[2],
        )
        laeufer = SystemdLaeufer(benutzer=art == "user", unsichtbar=unsichtbar, home=home)
        return s, laeufer


def _unit(aid: str) -> str:
    return f"{UNIT_PRAEFIX}{aid}"


def _weg(pfad: Path) -> None:
    """Ordner entfernen, ohne einem Symlink zu folgen (rmtree folgt keinen Links in der Tiefe)."""
    if pfad.is_symlink():
        pfad.unlink()
    elif pfad.exists():
        shutil.rmtree(pfad)


def _ausgabe_uebernehmen(aus: Path, ziel: Path) -> dict[str, Any]:
    """Nur erlaubte, reguläre Dateien ohne Links übernehmen; liefert das geprüfte Ergebnis.

    Was der Job schreibt, ist fremd (er hat fremde Dateien gelesen): keine Symlinks, keine
    Hardlinks, keine Unterordner, Namen und Größe begrenzt."""
    dateien: list[Path] = []
    summe = 0
    for eintrag in sorted(aus.iterdir()):
        st = eintrag.lstat()
        name = eintrag.name
        if (not (eintrag.is_file() and not eintrag.is_symlink()) or st.st_nlink != 1
                or not name.endswith(ERLAUBTE_ENDUNGEN) or name.startswith(".")
                or len(name) > 100 or not set(name) <= _NAME_OK):
            log.warning("Ausgabe übersprungen: %r", name)
            continue
        summe += st.st_size
        dateien.append(eintrag)
    if len(dateien) > AUSGABE_MAX_DATEIEN or summe > AUSGABE_MAX_BYTES:
        raise ValueError("Ausgabe zu groß")
    namen = {d.name for d in dateien}
    if "ergebnis.json" not in namen or "bericht.html" not in namen:
        raise ValueError("ergebnis.json oder bericht.html fehlt")
    roh = (aus / "ergebnis.json").read_bytes()
    if len(roh) > ERGEBNIS_MAX_BYTES:
        raise ValueError("ergebnis.json zu groß")
    e = json.loads(roh)
    gesamt = e.get("gesamt") if isinstance(e, dict) else None
    if not isinstance(gesamt, dict) or not isinstance(gesamt.get("urteil"), str):
        raise ValueError("ergebnis.json ohne Gesamturteil")
    ziel.mkdir(mode=0o750)
    for d in dateien:
        os.rename(d, ziel / d.name)
    return gesamt


def _paketbaum_pruefen(wurzel: Path) -> None:
    """Paket aus dem Auftrag (fremd): nur Ordner und reguläre Dateien mit einem Namen, ohne
    Gruppen-/Fremdschreibrechte, in Grenzen. Wirft ValueError."""
    dateien = 0
    summe = 0
    for ordner, unterordner, namen in os.walk(wurzel, followlinks=False):
        for n in [*unterordner, *namen]:
            st = (Path(ordner) / n).lstat()
            if st.st_mode & 0o022:
                raise ValueError("Schreibrechte für Gruppe/Andere")
            if stat.S_ISDIR(st.st_mode):
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ValueError("Symlink, Hardlink oder Sonderdatei")
            dateien += 1
            summe += st.st_size
    if dateien == 0 or dateien > PAKET_MAX_DATEIEN or summe > PAKET_MAX_BYTES:
        raise ValueError("Paket leer oder zu groß")
    if not (wurzel / "project.json").is_file():
        raise ValueError("project.json fehlt")


def _upload_datei(uploads: Path, upload: str, datei: str) -> tuple[Path, Path] | None:
    """(Upload-Ordner, Hauptdatei), wenn jeder Pfadteil ein echter Ordner bzw. die Datei eine
    reguläre Datei ist (kein Symlink); sonst None."""
    if not A.UPLOAD_ID_RE.fullmatch(upload) or not A.pfad_ok(datei):
        return None
    ordner = uploads / upload
    p = ordner
    for teil in [upload, *datei.split("/")]:
        p = p if teil == upload else p / teil
        try:
            st = p.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode):
            return None
    if not stat.S_ISREG(p.lstat().st_mode) or not stat.S_ISDIR(ordner.lstat().st_mode):
        return None
    return ordner, p


def _meldung(ablage: Path, pid: str) -> str | None:
    try:
        roh = (ablage / pid).read_bytes()[:64].decode("ascii", "replace").strip()
    except OSError:
        return None
    return roh if re.fullmatch(r"[A-Z_]{2,32}", roh) else "UNGUELTIG"


def _letzte_zeile(protokoll: Path) -> str | None:
    """„Abgelehnt: …“-Meldung des Imports (letzte Zeile, ohne Pfade), sonst None."""
    try:
        with protokoll.open("rb") as f:
            f.seek(max(0, protokoll.stat().st_size - 2000))
            zeilen = f.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return None
    if not zeilen or not zeilen[-1].startswith(_ABGELEHNT):
        return None
    text = zeilen[-1][len(_ABGELEHNT):].strip()
    if not 0 < len(text) <= 300 or not text.isprintable() or "/" in text:
        return None
    return text


class Worker:
    def __init__(self, s: Einstellungen, laeufer: Laeufer) -> None:
        self.s = s
        self.laeufer = laeufer
        self.conn = A.verbinden(s.jobs_db)
        A.schema(self.conn)
        self._stopp = threading.Event()
        self._abbruch = threading.Event()

    # --- Start und Aufräumen ------------------------------------------------------------

    def start(self) -> None:
        """Neustartfest: hängende Units stoppen, „laufend“ → „abgebrochen“, Arbeitsordner leer."""
        A.herzschlag(self.conn, os.getpid(), gestartet=True)
        for a in A.laufende(self.conn):
            self.laeufer.stoppen(_unit(a.id))
            A.abschliessen(self.conn, a.id, A.ABGEBROCHEN, fehler=F_NEUSTART)
            log.warning("Auftrag %s war beim Start noch laufend → abgebrochen", a.id)
        self._arbeitsordner_leeren()

    def _arbeitsordner_leeren(self) -> None:
        if self.s.uebergabe is not None and self.s.uebergabe.is_dir():
            for d in self.s.uebergabe.iterdir():
                if d.name.startswith(ARBEIT_PRAEFIX) or d.name.startswith(".verworfen-"):
                    _weg(d)
        if not self.s.berichte.is_dir():
            return
        for projekt in self.s.berichte.iterdir():
            if projekt.is_symlink() or not projekt.is_dir():
                continue
            for d in projekt.iterdir():
                if d.name.startswith(ARBEIT_PRAEFIX):
                    _weg(d)

    def loeschungen(self) -> int:
        n = 0
        for projekt, aid in A.zum_loeschen(self.conn):
            try:
                _weg(A.berichtsordner(self.s.berichte, projekt, aid))
            except A.AuftragUngueltig:
                pass  # ungültige IDs haben nie einen Ordner bekommen
            A.entfernen(self.conn, aid)
            n += 1
        self._uebergabe_raeumen()
        self.anhaenge_raeumen()
        return n

    def _uebergabe_raeumen(self) -> None:
        """Übergebene Pakete mit Meldung, auf die kein laufender Auftrag mehr wartet (z. B. nach
        einem Neustart), entfernen — erst danach räumt die Übergabe ihre Meldung weg."""
        if self.s.uebergabe is None or self.s.meldungen is None or not self.s.uebergabe.is_dir():
            return
        laufend = {a.projekt for a in A.laufende(self.conn)}
        for d in self.s.uebergabe.iterdir():
            if (A.PROJEKT_ID_RE.fullmatch(d.name) and d.name not in laufend
                    and _meldung(self.s.meldungen, d.name) is not None):
                _weg(d)

    # --- Schleife -------------------------------------------------------------------------

    def schritt(self) -> bool:
        """Einen Auftrag abarbeiten. False, wenn keiner wartet."""
        A.herzschlag(self.conn, os.getpid())
        self.loeschungen()
        a = A.naechster(self.conn)
        if a is None:
            return False
        self._mit_herzschlag(a)
        return True

    def _mit_herzschlag(self, a: A.Auftrag) -> None:
        fertig = threading.Event()

        def schlagen() -> None:
            conn = A.verbinden(self.s.jobs_db)
            try:
                schlaege = 0
                while not fertig.wait(2.0):
                    schlaege += 1
                    if schlaege % max(1, A.HERZSCHLAG_S // 2) == 0:
                        A.herzschlag(conn, os.getpid())
                    if a.typ in A.IMPORTE and A.abbruch_gewuenscht(conn, a.id):
                        self._abbruch.set()
                        for unit in (_unit(a.id), f"{_unit(a.id)}-verify"):
                            self.laeufer.stoppen(unit)
            finally:
                conn.close()

        t = threading.Thread(target=schlagen, name="herzschlag", daemon=True)
        t.start()
        try:
            self.ausfuehren(a)
        except Beenden:
            self.laeufer.stoppen(_unit(a.id))
            A.abschliessen(self.conn, a.id, A.ABGEBROCHEN, fehler=F_BEENDET)
            self._weg_arbeit(a)
            raise
        finally:
            fertig.set()
            t.join()

    def _weg_arbeit(self, a: A.Auftrag) -> None:
        _weg(self.s.berichte / a.projekt / f"{ARBEIT_PRAEFIX}{a.id}")

    def ausfuehren(self, a: A.Auftrag) -> None:
        if a.typ not in A.VERFUEGBAR or not A.PROJEKT_ID_RE.fullmatch(a.projekt) \
                or not A.AUFTRAG_ID_RE.fullmatch(a.id):
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler="Auftragstyp nicht verfügbar.")
            return
        try:
            optionen = A.pruefe(a.typ, a.optionen, erlaubt=A.VERFUEGBAR)
        except A.AuftragUngueltig as e:
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=str(e))
            return
        if a.typ in A.IMPORTE:
            self._abbruch.clear()
            self._importieren(a, optionen)
            return
        if a.typ in A.ANHAENGE:
            self._anhang(a, optionen["anhang"])
            return
        paket = self.s.data_dir / a.projekt
        if paket.is_symlink() or not (paket / "project.json").is_file():
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_PAKET)
            return
        projektordner = self.s.berichte / a.projekt
        projektordner.mkdir(mode=0o750, parents=True, exist_ok=True)
        if shutil.disk_usage(projektordner).free < self.s.reserve_bytes:
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_PLATTE)
            return
        arbeit = projektordner / f"{ARBEIT_PRAEFIX}{a.id}"
        _weg(arbeit)
        arbeit.mkdir(mode=0o750)
        aus = arbeit / "aus"
        aus.mkdir()
        budget = A.BUDGETS[a.typ]
        lauf = Lauf(
            name=_unit(a.id),
            argv=[str(self.s.import_bin / "scanview-pruefen"), str(paket),
                  "--profil", optionen["profil"], "--paket", str(paket), "--aus", str(aus)],
            arbeit=arbeit, protokoll=arbeit / "lauf.log",
            grenzen=Grenzen(ram_mb=budget.ram_mb, zeit_s=budget.zeit_s,
                            prozesse=budget.prozesse),
            # Pakete und Berichte anderer Projekte sieht der Job nicht, das eigene Paket nur
            # lesend (nur Projektrechte).
            verbergen=(self.s.data_dir, self.s.berichte), lesen=(paket,),
        )
        try:
            self._auswerten(a, lauf, aus, budget)
        finally:
            _weg(arbeit)

    def _auswerten(self, a: A.Auftrag, lauf: Lauf, aus: Path, budget: A.Budget) -> None:
        log.info("Auftrag %s (%s, Projekt %s) startet", a.id, a.typ, a.projekt)
        e = self.laeufer.ausfuehren(lauf)
        log.info("Auftrag %s: %s (Status %s) nach %.1f s", a.id, e.ausgang, e.status, e.dauer_s)
        if e.ausgang == "speicher":
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN,
                           fehler=F_SPEICHER.format(mb=budget.ram_mb))
            return
        if e.ausgang == "zeit":
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_ZEIT.format(s=budget.zeit_s))
            return
        if e.ausgang == "start":
            log.error("Auftrag %s: Start fehlgeschlagen: %s", a.id, e.roh[-500:])
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_START)
            return
        # Prüfer: 0 = alles erfüllt, 1 = mindestens eine Zeile nicht erfüllt (gültiger Bericht),
        # 2 = Katalog-/Eingabefehler, alles andere = Absturz.
        if e.ausgang == "exit" and e.status == 2:
            self._protokoll_ins_journal(a, lauf)
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_EINGABE)
            return
        if e.ausgang not in ("ok", "exit") or e.status not in (0, 1):
            self._protokoll_ins_journal(a, lauf)
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN,
                           fehler=F_ABBRUCH.format(code=e.status if e.status is not None else "?"))
            return
        try:
            gesamt = _ausgabe_uebernehmen(aus, A.berichtsordner(self.s.berichte, a.projekt, a.id))
        except (OSError, ValueError) as fehler:
            log.error("Auftrag %s: Ausgabe ungültig: %s", a.id, fehler)
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_AUSGABE)
            return
        zaehlung = gesamt.get("zaehlung")
        A.abschliessen(
            self.conn, a.id, A.FERTIG, urteil=str(gesamt["urteil"])[:80],
            zaehlung=zaehlung if isinstance(zaehlung, dict) else None,
        )

    # --- Bildanhang am Befund-Faden (F7) ------------------------------------------------

    def _anhang(self, a: A.Auftrag, anhang: str) -> None:
        """Bild isoliert dekodieren und neu kodieren lassen; nur geprüfte Ausgabe übernehmen."""
        anh = self.s.anhaenge
        if anh is None:
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_ANHANG_AUS)
            return
        ein = anh / "eingang" / anhang
        try:
            st = ein.lstat()
        except OSError:
            st = None
        if (st is None or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or not 0 < st.st_size <= ANHANG_EIN_MAX):
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_ANHANG_FEHLT)
            return
        geprueft = anh / "geprueft"
        geprueft.mkdir(mode=0o750, exist_ok=True)
        ziel = geprueft / anhang
        arbeit = geprueft / f"{ARBEIT_PRAEFIX}{a.id}"
        _weg(arbeit)
        arbeit.mkdir(mode=0o750)
        aus = arbeit / "aus"
        budget = A.BUDGETS[a.typ]
        s = self.s
        lauf = Lauf(
            name=_unit(a.id),
            argv=[str(s.import_bin / "scanview-import"), "anhang-bereinigen", str(ein), str(aus)],
            arbeit=arbeit, protokoll=arbeit / "lauf.log",
            grenzen=Grenzen(ram_mb=budget.ram_mb, zeit_s=budget.zeit_s, prozesse=budget.prozesse,
                            datei_mb=64),
            # Ein Thread genügt: OpenBLAS startet sonst je Kern einen Thread, scheitert an TasksMax
            # und blockiert den numpy-Import bis zum Zeitlimit (gemessen im echten Lauf).
            umgebung={v: "1" for v in ANHANG_EIN_THREAD},
            # Nichts außer der einen Eingabedatei (lesend) und dem eigenen Arbeitsordner.
            verbergen=tuple(p for p in (s.data_dir, s.berichte, s.uploads, s.uebergabe, anh)
                            if p is not None),
            lesen=(ein,),
        )
        try:
            log.info("Auftrag %s (%s, Projekt %s) startet", a.id, a.typ, a.projekt)
            e = self.laeufer.ausfuehren(lauf)
            log.info("Auftrag %s: %s (Status %s) nach %.1f s", a.id, e.ausgang, e.status,
                     e.dauer_s)
            if self._grenzfehler(a, e):
                return
            if e.ausgang != "ok":
                meldung = _letzte_zeile(lauf.protokoll) if e.status == 2 else None
                if meldung is None:
                    self._protokoll_ins_journal(a, lauf)
                A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN,
                               fehler=meldung or F_ANHANG_KAPUTT)
                return
            try:
                info = anhang_ausgabe_pruefen(aus)
            except (OSError, ValueError) as fehler:
                log.error("Auftrag %s: Ausgabe des Anhangs ungültig: %s", a.id, fehler)
                A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_ANHANG_KAPUTT)
                return
            _weg(ziel)
            ziel.mkdir(mode=0o750)
            for name in ANHANG_AUSGABE:
                os.rename(aus / name, ziel / name)
            A.abschliessen(self.conn, a.id, A.FERTIG, urteil="bereit",
                           zaehlung={"breite": info["breite"], "hoehe": info["hoehe"]})
        finally:
            _weg(arbeit)

    def anhaenge_raeumen(self, jetzt: float | None = None) -> int:
        """Geprüfte Anhänge, die der Webdienst nach ANHANG_FRIST_S nicht übernommen hat, weg."""
        if self.s.anhaenge is None or not (self.s.anhaenge / "geprueft").is_dir():
            return 0
        grenze = (time.time() if jetzt is None else jetzt) - ANHANG_FRIST_S
        n = 0
        for d in (self.s.anhaenge / "geprueft").iterdir():
            if A.ANHANG_ID_RE.fullmatch(d.name) and d.lstat().st_mtime < grenze:
                _weg(d)
                n += 1
        return n

    # --- Import: neues Projekt aus einem Upload ------------------------------------------

    def _fehl(self, a: A.Auftrag, text: str) -> None:
        status = A.ABGEBROCHEN if self._abbruch.is_set() else A.FEHLGESCHLAGEN
        A.abschliessen(self.conn, a.id, status,
                       fehler=F_ABGEBROCHEN if status == A.ABGEBROCHEN else text)

    def _importieren(self, a: A.Auftrag, optionen: dict[str, str]) -> None:
        s = self.s
        if s.uploads is None or s.uebergabe is None or s.meldungen is None:
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_NICHT_EINGERICHTET)
            return
        pid = optionen["projekt"]
        quelle = _upload_datei(s.uploads, optionen["upload"], optionen.get("datei", ""))
        if pid != a.projekt or quelle is None:
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_UPLOAD)
            return
        upload_ordner, datei = quelle
        if (s.data_dir / pid).exists() or (s.uebergabe / pid).exists():
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_VORHANDEN)
            return
        if shutil.disk_usage(s.uebergabe).free < s.reserve_bytes + 3 * datei.stat().st_size:
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_PLATTE_IMPORT)
            return
        arbeit = s.uebergabe / f"{ARBEIT_PRAEFIX}{a.id}"
        _weg(arbeit)
        arbeit.mkdir(mode=0o750)
        (arbeit / "paket").mkdir(mode=0o750)
        ziel = arbeit / "paket" / pid
        try:
            if self._import_laeufe(a, optionen, upload_ordner, datei, arbeit, ziel):
                self._uebergeben(a, pid, ziel)
        finally:
            _weg(arbeit)

    def _import_lauf(self, a: A.Auftrag, name: str, argv: list[str], arbeit: Path,
                     upload_ordner: Path, protokoll: str) -> tuple[Lauf, Ergebnis]:
        budget = A.BUDGETS[a.typ]
        s = self.s
        assert s.uploads is not None and s.uebergabe is not None
        lauf = Lauf(
            name=name, argv=argv, arbeit=arbeit, protokoll=arbeit / protokoll,
            grenzen=Grenzen(ram_mb=budget.ram_mb, zeit_s=budget.zeit_s,
                            prozesse=budget.prozesse, datei_mb=8192),
            # Pakete, Berichte, andere Uploads und die Übergabe sieht der Job nicht; nur den
            # eigenen Upload lesend und seinen Arbeitsordner schreibend.
            verbergen=(s.data_dir, s.berichte, s.uploads, s.uebergabe),
            lesen=(upload_ordner,),
        )
        log.info("Auftrag %s (%s, Projekt %s): %s", a.id, a.typ, a.projekt, argv[1])
        e = self.laeufer.ausfuehren(lauf)
        log.info("Auftrag %s: %s (Status %s) nach %.1f s", a.id, e.ausgang, e.status, e.dauer_s)
        return lauf, e

    def _grenzfehler(self, a: A.Auftrag, e: Ergebnis) -> bool:
        budget = A.BUDGETS[a.typ]
        if e.ausgang == "speicher":
            self._fehl(a, F_SPEICHER.format(mb=budget.ram_mb))
        elif e.ausgang == "zeit":
            self._fehl(a, F_ZEIT.format(s=budget.zeit_s))
        elif e.ausgang == "start":
            log.error("Auftrag %s: Start fehlgeschlagen: %s", a.id, e.roh[-500:])
            self._fehl(a, F_START)
        else:
            return False
        return True

    def _import_laeufe(self, a: A.Auftrag, optionen: dict[str, str], upload_ordner: Path,
                       datei: Path, arbeit: Path, ziel: Path) -> bool:
        imp = str(self.s.import_bin / "scanview-import")
        argv = [imp, "neu-ohne-wolke", str(datei), str(ziel),
                "--art", "mesh" if a.typ == "neu_mesh" else "splats",
                "--titel", optionen["titel"]]
        if "crs" in optionen:
            argv += ["--crs", optionen["crs"]]
        if "quellHoehe" in optionen:
            argv += ["--quell-hoehensystem", optionen["quellHoehe"]]
        if "zielHoehe" in optionen:
            argv += ["--ziel-hoehensystem", optionen["zielHoehe"]]
        A.stufe_setzen(self.conn, a.id, "konvertiert")
        lauf, e = self._import_lauf(a, _unit(a.id), argv, arbeit, upload_ordner, "import.log")
        if self._abbruch.is_set():
            self._fehl(a, F_ABGEBROCHEN)
            return False
        if self._grenzfehler(a, e):
            return False
        if e.ausgang != "ok":
            self._protokoll_ins_journal(a, lauf)
            meldung = _letzte_zeile(lauf.protokoll) if e.status == 2 else None
            self._fehl(a, meldung or F_KONVERTIEREN.format(
                code=e.status if e.status is not None else "?"))
            return False
        A.stufe_setzen(self.conn, a.id, "prueft")
        lauf, e = self._import_lauf(a, f"{_unit(a.id)}-verify", [imp, "verify", str(datei),
                                                                   str(ziel)],
                                    arbeit, upload_ordner, "verify.log")
        if self._abbruch.is_set():
            self._fehl(a, F_ABGEBROCHEN)
            return False
        if self._grenzfehler(a, e):
            return False
        if e.ausgang != "ok":
            self._protokoll_ins_journal(a, lauf)
            self._fehl(a, F_VERIFY)
            return False
        try:
            _paketbaum_pruefen(ziel)
        except (OSError, ValueError) as fehler:
            log.error("Auftrag %s: Paketbaum abgelehnt: %s", a.id, fehler)
            self._fehl(a, F_PAKETBAUM)
            return False
        return True

    def _uebergeben(self, a: A.Auftrag, pid: str, ziel: Path) -> None:
        s = self.s
        assert s.uebergabe is not None and s.meldungen is not None
        A.stufe_setzen(self.conn, a.id, "freischalten")
        # Eine alte Meldung zu dieser ID (voriger Versuch) räumt die Übergabe beim nächsten Lauf.
        frist = time.monotonic() + 60
        while _meldung(s.meldungen, pid) is not None and time.monotonic() < frist:
            if self._stopp.wait(2.0):
                raise Beenden
        if _meldung(s.meldungen, pid) is not None or (s.uebergabe / pid).exists():
            self._fehl(a, F_VORHANDEN)
            return
        os.rename(ziel, s.uebergabe / pid)
        ende = time.monotonic() + UEBERGABE_WARTEN_S
        wort = None
        while time.monotonic() < ende:
            wort = _meldung(s.meldungen, pid)
            if wort is not None:
                break
            if self._stopp.wait(2.0):
                raise Beenden
        if wort is None:
            # Nicht löschen, solange die Übergabe vielleicht kopiert: zur Seite legen, der
            # nächste Start räumt es weg.
            os.rename(s.uebergabe / pid, s.uebergabe / f".verworfen-{a.id}")
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN, fehler=F_FREISCHALTEN_ZEIT)
            return
        _weg(s.uebergabe / pid)
        if wort == "OK":
            A.abschliessen(self.conn, a.id, A.FERTIG, urteil="freigeschaltet")
            log.info("Auftrag %s: Projekt %s freigeschaltet", a.id, pid)
        else:
            log.error("Auftrag %s: Übergabe meldet %s", a.id, wort)
            A.abschliessen(self.conn, a.id, A.FEHLGESCHLAGEN,
                           fehler=MELDUNGEN.get(wort, F_MELDUNG.format(wort=wort)))

    @staticmethod
    def _protokoll_ins_journal(a: A.Auftrag, lauf: Lauf) -> None:
        """Ende des Job-Protokolls ins Journal des Workers (Admin), nie in jobs.db/API."""
        try:
            with lauf.protokoll.open("rb") as f:
                f.seek(max(0, lauf.protokoll.stat().st_size - 4000))
                ende = f.read().decode("utf-8", "replace")
        except OSError:
            return
        log.error("Auftrag %s: Ende des Protokolls:\n%s", a.id, ende)

    def schleife(self, *, einmal: bool = False) -> None:
        while not self._stopp.is_set():
            if self.schritt():
                continue
            if einmal:
                return
            self._stopp.wait(2.0)

    def stoppen(self) -> None:
        self._stopp.set()


# --- Selbsttest ---------------------------------------------------------------------------

_PROBE = r"""
import json, os, socket, sys
arbeit, aussen = sys.argv[1], sys.argv[2]
r = {}
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3).close(); r["netz"] = "offen"
except OSError:
    r["netz"] = "zu"
try:
    socket.getaddrinfo("example.org", 443); r["dns"] = "offen"
except OSError:
    r["dns"] = "zu"
try:
    socket.socket(socket.AF_UNIX).close(); r["unix"] = "offen"
except OSError:
    r["unix"] = "zu"
try:
    open(os.path.join(aussen, "probe"), "w").close(); r["aussen"] = "offen"
except OSError:
    r["aussen"] = "zu"
open(os.path.join(arbeit, "selbsttest.json"), "w").write(json.dumps(r))
"""


# RAM-Probe: Der Speicherfresser ist ein Kindprozess; die Unit trägt `OOMPolicy=continue`.
# So tötet der Kern bei wirksamer Grenze nur das Kind, die Unit endet regulär statt mit
# `oom-kill` (den der KDE-Melder als „Speichermangel abgewendet“ anzeigt). Beleg ist nicht der
# Ausgang der Unit, sondern: Kind per SIGKILL beendet UND Zähler `oom` (Grenze der eigenen cgroup
# erreicht) in `memory.events` gestiegen — `oom_kill` allein zählte auch den globalen OOM-Killer.
# Reicht der Speicher, endet das Kind regulär und die Probe mit 0 („offen“). Ist `memory.events`
# nicht lesbar, meldet sie _RAM_UNBELEGT; dann entscheidet die Speicherspitze, die systemd-run
# außerhalb der Unit ausgibt.
_RAM_PROBE = r"""
import os, signal, sys
mb, greift, unbelegt = (int(a) for a in sys.argv[1:4])
def zaehler():
    try:
        with open("/proc/self/cgroup") as f:
            pfad = next(z[3:].strip() for z in f if z.startswith("0::"))
        with open("/sys/fs/cgroup" + pfad + "/memory.events") as f:
            return {k: int(v) for k, v in (z.split() for z in f)}
    except (OSError, ValueError, StopIteration):
        return None
vorher = zaehler()
pid = os.fork()
if pid == 0:
    b = bytearray(mb * 1024 * 1024)
    for i in range(0, len(b), 4096):
        b[i] = 1
    os._exit(0)
_, st = os.waitpid(pid, 0)
if not (os.WIFSIGNALED(st) and os.WTERMSIG(st) == signal.SIGKILL):
    sys.exit(0)
nachher = zaehler()
if vorher is None or nachher is None or "oom" not in vorher or "oom" not in nachher:
    sys.exit(unbelegt)
sys.exit(greift if nachher["oom"] > vorher["oom"] else 0)
"""
_RAM_GREIFT, _RAM_UNBELEGT = 42, 43
# Grenze der Probe-Unit und Bedarf des Kindes (viermal so viel).
_RAM_GRENZE_MB, _RAM_BEDARF_MB = 64, 256
_SPITZE_RE = re.compile(r"^\s*Memory peak: ([0-9.]+)([BKMGT])\b", re.MULTILINE)
_EINHEIT_MB = {"B": 1 / 2**20, "K": 1 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0**2}


def _speicherspitze_mb(roh: str) -> float | None:
    """„Memory peak: 64M“ aus der Zusammenfassung von `systemd-run --wait` (letzte Zeile)."""
    treffer = _SPITZE_RE.findall(roh)
    if not treffer:
        return None
    zahl, einheit = treffer[-1]
    try:
        return float(zahl) * _EINHEIT_MB[einheit]
    except ValueError:
        return None


def ram_grenze(laeufer: Laeufer, arbeit: Path, verbergen: tuple[Path, ...] = (),
               ram_mb: int = _RAM_GRENZE_MB) -> str:
    """„zu“, wenn der Kern das Kind wegen der RAM-Grenze der Unit getötet hat, sonst „offen“.
    Im Zweifel „offen“."""
    e = laeufer.ausfuehren(Lauf(
        name=f"{UNIT_PRAEFIX}selbsttest-ram",
        argv=[sys.executable, "-I", "-c", _RAM_PROBE, str(_RAM_BEDARF_MB), str(_RAM_GREIFT),
              str(_RAM_UNBELEGT)],
        arbeit=arbeit, protokoll=arbeit / "ram.log",
        grenzen=Grenzen(ram_mb=ram_mb, zeit_s=60, prozesse=16), verbergen=verbergen,
        oom_fortsetzen=True,
    ))
    if e.ausgang == "speicher":
        # OOMPolicy nicht beachtet, die Grenze hat aber gegriffen.
        log.info("RAM-Probe: Unit wegen Speicher beendet")
        return "zu"
    if e.ausgang == "exit" and e.status == _RAM_GREIFT:
        log.info("RAM-Probe: Kind wegen der Grenze getötet (memory.events)")
        return "zu"
    if e.ausgang == "exit" and e.status == _RAM_UNBELEGT:
        spitze = _speicherspitze_mb(e.roh)
        log.info("RAM-Probe: memory.events nicht lesbar, Kind getötet, Spitze %s MB", spitze)
        return "zu" if spitze is not None and spitze <= ram_mb else "offen"
    log.info("RAM-Probe: Grenze nicht belegt (%s, %s)", e.ausgang, e.status)
    return "offen"


def selbsttest(s: Einstellungen, laeufer: Laeufer) -> dict[str, str]:
    """Probelauf mit denselben Grenzen: kein Netz, kein DNS, kein Unix-Socket, nichts außerhalb
    des Arbeitsordners beschreibbar, und innen beschreibbar; dazu greift die RAM-Grenze (ohne
    Memory-Delegation nähme systemd `MemoryMax` an, ohne es durchzusetzen; Probe: `ram_grenze`).
    Wirft SelbsttestFehler."""
    s.berichte.mkdir(mode=0o750, parents=True, exist_ok=True)
    arbeit = s.berichte / f"{ARBEIT_PRAEFIX}selbsttest"
    _weg(arbeit)
    arbeit.mkdir(mode=0o750)
    try:
        lauf = Lauf(
            name=f"{UNIT_PRAEFIX}selbsttest",
            argv=[sys.executable, "-I", "-c", _PROBE, str(arbeit), str(s.berichte)],
            arbeit=arbeit, protokoll=arbeit / "lauf.log",
            grenzen=Grenzen(ram_mb=256, zeit_s=60, prozesse=16),
            verbergen=(s.berichte,),
        )
        e = laeufer.ausfuehren(lauf)
        try:
            r: dict[str, str] = json.loads((arbeit / "selbsttest.json").read_text())
        except (OSError, ValueError):
            raise SelbsttestFehler(f"Probelauf ohne Ergebnis ({e.ausgang}, {e.status})") from None
        r["ram"] = ram_grenze(laeufer, arbeit, (s.berichte,))
    finally:
        _weg(arbeit)
        (s.berichte / "probe").unlink(missing_ok=True)
    offen = [k for k, v in r.items() if v != "zu"]
    if offen:
        raise SelbsttestFehler(f"Isolation greift nicht: {', '.join(sorted(offen))}")
    return r


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scanview-import-worker")
    p.add_argument("--einmal", action="store_true",
                   help="Warteschlange abarbeiten und dann beenden")
    p.add_argument("--selbsttest", action="store_true", help="nur den Isolationstest ausführen")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s, laeufer = Einstellungen.aus_umgebung()
    try:
        ergebnis = selbsttest(s, laeufer)
    except SelbsttestFehler as e:
        log.error("Selbsttest fehlgeschlagen, Worker startet nicht: %s", e)
        return 3
    log.info("Selbsttest bestanden: %s", ergebnis)
    if a.selbsttest:
        return 0
    w = Worker(s, laeufer)

    def beenden(_sig: int, _frame: FrameType | None) -> None:
        w.stoppen()
        raise Beenden

    signal.signal(signal.SIGTERM, beenden)
    signal.signal(signal.SIGINT, beenden)
    try:
        w.start()
        w.schleife(einmal=a.einmal)
    except Beenden:
        log.info("Worker beendet")
    except sqlite3.Error as e:
        log.error("jobs.db nicht nutzbar: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
