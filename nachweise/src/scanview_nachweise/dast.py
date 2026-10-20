"""DAST: OWASP ZAP (passiver Scan der Baseline-Regeln) gegen die Testinstanz L.

Ablauf (fail-closed, jede Verletzung `NachweisFehler`):

- **Gehärteter Container** über `werkzeuge.docker_befehl` (gepinntes Image mit Digest, keine
  Capabilities, `--read-only`, eigener Nutzer, `no-new-privileges`, Standardnetz der Laufzeit
  statt Host-Netz). Einziges Schreibziel ist der Arbeitsordner unter `/zap/wrk`.
- **Anmeldung per Cookie**, nie über argv oder Umgebung: `auth.prop` (0600) trägt eine
  Replacer-Regel, die jeder Anfrage das Sitzungs-Cookie anhängt (`-configfile`). Die Datei wird
  nach dem Lauf gelöscht, auch bei Abbruch. Das Konto ist ein Betrachter ohne Projektrechte.
- **Eigener Automation-Framework-Ablauf statt `zap-baseline.py`**: das Skript kennt keine
  zusätzlichen Einstiegs-URLs und liefert keine Liste der besuchten Adressen; die Oberfläche ist
  eine Einzelseiten-Anwendung, deren Spider die API-Pfade nicht von selbst findet. Der Ablauf nutzt
  dieselben Passiv-Regeln und setzt zusätzlich je GET-Operation der OpenAPI (ohne Platzhalter)
  eine Einstiegs-URL, schließt Anmelde-, Abmelde-, Konto-, Admin- und Statistik-Pfade aus und
  exportiert die besuchten URLs mit Antwortcode.
- **Wirksamkeit**: vor und nach dem Lauf muss `/api/v1/openapi.json` mit dem Cookie 200
  liefern (`dast-sitzung`), und mindestens eine URL unter `/api/v1/` muss mit 200 beantwortet
  worden sein (`dast-abdeckung`) — sonst hätte der Lauf die API nie berührt.
- **Gate** wie bei SAST: Alarm ab „mittel“ braucht einen Befund `B-DAST-<pluginid>`, jeder
  übrige einen Befund oder eine begründete Ausnahme aus `docs/compliance/dast-ausnahmen.toml`.
- **Bereinigung**: das Cookie wird aus allen Berichtsdateien entfernt, danach darf kein Treffer
  bleiben.
- **S** (Produktion mit Kundendaten) nur mit `--betreiber-freigabe <heutiges Datum, UTC>`; L nur
  gegen die feste Adresse der Testinstanz."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote, urlsplit

from . import archiv
from .fehler import NachweisFehler
from .positivliste import glob_regex
from .register import SCHWEREN, Befund, Schwere
from .werkzeuge import Werkzeug, docker_befehl, gefilterte_umgebung

#: Testinstanz L, festgelegt über den SHA-256 ihres Ursprungs `https://host[:port]` (wie ihn
#: `pruefe_ziel` normalisiert). Die Adresse selbst gehört nicht in den veröffentlichten Code; der
#: Hash lässt sich nicht umstellen, ohne den Code zu ändern. Gegen jede andere Adresse läuft die
#: Stufe nur als `S`.
L_URSPRUNG_SHA256 = "5cf14be113a9ad2f576f35c908b83ddfda2d9951070eb2330f752567ef573467"
INSTANZEN = ("L", "S")
AUSNAHMEN = Path("docs/compliance/dast-ausnahmen.toml")
COOKIE_NAME = "__Host-scanview"

#: Pfade, die die Anfragen des Scans nie berühren dürfen (relativ zum Ursprung, reguläre
#: Ausdrücke): Anmeldung und Abmeldung (beendet die Sitzung des Scans), Konto (Anmeldemittel,
#: Einwilligung), Verwaltung und Statistik (ändernde Aufrufe).
AUSSCHLUESSE = (r"/auth/.*", r"/.*/abmelden.*", r"/api/v1/konto.*", r"/api/v1/admin/.*",
                r"/api/v1/statistik/.*")

_RISIKO: Mapping[str, Schwere] = {"3": "hoch", "2": "mittel", "1": "niedrig", "0": "info"}
_PLUGINID = re.compile(r"[0-9]{1,6}")
_COOKIE = re.compile(r"[A-Za-z0-9._~+/-]{15,512}={0,2}")
_MIN_GEHEIMNIS = 8
_MIN_BEGRUENDUNG = 20
_MAX_SAATEN = 100
_LAUFZEIT_S = 30 * 60
_STDERR_AUSZUG = 1500
_API = "/api/v1/"
#: Der Spider sendet nie Formulare (kein POST), nur Abrufe per GET; zusammen mit der Beschränkung
#: auf Passiv-Regeln (kein Angriffs-Job im Ablauf) macht das den Scan lesend.
_NUR_LESEN = {"processForm": False, "postForm": False}
#: Erzeugte Dateien im Arbeitsordner; keine davon darf vor dem Lauf schon da sein.

Abruf = Callable[[str, str], tuple[int, bytes]]
Laeufer = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


# --- Bericht und Risikostufen ---------------------------------------------------------------------

def schwere(roh: object) -> Schwere:
    """ZAP-`riskcode` → Schwere: 3 hoch, 2 mittel, 1 niedrig, 0 info; alles andere (auch ein
    unbekannter Wert) → hoch (fail-closed)."""
    if isinstance(roh, bool) or not isinstance(roh, int | str):
        return "hoch"
    return _RISIKO.get(str(roh), "hoch")


@dataclass(frozen=True)
class Alarm:
    """Ein ZAP-Alarm, je Regel (`pluginid`) zusammengefasst: höchste Schwere, betroffene Pfade
    (ohne Ursprung, Abfrage und führenden Schrägstrich; die Wurzel ist der leere Pfad)."""

    pluginid: str
    name: str
    schwere: Schwere
    pfade: tuple[str, ...]


def _bericht_fehler(text: str) -> NachweisFehler:
    return NachweisFehler("dast-bericht", text)


def _pfad(uri: str) -> str:
    return urlsplit(uri).path.lstrip("/")


def werte_zap_aus(bericht: dict[str, Any]) -> list[Alarm]:
    """Alarme aus dem JSON-Bericht (`traditional-json`), je `pluginid` einer, nach ID sortiert.
    Ein strukturell abweichender Bericht bricht ab, statt Alarme zu übersehen."""
    sites = bericht.get("site")
    if not isinstance(sites, list):
        raise _bericht_fehler("„site“ fehlt oder ist keine Liste")
    sammlung: dict[str, tuple[str, Schwere, set[str]]] = {}
    for site in sites:
        if not isinstance(site, dict) or not isinstance(site.get("alerts"), list):
            raise _bericht_fehler("Eintrag unter „site“ ohne Liste „alerts“")
        for alarm in site["alerts"]:
            if not isinstance(alarm, dict):
                raise _bericht_fehler("Alarm ist kein Objekt")
            pid = alarm.get("pluginid")
            if not isinstance(pid, str) or _PLUGINID.fullmatch(pid) is None:
                raise _bericht_fehler(f"Alarm ohne gültige „pluginid“ ({pid!r})")
            instanzen = alarm.get("instances")
            if not isinstance(instanzen, list) or not instanzen:
                raise _bericht_fehler(f"Alarm {pid} ohne Instanzen")
            pfade: set[str] = set()
            for i in instanzen:
                if not isinstance(i, dict) or not isinstance(i.get("uri"), str):
                    raise _bericht_fehler(f"Alarm {pid}: Instanz ohne „uri“")
                pfade.add(_pfad(i["uri"]))
            stufe = schwere(alarm.get("riskcode"))
            name = alarm.get("name")
            name = name if isinstance(name, str) else pid
            alt = sammlung.get(pid)
            if alt is not None:
                pfade |= alt[2]
                if SCHWEREN.index(alt[1]) < SCHWEREN.index(stufe):
                    stufe = alt[1]
                name = alt[0]
            sammlung[pid] = (name, stufe, pfade)
    return [Alarm(pluginid=pid, name=n, schwere=s, pfade=tuple(sorted(p)))
            for pid, (n, s, p) in sorted(sammlung.items())]


# --- Ausnahmen und Gate ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ausnahme:
    """Begründete Ausnahme für Alarme unter „mittel“: Regel (`pluginid`) und Pfad-Globs (wie
    die Positivliste, auf den Pfad ohne führenden Schrägstrich); `schwere` ist die höchste
    erfasste Schwere (niedrig oder info)."""

    pluginid: str
    pfade: tuple[str, ...]
    schwere: Schwere
    begruendung: str
    datum: date

    def erfasst(self, alarm: Alarm) -> bool:
        if alarm.pluginid != self.pluginid or alarm.schwere not in ("niedrig", "info"):
            return False
        if SCHWEREN.index(alarm.schwere) < SCHWEREN.index(self.schwere):
            return False
        muster = [glob_regex(m) for m in self.pfade]
        return all(any(m.fullmatch(p) for m in muster) for p in alarm.pfade)


_AUSNAHME_FELDER = {"pluginid", "pfade", "schwere", "begruendung", "datum"}


def _ausnahme(nr: int, t: object, pfad: Path) -> Ausnahme:
    def fehler(text: str) -> NachweisFehler:
        return NachweisFehler("dast-ausnahmen", f"{pfad.name} Ausnahme Nr. {nr}: {text}")

    if not isinstance(t, dict):
        raise fehler("keine Tabelle")
    unbekannt = sorted(set(t) - _AUSNAHME_FELDER)
    if unbekannt:
        raise fehler(f"unbekannte Felder {', '.join(unbekannt)}")
    pid = t.get("pluginid")
    if not isinstance(pid, str) or _PLUGINID.fullmatch(pid) is None:
        raise fehler("„pluginid“ muss die ZAP-Regelnummer sein (nur Ziffern)")
    stufe = t.get("schwere")
    if stufe not in ("niedrig", "info"):
        raise fehler("„schwere“ nur niedrig oder info — Alarme ab mittel gehören ins "
                     "Befundregister")
    begruendung = t.get("begruendung")
    if not isinstance(begruendung, str) or len(begruendung.strip()) < _MIN_BEGRUENDUNG:
        raise fehler(f"„begruendung“ fehlt oder ist kürzer als {_MIN_BEGRUENDUNG} Zeichen")
    datum = t.get("datum")
    if not isinstance(datum, date) or isinstance(datum, datetime):
        raise fehler("„datum“ muss ein TOML-Datum JJJJ-MM-TT sein")
    pfade = t.get("pfade")
    if not isinstance(pfade, list) or not pfade \
            or not all(isinstance(m, str) and m.strip() for m in pfade):
        raise fehler("„pfade“ muss eine nicht leere Liste von Globs sein")
    for m in pfade:
        try:
            glob_regex(m)
        except NachweisFehler as e:
            raise fehler(f"Glob „{m}“ ungültig: {e.text}") from None
    return Ausnahme(pluginid=pid, pfade=tuple(pfade), schwere=cast(Schwere, stufe),
                    begruendung=begruendung, datum=datum)


def lade_ausnahmen(pfad: Path) -> list[Ausnahme]:
    """`dast-ausnahmen.toml` laden (fail-closed). Leere Datei = keine Ausnahmen."""
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NachweisFehler("dast-ausnahmen", f"{pfad}: Datei fehlt") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise NachweisFehler("dast-ausnahmen", f"{pfad}: nicht lesbar ({e})") from None
    if set(daten) - {"ausnahme"}:
        raise NachweisFehler("dast-ausnahmen", f"{pfad}: nur [[ausnahme]]-Einträge erlaubt")
    eintraege = daten.get("ausnahme", [])
    if not isinstance(eintraege, list):
        raise NachweisFehler("dast-ausnahmen", f"{pfad}: „ausnahme“ muss [[ausnahme]] sein")
    return [_ausnahme(nr, t, pfad) for nr, t in enumerate(eintraege, 1)]


_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HASH = re.compile(r"(?=[0-9a-fA-F]*[0-9])[0-9a-fA-F]{8,}")
# Inhaltsnamen des Bundlers: `index-DiwrgTda.js` (Zeichenfolge nach „-“ vor der Endung, mit
# Großbuchstaben oder Ziffer; reine Kleinbuchstaben bleiben, damit gewöhnliche Namen nicht
# zusammenfallen).
_BUNDLE = re.compile(r"(?<=-)(?=[A-Za-z0-9_]*[0-9A-Z])[A-Za-z0-9_]{6,}(?=\.[A-Za-z0-9]+$)")


def pfad_muster(pfad: str) -> str:
    """Pfad mit Platzhaltern für alles, was von Aufruf zu Aufruf wechselt: UUIDs (`{uuid}`),
    reine Zahlen (`{n}`) und Hex-Ketten ab 8 Zeichen mit mindestens einer Ziffer (`{hash}`, auch
    mitten im Dateinamen) sowie Inhaltsnamen des Bundlers. Die Abfrage fehlt schon im Pfad."""
    teile = []
    for teil in pfad.split("/"):
        teil = _UUID.sub("{uuid}", teil)
        teile.append("{n}" if teil.isdigit() else _HASH.sub("{hash}", _BUNDLE.sub("{hash}", teil)))
    return "/".join(teile)


def befund_id(pluginid: str, muster: str) -> str:
    """ID des Registereintrags zu einem Alarm an einem Pfadmuster:
    `B-DAST-<pluginid>-<8 hex>` (Großbuchstaben; die 8 Zeichen sind der Anfang von
    `sha256("<pluginid>|<muster>")`). Der Befund gilt nur für dieses Muster."""
    kurz = hashlib.sha256(f"{pluginid}|{muster}".encode()).hexdigest()[:8].upper()
    return f"B-DAST-{pluginid}-{kurz}"


def befund_ids(alarm: Alarm) -> dict[str, str]:
    """Pfadmuster → Befund-ID für alle Pfade des Alarms."""
    return {m: befund_id(alarm.pluginid, m) for m in sorted({pfad_muster(p) for p in alarm.pfade})}


def dast_gate(alarme: Iterable[Alarm], befunde: Iterable[Befund],
              ausnahmen: Iterable[Ausnahme]) -> list[str]:
    """Blockierende Alarme als Text (je Regel einmal). Der Schlüssel ist die Regel samt
    normalisiertem Pfadmuster (`befund_id`): Alarm ab „mittel“ braucht für **jedes** seiner
    Muster einen Befund, der nicht `behoben` ist — ein behobener Befund deckt einen erneuten
    Alarm nicht (Regression), und ein neuer Pfad ist nie gedeckt. Alarm darunter braucht
    Befunde für alle Muster oder eine Ausnahme, die alle Pfade erfasst. (Den Status eines
    vorhandenen Befunds bewertet zusätzlich das Befund-Gate.)"""
    vorhanden = {b.id: b for b in befunde}
    ausnahmen = list(ausnahmen)
    blockiert: dict[str, str] = {}
    for a in alarme:
        ids = befund_ids(a)
        offen = [i for i in ids.values()
                 if i not in vorhanden or vorhanden[i].status == "behoben"]
        if not offen or a.pluginid in blockiert:
            continue
        ort = f"{a.name} ({a.schwere}) in {', '.join(p or '/' for p in a.pfade[:3])}" \
              + (" …" if len(a.pfade) > 3 else "")
        wer = ", ".join(offen[:3]) + (" …" if len(offen) > 3 else "")
        if a.schwere in ("kritisch", "hoch", "mittel"):
            blockiert[a.pluginid] = f"{wer}: {ort} — ins Befundregister eintragen"
        elif not any(x.erfasst(a) for x in ausnahmen):
            blockiert[a.pluginid] = f"{wer}: {ort} — Ausnahme mit Begründung oder Befund nötig"
    return list(blockiert.values())


def zaehle(alarme: Iterable[Alarm]) -> dict[str, int]:
    zahlen = {"hoch": 0, "mittel": 0, "niedrig": 0, "info": 0}
    for a in alarme:
        zahlen[a.schwere if a.schwere != "kritisch" else "hoch"] += 1
    return zahlen


# --- Abdeckung ------------------------------------------------------------------------------------

def besuchte_urls(text: str) -> dict[str, int]:
    """URL → Antwortcode aus dem Export der Sites-Struktur (`export`-Job, YAML). Knoten ohne
    Antwort (nur Zwischenstufen des Pfadbaums) fehlen."""
    urls: dict[str, int] = {}
    aktuell: str | None = None
    for zeile in text.splitlines():
        s = zeile.strip().removeprefix("- ")
        if s.startswith("node:"):
            aktuell = None
        elif s.startswith("url:"):
            aktuell = s[4:].strip().strip("'\"")
        elif s.startswith("statusCode:") and aktuell is not None:
            try:
                urls[aktuell] = int(s[11:].strip())
            except ValueError:
                raise NachweisFehler("dast-lauf", f"Export: ungültiger Antwortcode in „{s}“"
                                     ) from None
    return urls


def pruefe_abdeckung(urls: Mapping[str, int], ziel: str) -> int:
    """Anzahl der URLs unter `<ziel>/api/v1/`, die mit 200 beantwortet wurden; keine → Abbruch."""
    treffer = sorted(u for u, code in urls.items() if code == 200 and u.startswith(ziel + _API))
    if not treffer:
        raise NachweisFehler("dast-abdeckung", "keine URL unter /api/v1/ wurde mit 200 "
                                               "beantwortet — der Scan hätte die API nie berührt "
                                               "(Sitzung oder Einstiegs-URLs prüfen)")
    return len(treffer)


# --- Bereinigung ----------------------------------------------------------------------------------

def _leeren(ordner: Path) -> None:
    for kind in ordner.iterdir():
        if kind.is_symlink() or not kind.is_dir():
            kind.unlink(missing_ok=True)
        else:
            shutil.rmtree(kind, ignore_errors=True)


def _formen(geheimnis: str) -> list[bytes]:
    formen = {geheimnis, quote(geheimnis, safe=""), json.dumps(geheimnis)[1:-1]}
    return sorted((f.encode("utf-8") for f in formen), key=len, reverse=True)


def bereinige(ordner: Path, geheimnisse: list[str]) -> None:
    """Ersetzt jedes Geheimnis (auch URL- und JSON-kodiert) in allen Dateien unter `ordner`
    durch eine Marke und prüft danach nochmals auf Treffer. Symlinks, Sonderdateien, ein zu
    kurzes Geheimnis oder ein verbliebener Treffer: Inhalt des Ordners löschen und
    `NachweisFehler("dast-geheimnis")`."""
    if not ordner.is_dir() or ordner.is_symlink():
        raise NachweisFehler("dast-geheimnis", f"{ordner}: kein Verzeichnis")
    if any(len(g.strip()) < _MIN_GEHEIMNIS or g != g.strip() for g in geheimnisse):
        _leeren(ordner)
        raise NachweisFehler("dast-geheimnis", "Geheimnis leer oder zu kurz, um es sicher zu "
                                               "entfernen — Berichte gelöscht")
    formen = [f for g in geheimnisse for f in _formen(g)]
    dateien: list[Path] = []
    for wurzel, verzeichnisse, namen in os.walk(ordner, followlinks=False):
        for name in [*verzeichnisse, *namen]:
            pfad = Path(wurzel) / name
            if pfad.is_symlink():
                _leeren(ordner)
                raise NachweisFehler("dast-geheimnis", f"{pfad.relative_to(ordner)}: Symlink "
                                                       "im Berichtsordner — Berichte gelöscht")
        dateien += [Path(wurzel) / n for n in namen]
    for datei in dateien:
        if not stat.S_ISREG(datei.lstat().st_mode):
            _leeren(ordner)
            raise NachweisFehler("dast-geheimnis", f"{datei.name}: keine reguläre Datei — "
                                                   "Berichte gelöscht")
        roh = datei.read_bytes()
        neu = roh
        for form in formen:
            neu = neu.replace(form, b"[ENTFERNT]")
        if neu != roh:
            datei.write_bytes(neu)
    for datei in dateien:
        inhalt = datei.read_bytes()
        if any(f in inhalt for f in formen):
            _leeren(ordner)
            raise NachweisFehler("dast-geheimnis", f"{datei.name}: Geheimnis nach der "
                                                   "Bereinigung noch vorhanden — Berichte "
                                                   "gelöscht")


# --- Eingaben -------------------------------------------------------------------------------------

def pruefe_ziel(url: str) -> str:
    """Ursprung `https://host[:port]` ohne Zugangsdaten, Pfad, Abfrage und Fragment; der Host
    klein und ohne Punkt am Ende, der Standardport 443 entfällt (sonst ergäbe dieselbe Instanz
    einen anderen Ursprung und damit einen anderen Hash, siehe `ist_l`)."""
    try:
        t = urlsplit(url)
        port = t.port
    except ValueError:
        raise NachweisFehler("dast-ziel", f"Ziel „{url}“ ist keine gültige Adresse") from None
    host = (t.hostname or "").removesuffix(".")
    if (t.scheme != "https" or not host or host.endswith(".") or t.username is not None
            or t.password is not None or t.path not in ("", "/") or t.query or t.fragment
            or "@" in t.netloc):
        raise NachweisFehler("dast-ziel", f"Ziel „{url}“: nur https://host[:port] ohne "
                                          "Zugangsdaten, Pfad, Abfrage und Fragment")
    return f"https://{host}" + (f":{port}" if port not in (None, 443) else "")


def pruefe_cookie(cookie: str) -> None:
    if _COOKIE.fullmatch(cookie) is None:
        raise NachweisFehler("dast-cookie", "Sitzungswert fehlt oder hat ein unerwartetes Format "
                                            "(erwartet: URL-sicheres Token, ≥ 15 Zeichen)")


def lies_cookie(pfad: Path) -> str:
    """Sitzungswert aus einer Datei (nur für den Besitzer lesbar, kein Symlink); die Datei
    enthält den Wert oder `__Host-scanview=<wert>`."""
    try:
        st = pfad.lstat()
    except OSError:
        raise NachweisFehler("dast-cookie", f"{pfad}: nicht lesbar") from None
    if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid():
        raise NachweisFehler("dast-cookie", f"{pfad}: muss eine eigene reguläre Datei mit Modus "
                                            "0600 sein (kein Symlink)")
    try:
        text = pfad.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise NachweisFehler("dast-cookie", f"{pfad}: nicht lesbar") from None
    wert = text.removeprefix(f"{COOKIE_NAME}=")
    pruefe_cookie(wert)
    return wert


def ist_l(ursprung: str) -> bool:
    """Ob der normalisierte Ursprung (`pruefe_ziel`) die Testinstanz L ist."""
    wert = hashlib.sha256(ursprung.encode("utf-8")).hexdigest()
    return wert == L_URSPRUNG_SHA256


def pruefe_freigabe(instanz: str, ziel: str, freigabe: str | None, heute: date) -> None:
    """S nur mit `--betreiber-freigabe <heutiges Datum, UTC>` (Produktion mit Kundendaten) und
    nie gegen die Adresse von L; L nur gegen die feste Adresse und ohne Freigabe-Datum (das
    Datum gehört zu S, bei L deutet es auf eine verwechselte Instanz)."""
    if instanz not in INSTANZEN:
        raise NachweisFehler("dast-instanz", f"Instanz „{instanz}“ unbekannt (erlaubt: "
                                             f"{', '.join(INSTANZEN)})")
    ursprung = pruefe_ziel(ziel)
    if instanz == "L":
        if freigabe is not None:
            raise NachweisFehler("dast-freigabe", "--betreiber-freigabe gehört zu S; bei L nicht "
                                                  "angeben")
        if not ist_l(ursprung):
            raise NachweisFehler("dast-ziel", f"{ursprung} ist nicht die Adresse von L "
                                              "(Vergleich über L_URSPRUNG_SHA256)")
        return
    if ist_l(ursprung):
        raise NachweisFehler("dast-ziel", "die angegebene Adresse ist L — nicht als S ausgeben")
    if freigabe is None:
        raise NachweisFehler("dast-freigabe", "Instanz S ist Produktion mit Kundendaten: ohne "
                                              "--betreiber-freigabe <heutiges Datum, UTC> kein "
                                              "Lauf")
    try:
        datum = date.fromisoformat(freigabe)
    except ValueError:
        raise NachweisFehler("dast-freigabe", f"--betreiber-freigabe „{freigabe}“ ist kein Datum "
                                              "JJJJ-MM-TT") from None
    if datum != heute:
        raise NachweisFehler("dast-freigabe", f"--betreiber-freigabe {datum.isoformat()} gilt nur "
                                              "für den Tag der Freigabe "
                                              f"({heute.isoformat()}, UTC)")


def neuer_arbeitsordner() -> Path:
    """Neuer Arbeitsordner (0700)."""
    return Path(tempfile.mkdtemp(prefix="scanview-dast-"))


# --- Lauf -----------------------------------------------------------------------------------------

class _KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen werden nie befolgt: der Sitzungswert geht nur an die genannte Adresse."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> None:
        return None


def _http_abruf(url: str, cookie: str, *, opener: Any = None) -> tuple[int, bytes]:
    t = urlsplit(url)
    if t.scheme != "https" or not t.hostname or t.username is not None:
        raise NachweisFehler("dast-sitzung", f"{url}: nur https ohne Zugangsdaten erlaubt")
    opener = opener or urllib.request.build_opener(_KeineWeiterleitung)
    anfrage = urllib.request.Request(
        url, headers={"Cookie": cookie, "Accept": "application/json"})
    try:
        with opener.open(anfrage, timeout=30) as antwort:
            return int(antwort.status), cast(bytes, antwort.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise NachweisFehler("dast-sitzung", f"{url}: nicht erreichbar ({e})") from None


def _an_ziel_gebunden(abruf: Abruf, ziel: str) -> Abruf:
    """Der Sitzungswert geht nur an Adressen unter dem Ursprung des Ziels (Schema, Host, Port)."""
    def gebunden(url: str, cookie: str) -> tuple[int, bytes]:
        if not url.startswith(ziel + "/"):
            raise NachweisFehler("dast-sitzung", f"{url}: nicht unter {ziel} — Sitzungswert "
                                                 "wird nicht gesendet")
        return abruf(url, cookie)
    return gebunden


def _container_lauf(befehl: list[str]) -> subprocess.CompletedProcess[str]:
    name = befehl[befehl.index("--name") + 1] if "--name" in befehl else None
    try:
        return subprocess.run(befehl, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", check=False, stdin=subprocess.DEVNULL,
                              env=gefilterte_umgebung(), timeout=_LAUFZEIT_S)
    except OSError as e:
        raise NachweisFehler("dast-lauf", f"{befehl[0]} nicht ausführbar: {e.strerror}"
                             ) from None
    except subprocess.TimeoutExpired:
        raise NachweisFehler("dast-lauf", f"Zeitüberschreitung nach {_LAUFZEIT_S // 60} "
                                          "Minuten — Container gestoppt") from None
    finally:
        # Auch bei Abbruch (Zeit, Strg-C): ein verwaister Container hielte die Sitzung offen.
        if name is not None:
            subprocess.run([befehl[0], "rm", "-f", name], capture_output=True, check=False,
                           stdin=subprocess.DEVNULL, env=gefilterte_umgebung())


def _sitzung(ziel: str, cookie_header: str, abruf: Abruf) -> dict[str, Any]:
    status, koerper = abruf(f"{ziel}{_API}openapi.json", cookie_header)
    if status != 200:
        raise NachweisFehler("dast-sitzung", f"{_API}openapi.json lieferte {status} statt 200 — "
                                             "die Sitzung ist ungültig oder verloren")
    try:
        daten = json.loads(koerper)
    except ValueError:
        daten = None
    if not isinstance(daten, dict) or not isinstance(daten.get("paths"), dict):
        raise NachweisFehler("dast-sitzung", f"{_API}openapi.json ist kein OpenAPI-Dokument")
    return cast(dict[str, Any], daten)


def _ausgeschlossen(ziel: str, pfad: str) -> bool:
    return any(re.fullmatch(re.escape(ziel) + m, ziel + pfad) for m in AUSSCHLUESSE)


def _saaten(openapi: dict[str, Any], ziel: str) -> list[str]:
    """Einstiegs-URLs: GET-Operationen unter /api/v1/ ohne Pfadparameter, nicht ausgeschlossen."""
    pfade = sorted(p for p, op in openapi["paths"].items()
                   if isinstance(p, str) and p.startswith(_API) and "{" not in p
                   and isinstance(op, dict) and "get" in op and not _ausgeschlossen(ziel, p))
    return [ziel + p for p in pfade[:_MAX_SAATEN]]


def _ablauf(ziel: str, saaten: list[str]) -> dict[str, Any]:
    ursprung = re.escape(ziel)
    jobs: list[dict[str, Any]] = [
        {"type": "passiveScan-config", "parameters": {"enableTags": False,
                                                      "maxAlertsPerRule": 10}},
        {"type": "spider", "parameters": {"context": "scanview", "url": ziel,
                                          "maxDuration": 2, **_NUR_LESEN}},
        *({"type": "spider", "parameters": {"context": "scanview", "url": s, "maxDepth": 1,
                                            "maxDuration": 1, **_NUR_LESEN}} for s in saaten),
        {"type": "passiveScan-wait", "parameters": {"maxDuration": 5}},
        {"type": "export", "parameters": {"context": "scanview", "source": "sitestree",
                                          "type": "yaml", "fileName": "/zap/wrk/urls.yaml"}},
        {"type": "report", "parameters": {"template": "traditional-json",
                                          "reportDir": "/zap/wrk/", "reportFile": "zap.json"}},
    ]
    return {
        "env": {
            "contexts": [{"name": "scanview", "urls": [ziel], "includePaths": [ursprung + "/.*"],
                          "excludePaths": [ursprung + m for m in AUSSCHLUESSE]}],
            "parameters": {"failOnError": True, "failOnWarning": False,
                           "progressToStdout": True},
        },
        "jobs": jobs,
    }


def _auth_datei(pfad: Path, cookie_header: str) -> None:
    zeilen = ["replacer.full_list(0).description=scanview-sitzung",
              "replacer.full_list(0).enabled=true",
              "replacer.full_list(0).matchtype=REQ_HEADER",
              "replacer.full_list(0).matchstr=Cookie",
              "replacer.full_list(0).regex=false",
              f"replacer.full_list(0).replacement={cookie_header}"]
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(zeilen) + "\n")


def zap_lauf(ziel_url: str, cookie: str, werkzeuge: Mapping[str, Werkzeug], arbeit: Path, *,
             abruf: Abruf | None = None, laeufer: Laeufer | None = None) -> Path:
    """ZAP-Passivscan gegen `ziel_url` im gehärteten Container, angemeldet mit dem
    Sitzungswert `cookie`; Rückgabe ist der JSON-Bericht `arbeit/zap.json` (daneben `ablauf.yaml`
    und `urls.yaml`). `auth.prop` mit dem Cookie existiert nur während des Laufs."""
    ziel = pruefe_ziel(ziel_url)
    pruefe_cookie(cookie)
    if not arbeit.is_dir() or arbeit.is_symlink():
        raise NachweisFehler("dast-ausgabe", f"{arbeit}: kein Arbeitsordner")
    if any(True for _ in arbeit.iterdir()):
        raise NachweisFehler("dast-ausgabe", f"{arbeit}: nicht leer — der Lauf legt Zugangsdaten "
                                             "und Rohberichte dort ab und räumt sie danach auf; "
                                             "ein fremder Ordner wird nie angefasst")
    zap = werkzeuge.get("zap")
    if zap is None:
        raise NachweisFehler("dast-lauf", "Werkzeug „zap“ fehlt in werkzeuge.json")
    abruf = _an_ziel_gebunden(abruf or _http_abruf, ziel)
    laeufer = laeufer or _container_lauf
    try:
        return _lauf_in(ziel, cookie, zap, arbeit, abruf, laeufer)
    except BaseException:
        _aufraeumen(arbeit, cookie)
        raise


def _aufraeumen(arbeit: Path, cookie: str) -> None:
    """Nach einem Fehler: Rohberichte von Zugangsdaten bereinigen (oder löschen). Der Ordner ist
    nach der Leerprüfung in `zap_lauf` Eigentum des Laufs."""
    try:
        bereinige(arbeit, [cookie])
    except NachweisFehler:
        pass  # `bereinige` hat den Inhalt dann schon gelöscht
    except OSError:
        _leeren(arbeit)


def _lauf_in(ziel: str, cookie: str, zap: Werkzeug, arbeit: Path, abruf: Abruf,
             laeufer: Laeufer) -> Path:
    header = f"{COOKIE_NAME}={cookie}"
    openapi = _sitzung(ziel, header, abruf)
    (arbeit / "ablauf.yaml").write_text(
        json.dumps(_ablauf(ziel, _saaten(openapi, ziel)), indent=2) + "\n", encoding="utf-8")
    auth = arbeit / "auth.prop"
    try:
        _auth_datei(auth, header)
        befehl = docker_befehl(
            zap, ["zap.sh", "-cmd", "-silent", "-dir", "/tmp/zap-home",
                  "-configfile", "/zap/wrk/auth.prop", "-autorun", "/zap/wrk/ablauf.yaml"],
            {}, {arbeit: "/zap/wrk"}, netz=True,
            # Java nimmt user.home aus /etc/passwd des Images (nicht aus HOME) und legt dort die
            # Benutzer-Einstellungen an; umgelenkt unter das gehärtete /tmp wie `-dir`, damit
            # kein weiterer beschreibbarer Ort nötig ist.
            umgebung={"JAVA_TOOL_OPTIONS": "-Duser.home=/tmp/zap-home"},
            name=f"scanview-zap-{secrets.token_hex(6)}")
        ergebnis = laeufer(befehl)
    finally:
        auth.unlink(missing_ok=True)
    if ergebnis.returncode != 0:
        auszug = (ergebnis.stdout[-_STDERR_AUSZUG:] + ergebnis.stderr[-_STDERR_AUSZUG:]
                  ).replace(cookie, "[ENTFERNT]").strip()
        raise NachweisFehler("dast-lauf", f"ZAP endete mit Exit {ergebnis.returncode}: "
                                          f"{auszug or '(keine Ausgabe)'}")
    _sitzung(ziel, header, abruf)
    export = arbeit / "urls.yaml"
    if not export.is_file():
        raise NachweisFehler("dast-lauf", "ZAP schrieb keine Liste der besuchten URLs")
    pruefe_abdeckung(besuchte_urls(export.read_text(encoding="utf-8")), ziel)
    bericht = arbeit / "zap.json"
    if not bericht.is_file():
        raise NachweisFehler("dast-lauf", "ZAP schrieb keinen Bericht (zap.json)")
    return bericht


# --- Gesamtlauf -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Ergebnis:
    """Ergebnis von `pruefen`: der geschriebene Nachweis, sein Inhalt und die blockierenden
    Alarme (kein Abbruch)."""

    ziel: Path
    bericht: dict[str, Any]
    blockiert: list[str]


def _version(ziel: str, cookie: str, abruf: Abruf) -> str:
    status, koerper = abruf(f"{ziel}{_API}version", f"{COOKIE_NAME}={cookie}")
    try:
        version = json.loads(koerper).get("version") if status == 200 else None
    except (ValueError, AttributeError):
        version = None
    if not isinstance(version, str) or not version:
        raise NachweisFehler("dast-sitzung", f"{_API}version lieferte keine Version "
                                             f"(Status {status})")
    return version


def pruefen(instanz: Literal["L", "S"] | str, ziel: str, cookie: str,
            werkzeuge: Mapping[str, Werkzeug], arbeit: Path, archiv_pfad: Path, *, heute: date,
            befunde: Iterable[Befund], ausnahmen: Iterable[Ausnahme],
            betreiber_freigabe: str | None = None, abruf: Abruf | None = None,
            laeufer: Laeufer | None = None) -> Ergebnis:
    """Freigabe prüfen, Scan fahren, Alarme gegen Befundregister und Ausnahmen prüfen, Berichte
    bereinigen und als `pruefungen/<datum>[.<lauf>]-dast-<instanz>/` im Archiv festhalten (auch
    bei blockierenden Alarmen: der Nachweis zeigt, was gefunden wurde). Ein weiterer Lauf am
    selben Tag bekommt die nächste Laufnummer, frühere Nachweise bleiben stehen. Die
    Archiv-Vorbedingungen werden vor dem Scan geprüft."""
    pruefe_freigabe(instanz, ziel, betreiber_freigabe, heute)
    ursprung = pruefe_ziel(ziel)
    # Archiv vor dem Scan prüfen: ein Lauf gegen die Instanz, dessen Nachweis danach nicht
    # geschrieben werden kann, wäre umsonst (bei S gegen Produktion).
    archiv.pruefe_schreibbar(archiv_pfad)
    abruf = _an_ziel_gebunden(abruf or _http_abruf, ursprung)
    bericht_pfad = zap_lauf(ursprung, cookie, werkzeuge, arbeit, abruf=abruf, laeufer=laeufer)
    try:
        version = _version(ursprung, cookie, abruf)
        try:
            roh = json.loads(bericht_pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise NachweisFehler("dast-bericht", "zap.json nicht lesbar oder kein JSON") from None
        if not isinstance(roh, dict):
            raise _bericht_fehler("zap.json: oberste Ebene muss ein Objekt sein")
        alarme = werte_zap_aus(roh)
        blockiert = dast_gate(alarme, befunde, list(ausnahmen))
        urls = besuchte_urls((arbeit / "urls.yaml").read_text(encoding="utf-8"))
        api_urls = pruefe_abdeckung(urls, ursprung)
        bereinige(arbeit, [cookie])
    except BaseException:
        _aufraeumen(arbeit, cookie)
        raise
    zap = werkzeuge["zap"]
    ergebnis: dict[str, Any] = {
        "instanz": instanz, "ziel": ursprung, "datum": heute.isoformat(), "version": version,
        "werkzeug": {"image": zap.image, "version": zap.version},
        "alarme": zaehle(alarme),
        "abdeckung": {"urls": len(urls), "api_urls": api_urls},
        "gate": "blockiert" if blockiert else "bestanden", "blockierend": blockiert,
    }
    ziel_pfad = archiv.schreibe_pruefung(
        archiv_pfad, "dast", instanz, ergebnis, {"zap.json": bericht_pfad.read_bytes()},
        heute=heute)
    return Ergebnis(ziel=ziel_pfad, bericht=ergebnis, blockiert=blockiert)
