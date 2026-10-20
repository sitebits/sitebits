"""security.txt nach RFC 9116: aus der Vorlage erzeugen (unsigniert) und signiert prüfen.

`erzeuge` füllt `deploy/platform/security.txt.vorlage` und verweigert, solange `SECURITY.md` den
Marker `export.POSTFACH_MARKER` trägt — eine Datei, die ein noch nicht eingerichtetes Postfach
nennt, wäre eine falsche Zusage. Die Ausgabe ist **unsigniert**; signiert wird außerhalb mit
`gpg --clearsign` (der private Schlüssel bleibt beim Betreiber).

`pruefe` verifiziert die OpenPGP-Klartextsignatur mit `gpg --verify` in einem frischen,
temporären `GNUPGHOME`, in das **nur** der übergebene öffentliche Schlüssel importiert wird —
eine Signatur eines anderen Schlüssels scheitert schon daran. Geprüft wird außerdem, dass
außerhalb des signierten Bereichs kein Text steht, und der Inhalt des signierten Bereichs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .export import POSTFACH_MARKER
from .fehler import NachweisFehler

GPG = "gpg"
GPGCONF = "gpgconf"
#: Standardlaufzeit; RFC 9116 verlangt `Expires` (Empfehlung: unter einem Jahr).
TAGE_STANDARD = 180
TAGE_MAX = 364
SPRACHEN = "de, en"
PFLICHTFELDER = ("Contact", "Expires", "Encryption", "Preferred-Languages", "Canonical", "Policy")
KANONISCHER_PFAD = "/.well-known/security.txt"
_PLATZHALTER = re.compile(r"@[A-Z][A-Z_]*@")
_KONTAKT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_STEUERZEICHEN = re.compile(r"[\s\x00-\x1f\x7f]")
_BEGIN_TEXT = "-----BEGIN PGP SIGNED MESSAGE-----"
_BEGIN_SIGNATUR = "-----BEGIN PGP SIGNATURE-----"
_ENDE_SIGNATUR = "-----END PGP SIGNATURE-----"
_SCHLECHT = frozenset({"BADSIG", "EXPKEYSIG", "EXPSIG", "REVKEYSIG", "ERRSIG", "NO_PUBKEY"})
_MAX_BYTES = 64 * 1024
_ABRUF_SEKUNDEN = 20
_GPG_SEKUNDEN = 30


# --- Erzeugen ------------------------------------------------------------------------------------

def _https(feld: str, wert: str) -> str:
    if _STEUERZEICHEN.search(wert) or not wert.startswith("https://") or len(wert) <= 8:
        raise NachweisFehler("securitytxt-eingabe", f"{feld} muss eine https://-Adresse ohne "
                                                    f"Leer- und Steuerzeichen sein: {wert!r}")
    return wert


def erzeuge(vorlage: Path, security_md: Path, *, kontakt: str, schluessel_url: str,
            kanonisch: str, richtlinie_url: str, heute: date, tage: int = TAGE_STANDARD) -> str:
    """security.txt (unsigniert) aus `vorlage`. `Expires` = `heute` + `tage` Tage (00:00 UTC)."""
    try:
        md = security_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise NachweisFehler("securitytxt-postfach", f"{security_md} nicht lesbar ({e}) — ohne "
                                                     "SECURITY.md kein Meldeweg") from None
    if POSTFACH_MARKER in md:
        raise NachweisFehler("securitytxt-postfach",
                             f"{security_md.name} trägt den Marker {POSTFACH_MARKER} — Postfach "
                             "und Schlüssel sind noch nicht eingerichtet; erst danach "
                             "security.txt erzeugen")
    if not isinstance(tage, int) or isinstance(tage, bool) or not 1 <= tage <= TAGE_MAX:
        raise NachweisFehler("securitytxt-eingabe", f"tage muss zwischen 1 und {TAGE_MAX} liegen "
                                                    f"(RFC 9116: Expires unter einem Jahr): {tage}")
    if _KONTAKT.fullmatch(kontakt) is None:
        raise NachweisFehler("securitytxt-eingabe", f"kontakt ist keine E-Mail-Adresse: "
                                                    f"{kontakt!r}")
    schluessel_url = _https("schluessel_url", schluessel_url)
    richtlinie_url = _https("richtlinie_url", richtlinie_url)
    kanonisch = _https("kanonisch", kanonisch)
    if not kanonisch.endswith(KANONISCHER_PFAD) or "?" in kanonisch or "#" in kanonisch:
        raise NachweisFehler("securitytxt-eingabe", f"kanonisch muss auf {KANONISCHER_PFAD} "
                                                    f"enden: {kanonisch!r}")
    try:
        text = vorlage.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise NachweisFehler("securitytxt-eingabe", f"Vorlage {vorlage} nicht lesbar: {e}") \
            from None
    ablauf = (datetime.combine(heute, time.min, UTC) + timedelta(days=tage)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    werte = {"@KONTAKT@": kontakt, "@ABLAUF@": ablauf, "@SCHLUESSEL_URL@": schluessel_url,
             "@KANONISCH@": kanonisch, "@RICHTLINIE_URL@": richtlinie_url}
    # Ein Durchgang: eingesetzte Werte werden nie erneut als Platzhalter gelesen.
    text = _PLATZHALTER.sub(lambda m: werte.get(m.group(0), m.group(0)), text)
    rest = _PLATZHALTER.findall(text)
    if rest:
        raise NachweisFehler("securitytxt-platzhalter", "übrig gebliebener Platzhalter in der "
                                                        f"Vorlage: {', '.join(sorted(set(rest)))}")
    return text


# --- Prüfen --------------------------------------------------------------------------------------

def _gpg(heim: Path, *args: str,
         eingabe: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    umgebung = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL")}
    umgebung["GNUPGHOME"] = str(heim)
    try:
        return subprocess.run([GPG, "--homedir", str(heim), "--batch", "--no-tty",
                               "--no-auto-key-retrieve", "--no-auto-check-trustdb", *args],
                              capture_output=True, check=False, input=eingabe,
                              stdin=None if eingabe is not None else subprocess.DEVNULL,
                              env=umgebung, timeout=_GPG_SEKUNDEN)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NachweisFehler("securitytxt-signatur", f"gpg nicht ausführbar: {e}") from None


def _signierter_text(text: str) -> str:
    """Der Text innerhalb des Klartext-Signaturblocks (ohne Dash-Escape). Es darf genau ein
    Block vorliegen und nichts außer Leerraum davor oder danach."""
    if not text.startswith(_BEGIN_TEXT + "\n") or text.count(_BEGIN_TEXT) != 1 \
            or text.count(_BEGIN_SIGNATUR) != 1 or text.count(_ENDE_SIGNATUR) != 1 \
            or not text.rstrip().endswith(_ENDE_SIGNATUR):
        raise NachweisFehler("securitytxt-signatur", "keine OpenPGP-Klartextsignatur (genau ein "
                                                     "signierter Block ohne Text davor/danach "
                                                     "erwartet) — die Datei ist unsigniert oder "
                                                     "verändert")
    zeilen = text.split("\n")
    i = 1
    while i < len(zeilen) and zeilen[i].strip() != "":
        i += 1  # Kopfzeilen (Hash: …)
    ende = zeilen.index(_BEGIN_SIGNATUR)
    return "\n".join(z[2:] if z.startswith("- ") else z for z in zeilen[i + 1:ende])


def _verifiziere(text: str, schluessel: Path) -> str:
    """Signatur mit gpg gegen genau `schluessel`; Rückgabe: Fingerabdruck des Signierschlüssels."""
    try:
        schluessel_bytes = schluessel.read_bytes()
    except OSError as e:
        raise NachweisFehler("securitytxt-signatur", f"Schlüssel {schluessel} nicht lesbar: "
                                                     f"{e.strerror}") from None
    heim = Path(tempfile.mkdtemp(prefix="sv-sectxt-"))  # kurz: der Agent-Socket liegt darin
    try:
        heim.chmod(0o700)
        imp = _gpg(heim, "--import", eingabe=schluessel_bytes)
        if imp.returncode != 0:
            raise NachweisFehler("securitytxt-signatur",
                                 f"{schluessel.name} ist kein lesbarer OpenPGP-Schlüssel")
        ergebnis = _gpg(heim, "--status-fd", "1", "--verify", "-",
                        eingabe=text.encode("utf-8"))
        status = ergebnis.stdout.decode("utf-8", errors="replace").splitlines()
        gueltig = [z.split() for z in status if z.startswith("[GNUPG:] VALIDSIG ")]
        schlecht = sorted({z.split()[1] for z in status if z.startswith("[GNUPG:] ")}
                          & _SCHLECHT)
        if ergebnis.returncode != 0 or len(gueltig) != 1 or schlecht:
            grund = ", ".join(schlecht) or f"gpg Exit {ergebnis.returncode}"
            raise NachweisFehler("securitytxt-signatur", "Signatur ungültig oder nicht vom "
                                                         f"übergebenen Schlüssel ({grund})")
        return gueltig[0][11] if len(gueltig[0]) > 11 else gueltig[0][2]
    finally:
        try:
            subprocess.run([GPGCONF, "--homedir", str(heim), "--kill", "all"], check=False,
                           capture_output=True, timeout=_GPG_SEKUNDEN,
                           stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass  # Aufräumen darf das Prüfergebnis nicht überdecken; das Verzeichnis geht unten
        shutil.rmtree(heim, ignore_errors=True)


def _felder(inhalt: str) -> dict[str, list[str]]:
    felder: dict[str, list[str]] = {}
    for zeile in inhalt.splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or ":" not in zeile:
            continue
        name, wert = zeile.split(":", 1)
        felder.setdefault(name.strip(), []).append(wert.strip())
    return felder


def _schema(name: str, werte: list[str], erlaubt: tuple[str, ...]) -> None:
    for wert in werte:
        if not any(wert.startswith(p) and len(wert) > len(p) for p in erlaubt) \
                or _STEUERZEICHEN.search(wert):
            raise NachweisFehler("securitytxt-feld", f"{name} muss mit {' oder '.join(erlaubt)} "
                                                     f"beginnen und darf keine Leer- oder "
                                                     f"Steuerzeichen enthalten: {wert!r}")


def pruefe(text: str, *, url: str, schluessel: Path, heute: date,
           jetzt: datetime | None = None) -> dict[str, Any]:
    """Signierte security.txt prüfen; Ergebnis als JSON-fähiges dict, sonst `NachweisFehler`.
    Bezugszeitpunkt für `Expires` ist `jetzt`, sonst der Tagesbeginn (00:00 UTC) von `heute`."""
    text = text.replace("\r\n", "\n")
    inhalt = _signierter_text(text)
    fingerabdruck = _verifiziere(text, schluessel)
    rest = _PLATZHALTER.findall(inhalt)
    if rest:
        raise NachweisFehler("securitytxt-platzhalter", "Platzhalter in der signierten Datei: "
                                                        f"{', '.join(sorted(set(rest)))}")
    felder = _felder(inhalt)
    fehlend = [f for f in PFLICHTFELDER if not felder.get(f)]
    if fehlend:
        raise NachweisFehler("securitytxt-feld", f"Pflichtfeld fehlt: {', '.join(fehlend)}")
    if len(felder["Expires"]) != 1:
        raise NachweisFehler("securitytxt-feld", "Expires darf nur einmal vorkommen")
    if felder["Preferred-Languages"] != [SPRACHEN]:
        raise NachweisFehler("securitytxt-feld", f"Preferred-Languages muss „{SPRACHEN}“ sein: "
                                                 f"{felder['Preferred-Languages']}")
    if url not in felder["Canonical"]:
        raise NachweisFehler("securitytxt-feld", f"Canonical nennt die abgerufene Adresse {url} "
                                                 f"nicht: {felder['Canonical']}")
    _schema("Contact", felder["Contact"], ("mailto:", "https://"))
    _schema("Encryption", felder["Encryption"], ("https://",))
    _schema("Policy", felder["Policy"], ("https://",))
    expires = felder["Expires"][0]
    try:
        ablauf = datetime.fromisoformat(expires)
    except ValueError:
        raise NachweisFehler("securitytxt-ablauf", f"Expires ist kein ISO-8601-Zeitpunkt: "
                                                   f"{expires!r}") from None
    if ablauf.tzinfo is None:
        raise NachweisFehler("securitytxt-ablauf", f"Expires ohne Zeitzone: {expires!r}")
    stichtag = jetzt if jetzt is not None else datetime.combine(heute, time.min, UTC)
    if ablauf <= stichtag:
        raise NachweisFehler("securitytxt-ablauf", f"Expires {expires} ist erreicht oder "
                                                   f"überschritten (Bezug {stichtag.isoformat()})")
    if ablauf - stichtag >= timedelta(days=365):
        raise NachweisFehler("securitytxt-ablauf", f"Expires {expires} liegt mindestens ein Jahr "
                                                   "in der Zukunft (RFC 9116: unter einem Jahr)")
    return {"signatur": "gueltig", "url": url, "fingerabdruck": fingerabdruck,
            "expires": expires, "tage_bis_ablauf": (ablauf - stichtag).days,
            "felder": {f: felder[f] for f in PFLICHTFELDER}, "geprueft_am": heute.isoformat()}


# --- Abruf ---------------------------------------------------------------------------------------

class _KeineUmleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:  # type: ignore[override]
        return None


def abrufen(url: str) -> str:
    """Datei per HTTPS abrufen: Zertifikatsprüfung, keine Umleitung (die kanonische Adresse muss
    selbst antworten), Status 200, `text/plain`, höchstens 64 KiB."""
    if not url.startswith("https://") or _STEUERZEICHEN.search(url):
        raise NachweisFehler("securitytxt-eingabe", f"nur https://-Adressen: {url!r}")
    oeffner = urllib.request.build_opener(_KeineUmleitung)
    try:
        anfrage = urllib.request.Request(url, headers={"User-Agent": "scanview-nachweise"})
        with oeffner.open(anfrage, timeout=_ABRUF_SEKUNDEN) as antwort:
            status = antwort.status
            roh_typ = antwort.headers.get("Content-Type")
            typ = antwort.headers.get_content_type() if roh_typ else None
            daten = antwort.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise NachweisFehler("securitytxt-abruf", f"{url}: HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise NachweisFehler("securitytxt-abruf", f"{url} nicht abrufbar: {e}") from None
    if status != 200:
        raise NachweisFehler("securitytxt-abruf", f"{url}: erwartet HTTP 200 (Status {status})")
    if typ != "text/plain" or len(daten) > _MAX_BYTES:
        raise NachweisFehler("securitytxt-abruf", f"{url}: erwartet text/plain bis 64 KiB "
                                                  f"(Typ {typ or 'fehlt'}, {len(daten)} Byte)")
    try:
        return daten.decode("utf-8")
    except UnicodeDecodeError:
        raise NachweisFehler("securitytxt-abruf", f"{url}: kein UTF-8") from None
