"""`scanview-post`: versendet fertige Benachrichtigungen aus dem Spool per SMTP (F8).

Eigener Dienst unter eigenem Nutzer. Er kennt nur das Spool-Verzeichnis und den SMTP-Zugang aus
seiner Umgebung — keine Datenbank, keine Konten, kein Paket (Importgrenze per Test). Netz nur zum
eingetragenen SMTP-Server (Drop-in des Betreibers).

Spool: `<spool>/ausgang/<id>.json` = {"id", "an", "nachricht": base64(RFC 5322)} schreibt der
Webdienst; `<spool>/erledigt/<id>.json` = {"status": gesendet|fehlgeschlagen|unklar, "zeit",
"code"} schreibt dieser Dienst. Die Ausgangsdatei löscht der Webdienst, wenn er das Ergebnis
übernommen hat; eine erledigte Nachricht wird hier nie ein zweites Mal gesendet.

Zustellung: genau ein Empfänger (Envelope getrennt von den Kopfzeilen, die trotzdem geprüft
werden: kein Cc/Bcc, To = Empfänger). TLS immer mit Zertifikats- und Hostnamenprüfung
(implizit „tls“, Port 465, oder „starttls“, Port 587); ohne TLS nur zu localhost (Tests,
lokales Relay). Vorübergehende Fehler (4xx, Verbindung) mit Backoff bis zu VERSUCHE Mal;
bricht die Verbindung nach dem Senden der Daten ab, ist die Annahme unklar — dann kein neuer
Versuch (höchstens eine Doppelmail wäre schlimmer als eine fehlende). Ins Journal gehen nur
ID, Zustand und SMTP-Code, nie Adressen oder Inhalte.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import signal
import smtplib
import ssl
import stat
import sys
import time
from dataclasses import dataclass
from email import message_from_bytes
from email.headerregistry import Address
from email.policy import SMTP
from pathlib import Path
from types import FrameType
from typing import Literal

log = logging.getLogger("scanview.post")

ID_RE = re.compile(r"[0-9a-f]{32}")
DATEI_MAX = 256 * 1024
VERSUCHE = 6
BACKOFF_S = (60, 300, 900, 3600, 4 * 3600)
LOKAL = ("localhost", "127.0.0.1", "::1")
Sicherheit = Literal["tls", "starttls", "keine"]


@dataclass(frozen=True)
class Einstellungen:
    spool: Path
    host: str
    port: int
    sicherheit: Sicherheit
    benutzer: str | None
    passwort: str | None
    absender: str
    pro_minute: int = 30
    zeitlimit_s: float = 30.0

    @staticmethod
    def aus_umgebung(env: dict[str, str] | None = None) -> Einstellungen:
        e = dict(os.environ if env is None else env)

        def wert(name: str) -> str:
            v = e.get(name, "").strip()
            if not v:
                raise SystemExit(f"Umgebungsvariable {name} fehlt.")
            return v

        spool = Path(wert("SCANVIEW_POST"))
        host = wert("SCANVIEW_SMTP_HOST")
        roh_sicherheit = e.get("SCANVIEW_SMTP_SICHERHEIT", "tls").strip()
        sicherheiten: dict[str, Sicherheit] = {"tls": "tls", "starttls": "starttls",
                                               "keine": "keine"}
        if roh_sicherheit not in sicherheiten:
            raise SystemExit("SCANVIEW_SMTP_SICHERHEIT muss tls, starttls oder keine sein.")
        sicherheit = sicherheiten[roh_sicherheit]
        if sicherheit == "keine" and host not in LOKAL:
            raise SystemExit("Ohne TLS nur zu localhost (lokales Relay).")
        port_roh = e.get("SCANVIEW_SMTP_PORT", "").strip() or (
            "465" if sicherheit == "tls" else "587" if sicherheit == "starttls" else "25")
        if not port_roh.isdigit() or not 0 < int(port_roh) < 65536:
            raise SystemExit("SCANVIEW_SMTP_PORT muss eine Portnummer sein.")
        benutzer = e.get("SCANVIEW_SMTP_BENUTZER", "").strip() or None
        passwort = None
        datei = e.get("SCANVIEW_SMTP_PASSWORT_DATEI", "").strip()
        if datei:
            st = os.stat(datei)
            if st.st_mode & 0o077:
                raise SystemExit("SCANVIEW_SMTP_PASSWORT_DATEI muss chmod 600 haben.")
            passwort = Path(datei).read_text(encoding="utf-8").strip()
        if bool(benutzer) != bool(passwort):
            raise SystemExit("SCANVIEW_SMTP_BENUTZER und SCANVIEW_SMTP_PASSWORT_DATEI nur "
                             "gemeinsam setzen.")
        absender = wert("SCANVIEW_POST_ABSENDER")
        _adresse(absender)
        pro_minute = e.get("SCANVIEW_POST_PRO_MINUTE", "30").strip()
        if not pro_minute.isdigit() or not 0 < int(pro_minute) <= 600:
            raise SystemExit("SCANVIEW_POST_PRO_MINUTE muss 1 bis 600 sein.")
        return Einstellungen(spool=spool, host=host, port=int(port_roh),
                             sicherheit=sicherheit,
                             benutzer=benutzer, passwort=passwort, absender=absender,
                             pro_minute=int(pro_minute))


def _adresse(roh: str) -> str:
    if any(ord(c) < 33 or ord(c) == 127 for c in roh) or "," in roh:
        raise ValueError("Adresse ungültig")
    a = Address(addr_spec=roh)
    if not a.username or not a.domain:
        raise ValueError("Adresse ungültig")
    return a.addr_spec


@dataclass(frozen=True)
class Nachricht:
    id: str
    an: str
    roh: bytes


def lesen(datei: Path) -> Nachricht:
    """Ausgangsdatei prüfen: reguläre Datei, Größe, ID im Namen = ID im Inhalt, genau ein
    Empfänger, Kopfzeilen ohne Cc/Bcc und mit passendem To. Wirft ValueError."""
    st = datei.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > DATEI_MAX:
        raise ValueError("Ausgangsdatei unzulässig")
    d = json.loads(datei.read_bytes())
    if not isinstance(d, dict) or set(d) != {"id", "an", "nachricht"}:
        raise ValueError("Ausgangsdatei Form")
    mid, an, roh = d["id"], d["an"], base64.b64decode(d["nachricht"], validate=True)
    if not isinstance(mid, str) or not ID_RE.fullmatch(mid) or datei.name != f"{mid}.json":
        raise ValueError("ID")
    an = _adresse(an)
    msg = message_from_bytes(roh, policy=SMTP)
    if msg.get_all("Cc") or msg.get_all("Bcc") or len(msg.get_all("To") or []) != 1:
        raise ValueError("Empfängerkopf")
    if _adresse(msg["To"].addresses[0].addr_spec) != an:
        raise ValueError("To passt nicht")
    return Nachricht(id=mid, an=an, roh=roh)


Ergebnis = Literal["gesendet", "fehlgeschlagen", "unklar", "spaeter"]


class Versand:
    def __init__(self, s: Einstellungen) -> None:
        self.s = s
        self.ausgang = s.spool / "ausgang"
        self.erledigt = s.spool / "erledigt"
        self._versuche: dict[str, tuple[int, float]] = {}
        self._gesendet: list[float] = []

    def _verbinden(self) -> smtplib.SMTP:
        ctx = ssl.create_default_context()
        s = self.s
        verb: smtplib.SMTP
        if s.sicherheit == "tls":
            verb = smtplib.SMTP_SSL(s.host, s.port, timeout=s.zeitlimit_s, context=ctx)
        else:
            verb = smtplib.SMTP(s.host, s.port, timeout=s.zeitlimit_s)
            if s.sicherheit == "starttls":
                verb.starttls(context=ctx)
        if s.benutzer and s.passwort:
            verb.login(s.benutzer, s.passwort)
        return verb

    def senden(self, n: Nachricht) -> tuple[Ergebnis, int | None]:
        try:
            verb = self._verbinden()
        except (OSError, smtplib.SMTPException) as e:
            code = getattr(e, "smtp_code", None)
            if isinstance(code, int) and 500 <= code < 600:
                return "fehlgeschlagen", code
            return "spaeter", code if isinstance(code, int) else None
        try:
            verb.sendmail(self.s.absender, [n.an], n.roh)
        except smtplib.SMTPRecipientsRefused as e:
            codes = [c for c, _m in e.recipients.values()]
            code = codes[0] if codes else None
            return ("spaeter" if code is not None and code < 500 else "fehlgeschlagen"), code
        except smtplib.SMTPResponseException as e:
            return ("spaeter" if e.smtp_code < 500 else "fehlgeschlagen"), e.smtp_code
        except (smtplib.SMTPServerDisconnected, OSError):
            # Verbindung während/nach DATA verloren: Annahme unbekannt — nicht wiederholen.
            return "unklar", None
        finally:
            try:
                verb.quit()
            except (OSError, smtplib.SMTPException):
                verb.close()
        return "gesendet", 250

    def _erledigt(self, mid: str, status: str, code: int | None) -> None:
        self.erledigt.mkdir(mode=0o750, exist_ok=True)
        tmp = self.erledigt / f".{mid}.tmp"
        tmp.write_text(json.dumps({"status": status, "zeit": int(time.time()), "code": code}))
        os.replace(tmp, self.erledigt / f"{mid}.json")
        log.info("Nachricht %s: %s (SMTP %s)", mid, status, code if code is not None else "-")

    def schritt(self, jetzt: float | None = None) -> int:
        """Ein Durchlauf über den Ausgang; Anzahl der Zustellversuche."""
        t = time.time() if jetzt is None else jetzt
        if not self.ausgang.is_dir():
            return 0
        self._gesendet = [x for x in self._gesendet if x > t - 60]
        n = 0
        for datei in sorted(self.ausgang.glob("*.json"), key=lambda p: p.stat().st_mtime):
            mid = datei.stem
            if not ID_RE.fullmatch(mid) or (self.erledigt / f"{mid}.json").exists():
                continue
            versuche, naechster = self._versuche.get(mid, (0, 0.0))
            if naechster > t:
                continue
            if len(self._gesendet) >= self.s.pro_minute:
                break
            try:
                nachricht = lesen(datei)
            except (OSError, ValueError):
                self._erledigt(mid, "fehlgeschlagen", None)
                continue
            ergebnis, code = self.senden(nachricht)
            n += 1
            self._gesendet.append(t)
            if ergebnis == "spaeter":
                versuche += 1
                if versuche >= VERSUCHE:
                    self._erledigt(mid, "fehlgeschlagen", code)
                    self._versuche.pop(mid, None)
                else:
                    warte = BACKOFF_S[min(versuche - 1, len(BACKOFF_S) - 1)]
                    self._versuche[mid] = (versuche, t + warte)
                    log.warning("Nachricht %s: später erneut (Versuch %d, SMTP %s)", mid,
                                versuche, code if code is not None else "-")
                continue
            self._versuche.pop(mid, None)
            self._erledigt(mid, ergebnis, code)
        return n

    def raeumen(self, jetzt: float | None = None) -> None:
        """Ergebnisse, deren Ausgangsdatei der Webdienst schon gelöscht hat, nach 30 Tagen weg."""
        t = time.time() if jetzt is None else jetzt
        if not self.erledigt.is_dir():
            return
        for d in self.erledigt.glob("*.json"):
            if (not (self.ausgang / d.name).exists()
                    and d.lstat().st_mtime < t - 30 * 86400):
                d.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scanview-post",
                                description="Benachrichtigungen aus dem Spool per SMTP senden")
    p.add_argument("--einmal", action="store_true", help="ein Durchlauf, dann beenden")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)
    v = Versand(Einstellungen.aus_umgebung())
    if a.einmal:
        v.schritt()
        return 0
    stopp = False

    def beenden(_sig: int, _f: FrameType | None) -> None:
        nonlocal stopp
        stopp = True

    signal.signal(signal.SIGTERM, beenden)
    signal.signal(signal.SIGINT, beenden)
    while not stopp:
        v.schritt()
        v.raeumen()
        for _ in range(10):
            if stopp:
                break
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
