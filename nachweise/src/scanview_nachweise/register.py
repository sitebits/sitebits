"""Compliance-Register als TOML laden und validieren (fail-closed).

Kanonisch sind die Dateien unter `docs/compliance/`: `kontrollmatrix.toml` (Soll je Anforderung),
`anwendbarkeit.toml` (datierte Anwendbarkeitsentscheidung je Betriebsmodell),
`befunde.toml` (Befundregister) und `negativtests.toml` (feste Suite der
Sicherheits-Negativtests je Kategorie).
Jede Regelverletzung — auch ein unbekanntes Feld (Tippfehler) — bricht mit `NachweisFehler` ab,
statt still einen Standardwert anzunehmen."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast

from .fehler import NachweisFehler
from .werkzeuge import gefilterte_umgebung, git_befehl

Art = Literal["pflicht", "freiwillig", "kunde"]
_ARTEN: tuple[str, ...] = ("pflicht", "freiwillig", "kunde")

_ID = re.compile(r"K-[A-Z0-9-]+")
# Ein Nachweisschlüssel benennt, wo das Ergebnis einer Kontrolle herkommt: eine Prüfung der
# Release-Kette (`scan`, `sbom`, …) oder ein Dokument (`doku:…`, `vault:…`, `betrieb:…`).
# Mehrere Nachweise werden mit `+` verbunden (z. B. `lizenzen+scan`); alle müssen vorliegen.
_SCHLUESSEL_TEIL = r"[a-z0-9-]+(?::[a-z0-9-]+)?"
_SCHLUESSEL = re.compile(rf"{_SCHLUESSEL_TEIL}(?:\+{_SCHLUESSEL_TEIL})*")
_DATUM = re.compile(r"\d{4}-\d{2}-\d{2}")
_MIN_BEGRUENDUNG = 20


@dataclass(frozen=True)
class Kontrolle:
    """Eine Zeile der Kontrollmatrix."""

    id: str
    quelle: str
    titel: str
    art: Art
    anwendbar: bool
    begruendung: str
    pruefhandlung: str
    sollwert: str
    release_voraussetzung: bool
    iso: tuple[str, ...]
    oeffentlich: bool
    nachweis_schluessel: str | None
    #: False ⇒ die öffentliche Fassung lässt die Zeile ganz weg (nur bei `oeffentlich = false`).
    oeffentlich_zeile: bool = True

    @property
    def schluessel_teile(self) -> tuple[str, ...]:
        """Die einzelnen Nachweisschlüssel (`lizenzen+scan` → `("lizenzen", "scan")`)."""
        if self.nachweis_schluessel is None:
            return ()
        return tuple(self.nachweis_schluessel.split("+"))


@dataclass(frozen=True)
class Modell:
    """Ein Betriebs-/Vertriebsmodell der Anwendbarkeitsentscheidung."""

    name: str
    produktgrenze: str
    rechtsperson: str
    seit: str
    einordnung: str
    offene_fragen: tuple[str, ...]


@dataclass(frozen=True)
class Anwendbarkeit:
    """Die ganze Anwendbarkeitsentscheidung: Datum, Gesamtstatus, Hinweise und Modelle."""

    entschieden_am: str
    status: str
    hinweise: tuple[str, ...]
    modelle: tuple[Modell, ...]


# --- Hilfen ---------------------------------------------------------------------------------------


def _lies_toml(pfad: Path, regel: str) -> dict[str, Any]:
    try:
        roh = pfad.read_bytes()
    except FileNotFoundError:
        raise NachweisFehler(regel, f"{pfad}: Datei fehlt") from None
    except OSError as e:
        raise NachweisFehler(regel, f"{pfad}: nicht lesbar ({e.strerror})") from None
    try:
        return tomllib.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise NachweisFehler(regel, f"{pfad}: kein gültiges TOML ({e})") from None


class _Pruefer:
    """Liest Felder einer TOML-Tabelle typgeprüft; merkt sich, welche Felder gelesen wurden."""

    def __init__(self, regel: str, ort: str, daten: dict[str, Any]) -> None:
        self.regel, self.ort, self.daten = regel, ort, daten
        self.gelesen: set[str] = set()

    def fehler(self, text: str) -> NachweisFehler:
        return NachweisFehler(self.regel, f"{self.ort}: {text}")

    def _roh(self, feld: str, pflicht: bool) -> Any:
        self.gelesen.add(feld)
        if feld not in self.daten:
            if pflicht:
                raise self.fehler(f"Feld „{feld}“ fehlt")
            return None
        return self.daten[feld]

    def text(self, feld: str, *, leer_erlaubt: bool = False) -> str:
        wert = self._roh(feld, True)
        if not isinstance(wert, str):
            raise self.fehler(f"Feld „{feld}“ muss Text sein")
        if not leer_erlaubt and not wert.strip():
            raise self.fehler(f"Feld „{feld}“ ist leer")
        return wert

    def text_optional(self, feld: str) -> str | None:
        wert = self._roh(feld, False)
        if wert is not None and not isinstance(wert, str):
            raise self.fehler(f"Feld „{feld}“ muss Text sein")
        return cast("str | None", wert)

    def wahrheit(self, feld: str) -> bool:
        wert = self._roh(feld, True)
        if not isinstance(wert, bool):
            raise self.fehler(f"Feld „{feld}“ muss true/false sein")
        return wert

    def wahrheit_optional(self, feld: str, standard: bool) -> bool:
        wert = self._roh(feld, False)
        if wert is None:
            return standard
        if not isinstance(wert, bool):
            raise self.fehler(f"Feld „{feld}“ muss true/false sein")
        return wert

    def textliste(self, feld: str, *, leer_erlaubt: bool = False) -> tuple[str, ...]:
        wert = self._roh(feld, True)
        if not isinstance(wert, list) or not all(isinstance(w, str) and w.strip() for w in wert):
            raise self.fehler(f"Feld „{feld}“ muss eine Liste nicht leerer Texte sein")
        if not wert and not leer_erlaubt:
            raise self.fehler(f"Feld „{feld}“ ist leer")
        return tuple(wert)

    def auswahl(self, feld: str, erlaubt: tuple[str, ...]) -> str:
        wert = self.text(feld)
        if wert not in erlaubt:
            raise self.fehler(
                f"Feld „{feld}“ muss einer von {', '.join(erlaubt)} sein, nicht „{wert}“")
        return wert

    def datum(self, feld: str, *, pflicht: bool = True) -> date | None:
        wert = self._roh(feld, pflicht)
        if wert is None:
            return None
        # TOML-Datum ohne Uhrzeit (`entdeckt = 2026-09-27`); datetime ist eine Unterklasse von
        # date und wird ausdrücklich abgelehnt, ebenso Text („2026-09-27“ in Anführungszeichen).
        if not isinstance(wert, date) or isinstance(wert, datetime):
            raise self.fehler(f"Feld „{feld}“ muss ein TOML-Datum JJJJ-MM-TT sein "
                              "(ohne Anführungszeichen)")
        return wert

    def keine_unbekannten(self) -> None:
        unbekannt = sorted(set(self.daten) - self.gelesen)
        if unbekannt:
            raise self.fehler("unbekannte Felder " + ", ".join(f"„{f}“" for f in unbekannt))


def _tabellen(daten: dict[str, Any], name: str, regel: str, pfad: Path) -> list[dict[str, Any]]:
    tabellen = daten.get(name, [])
    if not isinstance(tabellen, list) or not all(isinstance(t, dict) for t in tabellen):
        raise NachweisFehler(
            regel, f"{pfad}: „{name}“ muss ein Array von Tabellen sein ([[{name}]])")
    if not tabellen:
        raise NachweisFehler(regel, f"{pfad}: keine Einträge [[{name}]]")
    return tabellen


# --- Kontrollmatrix -------------------------------------------------------------------------------


def lade_matrix(pfad: Path) -> list[Kontrolle]:
    """Lädt `kontrollmatrix.toml` in Dateireihenfolge. Regeln: eindeutige IDs `K-[A-Z0-9-]+`,
    Pflichtfelder vorhanden und nicht leer, `anwendbar = false` ⇒ Begründung ≥ 20 Zeichen,
    `release_voraussetzung = true` ⇒ `nachweis_schluessel` gesetzt; keine unbekannten Felder."""
    regel = "matrix"
    daten = _lies_toml(pfad, regel)
    unbekannt = sorted(set(daten) - {"kontrolle"})
    if unbekannt:
        raise NachweisFehler(regel, f"{pfad}: unbekannte Schlüssel {', '.join(unbekannt)}")

    ergebnis: list[Kontrolle] = []
    gesehen: set[str] = set()
    for nr, tabelle in enumerate(_tabellen(daten, "kontrolle", regel, pfad), start=1):
        roh_id = tabelle.get("id")
        ort = f"{pfad.name} Kontrolle {roh_id if isinstance(roh_id, str) else f'Nr. {nr}'}"
        p = _Pruefer(regel, ort, tabelle)

        id_ = p.text("id")
        if not _ID.fullmatch(id_):
            raise p.fehler(f"ID „{id_}“ entspricht nicht K-[A-Z0-9-]+")
        if id_ in gesehen:
            raise p.fehler(f"ID „{id_}“ doppelt")
        gesehen.add(id_)

        art = p.text("art")
        if art not in _ARTEN:
            raise p.fehler(f"Feld „art“ muss einer von {', '.join(_ARTEN)} sein, nicht „{art}“")

        k = Kontrolle(
            id=id_,
            quelle=p.text("quelle"),
            titel=p.text("titel"),
            art=cast(Art, art),
            anwendbar=p.wahrheit("anwendbar"),
            begruendung=p.text("begruendung", leer_erlaubt=True),
            pruefhandlung=p.text("pruefhandlung"),
            sollwert=p.text("sollwert"),
            release_voraussetzung=p.wahrheit("release_voraussetzung"),
            # `iso = []` ist zulässig: nicht jede Anforderung hat eine Annex-A-Entsprechung.
            iso=p.textliste("iso", leer_erlaubt=True),
            oeffentlich=p.wahrheit("oeffentlich"),
            nachweis_schluessel=p.text_optional("nachweis_schluessel"),
            oeffentlich_zeile=p.wahrheit_optional("oeffentlich_zeile", True),
        )
        p.keine_unbekannten()

        if k.oeffentlich and not k.oeffentlich_zeile:
            raise p.fehler("„oeffentlich_zeile = false“ nur zusammen mit „oeffentlich = false“")

        if not k.anwendbar and len(k.begruendung.strip()) < _MIN_BEGRUENDUNG:
            raise p.fehler(
                f"nicht anwendbar, aber Begründung fehlt oder ist kürzer als "
                f"{_MIN_BEGRUENDUNG} Zeichen")
        if k.nachweis_schluessel is not None and not _SCHLUESSEL.fullmatch(k.nachweis_schluessel):
            raise p.fehler(
                f"„nachweis_schluessel“ „{k.nachweis_schluessel}“ ungültig "
                "(erwartet z. B. scan, doku:support, lizenzen+scan)")
        if k.release_voraussetzung and k.nachweis_schluessel is None:
            raise p.fehler("Release-Voraussetzung ohne „nachweis_schluessel“")
        ergebnis.append(k)
    return ergebnis


# --- Anwendbarkeit --------------------------------------------------------------------------------


def lade_anwendbarkeit_register(pfad: Path) -> Anwendbarkeit:
    """Lädt `anwendbarkeit.toml`: `entschieden_am` (JJJJ-MM-TT), `status`, optional `hinweise`,
    mindestens ein `[[modell]]` mit eindeutigem Namen und nicht leeren Pflichtfeldern."""
    regel = "anwendbarkeit"
    daten = _lies_toml(pfad, regel)
    kopf = _Pruefer(regel, pfad.name, {k: v for k, v in daten.items() if k != "modell"})
    entschieden_am = kopf.text("entschieden_am")
    if not _DATUM.fullmatch(entschieden_am):
        raise kopf.fehler(f"„entschieden_am“ „{entschieden_am}“ ist kein Datum JJJJ-MM-TT")
    status = kopf.text("status")
    hinweise = kopf.textliste("hinweise") if "hinweise" in daten else ()
    kopf.keine_unbekannten()

    modelle: list[Modell] = []
    for nr, tabelle in enumerate(_tabellen(daten, "modell", regel, pfad), start=1):
        name = tabelle.get("name")
        p = _Pruefer(regel, f"{pfad.name} Modell {name if isinstance(name, str) else nr}", tabelle)
        m = Modell(
            name=p.text("name"),
            produktgrenze=p.text("produktgrenze"),
            rechtsperson=p.text("rechtsperson"),
            seit=p.text("seit"),
            einordnung=p.text("einordnung"),
            offene_fragen=p.textliste("offene_fragen"),
        )
        p.keine_unbekannten()
        if any(m.name == vorher.name for vorher in modelle):
            raise p.fehler(f"Modell „{m.name}“ doppelt")
        modelle.append(m)
    return Anwendbarkeit(entschieden_am=entschieden_am, status=status, hinweise=hinweise,
                         modelle=tuple(modelle))


def lade_anwendbarkeit(pfad: Path) -> list[Modell]:
    """Die Betriebsmodelle aus `anwendbarkeit.toml` (Validierung wie
    `lade_anwendbarkeit_register`)."""
    return list(lade_anwendbarkeit_register(pfad).modelle)


# --- Befundregister -------------------------------------------------------------------------------

Schwere = Literal["kritisch", "hoch", "mittel", "niedrig", "info"]
Kategorie = Literal["auth", "rechte", "trennung", "abfluss", "verfuegbarkeit", "lieferkette",
                    "haertung", "sonstiges"]
Status = Literal["offen", "behoben", "akzeptiert", "kein-befund"]
SCHWEREN: tuple[str, ...] = ("kritisch", "hoch", "mittel", "niedrig", "info")
KATEGORIEN: tuple[str, ...] = ("auth", "rechte", "trennung", "abfluss", "verfuegbarkeit",
                               "lieferkette", "haertung", "sonstiges")
STATUS: tuple[str, ...] = ("offen", "behoben", "akzeptiert", "kein-befund")

# Die Kennung aus dem Quellbericht bleibt erhalten, vorn ein Kürzel des Berichts, damit gleich
# benannte Befunde verschiedener Berichte (z. B. „M1“) nicht zusammenfallen: B-WB-M1, B-TE-M1.
_BEFUND_ID = re.compile(r"B-[A-Z0-9]+(?:-[A-Z0-9]+)*")
_COMMIT = re.compile(r"[0-9a-f]{7,40}(?:\+[0-9a-f]{7,40})*")
# Verweis auf einen Test: `<pfad>::<name>` (pytest-Node-ID ohne Parameter, oder eine
# describe/it-Bezeichnung einer Vitest-Datei) oder ein Punkt der Instanzprüfung.
_PFAD = r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*"
_TESTVERWEIS = re.compile(rf"(?P<pfad>{_PFAD})::(?P<name>\S.*)")
_PYTEST_VERWEIS = re.compile(rf"(?P<pfad>{_PFAD}/tests/[A-Za-z0-9_/]*test_[A-Za-z0-9_]+\.py)"
                             r"::(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
CADDY_PRUEFUNG = "deploy/platform/test/caddy-pruefen.sh"
_CADDY_VERWEIS = re.compile(rf"{re.escape(CADDY_PRUEFUNG)} (?P<punkt>[0-9]+[a-z]?)")
FREIGABE_DURCH = "Andre"
#: Release-Artefakte, auf die sich ein Befund beschränken kann (Feld `artefakte`).
ARTEFAKTE = ("web", "server", "mcp", "import")
#: Befunde aus der SAST-Stufe: `B-SAST-<WERKZEUG>-<Fingerabdruck in Großbuchstaben>`; das Feld
#: `fingerabdruck` (16 hex, klein) verbindet den Eintrag mit dem Werkzeugfund.
_SAST_ID = re.compile(r"B-SAST-(?:SEMGREP|BANDIT)-(?P<fp>[0-9A-F]{16})")
_FINGERABDRUCK = re.compile(r"[0-9a-f]{16}")


@dataclass(frozen=True)
class Befund:
    """Ein Eintrag des Befundregisters."""

    id: str
    quelle: str
    titel: str
    schwere: Schwere
    kategorie: Kategorie
    status: Status
    staende: tuple[str, ...]
    fix_commit: str | None
    retest: str | None
    akzeptanz_begruendung: str | None
    frist: date | None
    entdeckt: date
    #: Betroffene Release-Artefakte; leer = das ganze Produkt.
    artefakte: tuple[str, ...] = ()
    #: Pflicht, sobald `artefakte` gesetzt ist: warum der Befund nur diese Artefakte betrifft.
    artefakte_begruendung: str | None = None
    #: Nur bei SAST-Befunden (`B-SAST-…`), dort Pflicht: Fingerabdruck des Werkzeugfunds.
    fingerabdruck: str | None = None


def _gueltiger_verweis(verweis: str) -> bool:
    if _CADDY_VERWEIS.fullmatch(verweis):
        return True
    m = _TESTVERWEIS.fullmatch(verweis)
    return m is not None and ".." not in m["pfad"].split("/") and \
        not m["pfad"].startswith(CADDY_PRUEFUNG)


def _artefakte(p: _Pruefer) -> tuple[str, ...]:
    """Optionales Feld `artefakte`: nicht leere Liste ohne Doppelte aus `ARTEFAKTE`."""
    if "artefakte" not in p.daten:
        p.gelesen.add("artefakte")
        return ()
    werte = p.textliste("artefakte")
    unbekannt = [w for w in werte if w not in ARTEFAKTE]
    if unbekannt or len(set(werte)) != len(werte):
        raise p.fehler(f"Feld „artefakte“ nur aus {', '.join(ARTEFAKTE)}, ohne Doppelte "
                       f"(gefunden: {', '.join(werte)})")
    return werte


def _pruefe_fingerabdruck(p: _Pruefer, b: Befund) -> None:
    """SAST-Befund ⇔ `fingerabdruck`; die ID trägt denselben Fingerabdruck in Großbuchstaben."""
    fp = b.fingerabdruck
    if fp is not None and not _FINGERABDRUCK.fullmatch(fp):
        raise p.fehler(f"„fingerabdruck“ „{fp}“ muss aus 16 hex-Zeichen (klein) bestehen")
    if b.id.startswith("B-SAST-"):
        m = _SAST_ID.fullmatch(b.id)
        if m is None:
            raise p.fehler(f"SAST-ID „{b.id}“ entspricht nicht B-SAST-<SEMGREP|BANDIT>-<16 hex>")
        if fp is None or m["fp"] != fp.upper():
            raise p.fehler(f"SAST-Befund braucht „fingerabdruck“ = {m['fp'].lower()} (wie in "
                           "der ID)")
    elif fp is not None:
        raise p.fehler("„fingerabdruck“ nur bei SAST-Befunden (B-SAST-…)")


def lade_befunde(pfad: Path) -> list[Befund]:
    """Lädt `befunde.toml` in Dateireihenfolge. Regeln: eindeutige IDs `B-…`, Schwere/Kategorie/
    Status aus den festen Listen, `entdeckt` als TOML-Datum, `staende` nicht leer;
    `behoben` ⇒ `fix_commit` und `retest`; `akzeptiert` ⇒ Begründung (≥ 20 Zeichen), `frist` und
    `freigabe = "Andre"` — und diese drei Felder nur bei `akzeptiert`; keine unbekannten Felder."""
    regel = "befunde"
    daten = _lies_toml(pfad, regel)
    unbekannt = sorted(set(daten) - {"befund"})
    if unbekannt:
        raise NachweisFehler(regel, f"{pfad}: unbekannte Schlüssel {', '.join(unbekannt)}")

    ergebnis: list[Befund] = []
    gesehen: set[str] = set()
    for nr, tabelle in enumerate(_tabellen(daten, "befund", regel, pfad), start=1):
        roh_id = tabelle.get("id")
        ort = f"{pfad.name} Befund {roh_id if isinstance(roh_id, str) else f'Nr. {nr}'}"
        p = _Pruefer(regel, ort, tabelle)

        id_ = p.text("id")
        if not _BEFUND_ID.fullmatch(id_):
            raise p.fehler(f"ID „{id_}“ entspricht nicht B-[A-Z0-9-]+")
        if id_ in gesehen:
            raise p.fehler(f"ID „{id_}“ doppelt")
        gesehen.add(id_)

        entdeckt = p.datum("entdeckt")
        assert entdeckt is not None  # Pflichtfeld, sonst hätte p.datum geworfen
        b = Befund(
            id=id_,
            quelle=p.text("quelle"),
            titel=p.text("titel"),
            schwere=cast(Schwere, p.auswahl("schwere", SCHWEREN)),
            kategorie=cast(Kategorie, p.auswahl("kategorie", KATEGORIEN)),
            status=cast(Status, p.auswahl("status", STATUS)),
            staende=p.textliste("staende"),
            fix_commit=p.text_optional("fix_commit"),
            retest=p.text_optional("retest"),
            akzeptanz_begruendung=p.text_optional("akzeptanz_begruendung"),
            frist=p.datum("frist", pflicht=False),
            entdeckt=entdeckt,
            artefakte=_artefakte(p),
            artefakte_begruendung=p.text_optional("artefakte_begruendung"),
            fingerabdruck=p.text_optional("fingerabdruck"),
        )
        _pruefe_fingerabdruck(p, b)
        if b.artefakte and len((b.artefakte_begruendung or "").strip()) < _MIN_BEGRUENDUNG:
            raise p.fehler(f"„artefakte“ nur mit „artefakte_begruendung“ (≥ {_MIN_BEGRUENDUNG} "
                           "Zeichen): warum betrifft der Befund nur diese Artefakte?")
        if not b.artefakte and b.artefakte_begruendung is not None:
            raise p.fehler("„artefakte_begruendung“ nur zusammen mit „artefakte“")
        freigabe = p.text_optional("freigabe")
        p.keine_unbekannten()

        if b.fix_commit is not None and not _COMMIT.fullmatch(b.fix_commit):
            raise p.fehler(f"„fix_commit“ „{b.fix_commit}“ ist kein Commit-Hash (7 bis 40 Zeichen "
                           "hex, mehrere mit +)")
        if b.retest is not None and not _gueltiger_verweis(b.retest):
            raise p.fehler(f"„retest“ „{b.retest}“ ungültig (erwartet <pfad>::<test> oder "
                           f"„{CADDY_PRUEFUNG} <Punkt>“)")
        if b.status == "behoben":
            for feld, wert in (("fix_commit", b.fix_commit), ("retest", b.retest)):
                if wert is None:
                    raise p.fehler(f"Status behoben ohne „{feld}“")
        akzeptanz = {"akzeptanz_begruendung": b.akzeptanz_begruendung, "frist": b.frist,
                     "freigabe": freigabe}
        if b.status == "akzeptiert":
            begruendung = (b.akzeptanz_begruendung or "").strip()
            if len(begruendung) < _MIN_BEGRUENDUNG:
                raise p.fehler(f"akzeptiert, aber „akzeptanz_begruendung“ fehlt oder ist kürzer "
                               f"als {_MIN_BEGRUENDUNG} Zeichen")
            if b.frist is None:
                raise p.fehler("akzeptiert ohne „frist“")
            if freigabe != FREIGABE_DURCH:
                raise p.fehler(f"akzeptiert nur mit „freigabe = \"{FREIGABE_DURCH}\"“ "
                               "(Risikoakzeptanz entscheidet Andre)")
        else:
            for feld, gesetzt in akzeptanz.items():
                if gesetzt is not None:
                    raise p.fehler(f"„{feld}“ nur bei Status akzeptiert")
        ergebnis.append(b)
    return ergebnis


# --- Negativtest-Suite ----------------------------------------------------------------------------

_NT_ID = re.compile(r"NT-[A-Z0-9]+(?:-[A-Z0-9]+)*")


@dataclass(frozen=True)
class NegativKategorie:
    """Eine Kategorie der Sicherheits-Negativtests. `instanz = true`: die Tests laufen
    nur gegen eine laufende Instanz (Punkte von `caddy-pruefen.sh`) und sind Deploy-Smoke, nicht
    Teil des Release-Gates."""

    id: str
    titel: str
    bezug: tuple[str, ...]
    instanz: bool
    tests: tuple[str, ...]


def lade_negativtests(pfad: Path) -> list[NegativKategorie]:
    """Lädt `negativtests.toml`. Regeln: eindeutige IDs `NT-…`, mindestens ein Bezug (Befund- oder
    Kontroll-ID) und mindestens ein Test je Kategorie; ohne `instanz` nur pytest-Node-IDs
    (`<teilprojekt>/tests/…py::test_…`), mit `instanz = true` nur Punkte von `caddy-pruefen.sh`."""
    regel = "negativtests"
    daten = _lies_toml(pfad, regel)
    unbekannt = sorted(set(daten) - {"kategorie"})
    if unbekannt:
        raise NachweisFehler(regel, f"{pfad}: unbekannte Schlüssel {', '.join(unbekannt)}")

    ergebnis: list[NegativKategorie] = []
    for nr, tabelle in enumerate(_tabellen(daten, "kategorie", regel, pfad), start=1):
        roh_id = tabelle.get("id")
        ort = f"{pfad.name} Kategorie {roh_id if isinstance(roh_id, str) else f'Nr. {nr}'}"
        p = _Pruefer(regel, ort, tabelle)
        id_ = p.text("id")
        if not _NT_ID.fullmatch(id_):
            raise p.fehler(f"ID „{id_}“ entspricht nicht NT-[A-Z0-9-]+")
        if any(k.id == id_ for k in ergebnis):
            raise p.fehler(f"ID „{id_}“ doppelt")
        titel = p.text("titel")
        bezug = p.textliste("bezug")
        instanz = p.wahrheit("instanz")
        tests = p.textliste("tests", leer_erlaubt=True)
        p.keine_unbekannten()
        if not tests:
            raise NachweisFehler(regel, f"Kategorie {id_} ohne Test")
        for t in tests:
            if instanz and not _CADDY_VERWEIS.fullmatch(t):
                raise p.fehler(f"Instanzkategorie: „{t}“ ist kein Punkt von "
                               f"„{CADDY_PRUEFUNG} <Punkt>“")
            if not instanz and (not _PYTEST_VERWEIS.fullmatch(t) or ".." in t.split("/")):
                raise p.fehler(f"„{t}“ ist keine pytest-Node-ID "
                               "<teilprojekt>/tests/…test_….py::test_…")
        ergebnis.append(NegativKategorie(id=id_, titel=titel, bezug=bezug, instanz=instanz,
                                         tests=tests))
    return ergebnis


# --- Querprüfungen --------------------------------------------------------------------------------


def _verweis_existiert(wurzel: Path, verweis: str) -> bool:
    if m := _CADDY_VERWEIS.fullmatch(verweis):
        skript = wurzel / CADDY_PRUEFUNG
        return skript.is_file() and re.search(
            rf"^# {re.escape(m['punkt'])} ", skript.read_text(encoding="utf-8"), re.M) is not None
    m = _TESTVERWEIS.fullmatch(verweis)
    if m is None:
        return False
    datei = wurzel / m["pfad"]
    if not datei.is_file():
        return False
    text = datei.read_text(encoding="utf-8")
    if datei.suffix == ".py":
        return re.search(rf"^(?:async )?def {re.escape(m['name'])}\(", text, re.M) is not None
    # Vitest: die Bezeichnung steht wörtlich als String in describe()/it().
    return any(f"{q}{m['name']}{q}" in text for q in ("'", '"', "`"))


def pruefe_verweise(wurzel: Path, verweise: Iterable[str], regel: str) -> None:
    """Jeder Verweis muss auf einen vorhandenen Test bzw. Prüfpunkt zeigen (statisch: Datei
    vorhanden, Testfunktion/Bezeichnung bzw. Kommentar `# <Punkt> ` im Prüfskript) — sonst
    `NachweisFehler`, noch bevor irgendetwas ausgeführt wird."""
    fehlend = [v for v in verweise if not _verweis_existiert(wurzel, v)]
    if fehlend:
        raise NachweisFehler(regel, "unbekannter Test/Prüfpunkt: " + ", ".join(fehlend))


def pruefe_bezuege(
    kategorien: Iterable[NegativKategorie], befunde: Iterable[Befund], kontroll_ids: Iterable[str],
) -> None:
    """Jeder Bezug einer Negativtest-Kategorie muss eine Befund-ID aus dem Befundregister oder eine
    Kontroll-ID aus der Kontrollmatrix sein."""
    bekannt = {b.id for b in befunde} | set(kontroll_ids)
    fehlend = [f"{k.id} → {z}" for k in kategorien for z in k.bezug if z not in bekannt]
    if fehlend:
        raise NachweisFehler("negativtests", "unbekannter Bezug: " + ", ".join(fehlend))


def pruefe_fix_commits(wurzel: Path, befunde: Iterable[Befund]) -> None:
    """Jeder Teil jedes `fix_commit` muss ein Commit im Repo `wurzel` sein
    (`git cat-file -e <sha>^{commit}`) **und** im geprüften Stand enthalten sein
    (`git merge-base --is-ancestor <sha> HEAD`; im Release-Worktree ist HEAD der
    Release-Commit — ein Fix auf einem anderen Zweig zählt nicht). Argumentliste ohne Shell.
    Kein Git, kein Repo, unbekannter oder nicht enthaltener Commit → `NachweisFehler`."""
    commits = sorted({c for b in befunde if b.fix_commit for c in b.fix_commit.split("+")})
    umgebung = gefilterte_umgebung()
    fehlend: list[str] = []
    nicht_enthalten: list[str] = []
    for c in commits:
        try:
            r = subprocess.run(git_befehl("cat-file", "-e", f"{c}^{{commit}}"), cwd=wurzel,
                               capture_output=True, check=False, env=umgebung)
            if r.returncode != 0:
                fehlend.append(c)
                continue
            r = subprocess.run(git_befehl("merge-base", "--is-ancestor", f"{c}^{{commit}}", "HEAD"),
                               cwd=wurzel, capture_output=True, check=False, env=umgebung)
        except OSError as e:
            raise NachweisFehler("befunde", f"git nicht ausführbar ({e.strerror})") from None
        if r.returncode != 0:
            nicht_enthalten.append(c)
    if fehlend:
        raise NachweisFehler("befunde", f"Fix-Commit nicht im Repo {wurzel}: "
                                        + ", ".join(fehlend))
    if nicht_enthalten:
        raise NachweisFehler("befunde", "Fix-Commit nicht im geprüften Stand (HEAD) enthalten "
                                        "— Fix auf einem anderen Zweig?: "
                                        + ", ".join(nicht_enthalten))


# --- Alle Register zusammen ----------------------------------------------------------------------

MATRIX = Path("docs/compliance/kontrollmatrix.toml")
ANWENDBARKEIT = Path("docs/compliance/anwendbarkeit.toml")
BEFUNDE = Path("docs/compliance/befunde.toml")
NEGATIVTESTS = Path("docs/compliance/negativtests.toml")


@dataclass(frozen=True)
class Register:
    kontrollen: list[Kontrolle]
    anwendbarkeit: Anwendbarkeit
    befunde: list[Befund]
    negativtests: list[NegativKategorie]


def lade_register(wurzel: Path) -> Register:
    """Alle vier Register laden und querprüfen: Verweise der Befunde (Retest) und der
    Negativtests zeigen auf vorhandene Tests/Prüfpunkte, jeder Fix-Commit existiert im Repo,
    Bezüge zeigen auf vorhandene Befunde oder Kontrollen."""
    r = Register(
        kontrollen=lade_matrix(wurzel / MATRIX),
        anwendbarkeit=lade_anwendbarkeit_register(wurzel / ANWENDBARKEIT),
        befunde=lade_befunde(wurzel / BEFUNDE),
        negativtests=lade_negativtests(wurzel / NEGATIVTESTS),
    )
    pruefe_verweise(wurzel, [b.retest for b in r.befunde if b.retest], "befunde")
    pruefe_fix_commits(wurzel, r.befunde)
    pruefe_verweise(wurzel, [t for k in r.negativtests for t in k.tests], "negativtests")
    pruefe_bezuege(r.negativtests, r.befunde, (k.id for k in r.kontrollen))
    return r
