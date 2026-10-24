"""SBOM je Release-Artefakt nach BSI TR-03183-2 v2.1.0.

Ablauf je Artefakt (aus `artefakte.baue_artefakte`):

1. `erzeuge_roh` — Generator laut Werkzeug-Probe (`docs/compliance/werkzeuge.md`): Web
   `cyclonedx-npm --package-lock-only`, Python `cyclonedx-py environment` über eine im gepinnten
   uv-Image gebaute venv. Die Container-Images des Imports (PDAL, untwine) beschreibt `syft` in
   einem eigenen, nicht ausgelieferten SBOM (`erzeuge_bilder`, Name `BILDER`): sie laufen nur
   beim Datenaufbereiter und kommen nicht mit dem Import-Archiv auf den Host. Belegte
   Anreicherung: SHA-512 der Python-Wheels (Download gegen `uv.lock`, SHA-256
   geprüft), npm-Hashes gegen `package-lock.json` abgeglichen, Ersteller aus den Paketmetadaten.
2. `nachbearbeiten` — Pflichtfelder nach TR §5.2 und Anhang 8.2 (CycloneDX 1.6): Ersteller,
   Zeitstempel, SBOM-URI (= `serialNumber`, TR Tabelle 10), BSI-Taxonomie, SHA-512 in
   `externalReferences[distribution].hashes`, Verbreitungslizenz (`concluded`),
   Abhängigkeitseinträge für jede Komponente, `compositions` mit `complete` **nur mit Beleg**.
3. `abdeckung` — jede Datei des Lieferumfangs muss einer Komponente zugeordnet sein; die CLI
   bricht ab, wenn eine offene Datei nicht in `nachweise/abdeckung-ausnahmen.toml` begründet ist.
4. `validieren` — cyclonedx-cli (Schema 1.6, `--fail-on-errors`) und sbomqs (`--bsi-v21`, JSON
   ausgewertet). Beides ist ein **Werkzeugbefund**, kein Konformitätsbeweis.
5. `zusammenfuehren` — Gesamt-SBOM je Version.

Grundsätze: Kein Hash wird erfunden — fehlt der Beleg, steht die Komponente in einer
`incomplete`-Komposition. Das SBOM trägt nie Schwachstelleninformation (TR §3.1). Netz gibt es
nur für die Bau-Stufe gegen Lockfiles; jeder solche Schritt kommt als `Netzschritt` zurück.

`erzeuge_alle` lädt außerdem `docs/compliance/npm-signaturen-ausnahmen.toml` und reicht sie als
`npm_ausnahmen` an `baue_artefakte` durch: die npm-Registry-Signaturprüfung ist
Teil des Baus, nicht der SBOM-Erzeugung selbst, ihr Ergebnis steht in
`Bauergebnis.npm_signaturen`."""

from __future__ import annotations

import base64
import contextlib
import copy
import email.parser
import email.utils
import fnmatch
import hashlib
import json
import re
import tempfile
import tomllib
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .artefakte import (
    _PYTHON_KOMPONENTEN,
    Artefakt,
    Bauergebnis,
    Datei,
    Netzschritt,
    baue_artefakte,
    lade_npm_ausnahmen,
)
from .fehler import NachweisFehler
from .werkzeuge import Werkzeug, ausfuehren, docker_befehl, lade_werkzeuge

#: SBOM-Ersteller (TR §5.2.1: E-Mail). Geplantes PSIRT-Postfach, noch nicht endgültig festgelegt.
ERSTELLER = "security@sitebits.de"
#: Hersteller/Autor: Rechteinhaberin laut Copyright-Zeile (Rechteeinräumung 15.10.2026), Kontakt
#: über das PSIRT-Postfach. Bei Änderung Copyright-Zeile, README und anwendbarkeit.toml nachziehen.
HERSTELLER_NAME = "Stiebitz Souverain Systems UG (haftungsbeschränkt)"
#: Lizenz der eigenen Komponenten laut CONTRIBUTING.md („EUPL-1.2“).
EIGENE_LIZENZ = "EUPL-1.2"

_SCHEMA = "http://cyclonedx.org/schema/bom-1.6.schema.json"
_NAMENSRAUM = uuid.UUID("6f0b7c1e-2a4d-5e8f-9a1b-3c5d7e9f0a2b")
_SHA512 = re.compile(r"[0-9a-f]{128}")
_BSI = "bsi:component:"
_BSI_FELDER = tuple(_BSI + f for f in ("filename", "executable", "archive", "structured"))

# --- Klassifikation (TR §5.2.2, Anhang 8.1.4 bis 8.1.7; Werte laut BSI-Property-Taxonomie) --------
# Archive mit Metadaten (Paketformate, tar/zip) sind „structured“ (TR 8.1.6). Ausführbar sind
# Binärdateien, Shared Libraries und interpretierter Code (TR 8.1.4). Konfigurations-/Datenformate
# mit erhaltener Struktur gelten als „structured“ (TR Tabelle 7: Konfigurationsdatei „-/√“).
_ARCHIV = (".tar.gz", ".tgz", ".tar", ".zip", ".whl", ".jar", ".conda", ".tar.bz2")
_AUSFUEHRBAR = (".js", ".mjs", ".cjs", ".wasm", ".so", ".py", ".sh")
_STRUKTURIERT = (".json", ".toml", ".css", ".lock", ".yml", ".yaml")
_UNSTRUKTURIERT = (".html", ".txt", ".md", ".sql")


def klassifiziere(dateiname: str) -> dict[str, str] | None:
    """BSI-Eigenschaften `executable`/`archive`/`structured` aus dem Dateinamen; `None`, wenn die
    Endung nicht sicher einzuordnen ist (dann wird nichts behauptet)."""
    name = dateiname.lower()
    # Das gepinnte MediaPipe-Modell ist ein ZIP mit TFLite-Modellen und Metadaten.
    # Keine allgemeine Aussage über beliebige Dateien mit der Endung .task.
    if name == "face_landmarker.task":
        return {"executable": "non-executable", "archive": "archive", "structured": "structured"}
    if name.endswith(_ARCHIV):
        return {"executable": "non-executable", "archive": "archive", "structured": "structured"}
    if name.endswith(_AUSFUEHRBAR) or re.search(r"\.so(\.\d+)*$", name):
        return {"executable": "executable", "archive": "no archive", "structured": "unstructured"}
    if name.endswith(_STRUKTURIERT):
        return {"executable": "non-executable", "archive": "no archive", "structured": "structured"}
    if name.endswith(_UNSTRUKTURIERT):
        return {"executable": "non-executable", "archive": "no archive",
                "structured": "unstructured"}
    return None


# --- Datentypen --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Beleg:
    """Belegter SHA-512 der auslieferbaren Form: URL der Datei, Hash über ihren tatsächlichen
    Inhalt, Dateiname."""

    url: str
    sha512: str
    dateiname: str


@dataclass(frozen=True)
class Rohergebnis:
    """Ausgabe von `erzeuge_roh`: Generator-SBOM (belegt angereichert), SHA-512-Belege je
    bom-ref, Komponenten mit bekannter Lücke (bom-ref → Grund) und die Netzschritte (Pflicht
    fürs Manifest)."""

    bom: dict[str, Any]
    belege: dict[str, Beleg]
    unvollstaendig: dict[str, str]
    netzschritte: list[Netzschritt]


@dataclass(frozen=True)
class Installiert:
    """Ein in der venv installiertes Paket (aus `*.dist-info`)."""

    name: str
    version: str
    tags: frozenset[str]
    metadaten: dict[str, Any]


@dataclass(frozen=True)
class Ausnahme:
    """Begründete Abdeckungs-Ausnahme: Pfadmuster (fnmatch) + Begründung (≥ 20 Zeichen)."""

    muster: str
    begruendung: str


# --- Hilfen ------------------------------------------------------------------------------------

def _jetzt() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _zeitstempel(jetzt: datetime | None) -> str:
    zeit = (jetzt or datetime.now(UTC)).astimezone(UTC)
    return zeit.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalisiere(name: str) -> str:
    """PEP-503-Normalform eines Python-Paketnamens."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _lade_json(pfad: Path, regel: str) -> dict[str, Any]:
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except OSError as e:
        raise NachweisFehler(regel, f"{pfad} nicht lesbar: {e.strerror}") from None
    except json.JSONDecodeError as e:
        raise NachweisFehler(regel, f"{pfad} ist kein gültiges JSON: {e}") from None
    if not isinstance(daten, dict):
        raise NachweisFehler(regel, f"{pfad}: oberste Ebene muss ein Objekt sein")
    return daten


def alle_komponenten(komponenten: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Alle Komponenten einschließlich verschachtelter (`components[].components`, z. B. npm-Pakete
    in einem eigenen `node_modules` ihres Elternpakets), Tiefensuche in Dokumentreihenfolge."""
    ergebnis: list[dict[str, Any]] = []
    for k in komponenten:
        ergebnis.append(k)
        ergebnis += alle_komponenten(k.get("components", []))
    return ergebnis


def _eigenschaften(k: dict[str, Any], name: str) -> list[str]:
    return [p["value"] for p in k.get("properties", []) if p.get("name") == name]


def _verteilungen(k: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in k.get("externalReferences", []) if r.get("type") == "distribution"]


def _sha512_der_verteilung(k: dict[str, Any]) -> list[str]:
    return [h["content"] for r in _verteilungen(k) for h in r.get("hashes", [])
            if h.get("alg") == "SHA-512"]


def _dateiname_aus_url(url: str) -> str | None:
    teile = urllib.parse.urlsplit(url)
    if teile.scheme not in ("http", "https"):
        return None
    name = urllib.parse.unquote(PurePosixPath(teile.path).name)
    return name or None


def _hersteller(ersteller: str) -> dict[str, Any]:
    return {"name": HERSTELLER_NAME, "contact": [{"email": ersteller}]}


def _haupt(bom: dict[str, Any]) -> dict[str, Any]:
    haupt = bom.get("metadata", {}).get("component")
    if not isinstance(haupt, dict) or not haupt.get("bom-ref"):
        raise NachweisFehler("sbom-format", "metadata.component mit bom-ref fehlt")
    return haupt


# --- Abdeckung ---------------------------------------------------------------------------------

#: Bekannte Beigaben im Web-Bundle: Datei → Komponente, aus der sie unverändert stammt
#: (`web/vite.config.ts`, Plugins für laz-perf, Basis-Transcoder und MediaPipe):
#: laz-perf aus npm; der Basis-Universal-Transcoder ist ein Eigenbau ohne Laufzeit-Codeerzeugung
#: (`web/vendor/basis/`, erklärt in `nachweise/eigenbau.toml`, Lizenzen in
#: `THIRD_PARTY_LICENSES.txt`). MediaPipe-Wasm/JS stammt aus npm, das Modell aus vendor/mediapipe.
_WEB_BEIGABEN = {
    "web/dist/wasm/laz-perf.wasm": "laz-perf",
    "web/dist/wasm/basis/basis_transcoder.js": "basis-universal-transcoder",
    "web/dist/wasm/basis/basis_transcoder.wasm": "basis-universal-transcoder",
    "web/dist/wasm/mediapipe/vision_wasm_internal.js": "@mediapipe/tasks-vision",
    "web/dist/wasm/mediapipe/vision_wasm_internal.wasm": "@mediapipe/tasks-vision",
    "web/dist/wasm/mediapipe/vision_wasm_nosimd_internal.js": "@mediapipe/tasks-vision",
    "web/dist/wasm/mediapipe/vision_wasm_nosimd_internal.wasm": "@mediapipe/tasks-vision",
    "web/dist/wasm/mediapipe/face_landmarker.task": "MediaPipe Face Landmarker",
}
#: Eigene Dateien des Web-Bundles (Hauptkomponente): Einstiegs-HTML, von Vite erzeugte Chunks mit
#: 8-stelligem Inhalts-Hash (Name auch mit Punkten, Vite übernimmt ihn aus der Quelldatei, z. B.
#: `spark.module-…js`), die beim Build erzeugte Lizenzübersicht, Manifest und Symbole der
#: Web-App (beim Build aus `web/scripts/webApp.ts` erzeugt, genau diese fünf Namen). Bewusst eng —
#: eine neue Datei (Bild, Schrift, weitere .wasm) fällt als nicht zugeordnet auf.
_WEB_EIGEN = re.compile(r"web/dist/(?:[A-Za-z0-9_-]+\.html|THIRD_PARTY_LICENSES\.txt"
                        r"|assets/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*-[A-Za-z0-9_-]{8}\.(?:js|css)"
                        r"|manifest\.webmanifest|icons/scanview\.svg"
                        r"|icons/scanview-(?:180|192|512)\.png)")
_PYTHON_WURZELN = {k.name: k.wurzeln for k in _PYTHON_KOMPONENTEN}
#: In Quell-Artefakten nie automatisch der Hauptkomponente zugeordnet: Binär- und Paketdateien
#: wären mitgelieferter Fremdcode und brauchen eine eigene Komponente.
_FREMD_VERDAECHTIG = (".so", ".pyd", ".dll", ".dylib", ".wasm", ".whl", ".zip", ".tar.gz",
                      ".tgz", ".jar", ".exe", ".node", ".a", ".bin")


def _zuordnen(bom: dict[str, Any], artefakt: Artefakt) -> tuple[dict[str, str], list[str]]:
    haupt_ref = _haupt(bom)["bom-ref"]
    namen: dict[str, str] = {}
    for k in alle_komponenten(bom.get("components", [])):
        if k.get("type") == "file":
            continue
        voll = f"{k['group']}/{k['name']}" if k.get("group") else k.get("name", "")
        namen.setdefault(voll, k["bom-ref"])
    zuordnung: dict[str, str] = {}
    offen: list[str] = []
    for datei in artefakt.lieferumfang:
        pfad = datei.pfad_im_artefakt
        ref: str | None = None
        if artefakt.name == "web":
            if pfad in _WEB_BEIGABEN:
                ref = namen.get(_WEB_BEIGABEN[pfad])
            elif _WEB_EIGEN.fullmatch(pfad):
                ref = haupt_ref
        elif artefakt.name in _PYTHON_WURZELN:
            if pfad.startswith(_PYTHON_WURZELN[artefakt.name]) \
                    and not pfad.lower().endswith(_FREMD_VERDAECHTIG):
                ref = haupt_ref
        if ref is None:
            offen.append(pfad)
        else:
            zuordnung[pfad] = ref
    return zuordnung, offen


def abdeckung(bom: dict[str, Any], artefakt: Artefakt) -> list[str]:
    """Dateien des Lieferumfangs, die keiner Komponente des SBOM zugeordnet sind (leer = voll
    abgedeckt). Unbekannte Artefaktnamen sind vollständig offen (fail-closed)."""
    return _zuordnen(bom, artefakt)[1]


def pruefe_abdeckung(bom: dict[str, Any], artefakt: Artefakt, ausnahmen: list[Ausnahme]) -> None:
    """Fail-closed-Regel der Abdeckung: jede Datei im Lieferumfang ist einer Komponente zugeordnet
    oder durch eine begründete Ausnahme gedeckt — sonst `sbom-abdeckung`."""
    offen = ohne_ausnahme(abdeckung(bom, artefakt), ausnahmen)
    if offen:
        raise NachweisFehler("sbom-abdeckung", f"{artefakt.name}: {len(offen)} Datei(en) keiner "
                                               "Komponente zugeordnet und ohne Ausnahme: "
                                               + ", ".join(offen[:20]))


def lade_ausnahmen(pfad: Path) -> list[Ausnahme]:
    """`abdeckung-ausnahmen.toml` laden: `[[ausnahme]]` mit `muster` und `begruendung`
    (≥ 20 Zeichen). Fehlende oder ungültige Datei → Fehler (fail-closed)."""
    regel = "sbom-ausnahmen"
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except OSError as e:
        raise NachweisFehler(regel, f"{pfad} nicht lesbar: {e.strerror}") from None
    except tomllib.TOMLDecodeError as e:
        raise NachweisFehler(regel, f"{pfad}: ungültiges TOML: {e}") from None
    unbekannt = sorted(set(daten) - {"ausnahme"})
    if unbekannt:
        raise NachweisFehler(regel, f"{pfad}: unbekannte Schlüssel {', '.join(unbekannt)}")
    ausnahmen: list[Ausnahme] = []
    for nr, eintrag in enumerate(daten.get("ausnahme", []), start=1):
        if not isinstance(eintrag, dict) or set(eintrag) != {"muster", "begruendung"}:
            raise NachweisFehler(regel, f"{pfad}: Ausnahme {nr} braucht genau „muster“ und "
                                        "„begruendung“")
        muster, begruendung = eintrag["muster"], eintrag["begruendung"]
        if not isinstance(muster, str) or not muster.strip():
            raise NachweisFehler(regel, f"{pfad}: Ausnahme {nr}: leeres Muster")
        if not isinstance(begruendung, str) or len(begruendung.strip()) < 20:
            raise NachweisFehler(regel, f"{pfad}: Ausnahme {nr} ({muster}): Begründung fehlt "
                                        "oder ist kürzer als 20 Zeichen")
        ausnahmen.append(Ausnahme(muster, begruendung.strip()))
    return ausnahmen


def ohne_ausnahme(offen: Iterable[str], ausnahmen: list[Ausnahme]) -> list[str]:
    """Offene Dateien, die von keiner Ausnahme erfasst sind."""
    return [p for p in offen if not any(fnmatch.fnmatchcase(p, a.muster) for a in ausnahmen)]


# --- Nachbearbeitung ---------------------------------------------------------------------------

def _bsi_setzen(k: dict[str, Any], dateiname: str | None) -> bool:
    """Setzt `bsi:component:*` genau einmal; `False`, wenn Dateiname oder Klassifikation fehlt."""
    eigen = [p for p in k.get("properties", []) if p.get("name") not in _BSI_FELDER]
    klasse = klassifiziere(dateiname) if dateiname else None
    if dateiname:
        eigen.append({"name": _BSI + "filename", "value": dateiname})
    if klasse:
        eigen += [{"name": _BSI + feld, "value": wert} for feld, wert in klasse.items()]
    if eigen:
        k["properties"] = eigen
    else:
        k.pop("properties", None)
    return bool(dateiname and klasse)


def _verteilung_ergaenzen(k: dict[str, Any], beleg: Beleg) -> None:
    if _SHA512.fullmatch(beleg.sha512) is None:
        raise NachweisFehler("sbom-hash", f"{k['bom-ref']}: Beleg ohne gültigen SHA-512")
    for vorhanden in _sha512_der_verteilung(k):
        if vorhanden != beleg.sha512:
            raise NachweisFehler("sbom-hash", f"{k['bom-ref']}: SHA-512 des Generators "
                                              "widerspricht dem Beleg")
    hash_eintrag = {"alg": "SHA-512", "content": beleg.sha512}
    for r in _verteilungen(k):
        if r.get("url") == beleg.url:
            r["hashes"] = [h for h in r.get("hashes", []) if h.get("alg") != "SHA-512"] \
                + [hash_eintrag]
            return
    k.setdefault("externalReferences", []).append({
        "type": "distribution", "url": beleg.url, "hashes": [hash_eintrag],
        "comment": "SHA-512 über die heruntergeladene Datei, SHA-256 gegen das Lockfile geprüft",
    })


def _lizenz_schliessen(k: dict[str, Any]) -> None:
    """Verbreitungslizenz (TR §3.2.8: Lizenzen, unter denen ein Lizenznehmer die Komponente
    nutzen kann; Anhang 8.2: `acknowledgement: concluded`). scanview gibt Fremdkomponenten
    unverändert unter ihrer eigenen Lizenz weiter — die Verbreitungslizenz ist deshalb die vom
    Ersteller vergebene Originallizenz. Abgeleitet wird nur aus genau **einer** SPDX-ID; ein
    Lizenzausdruck kann nicht daneben stehen (CycloneDX 1.6 erlaubt genau einen Ausdruck), ein
    Freitext-Name oder mehrere IDs sind keine eindeutige Aussage."""
    lizenzen = k.get("licenses", [])
    if any(lz.get("acknowledgement") == "concluded"
           or lz.get("license", {}).get("acknowledgement") == "concluded" for lz in lizenzen):
        return
    if len(lizenzen) != 1 or "license" not in lizenzen[0]:
        return
    lizenz = lizenzen[0]["license"]
    if lizenz.get("id") and lizenz.get("acknowledgement", "declared") == "declared":
        lizenzen.append({"license": {"id": lizenz["id"], "acknowledgement": "concluded"}})


def _haupt_setzen(haupt: dict[str, Any], artefakt: Artefakt, version: str,
                  ersteller: str) -> None:
    dateiname = artefakt.pfad.name
    haupt["version"] = version
    haupt["manufacturer"] = _hersteller(ersteller)
    haupt["licenses"] = [{"license": {"id": EIGENE_LIZENZ, "acknowledgement": a}}
                         for a in ("declared", "concluded")]
    haupt["hashes"] = [{"alg": "SHA-256", "content": artefakt.sha256},
                       {"alg": "SHA-512", "content": artefakt.sha512}]
    andere = [r for r in haupt.get("externalReferences", []) if r.get("type") != "distribution"]
    haupt["externalReferences"] = [*andere, {
        "type": "distribution", "url": dateiname,
        "hashes": [{"alg": "SHA-512", "content": artefakt.sha512}],
        "comment": "Release-Artefakt (Pfad relativ zum Release-Verzeichnis)",
    }]
    _bsi_setzen(haupt, dateiname)


def _dateikomponente(datei: Datei) -> dict[str, Any]:
    name = PurePosixPath(datei.pfad_im_artefakt).name
    k: dict[str, Any] = {
        "type": "file", "bom-ref": f"datei:{datei.pfad_im_artefakt}", "name": name,
        "hashes": [{"alg": "SHA-512", "content": datei.sha512}],
    }
    _bsi_setzen(k, name)
    k["properties"] = [{"name": "scanview:pfad", "value": datei.pfad_im_artefakt},
                       *k.get("properties", [])]
    return k


_GRUND_SHA = "SHA-512 der auslieferbaren Form ohne Beleg"
_GRUND_DATEINAME = "Dateiname bzw. BSI-Eigenschaften nicht belegbar"
_GRUND_DATEI = "Datei im Lieferumfang keiner Komponente zugeordnet"
_GRUND_ABHAENGIGKEIT = "keine Abhängigkeitsangabe des Generators"


def nachbearbeiten(
    bom: dict[str, Any], artefakt: Artefakt, ersteller: str, sbom_uri: str, *,
    version: str, belege: dict[str, Beleg] | None = None,
    unvollstaendig: dict[str, str] | None = None, jetzt: datetime | None = None,
) -> dict[str, Any]:
    """Generator-SBOM → TR-03183-2-Fassung (neue Kopie, Eingabe bleibt unverändert).

    `belege` (bom-ref → `Beleg`) ergänzt SHA-512 aus belegter Quelle; `unvollstaendig`
    (bom-ref → Grund) markiert bekannte Lücken aus der Erzeugung. `serialNumber` ist die
    SBOM-URI im Sinne der TR (Tabelle 10) und deterministisch aus Artefakt-SHA-256 und Version;
    `sbom_uri` (sprechender Name, z. B. `urn:scanview:sbom:web:0.1.0`) steht zusätzlich als
    Eigenschaft `scanview:sbom-uri` darin."""
    b = copy.deepcopy(bom)
    b.pop("vulnerabilities", None)
    b.update({"$schema": _SCHEMA, "bomFormat": "CycloneDX", "specVersion": "1.6"})
    b.setdefault("version", 1)
    b["serialNumber"] = "urn:uuid:" + str(uuid.uuid5(_NAMENSRAUM,
                                                     f"{artefakt.sha256}:{version}"))
    meta = b.setdefault("metadata", {})
    meta["timestamp"] = _zeitstempel(jetzt)
    meta["manufacturer"] = _hersteller(ersteller)
    meta["authors"] = [{"name": HERSTELLER_NAME, "email": ersteller}]
    haupt = _haupt(b)
    haupt_ref: str = haupt["bom-ref"]
    oberste: list[dict[str, Any]] = b.setdefault("components", [])
    komponenten = alle_komponenten(oberste)
    refs: list[str] = [k.get("bom-ref") or "" for k in komponenten]
    if not all(isinstance(r, str) and r for r in refs) or len(set(refs)) != len(refs) \
            or haupt_ref in refs:
        raise NachweisFehler("sbom-format", "jede Komponente braucht eine eindeutige bom-ref")
    belege = belege or {}
    unbekannt = sorted(set(belege) - set(refs))
    if unbekannt:
        raise NachweisFehler("sbom-hash", f"Belege für unbekannte Komponenten: {unbekannt[:5]}")
    unbekannt = sorted(set(unvollstaendig or {}) - set(refs) - {haupt_ref})
    if unbekannt:
        raise NachweisFehler("sbom-abdeckung", "Lücken für unbekannte Komponenten: "
                                               f"{unbekannt[:5]}")
    gruende: dict[str, list[str]] = {r: [g] for r, g in (unvollstaendig or {}).items()}

    zuordnung, offen = _zuordnen(b, artefakt)
    dateien_je_ref: dict[str, list[Datei]] = {}
    for datei in artefakt.lieferumfang:
        if datei.pfad_im_artefakt in zuordnung:
            dateien_je_ref.setdefault(zuordnung[datei.pfad_im_artefakt], []).append(datei)

    beigaben = {ref: [_dateikomponente(d) for d in dateien]
                for ref, dateien in dateien_je_ref.items()
                if ref != haupt_ref and len(dateien) > 1}
    beigabe_refs = [k["bom-ref"] for dateien in beigaben.values() for k in dateien]

    _haupt_setzen(haupt, artefakt, version, ersteller)
    for k in komponenten:
        ref = k["bom-ref"]
        beleg = belege.get(ref)
        if beleg is not None:
            _verteilung_ergaenzen(k, beleg)
        hashes = _sha512_der_verteilung(k)
        if any(_SHA512.fullmatch(h) is None for h in hashes):
            raise NachweisFehler("sbom-hash", f"{ref}: SHA-512 ist kein 128-stelliger Hexwert")
        if not hashes:
            gruende.setdefault(ref, []).append(_GRUND_SHA)
        dateien = dateien_je_ref.get(ref, [])
        dateiname: str | None
        if len(dateien) == 1:
            # Ausgeliefert ist die Datei im Bundle (z. B. laz-perf.wasm): ihr SHA-512 steht in
            # components[].hashes; der SHA-512 in externalReferences[distribution] bleibt der
            # des npm-Tarballs, aus dem sie stammt — zwei verschiedene Dateien, beide belegt.
            dateiname = PurePosixPath(dateien[0].pfad_im_artefakt).name
            k["hashes"] = [h for h in k.get("hashes", []) if h.get("alg") != "SHA-512"] \
                + [{"alg": "SHA-512", "content": dateien[0].sha512}]
        elif beleg is not None:
            dateiname = beleg.dateiname
        else:
            mit_hash = [r for r in _verteilungen(k) if r.get("hashes")]
            kandidaten = [_dateiname_aus_url(r.get("url", ""))
                          for r in [*mit_hash, *_verteilungen(k)]]
            if ref.startswith(_EIGENBAU_PRAEFIX):  # Verteilung = eingecheckte Datei im Quellrepo
                kandidaten += [PurePosixPath(r.get("url", "")).name for r in _verteilungen(k)]
            dateiname = next((n for n in kandidaten if n and klassifiziere(n)), None)
        if not _bsi_setzen(k, dateiname):
            gruende.setdefault(ref, []).append(_GRUND_DATEINAME)
        _lizenz_schliessen(k)
        # Mehrere Bundle-Dateien behalten eigene Pfade/Hashes; der Paket-Hash bleibt erhalten.
        if ref in beigaben:
            k.setdefault("components", []).extend(beigaben[ref])

    offen_dateien = [d for d in artefakt.lieferumfang if d.pfad_im_artefakt in set(offen)]
    datei_refs: list[str] = []
    for datei in offen_dateien:
        dk = _dateikomponente(datei)
        oberste.append(dk)
        datei_refs.append(dk["bom-ref"])
        gruende[dk["bom-ref"]] = [_GRUND_DATEI]

    abhaengigkeiten: list[dict[str, Any]] = b.setdefault("dependencies", [])
    vorhanden = {d.get("ref") for d in abhaengigkeiten}
    for ref in [haupt_ref, *refs, *datei_refs, *beigabe_refs]:
        if ref not in vorhanden:
            abhaengigkeiten.append({"ref": ref, "dependsOn": []})
            if ref not in [*datei_refs, *beigabe_refs]:
                gruende.setdefault(ref, []).append(_GRUND_ABHAENGIGKEIT)
    for d in abhaengigkeiten:
        d.setdefault("dependsOn", [])
        d["dependsOn"].extend(k["bom-ref"] for k in beigaben.get(d["ref"], []))
        if d["ref"] == haupt_ref:
            d["dependsOn"] = [*d["dependsOn"], *datei_refs]

    unvoll = {r for r, g in gruende.items() if g}
    if unvoll:
        unvoll.add(haupt_ref)  # die Hauptkomponente umfasst alles
    alle = [haupt_ref, *refs, *datei_refs, *beigabe_refs]
    b["compositions"] = _kompositionen(alle, unvoll)

    zaehler = Counter(g for liste in gruende.values() for g in liste)
    eigen = [p for p in meta.get("properties", [])
             if p.get("name") != "cdx:reproducible" and not p.get("name", "").startswith(
                 "scanview:")]
    eigen.append({"name": "scanview:sbom-uri", "value": sbom_uri})
    eigen += [{"name": "scanview:unvollstaendig", "value": f"{g}: {n} Komponente(n)"}
              for g, n in sorted(zaehler.items())]
    eigen += [{"name": "scanview:abdeckung:nicht-zugeordnet", "value": d.pfad_im_artefakt}
              for d in offen_dateien]
    meta["properties"] = eigen
    return b


def _kompositionen(alle: list[str], unvoll: set[str], praefix: str = "") -> list[dict[str, Any]]:
    vollstaendig = [r for r in alle if r not in unvoll]
    unvollstaendig = [r for r in alle if r in unvoll]
    ergebnis: list[dict[str, Any]] = []
    if vollstaendig:
        ergebnis.append({"bom-ref": f"{praefix}komposition:vollstaendig", "aggregate": "complete",
                         "assemblies": vollstaendig, "dependencies": vollstaendig})
    if unvollstaendig:
        ergebnis.append({"bom-ref": f"{praefix}komposition:unvollstaendig",
                         "aggregate": "incomplete", "assemblies": unvollstaendig,
                         "dependencies": unvollstaendig})
    return ergebnis


# --- Zusammenführung ---------------------------------------------------------------------------

def zusammenfuehren(boms: list[dict[str, Any]], version: str, *, ersteller: str = ERSTELLER,
                    jetzt: datetime | None = None) -> dict[str, Any]:
    """Gesamt-SBOM je Version: logische Hauptkomponente `scanview` (TR §3.2.1), darunter die
    Hauptkomponenten der Artefakte samt Komponenten. bom-refs werden je Artefakt mit dem Namen
    seiner Hauptkomponente präfixiert (server und mcp teilen Pakete). Die Hauptkomponente ist nur
    `complete`, wenn jedes Teil-SBOM vollständig ist."""
    wurzel_ref = "scanview"
    komponenten: list[dict[str, Any]] = []
    abhaengigkeiten: list[dict[str, Any]] = []
    kompositionen: list[dict[str, Any]] = []
    werkzeuge: dict[tuple[str, str, str], dict[str, Any]] = {}
    kinder: list[str] = []
    seriennummern: list[str] = []
    alles_vollstaendig = True
    for teil in boms:
        if "vulnerabilities" in teil:
            raise NachweisFehler("sbom-schwachstellen", "Teil-SBOM enthält vulnerabilities")
        haupt = copy.deepcopy(_haupt(teil))
        praefix = f"{haupt['name']}:"
        if haupt["bom-ref"] == wurzel_ref or praefix + haupt["bom-ref"] in kinder:
            raise NachweisFehler("sbom-zusammenfuehren", f"Hauptkomponente {haupt['name']} "
                                                         "doppelt")

        def neu(ref: str, p: str = praefix) -> str:
            return p + ref
        haupt["bom-ref"] = neu(haupt["bom-ref"])
        haupt["properties"] = haupt.get("properties", []) + [
            p for p in teil["metadata"].get("properties", [])
            if p.get("name", "").startswith("scanview:")]
        kinder.append(haupt["bom-ref"])
        komponenten.append(haupt)
        for k in copy.deepcopy(teil.get("components", [])):
            for unter in alle_komponenten([k]):
                unter["bom-ref"] = neu(unter["bom-ref"])
            komponenten.append(k)
        for d in teil.get("dependencies", []):
            abhaengigkeiten.append({"ref": neu(d["ref"]),
                                    "dependsOn": [neu(r) for r in d.get("dependsOn", [])]})
        if not teil.get("compositions"):
            raise NachweisFehler("sbom-zusammenfuehren", f"{haupt['name']}: Teil-SBOM ohne "
                                                         "compositions (Vollständigkeit unbelegt)")
        for c in teil.get("compositions", []):
            c = copy.deepcopy(c)
            if c.get("bom-ref"):
                c["bom-ref"] = neu(c["bom-ref"])
            for feld in ("assemblies", "dependencies"):
                if feld in c:
                    c[feld] = [neu(r) for r in c[feld]]
            alles_vollstaendig &= c.get("aggregate") == "complete"
            kompositionen.append(c)
        for w in teil["metadata"].get("tools", {}).get("components", []):
            werkzeuge.setdefault((w.get("group", ""), w["name"], w.get("version", "")), w)
        seriennummern.append(teil.get("serialNumber", ""))
    refs = [k["bom-ref"] for k in alle_komponenten(komponenten)]
    if len(set(refs)) != len(refs):
        raise NachweisFehler("sbom-zusammenfuehren", "bom-refs nach dem Zusammenführen nicht "
                                                     "eindeutig")
    abhaengigkeiten.insert(0, {"ref": wurzel_ref, "dependsOn": kinder})
    kompositionen += _kompositionen([wurzel_ref], set() if alles_vollstaendig else {wurzel_ref},
                                    praefix="scanview:")
    name = f"scanview-gesamt:{version}:" + ",".join(sorted(seriennummern))
    return {
        "$schema": _SCHEMA, "bomFormat": "CycloneDX", "specVersion": "1.6",
        "serialNumber": "urn:uuid:" + str(uuid.uuid5(_NAMENSRAUM, name)), "version": 1,
        "metadata": {
            "timestamp": _zeitstempel(jetzt),
            "tools": {"components": list(werkzeuge.values())},
            "authors": [{"name": HERSTELLER_NAME, "email": ersteller}],
            "manufacturer": _hersteller(ersteller),
            "component": {
                "type": "application", "bom-ref": wurzel_ref, "name": "scanview",
                "version": version, "manufacturer": _hersteller(ersteller),
                "licenses": [{"license": {"id": EIGENE_LIZENZ, "acknowledgement": a}}
                             for a in ("declared", "concluded")],
            },
            "properties": [{"name": "scanview:sbom-uri",
                            "value": f"urn:scanview:sbom:gesamt:{version}"}],
        },
        "components": komponenten,
        "dependencies": abhaengigkeiten,
        "compositions": kompositionen,
    }


# --- Validierung -------------------------------------------------------------------------------

_SBOMQS_REVISION = "TR-03183-2 (2.1.0)"


def werte_sbomqs_aus(text: str) -> dict[str, Any]:
    """sbomqs-JSON (`compliance --bsi-v21 --json`) auswerten — sbomqs endet auch mit Lücken mit
    Exit 0. Lücke = Score < 10, getrennt nach `required`, geschlüsselt nach
    `section_data_field` (sbomqs nummeriert abweichend von der TR)."""
    regel = "sbom-werkzeug"
    try:
        daten = json.loads(text)
    except json.JSONDecodeError as e:
        raise NachweisFehler(regel, f"sbomqs lieferte kein JSON: {e}") from None
    if not isinstance(daten, dict) or daten.get("revision") != _SBOMQS_REVISION:
        raise NachweisFehler(regel, f"sbomqs-Ergebnis nicht für {_SBOMQS_REVISION}")
    abschnitte, zusammenfassung = daten.get("sections"), daten.get("summary")
    if not isinstance(abschnitte, list) or not abschnitte or not isinstance(zusammenfassung, dict):
        raise NachweisFehler(regel, "sbomqs-Ergebnis ohne sections/summary")
    for feld in ("total_score", "required_elements_score"):
        wert = zusammenfassung.get(feld)
        if isinstance(wert, bool) or not isinstance(wert, int | float):
            raise NachweisFehler(regel, f"sbomqs-summary.{feld} ist keine Zahl: {wert!r}")
    pflicht: Counter[str] = Counter()
    optional: Counter[str] = Counter()
    beispiele: dict[str, list[str]] = {}
    for s in abschnitte:
        try:
            feld, noetig, punkte = s["section_data_field"], s["required"], s["score"]
            if isinstance(punkte, bool) or not isinstance(punkte, int | float) \
                    or not isinstance(noetig, bool):
                raise TypeError
        except (KeyError, TypeError):
            raise NachweisFehler(regel, f"sbomqs-Abschnitt unlesbar: {s!r}"[:300]) from None
        if punkte < 10:
            (pflicht if noetig else optional)[feld] += 1
            liste = beispiele.setdefault(feld, [])
            if len(liste) < 5:
                liste.append(str(s.get("element_id", "")))
    return {
        "revision": daten["revision"],
        "gesamt": zusammenfassung.get("total_score"),
        "pflichtwert": zusammenfassung.get("required_elements_score"),
        "pflichtluecken": dict(sorted(pflicht.items())),
        "optionale_luecken": dict(sorted(optional.items())),
        "beispiele": dict(sorted(beispiele.items())),
    }


def validieren(bom_pfad: Path, werkzeuge: dict[str, Werkzeug]) -> dict[str, Any]:
    """Schema-Prüfung mit cyclonedx-cli (1.6, `--fail-on-errors`, Erfolgsmeldung verlangt) und
    TR-Werkzeugbefund mit sbomqs. Schwachstelleninformation im SBOM → sofortiger Abbruch."""
    bom = _lade_json(bom_pfad, "sbom-schema")
    if "vulnerabilities" in bom:
        raise NachweisFehler("sbom-schwachstellen", f"{bom_pfad.name}: SBOM enthält "
                                                    "vulnerabilities (TR §3.1)")
    if bom.get("bomFormat") != "CycloneDX" or bom.get("specVersion") != "1.6":
        raise NachweisFehler("sbom-schema", f"{bom_pfad.name}: kein CycloneDX 1.6")
    ziel = "/eingabe/bom.json"
    lesend = {bom_pfad.resolve(): ziel}
    cli = werkzeuge["cyclonedx-cli"]
    befehl = docker_befehl(cli, ["validate", "--input-file", ziel, "--input-format", "json",
                                 "--input-version", "v1_6", "--fail-on-errors"], lesend)
    try:
        ergebnis = ausfuehren(befehl, "sbom-schema")
    except NachweisFehler as e:
        raise NachweisFehler("sbom-schema", f"{bom_pfad.name}: {e.text}") from None
    if "BOM validated successfully" not in ergebnis.stdout:
        raise NachweisFehler("sbom-schema", f"{bom_pfad.name}: cyclonedx-cli ohne "
                                            f"Erfolgsmeldung: {ergebnis.stdout.strip()[-500:]}")
    qs = werkzeuge["sbomqs"]
    ausgabe = ausfuehren(docker_befehl(qs, ["compliance", "--bsi-v21", "--json", ziel], lesend),
                         "sbom-werkzeug").stdout
    return {
        "cyclonedx_cli": {"gueltig": True, "version": cli.version,
                          "meldung": "BOM validated successfully."},
        "sbomqs": {"version": qs.version, **werte_sbomqs_aus(ausgabe)},
    }


# --- npm: Lockfile-Abgleich und Ersteller ------------------------------------------------------

def _sha512_aus_integritaet(integritaet: str | None) -> str | None:
    for teil in (integritaet or "").split():
        if teil.startswith("sha512-"):
            try:
                return base64.b64decode(teil[7:], validate=True).hex()
            except ValueError:
                return None
    return None


def npm_pruefen(bom: dict[str, Any], lock: dict[str, Any]) -> dict[str, str]:
    """Abgleich cyclonedx-npm ↔ `package-lock.json`: Komponentenzahl = Nicht-dev-Pakete des
    Lockfiles („erwartete Abdeckung“), jeder SHA-512 des Generators = `integrity` des
    Lockfiles. Liefert Komponenten ohne SHA-512-Beleg (bom-ref → Grund)."""
    pakete = lock.get("packages")
    if not isinstance(pakete, dict):
        raise NachweisFehler("sbom-komponenten", "package-lock.json ohne packages")
    erwartet = [p for p, e in pakete.items() if p and not e.get("dev") and not e.get("link")]
    komponenten = alle_komponenten(bom.get("components", []))
    if len(komponenten) != len(erwartet):
        raise NachweisFehler("sbom-komponenten", f"cyclonedx-npm lieferte {len(komponenten)} "
                                                 f"Komponenten, das Lockfile hat {len(erwartet)} "
                                                 "Produktionspakete")
    luecken: dict[str, str] = {}
    for k in komponenten:
        pfade = _eigenschaften(k, "cdx:npm:package:path")
        eintrag = pakete.get(pfade[0]) if pfade else None
        if not isinstance(eintrag, dict):
            raise NachweisFehler("sbom-komponenten", f"{k.get('bom-ref')}: nicht im Lockfile")
        beleg = _sha512_aus_integritaet(eintrag.get("integrity"))
        generator = _sha512_der_verteilung(k)
        if generator and (beleg is None or any(h != beleg for h in generator)):
            raise NachweisFehler("sbom-hash", f"{k['bom-ref']}: SHA-512 des Generators nicht "
                                              "durch package-lock.json belegt")
        if not generator:
            luecken[k["bom-ref"]] = "npm-Lockfile ohne SHA-512 (integrity)"
    return luecken


def _url(wert: object) -> str | None:
    if not isinstance(wert, str):
        return None
    url = wert.strip()
    kurz = re.fullmatch(r"(?:github:)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", url)
    if kurz and "://" not in url:
        url = f"https://github.com/{kurz.group(1)}"
    url = re.sub(r"^git\+", "", url)
    url = re.sub(r"^git://", "https://", url)
    url = re.sub(r"\.git$", "", url)
    return url if re.match(r"https?://[^\s]+$", url) else None


def _email(wert: object) -> str | None:
    if not isinstance(wert, str):
        return None
    for _, adresse in email.utils.getaddresses([wert]):
        if re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", adresse):
            return adresse
    return None


def npm_ersteller(paket: dict[str, Any]) -> dict[str, Any] | None:
    """Komponentenersteller aus `package.json` (TR §5.2.2: E-Mail, sonst URL der Ersteller- oder
    Projektseite): `author.email` → `author.url` → `homepage` → `repository`."""
    autor = paket.get("author")
    mail = url = None
    if isinstance(autor, dict):
        mail, url = _email(autor.get("email")), _url(autor.get("url"))
    elif isinstance(autor, str):
        teile = re.fullmatch(r"\s*([^<(]*?)\s*(?:<([^>]*)>)?\s*(?:\(([^)]*)\))?\s*", autor)
        if teile:
            mail, url = _email(teile.group(2)), _url(teile.group(3))
    if mail:
        return {"contact": [{"email": mail}]}
    repo = paket.get("repository")
    for kandidat in (url, _url(paket.get("homepage")),
                     _url(repo.get("url") if isinstance(repo, dict) else repo)):
        if kandidat:
            return {"url": [kandidat]}
    return None


def npm_anreichern(bom: dict[str, Any], web: Path) -> None:
    """Ersteller und Quellcode-URI (Repository) aus `web/node_modules/<pfad>/package.json` des
    Bau-Worktrees ergänzen, soweit vorhanden."""
    for k in alle_komponenten(bom.get("components", [])):
        pfade = _eigenschaften(k, "cdx:npm:package:path")
        datei = web / pfade[0] / "package.json" if pfade else None
        if datei is None or not datei.is_file():
            continue
        try:
            paket = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(paket, dict):
            continue
        ersteller = npm_ersteller(paket)
        if ersteller and "manufacturer" not in k and "authors" not in k:
            k["manufacturer"] = ersteller
        repo = paket.get("repository")
        quelle = _url(repo.get("url") if isinstance(repo, dict) else repo)
        verweise = k.setdefault("externalReferences", [])
        if quelle and not any(r.get("type") == "source-distribution" for r in verweise):
            verweise.append({"type": "source-distribution", "url": quelle,
                             "comment": "Repository laut package.json"})


# --- Python: venv, Wheel-Belege, Ersteller -----------------------------------------------------

def wheel_tags(dateiname: str) -> set[str]:
    """Kompatibilitäts-Tags eines Wheel-Dateinamens (PEP 427, komprimierte Tag-Sets expandiert)."""
    if not dateiname.endswith(".whl"):
        return set()
    teile = dateiname[:-4].split("-")
    if len(teile) not in (5, 6):
        return set()
    py, abi, plattform = teile[-3:]
    return {f"{p}-{a}-{pl}" for p in py.split(".") for a in abi.split(".")
            for pl in plattform.split(".")}


def python_belege(
    installiert: list[Installiert], lock_text: str, laden: Callable[[str], bytes], *, cwd: str,
) -> tuple[dict[tuple[str, str], Beleg], dict[tuple[str, str], str], list[Netzschritt]]:
    """Je installiertem Paket das Wheel aus `uv.lock` wählen, dessen Tags genau denen der
    installierten Form (`WHEEL`) entsprechen, herunterladen, SHA-256 gegen das Lockfile prüfen und
    SHA-512 über die Datei berechnen. Keine oder mehrere Treffer (z. B. aus sdist gebaut) →
    Lücke, kein Download."""
    try:
        lock = tomllib.loads(lock_text)
    except tomllib.TOMLDecodeError as e:
        raise NachweisFehler("sbom-hash", f"uv.lock ungültig: {e}") from None
    pakete = {(_normalisiere(p["name"]), str(p.get("version", ""))): p
              for p in lock.get("package", [])}
    belege: dict[tuple[str, str], Beleg] = {}
    fehlend: dict[tuple[str, str], str] = {}
    schritte: list[Netzschritt] = []
    for inst in installiert:
        schluessel = (_normalisiere(inst.name), inst.version)
        paket = pakete.get(schluessel)
        if paket is None:
            fehlend[schluessel] = "nicht im uv.lock"
            continue
        treffer = [w for w in paket.get("wheels", [])
                   if inst.tags and wheel_tags(PurePosixPath(
                       urllib.parse.urlsplit(w["url"]).path).name) == set(inst.tags)]
        if len(treffer) != 1:
            fehlend[schluessel] = ("keine eindeutige Wheel-Datei im uv.lock zur installierten "
                                   "Form (vermutlich aus sdist gebaut)")
            continue
        url, soll = treffer[0]["url"], str(treffer[0].get("hash", ""))
        if not soll.startswith("sha256:"):
            fehlend[schluessel] = "uv.lock ohne SHA-256 für das Wheel"
            continue
        schritte.append(Netzschritt(befehl=["herunterladen", url], cwd=cwd, zeit=_jetzt()))
        daten = laden(url)
        if hashlib.sha256(daten).hexdigest() != soll[7:]:
            raise NachweisFehler("sbom-hash", f"{url}: SHA-256 weicht von uv.lock ab")
        belege[schluessel] = Beleg(url=url, sha512=hashlib.sha512(daten).hexdigest(),
                                   dateiname=urllib.parse.unquote(
                                       PurePosixPath(urllib.parse.urlsplit(url).path).name))
    return belege, fehlend, schritte


def python_quellen(lock_text: str) -> dict[tuple[str, str], str]:
    """sdist-URL je Paket aus `uv.lock` (Quellcode-URI, TR §5.2.4)."""
    ergebnis: dict[tuple[str, str], str] = {}
    for p in tomllib.loads(lock_text).get("package", []):
        sdist = p.get("sdist")
        if isinstance(sdist, dict) and isinstance(sdist.get("url"), str):
            ergebnis[(_normalisiere(p["name"]), str(p.get("version", "")))] = sdist["url"]
    return ergebnis


@dataclass(frozen=True)
class NurSdist:
    """Paket, das es nur als Quellpaket (sdist) gibt: im gehärteten SBOM-Container nicht baubar
    (tmpfs noexec, kein Compiler). Es wird nicht installiert, sondern aus `uv.lock` belegt
    (Name, Version, sdist-URL, SHA-256) und als unvollständig ausgewiesen."""

    artefakt: str
    name: str
    version: str
    sha256: str
    lizenz: str
    lizenzbeleg: str
    grund: str
    adr: str


_NUR_SDIST_FELDER = frozenset(NurSdist.__dataclass_fields__)


def lade_nur_sdist(pfad: Path) -> list[NurSdist]:
    """`nur-sdist.toml` laden: `[[paket]]` mit allen Feldern von `NurSdist`. Fehlende Datei = keine
    Ausnahme; ungültige Einträge brechen ab (fail-closed)."""
    regel = "sbom-nur-sdist"
    if not pfad.exists():
        return []
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as fehler:
        raise NachweisFehler(regel, f"{pfad}: {fehler}") from None
    if set(daten) - {"paket"}:
        unbekannt = sorted(set(daten) - {"paket"})
        raise NachweisFehler(regel, f"{pfad}: unbekannte Schlüssel {unbekannt}")
    ergebnis: list[NurSdist] = []
    for i, ns in enumerate(daten.get("paket", [])):
        if not isinstance(ns, dict) or set(ns) != _NUR_SDIST_FELDER \
                or not all(isinstance(v, str) and v.strip() for v in ns.values()):
            raise NachweisFehler(regel, f"{pfad}: Eintrag {i + 1} braucht genau "
                                        f"{sorted(_NUR_SDIST_FELDER)} als nicht leere Texte")
        if not re.fullmatch(r"[0-9a-f]{64}", ns["sha256"]):
            raise NachweisFehler(regel, f"{pfad}: {ns['name']}: sha256 ist kein "
                                        "64-stelliger Hexwert")
        if not re.fullmatch(r"[A-Za-z0-9.+-]+", ns["lizenz"]):
            raise NachweisFehler(regel, f"{pfad}: {ns['name']}: lizenz muss genau eine "
                                        "SPDX-ID sein")
        if len(ns["grund"]) < 20:
            raise NachweisFehler(regel, f"{pfad}: {ns['name']}: grund zu kurz (≥ 20 Zeichen)")
        ergebnis.append(NurSdist(**ns))
    return ergebnis


def _lock_pakete(lock_text: str) -> dict[str, dict[str, Any]]:
    return {_normalisiere(p["name"]): p for p in tomllib.loads(lock_text).get("package", [])}


def nur_sdist_pruefen(eintraege: list[NurSdist], lock_text: str) -> None:
    """Jeder Eintrag muss genau das gelockte Paket treffen (Version, sdist-SHA-256) und darf keine
    Wheels haben — gibt es inzwischen Wheels, ist die Ausnahme überflüssig und fällt auf."""
    regel = "sbom-nur-sdist"
    pakete = _lock_pakete(lock_text)
    for ns in eintraege:
        p = pakete.get(_normalisiere(ns.name))
        if p is None or str(p.get("version")) != ns.version:
            raise NachweisFehler(regel, f"{ns.name} {ns.version}: nicht so in uv.lock "
                                        f"(dort {p.get('version') if p else 'fehlt'})")
        roh_sdist = p.get("sdist")
        sdist: dict[str, Any] = roh_sdist if isinstance(roh_sdist, dict) else {}
        if sdist.get("hash") != f"sha256:{ns.sha256}" or not isinstance(sdist.get("url"), str):
            raise NachweisFehler(regel, f"{ns.name} {ns.version}: sdist-Hash weicht von uv.lock ab")
        if p.get("wheels"):
            raise NachweisFehler(regel, f"{ns.name} {ns.version}: uv.lock kennt Wheels — Ausnahme "
                                        "entfernen, das Paket ist normal installierbar")


def nur_sdist_einfuegen(bom: dict[str, Any], eintraege: list[NurSdist],
                        lock_text: str) -> dict[str, str]:
    """Nicht installierte sdist-Pakete als Komponenten aus `uv.lock` eintragen (Abhängigkeiten
    laut Lock, direkte Abhängigkeit des Projekts an der Wurzel). Liefert bom-ref → Grund für die
    `incomplete`-Komposition."""
    pakete = _lock_pakete(lock_text)
    refs = {_normalisiere(k["name"]): k["bom-ref"] for k in bom.get("components", [])}
    haupt_ref = _haupt(bom)["bom-ref"]
    projekt = next((p for p in pakete.values()
                    if isinstance(p.get("source"), dict)
                    and ({"editable", "virtual"} & set(p["source"]))), None)
    direkt = {_normalisiere(d["name"]) for d in (projekt or {}).get("dependencies", [])}
    luecken: dict[str, str] = {}
    abhaengigkeiten = bom.setdefault("dependencies", [])
    for ns in eintraege:
        p = pakete[_normalisiere(ns.name)]
        ref = f"{ns.name}=={ns.version}"
        bom.setdefault("components", []).append({
            "type": "library", "bom-ref": ref, "name": ns.name, "version": ns.version,
            "purl": f"pkg:pypi/{_normalisiere(ns.name)}@{ns.version}",
            "licenses": [{"license": {"id": ns.lizenz, "acknowledgement": "declared"}}],
            "externalReferences": [{
                "type": "distribution", "url": p["sdist"]["url"],
                "hashes": [{"alg": "SHA-256", "content": ns.sha256}],
                "comment": f"nur Quellpaket, aus uv.lock belegt; Lizenzbeleg {ns.lizenzbeleg}"}],
        })
        unter = [refs[n] for n in (_normalisiere(d["name"]) for d in p.get("dependencies", []))
                 if n in refs]
        abhaengigkeiten.append({"ref": ref, "dependsOn": unter})
        if _normalisiere(ns.name) in direkt:
            wurzel = next((d for d in abhaengigkeiten if d.get("ref") == haupt_ref), None)
            if wurzel is None:
                wurzel = {"ref": haupt_ref, "dependsOn": []}
                abhaengigkeiten.append(wurzel)
            wurzel.setdefault("dependsOn", []).append(ref)
        luecken[ref] = f"nur Quellpaket (sdist), nicht installiert: {ns.grund} (ADR {ns.adr})"
    return luecken


@dataclass(frozen=True)
class Enthalten:
    """Fremde Bibliothek, die im Quellbaum eines Eigenbaus mitgeliefert und mitgebaut wird (z. B.
    zstd im Basis-Transcoder): eigene Quelle + Commit (für OSV), Lizenz und der eingebaute Teil."""

    name: str
    version: str
    quelle: str
    commit: str
    tag: str
    lizenz: str
    teil: str


@dataclass(frozen=True)
class Eigenbau:
    """Fremdkomponente, die scanview aus gepinnter Quelle selbst baut und als Datei(en) im Repo
    mitliefert (z. B. der Basis-Transcoder ohne Laufzeit-Codeerzeugung). Kein Paketmanager belegt
    sie; Beleg sind Quelle + Commit, gepinnte Toolchain, Bau-Skript und die SHA-256 der Dateien."""

    artefakt: str
    name: str
    version: str
    quelle: str
    commit: str
    tag: str
    werkzeug: str
    bau: str
    lizenz: str
    lizenzbeleg: str
    dateien: tuple[tuple[str, str], ...]
    grund: str
    adr: str
    enthalten: tuple[Enthalten, ...] = ()


_EIGENBAU_PRAEFIX = "eigenbau:"
_EIGENBAU_TEXTE = frozenset(f for f in Eigenbau.__dataclass_fields__
                            if f not in ("dateien", "enthalten"))
_ENTHALTEN_TEXTE = frozenset(Enthalten.__dataclass_fields__)


def lade_eigenbau(pfad: Path) -> list[Eigenbau]:
    """`eigenbau.toml` laden: `[[komponente]]` mit allen Textfeldern von `Eigenbau`,
    `[[komponente.datei]]` (`pfad`, `sha256`) und optional `[[komponente.enthalten]]` (alle
    Felder von `Enthalten`). Fehlende Datei = kein Eigenbau; ungültige Einträge brechen ab
    (fail-closed)."""
    regel = "sbom-eigenbau"
    if not pfad.exists():
        return []
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as fehler:
        raise NachweisFehler(regel, f"{pfad}: {fehler}") from None
    if set(daten) - {"komponente"}:
        unbekannt = sorted(set(daten) - {"komponente"})
        raise NachweisFehler(regel, f"{pfad}: unbekannte Schlüssel {unbekannt}")
    ergebnis: list[Eigenbau] = []
    for i, e in enumerate(daten.get("komponente", [])):
        texte = {k: v for k, v in e.items() if k not in ("datei", "enthalten")} \
            if isinstance(e, dict) else {}
        if set(texte) != _EIGENBAU_TEXTE \
                or not all(isinstance(v, str) and v.strip() for v in texte.values()):
            raise NachweisFehler(regel, f"{pfad}: Eintrag {i + 1} braucht genau "
                                        f"{sorted(_EIGENBAU_TEXTE)} als nicht leere Texte")
        dateien = e.get("datei", [])
        if not dateien or not all(isinstance(d, dict) and set(d) == {"pfad", "sha256"}
                                  and re.fullmatch(r"[0-9a-f]{64}", str(d["sha256"]))
                                  for d in dateien):
            raise NachweisFehler(regel, f"{pfad}: {texte['name']}: datei braucht pfad und "
                                        "64-stelligen sha256")
        if not re.fullmatch(r"[0-9a-f]{40}", texte["commit"]):
            raise NachweisFehler(regel, f"{pfad}: {texte['name']}: commit ist kein voller Git-Hash")
        if "@sha256:" not in texte["werkzeug"]:
            raise NachweisFehler(regel, f"{pfad}: {texte['name']}: werkzeug per Digest pinnen")
        if len(texte["grund"]) < 20:
            raise NachweisFehler(regel, f"{pfad}: {texte['name']}: grund zu kurz (≥ 20 Zeichen)")
        enthalten: list[Enthalten] = []
        for c in e.get("enthalten", []):
            if not isinstance(c, dict) or set(c) != _ENTHALTEN_TEXTE \
                    or not all(isinstance(v, str) and v.strip() for v in c.values()):
                raise NachweisFehler(regel, f"{pfad}: {texte['name']}: enthalten braucht genau "
                                            f"{sorted(_ENTHALTEN_TEXTE)} als nicht leere Texte")
            if not re.fullmatch(r"[0-9a-f]{40}", c["commit"]):
                raise NachweisFehler(regel, f"{pfad}: {texte['name']}/{c['name']}: commit ist "
                                            "kein voller Git-Hash")
            enthalten.append(Enthalten(**c))
        ergebnis.append(Eigenbau(**texte, dateien=tuple((d["pfad"], d["sha256"]) for d in dateien),
                                 enthalten=tuple(enthalten)))
    return ergebnis


def _github_purl(quelle: str, commit: str) -> str:
    teile = urllib.parse.urlsplit(quelle).path.strip("/").removesuffix(".git").split("/")
    if urllib.parse.urlsplit(quelle).netloc != "github.com" or len(teile) != 2:
        raise NachweisFehler("sbom-eigenbau",
                             f"{quelle}: Quelle muss github.com/<owner>/<repo> sein")
    return f"pkg:github/{teile[0].lower()}/{teile[1].lower()}@{commit}"


def eigenbau_refs(e: Eigenbau) -> list[tuple[str, str, str, str, str]]:
    """(bom-ref, PURL, Commit, Quelle, Tag) des Eigenbaus und seiner enthaltenen Bibliotheken —
    wie sie `eigenbau_einfuegen` ins SBOM schreibt (Grundlage der OSV-Abfragen)."""
    ref = f"{_EIGENBAU_PRAEFIX}{e.name}@{e.version}"
    return [(ref, _github_purl(e.quelle, e.commit), e.commit, e.quelle, e.tag),
            *((f"{ref}/{c.name}@{c.version}", _github_purl(c.quelle, c.commit), c.commit,
               c.quelle, c.tag) for c in e.enthalten)]


def eigenbau_einfuegen(bom: dict[str, Any], eintraege: list[Eigenbau], worktree: Path) -> None:
    """Eigenbau-Komponenten eintragen: Dateien müssen im Worktree liegen und ihre SHA-256 treffen
    (sonst Abbruch — dann bauen bzw. Eintrag nachziehen). Verteilungsform sind die eingecheckten
    Dateien (SHA-512); Herkunft steht in pedigree, Bau-Skript und Quelle als Referenzen."""
    regel = "sbom-eigenbau"
    haupt_ref = _haupt(bom)["bom-ref"]
    abhaengigkeiten = bom.setdefault("dependencies", [])
    for e in eintraege:
        verteilungen: list[dict[str, Any]] = []
        for pfad, sha256 in e.dateien:
            datei = worktree / pfad
            if not datei.is_file():
                raise NachweisFehler(regel, f"{e.name}: {pfad} fehlt")
            inhalt = datei.read_bytes()
            if hashlib.sha256(inhalt).hexdigest() != sha256:
                raise NachweisFehler(regel, f"{e.name}: {pfad} weicht vom erklärten SHA-256 ab")
            verteilungen.append({"type": "distribution", "url": pfad,
                                 "hashes": [{"alg": "SHA-512",
                                             "content": hashlib.sha512(inhalt).hexdigest()}],
                                 "comment": "eingecheckter Eigenbau (Pfad im Quellrepo)"})
        if not (worktree / e.lizenzbeleg).is_file():
            raise NachweisFehler(regel, f"{e.name}: Lizenzbeleg {e.lizenzbeleg} fehlt")
        (ref, purl, *_), *innen = eigenbau_refs(e)
        enthalten = [{
            "type": "library", "bom-ref": c_ref, "name": c.name, "version": c.version,
            "purl": c_purl,
            "licenses": [{"expression": c.lizenz, "acknowledgement": "declared"}],
            # Einkompiliert in die Wasm-Datei des Eigenbaus — das ist ihre Verteilungsform.
            "externalReferences": [{"type": "vcs", "url": c.quelle,
                                    "comment": f"Commit {c.commit}, Tag {c.tag}"},
                                   *(dict(v, comment=f"einkompiliert in {v['url']}")
                                     for v in verteilungen if v["url"].endswith(".wasm"))],
            "properties": [{"name": "scanview:git-tag", "value": c.tag}],
            "description": f"im Eigenbau {e.name} enthalten und mitgebaut: {c.teil}",
        } for c, (c_ref, c_purl, *_) in zip(e.enthalten, innen, strict=True)]
        bom.setdefault("components", []).append({
            "type": "library", "bom-ref": ref, "name": e.name, "version": e.version,
            "purl": purl,
            "licenses": [{"expression": e.lizenz, "acknowledgement": "declared"}],
            "externalReferences": [
                {"type": "vcs", "url": e.quelle, "comment": f"Commit {e.commit}, Tag {e.tag}"},
                {"type": "build-meta", "url": e.bau,
                 "comment": f"Bau-Skript im Quellrepo, Toolchain {e.werkzeug}"},
                *verteilungen],
            "pedigree": {"notes": f"Eigenbau aus {e.quelle} @ {e.commit} mit {e.werkzeug}: "
                                  f"{e.grund} (ADR {e.adr}); Lizenzbeleg {e.lizenzbeleg}"},
            "properties": [{"name": "scanview:git-tag", "value": e.tag}],
            **({"components": enthalten} if enthalten else {}),
        })
        abhaengigkeiten.append({"ref": ref, "dependsOn": [c["bom-ref"] for c in enthalten]})
        abhaengigkeiten.extend({"ref": c["bom-ref"], "dependsOn": []} for c in enthalten)
        wurzel = next((d for d in abhaengigkeiten if d.get("ref") == haupt_ref), None)
        if wurzel is None:
            wurzel = {"ref": haupt_ref, "dependsOn": []}
            abhaengigkeiten.append(wurzel)
        wurzel.setdefault("dependsOn", []).append(ref)


_PROJEKT_URL_BEVORZUGT = ("homepage", "home", "source", "source code", "repository", "code",
                          "github")


def python_ersteller(metadaten: dict[str, Any]) -> dict[str, Any] | None:
    """Komponentenersteller aus Paket-METADATA: `Author-email` → `Maintainer-email` →
    `Home-page` → `Project-URL` (Homepage/Source/Repository)."""
    for feld in ("Author-email", "Maintainer-email"):
        mail = _email(metadaten.get(feld))
        if mail:
            return {"contact": [{"email": mail}]}
    url = _url(metadaten.get("Home-page"))
    if url:
        return {"url": [url]}
    projekt: dict[str, str] = {}
    for eintrag in metadaten.get("Project-URL", []) or []:
        if "," in eintrag:
            name, wert = eintrag.split(",", 1)
            kandidat = _url(wert)
            if kandidat:
                projekt.setdefault(name.strip().lower(), kandidat)
    for name in _PROJEKT_URL_BEVORZUGT:
        if name in projekt:
            return {"url": [projekt[name]]}
    return None


def _export_zeilen(text: str) -> list[tuple[str, str | None]]:
    """(Anforderung, Umgebungsmarker oder None) je Paketzeile von `uv export`."""
    zeilen: list[tuple[str, str | None]] = []
    for zeile in text.splitlines():
        if not zeile.strip() or zeile[0] in "# \t-":
            continue
        anforderung, _, marker = zeile.partition(";")
        zeilen.append((anforderung.strip(), marker.strip() or None))
    return zeilen


def python_marker(text: str) -> list[str]:
    """Alle verschiedenen Umgebungsmarker eines `uv export` (sortiert) — sie werden im Image der
    venv ausgewertet (`_MARKER_CODE`), weil nur dort Interpreter und Plattform stimmen."""
    return sorted({m for _, m in _export_zeilen(text) if m})


def erwartete_python_pakete(text: str, auswerten: Callable[[str], bool] | None = None
                            ) -> set[tuple[str, str]]:
    """(Name, Version) aus `uv export --format requirements-txt --no-hashes`. Zeilen mit
    Umgebungsmarker zählen nur, wenn `auswerten(marker)` wahr ist; ohne `auswerten` ist ein
    Marker ein Fehler (fail-closed — sonst zählten z. B. `pywin32 ; sys_platform == 'win32'`)."""
    ergebnis: set[tuple[str, str]] = set()
    for anforderung, marker in _export_zeilen(text):
        if marker is not None:
            if auswerten is None:
                raise NachweisFehler("sbom-komponenten", f"Umgebungsmarker ohne Auswertung: "
                                                         f"{marker}")
            if not auswerten(marker):
                continue
        if "==" not in anforderung:
            raise NachweisFehler("sbom-komponenten", f"uv export: keine exakte Version: "
                                                     f"{anforderung}")
        name, version = anforderung.split("==", 1)
        ergebnis.add((_normalisiere(name.strip()), version.strip()))
    return ergebnis


#: Dünner Container-Aufruf: wertet die als JSON-Liste übergebenen Marker aus, gibt JSON aus.
_MARKER_CODE = ("import sys,json; sys.path.insert(0,'/werkzeug/{site}'); "
                "from packaging.markers import Marker; "
                "print(json.dumps({{m: Marker(m).evaluate() for m in json.loads(sys.argv[1])}}))")


def marker_auswertung(ausgabe: str, marker: list[str]) -> dict[str, bool]:
    """Ausgabe von `_MARKER_CODE` prüfen: genau die angefragten Marker, je ein Wahrheitswert."""
    try:
        daten = json.loads(ausgabe)
    except json.JSONDecodeError:
        raise NachweisFehler("sbom-komponenten", "Markerauswertung lieferte kein JSON") from None
    if not isinstance(daten, dict) or set(daten) != set(marker) \
            or not all(isinstance(v, bool) for v in daten.values()):
        raise NachweisFehler("sbom-komponenten", "Markerauswertung unvollständig oder ungültig")
    return daten


def _lese_venv(site: Path) -> list[Installiert]:
    ergebnis: list[Installiert] = []
    for info in sorted(site.glob("*.dist-info")):
        try:
            meta = email.parser.Parser().parsestr(
                (info / "METADATA").read_text(encoding="utf-8", errors="replace"),
                headersonly=True)
            wheel = (info / "WHEEL").read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise NachweisFehler("sbom-generator", f"{info.name}: {e.strerror}") from None
        tags = frozenset(z.split(":", 1)[1].strip() for z in wheel.splitlines()
                         if z.startswith("Tag:"))
        metadaten: dict[str, Any] = {f: meta.get(f) for f in
                                     ("Author-email", "Maintainer-email", "Home-page")}
        metadaten["Project-URL"] = meta.get_all("Project-URL") or []
        ergebnis.append(Installiert(name=str(meta.get("Name", "")),
                                    version=str(meta.get("Version", "")), tags=tags,
                                    metadaten=metadaten))
    return ergebnis


def _herunterladen(url: str) -> bytes:
    if not url.startswith("https://"):
        raise NachweisFehler("sbom-hash", f"{url}: nur https-Downloads erlaubt")
    try:
        with urllib.request.urlopen(url, timeout=120) as antwort:
            daten: bytes = antwort.read()
    except OSError as e:
        raise NachweisFehler("sbom-hash", f"{url}: Download fehlgeschlagen: {e}") from None
    return daten


# --- Erzeugung (Docker) ------------------------------------------------------------------------

#: Python-Minor-Version (untere Schranke von `requires-python`) → (Werkzeug, Site-Ordner).
_PY_WERKZEUGE = {"3.11": ("cyclonedx-py", "site"), "3.12": ("cyclonedx-py-py312", "site-py312")}
_NPM_CLI = "/werkzeug/node_modules/@cyclonedx/cyclonedx-npm/bin/cyclonedx-npm-cli.js"
_IMPORT_BILDER = Path("import/src/scanview_import/pdal_runner.py")


def _werkzeug(werkzeuge: dict[str, Werkzeug], name: str, nachweise: Path) -> tuple[Werkzeug, Path]:
    w = werkzeuge.get(name)
    if w is None or w.werkzeugordner is None:
        raise NachweisFehler("werkzeuge", f"{name}: fehlt in werkzeuge.json oder hat keinen "
                                          "werkzeugordner")
    return w, nachweise / w.werkzeugordner


_MARKE = ".scanview-bereitstellung.json"


def _soll_marke(w: Werkzeug, lockfile: Path, regel: str = "sbom-werkzeug") -> dict[str, str]:
    try:
        lock_hash = hashlib.sha256(lockfile.read_bytes()).hexdigest()
    except OSError as e:
        raise NachweisFehler(regel, f"{lockfile}: {e.strerror}") from None
    return {"image": w.image, "lockfile": lockfile.name, "lockfile_sha256": lock_hash}


def _pruefe_marke(ziel: Path, soll: dict[str, str], regel: str = "sbom-werkzeug") -> bool:
    """`False`: Ziel fehlt (installieren). `True`: Ziel passt zu Lockfile und Image. Ziel vorhanden,
    Marke fehlt oder weicht ab (halb installiert, veraltet, fremd befüllt) → Fehler: nie still
    wiederverwenden."""
    if not ziel.exists():
        return False
    try:
        ist = json.loads((ziel / _MARKE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ist = None
    if ist != soll:
        raise NachweisFehler(regel, f"{ziel}: Installation passt nicht zu Lockfile/Image "
                                    f"(Marke {_MARKE} fehlt oder weicht ab) — Ordner löschen "
                                    "und neu bereitstellen")
    return True


def _werkzeug_ziele(werkzeuge: dict[str, Werkzeug], nachweise: Path
                    ) -> list[tuple[str, Werkzeug, Path, Path, dict[str, str]]]:
    """(Python-Version oder "npm", Werkzeug, Werkzeugordner, Installationsziel, Soll-Marke)."""
    w, ordner = _werkzeug(werkzeuge, "cyclonedx-npm", nachweise)
    ziele = [("npm", w, ordner, ordner / "node_modules",
              _soll_marke(w, ordner / "package-lock.json"))]
    for version, (name, site) in _PY_WERKZEUGE.items():
        w, ordner = _werkzeug(werkzeuge, name, nachweise)
        ziele.append((version, w, ordner, ordner / site,
                      _soll_marke(w, ordner / "requirements.txt")))
    return ziele


def werkzeuge_bereitstellen(nachweise: Path, werkzeuge: dict[str, Werkzeug]) -> list[Netzschritt]:
    """Installiert cyclonedx-npm und cyclonedx-py aus ihren Lockfiles in die Werkzeugordner, falls
    noch nicht vorhanden (Netz — Rückgabe fürs Manifest). Nach erfolgreicher Installation
    wird eine Marke (Image-Digest + SHA-256 des Lockfiles) ins Ziel geschrieben; ein vorhandenes
    Ziel ohne passende Marke ist ein Fehler."""
    schritte: list[Netzschritt] = []
    # Erst alle Einträge und Marken prüfen, dann installieren: Abbruch vor jedem Netzlauf.
    ziele = _werkzeug_ziele(werkzeuge, nachweise)
    offen = [z for z in ziele if not _pruefe_marke(z[3], z[4])]
    for version, w, ordner, ziel, soll in offen:
        if version == "npm":
            befehl = ["npm", "ci", "--ignore-scripts"]
            schritte.append(Netzschritt(befehl, f"nachweise/{w.werkzeugordner}", _jetzt()))
            ausfuehren(docker_befehl(w, [*befehl, "--prefix", "/werkzeug"], {},
                                     {ordner: "/werkzeug"}, netz=True), "sbom-werkzeug")
        else:
            befehl = ["uv", "pip", "install", "--python", f"/usr/local/bin/python{version}",
                      "--target", f"/werkzeug/{ziel.name}", "--require-hashes", "--no-deps",
                      "--only-binary", ":all:", "-r", "/werkzeug/requirements.txt"]
            schritte.append(Netzschritt(befehl, f"nachweise/{w.werkzeugordner}", _jetzt()))
            ausfuehren(docker_befehl(w, befehl, {}, {ordner: "/werkzeug"}, netz=True,
                                     umgebung={"UV_CACHE_DIR": "/tmp/uv-cache"}),
                       "sbom-werkzeug")
        if not ziel.is_dir():
            raise NachweisFehler("sbom-werkzeug", f"{ziel}: nach der Installation nicht vorhanden")
        (ziel / _MARKE).write_text(json.dumps(soll, sort_keys=True), encoding="utf-8")
    return schritte


def _pruefe_installiert(werkzeuge: dict[str, Werkzeug], nachweise: Path, version: str) -> None:
    for v, _w, _o, ziel, soll in _werkzeug_ziele(werkzeuge, nachweise):
        if v == version and not _pruefe_marke(ziel, soll):
            raise NachweisFehler("sbom-werkzeug", f"{ziel}: nicht installiert "
                                                  "(werkzeuge_bereitstellen)")


def mediapipe_modell_einfuegen(bom: dict[str, Any], worktree: Path) -> None:
    """Unverändertes Modell belegen, analog zum Eigenbau mit geprüftem Quellbaum-Hash.

    Kein Eigenbau: es gibt weder einen scanview-Bau noch einen Git-Commit/Toolchain-Beleg.
    Quelle, Variante und SHA-256 laut web/vendor/mediapipe/README.md; Verteilungs-SHA-512
    aus den geprüften Bytes (TR-03183-2). Bei Modellwechsel beide Erklärungen nachziehen.
    """
    pfad = "web/vendor/mediapipe/face_landmarker.task"
    sha256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
    url = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
           "face_landmarker/float16/1/face_landmarker.task")
    try:
        inhalt = (worktree / pfad).read_bytes()
    except OSError as e:
        raise NachweisFehler("sbom-modell", f"{pfad} nicht lesbar: {e.strerror}") from None
    if hashlib.sha256(inhalt).hexdigest() != sha256:
        raise NachweisFehler("sbom-modell", f"{pfad} weicht vom erklärten SHA-256 ab")
    ref = "modell:mediapipe-face-landmarker@float16/1"
    bom.setdefault("components", []).append({
        "type": "machine-learning-model", "bom-ref": ref,
        "name": "MediaPipe Face Landmarker", "version": "1",
        "description": "MediaPipe Face Landmarker, Variante float16/1, unverändert übernommen",
        "supplier": {"name": "Google LLC", "url": ["https://ai.google.dev/edge/mediapipe"]},
        "manufacturer": {"name": "Google LLC", "url": ["https://ai.google.dev/edge/mediapipe"]},
        "licenses": [{"license": {"id": "Apache-2.0", "acknowledgement": "declared"}}],
        "hashes": [{"alg": "SHA-256", "content": sha256}],
        "externalReferences": [{"type": "distribution", "url": url,
                                "hashes": [{"alg": "SHA-256", "content": sha256},
                                           {"alg": "SHA-512",
                                            "content": hashlib.sha512(inhalt).hexdigest()}]}],
        "properties": [{"name": "scanview:modell-variante", "value": "float16/1"}],
    })
    deps = bom.setdefault("dependencies", [])
    deps.append({"ref": ref, "dependsOn": []})
    haupt_ref = _haupt(bom)["bom-ref"]
    wurzel = next((d for d in deps if d.get("ref") == haupt_ref), None)
    if wurzel is None:
        wurzel = {"ref": haupt_ref, "dependsOn": []}
        deps.append(wurzel)
    wurzel.setdefault("dependsOn", []).append(ref)


def _roh_web(worktree: Path, werkzeuge: dict[str, Werkzeug], nachweise: Path,
             arbeit: Path) -> Rohergebnis:
    w, ordner = _werkzeug(werkzeuge, "cyclonedx-npm", nachweise)
    _pruefe_installiert(werkzeuge, nachweise, "npm")
    web = worktree / "web"
    aus = arbeit / "aus"
    aus.mkdir()
    befehl = docker_befehl(
        w, ["node", _NPM_CLI, "--omit", "dev", "--spec-version", "1.6", "--package-lock-only",
            "--output-reproducible", "--output-format", "JSON", "--output-file", "/aus/bom.json",
            "/quelle/package.json"],
        {ordner: "/werkzeug", web: "/quelle"}, {aus: "/aus"},
        umgebung={"npm_execpath": "/werkzeug/node_modules/npm/bin/npm-cli.js"})
    ausfuehren(befehl, "sbom-generator")
    bom = _lade_json(aus / "bom.json", "sbom-generator")
    luecken = npm_pruefen(bom, _lade_json(web / "package-lock.json", "sbom-komponenten"))
    npm_anreichern(bom, web)
    eigenbau_einfuegen(bom, [e for e in lade_eigenbau(nachweise / "eigenbau.toml")
                             if e.artefakt == "web"], worktree)
    mediapipe_modell_einfuegen(bom, worktree)
    return Rohergebnis(bom=bom, belege={}, unvollstaendig=luecken, netzschritte=[])


def _python_minor(pyproject: dict[str, Any], pfad: Path) -> str:
    angabe = str(pyproject.get("project", {}).get("requires-python", ""))
    treffer = re.search(r">=\s*3\.(\d+)", angabe)
    minor = f"3.{treffer.group(1)}" if treffer else ""
    if minor not in _PY_WERKZEUGE:
        raise NachweisFehler("sbom-werkzeug", f"{pfad}: requires-python „{angabe}“ — kein "
                                              "gepinntes Python-Image dafür")
    return minor


def _roh_python(artefakt: Artefakt, worktree: Path, werkzeuge: dict[str, Werkzeug],
                nachweise: Path, arbeit: Path) -> Rohergebnis:
    ordnername = _PYTHON_WURZELN[artefakt.name][0].rstrip("/")
    quelle = worktree / ordnername
    pyproject_pfad = quelle / "pyproject.toml"
    try:
        pyproject = tomllib.loads(pyproject_pfad.read_text(encoding="utf-8"))
        lock_text = (quelle / "uv.lock").read_text(encoding="utf-8")
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise NachweisFehler("sbom-generator", f"{ordnername}: {e}") from None
    minor = _python_minor(pyproject, pyproject_pfad)
    name, site = _PY_WERKZEUGE[minor]
    w, ordner = _werkzeug(werkzeuge, name, nachweise)
    _pruefe_installiert(werkzeuge, nachweise, minor)
    venv, aus = arbeit / "venv", arbeit / "aus"
    venv.mkdir()
    aus.mkdir()
    umgebung = {"UV_PROJECT_ENVIRONMENT": "/venv", "UV_PYTHON_DOWNLOADS": "never",
                "UV_CACHE_DIR": "/tmp/uv-cache", "UV_LINK_MODE": "copy", "UV_NO_PROGRESS": "1"}
    nur_sdist = [ns for ns in lade_nur_sdist(nachweise / "nur-sdist.toml")
                 if ns.artefakt == artefakt.name]
    nur_sdist_pruefen(nur_sdist, lock_text)
    for ns in nur_sdist:
        if not (worktree / ns.lizenzbeleg).is_file():
            raise NachweisFehler("sbom-nur-sdist", f"{ns.name}: Lizenzbeleg {ns.lizenzbeleg} fehlt")
    # Extras gehören zum Lieferumfang (import: `[sonne]` mit pvlib/embreex): Sync und Export
    # beide mit `--all-extras`, sonst fehlten sie in der SBOM und der Abgleich sähe es nicht.
    sync = ["uv", "sync", "--locked", "--no-dev", "--no-editable", "--all-extras",
            *[x for ns in nur_sdist for x in ("--no-install-package", ns.name)]]
    schritte = [Netzschritt(sync, ordnername, _jetzt())]
    ausfuehren(docker_befehl(w, [*sync, "--directory", "/quelle"], {quelle: "/quelle"},
                             {venv: "/venv"}, netz=True, umgebung=umgebung), "sbom-generator")
    ausfuehren(docker_befehl(
        w, ["uv", "export", "--locked", "--no-dev", "--no-editable", "--all-extras",
            "--no-emit-project",
            "--no-hashes", "--format", "requirements-txt", "--directory", "/quelle", "-o",
            "/aus/erwartet.txt"],
        {quelle: "/quelle"}, {aus: "/aus"}, umgebung={**umgebung, "UV_OFFLINE": "1"}),
        "sbom-generator")
    # `uv export` behält Umgebungsmarker (z. B. `sys_platform == 'win32'`); ausgewertet wird im
    # selben Image wie die venv, mit `packaging` aus dem Werkzeugordner.
    export_text = (aus / "erwartet.txt").read_text(encoding="utf-8")
    marker = python_marker(export_text)
    auswertung: dict[str, bool] = {}
    if marker:
        ausgabe = ausfuehren(docker_befehl(
            w, ["python", "-c", _MARKER_CODE.format(site=site), json.dumps(marker)],
            {ordner: "/werkzeug"}), "sbom-generator").stdout
        auswertung = marker_auswertung(ausgabe, marker)
    # Kein PYTHONPATH: er würde an den venv-Interpreter vererbt und die Werkzeugpakete ins SBOM
    # ziehen.
    code = ("import sys,runpy; sys.path.insert(0,'/werkzeug/" + site + "'); "
            "sys.argv=['cyclonedx-py','environment','--spec-version','1.6','--output-format',"
            "'JSON','--output-reproducible','--pyproject','/quelle/pyproject.toml',"
            "'--output-file','/aus/bom.json','/venv']; "
            "runpy.run_module('cyclonedx_py', run_name='__main__')")
    ausfuehren(docker_befehl(w, ["python", "-c", code],
                             {ordner: "/werkzeug", venv: "/venv", quelle: "/quelle"},
                             {aus: "/aus"}), "sbom-generator")
    bom = _lade_json(aus / "bom.json", "sbom-generator")
    nur_sdist_luecken = nur_sdist_einfuegen(bom, nur_sdist, lock_text)

    erwartet = erwartete_python_pakete(export_text, auswertung.__getitem__)
    ist = {(_normalisiere(k["name"]), str(k.get("version", ""))) for k in bom["components"]}
    if ist != erwartet:
        raise NachweisFehler("sbom-komponenten", f"{ordnername}: cyclonedx-py ≠ uv.lock — "
                                                 f"zu viel {sorted(ist - erwartet)[:5]}, "
                                                 f"fehlt {sorted(erwartet - ist)[:5]}")
    projekt = _normalisiere(str(pyproject.get("project", {}).get("name", "")))
    installiert = [i for i in _lese_venv(venv / "lib" / f"python{minor}" / "site-packages")
                   if _normalisiere(i.name) != projekt]
    belege_nv, fehlend, downloads = python_belege(installiert, lock_text, _herunterladen,
                                                  cwd=ordnername)
    quellen = python_quellen(lock_text)
    meta = {(_normalisiere(i.name), i.version): i.metadaten for i in installiert}
    belege: dict[str, Beleg] = {}
    luecken: dict[str, str] = {}
    for k in bom["components"]:
        schluessel = (_normalisiere(k["name"]), str(k.get("version", "")))
        if schluessel in belege_nv:
            belege[k["bom-ref"]] = belege_nv[schluessel]
        else:
            luecken[k["bom-ref"]] = fehlend.get(schluessel, "nicht in der venv gefunden")
        ersteller = python_ersteller(meta.get(schluessel, {}))
        if ersteller and "manufacturer" not in k and "authors" not in k:
            k["manufacturer"] = ersteller
        if schluessel in quellen:
            k.setdefault("externalReferences", []).append(
                {"type": "source-distribution", "url": quellen[schluessel],
                 "comment": "sdist laut uv.lock"})
    luecken.update(nur_sdist_luecken)
    return Rohergebnis(bom=bom, belege=belege, unvollstaendig=luecken,
                       netzschritte=[*schritte, *downloads])


def _konstante(text: str, name: str) -> str:
    treffer = re.search(rf'^{name}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if treffer is None:
        raise NachweisFehler("sbom-image", f"{_IMPORT_BILDER}: {name} nicht gefunden")
    return treffer.group(1)


def container_purl(referenz: str) -> str | None:
    """PURL eines per Digest gepinnten Images (`name[:tag]@sha256:…`), sonst `None` — eine lokale
    Image-ID ist keine Registry-Kennung."""
    name, _, digest = referenz.partition("@")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        return None
    name = re.sub(r":[^/:]+$", "", name)
    return f"pkg:docker/{name}@{urllib.parse.quote(digest, safe='')}"


def _roh_images(bom: dict[str, Any], worktree: Path, werkzeuge: dict[str, Werkzeug],
                arbeit: Path) -> dict[str, str]:
    """syft über die vom Import genutzten Container-Images (`docker save` der lokalen Image-ID,
    ohne Netz). Jedes Image wird Komponente `container`, sein Paketinhalt (ohne syft-Dateieinträge)
    hängt darunter; alles ist `incomplete` (kein SHA-512 der Paketform, conda ohne Lock)."""
    try:
        text = (worktree / _IMPORT_BILDER).read_text(encoding="utf-8")
    except OSError as e:
        raise NachweisFehler("sbom-image", f"{_IMPORT_BILDER}: {e.strerror}") from None
    syft = werkzeuge.get("syft")
    if syft is None:
        raise NachweisFehler("werkzeuge", "syft fehlt in werkzeuge.json")
    gruende = {
        "pdal": ("Container-Image pdal/pdal (per Digest gepinnt): Paketinhalt laut syft ohne "
                 "SHA-512 der Paketform und ohne Dateinamen, conda-Pakete ohne Lock-Beleg"),
        "untwine": ("Container-Image scanview-untwine (lokal gebaut, nur Image-ID): transitive "
                    "conda-Pakete ohne Lock, kein Registry-Digest (B-LK-UNTWINE)"),
    }
    haupt_ref = _haupt(bom)["bom-ref"]
    luecken: dict[str, str] = {}
    container_refs: list[str] = []
    for kennung, konstante in (("pdal", "IMAGE"), ("untwine", "UNTWINE_IMAGE")):
        referenz = _konstante(text, konstante)
        info = ausfuehren(["docker", "image", "inspect", "--format", "{{.Id}} {{.Created}}",
                           referenz], "sbom-image").stdout.split()
        if len(info) != 2 or not info[0].startswith("sha256:"):
            raise NachweisFehler("sbom-image", f"{referenz}: Image-ID nicht lesbar")
        bild_id, erstellt = info
        ordner = arbeit / f"image-{kennung}"
        ordner.mkdir()
        tar = ordner / "bild.tar"
        aus = ordner / "aus"
        aus.mkdir()
        try:
            ausfuehren(["docker", "save", "-o", str(tar), bild_id], "sbom-image")
            ausfuehren(docker_befehl(
                syft, ["scan", "docker-archive:/img/bild.tar", "--select-catalogers",
                       "+conda-meta-cataloger", "-o", "cyclonedx-json@1.6=/aus/bom.json"],
                {tar: "/img/bild.tar"}, {aus: "/aus"},
                umgebung={"SYFT_CHECK_FOR_APP_UPDATE": "false"}), "sbom-generator")
        finally:
            tar.unlink(missing_ok=True)
        roh = _lade_json(aus / "bom.json", "sbom-generator")
        name, _, rest = referenz.partition("@")[0].rpartition(":")
        version = rest if rest and rest != "latest" else erstellt
        cref = f"image:{kennung}"
        komponente: dict[str, Any] = {
            "type": "container", "bom-ref": cref, "name": name or referenz, "version": version,
            "properties": [{"name": "scanview:image", "value": referenz},
                           {"name": "scanview:image-id", "value": bild_id}],
        }
        purl = container_purl(referenz)
        if purl:
            komponente["purl"] = purl
        behalten = {k["bom-ref"] for k in roh.get("components", []) if k.get("type") != "file"}
        neu = {r: f"{kennung}|{r}" for r in behalten}
        bom["components"].append(komponente)
        for k in roh.get("components", []):
            if k.get("bom-ref") in behalten:
                k = copy.deepcopy(k)
                k["bom-ref"] = neu[k["bom-ref"]]
                bom["components"].append(k)
                luecken[k["bom-ref"]] = gruende[kennung]
        for d in roh.get("dependencies", []):
            if d.get("ref") in behalten:
                bom["dependencies"].append({
                    "ref": neu[d["ref"]],
                    "dependsOn": sorted(neu[r] for r in d.get("dependsOn", []) if r in behalten)})
        bom["dependencies"].append({"ref": cref, "dependsOn": sorted(neu.values())})
        luecken[cref] = gruende[kennung]
        container_refs.append(cref)
    for d in bom["dependencies"]:
        if d.get("ref") == haupt_ref:
            d["dependsOn"] = [*d.get("dependsOn", []), *container_refs]
    return luecken


#: Name des Nachweis-SBOMs der Container-Images des Imports (`scanview-<BILDER>-<v>.cdx.json`).
#: Kein Artefakt, nie ausgeliefert; ohne Bindestrich, weil Dateinamen an „-“ geteilt werden.
BILDER = "importbilder"
_BILDER_REF = "scanview-importbilder"


def erzeuge_bilder(worktree: Path, werkzeuge: dict[str, Werkzeug], *, version: str,
                   ersteller: str, arbeitsordner: Path,
                   jetzt: datetime | None = None) -> dict[str, Any]:
    """Nachweis-SBOM der Container-Images des Import-Werkzeugs (syft über `docker save`, ohne
    Netz). Eigene Hauptkomponente `scanview-importbilder`, weil es kein Artefakt dazu gibt: kein
    Dateiname, keine Artefakt-Hashes. `serialNumber` aus Version und Image-IDs. Der ganze Inhalt
    ist `incomplete` (kein SHA-512 der Paketform, conda ohne Lock) — ehrlich ausgewiesen, aber
    kein Teil eines ausgelieferten SBOMs."""
    arbeit = arbeitsordner / BILDER
    try:
        arbeit.mkdir(parents=True)
    except FileExistsError:
        raise NachweisFehler("sbom-generator", f"{arbeit}: existiert schon") from None
    b: dict[str, Any] = {
        "$schema": _SCHEMA, "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
        "metadata": {"component": {
            "type": "application", "bom-ref": _BILDER_REF, "name": _BILDER_REF,
            "version": version,
            "description": "Container-Images des Import-Werkzeugs (PDAL, untwine) — laufen nur "
                           "beim Datenaufbereiter, nicht ausgeliefert"}},
        "components": [], "dependencies": [{"ref": _BILDER_REF, "dependsOn": []}],
    }
    luecken = _roh_images(b, worktree, werkzeuge, arbeit)
    ids = sorted(p["value"] for k in b["components"] if k.get("type") == "container"
                 for p in k.get("properties", []) if p.get("name") == "scanview:image-id")
    b["serialNumber"] = "urn:uuid:" + str(uuid.uuid5(
        _NAMENSRAUM, f"{BILDER}:{','.join(ids)}:{version}"))
    meta = b["metadata"]
    meta["timestamp"] = _zeitstempel(jetzt)
    meta["manufacturer"] = _hersteller(ersteller)
    meta["authors"] = [{"name": HERSTELLER_NAME, "email": ersteller}]
    refs = [k["bom-ref"] for k in alle_komponenten(b["components"])]
    vorhanden = {d.get("ref") for d in b["dependencies"]}
    b["dependencies"] += [{"ref": r, "dependsOn": []} for r in refs if r not in vorhanden]
    b["compositions"] = _kompositionen([_BILDER_REF, *refs], {_BILDER_REF, *luecken})
    zaehler = Counter(luecken.values())
    meta["properties"] = [
        {"name": "scanview:sbom-uri", "value": f"urn:scanview:sbom:{BILDER}:{version}"},
        {"name": "scanview:ausgeliefert", "value": "nein"},
        *({"name": "scanview:unvollstaendig", "value": f"{g}: {n} Komponente(n)"}
          for g, n in sorted(zaehler.items()))]
    return b


def erzeuge_roh(artefakt: Artefakt, worktree: Path, werkzeuge: dict[str, Werkzeug], *,
                nachweise: Path, arbeitsordner: Path) -> Rohergebnis:
    """Generator-SBOM eines Artefakts. `worktree` ist der
    Bau-Worktree (web/node_modules und Lockfiles), `nachweise` der Ordner mit den installierten
    Werkzeugen, `arbeitsordner` ein Ordner für venvs/Image-Archive (je Artefakt ein frischer
    Unterordner; ein schon vorhandener ist ein Fehler)."""
    arbeit = arbeitsordner / artefakt.name
    try:
        arbeit.mkdir(parents=True)
    except FileExistsError:
        raise NachweisFehler("sbom-generator", f"{arbeit}: existiert schon") from None
    if artefakt.name == "web":
        return _roh_web(worktree, werkzeuge, nachweise, arbeit)
    if artefakt.name not in _PYTHON_WURZELN:
        raise NachweisFehler("sbom-generator", f"unbekanntes Artefakt {artefakt.name}")
    return _roh_python(artefakt, worktree, werkzeuge, nachweise, arbeit)


# --- Gesamtablauf (CLI `sbom erzeugen`) --------------------------------------------------------

def _schreibe_json(pfad: Path, daten: object) -> None:
    pfad.write_text(json.dumps(daten, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class SbomErgebnis:
    """Ergebnis von `erzeuge_alle`: der Bau (Artefakte + Netzschritte des Baus), je Artefakt das
    SBOM (Artefaktname → Pfad), Gesamt-SBOM, Werkzeugbefunde und **alle** Netzschritte
    (Werkzeug-Installation, Bau, Generatoren) in Ausführungsreihenfolge."""

    bau: Bauergebnis
    sboms: dict[str, Path]
    gesamt: Path
    werkzeugbefunde: Path
    netzschritte: list[Netzschritt]
    zeilen: list[str]


def erzeugen(wurzel: Path, worktree: Path, version: str, ziel: Path, *,
             ersteller: str = ERSTELLER, arbeitsordner: Path | None = None,
             jetzt: datetime | None = None) -> list[str]:
    """Wie `erzeuge_alle`, liefert nur die Zusammenfassungszeilen (CLI `sbom erzeugen`)."""
    return erzeuge_alle(wurzel, worktree, version, ziel, ersteller=ersteller,
                        arbeitsordner=arbeitsordner, jetzt=jetzt).zeilen


def erzeuge_alle(wurzel: Path, worktree: Path, version: str, ziel: Path, *,
                 ersteller: str = ERSTELLER, arbeitsordner: Path | None = None,
                 jetzt: datetime | None = None) -> SbomErgebnis:
    """Artefakte bauen, je Artefakt SBOM erzeugen/nachbearbeiten, Abdeckung fail-closed prüfen
    (erst wenn **alle** Artefakte abgedeckt sind, wird geschrieben), Gesamt-SBOM bilden, alles
    validieren. Schreibt nach `<ziel>/sbom/`: je Artefakt `scanview-<name>-<v>.cdx.json`,
    `scanview-<v>.cdx.json`, `werkzeugbefunde.json`, `netzschritte.json`."""
    nachweise = wurzel / "nachweise"
    werkzeuge = lade_werkzeuge(nachweise / "werkzeuge.json")
    ausnahmen = lade_ausnahmen(nachweise / "abdeckung-ausnahmen.toml")
    npm_ausnahmen = lade_npm_ausnahmen(
        wurzel / "docs" / "compliance" / "npm-signaturen-ausnahmen.toml")
    ausgabe = ziel / "sbom"
    if ausgabe.exists():
        raise NachweisFehler("sbom-ziel", f"{ausgabe}: existiert schon — wird nicht überschrieben")
    zeit = jetzt or datetime.now(UTC)
    netz = list(werkzeuge_bereitstellen(nachweise, werkzeuge))
    bau = baue_artefakte(worktree, version, ziel / "artefakte", npm_ausnahmen=npm_ausnahmen)
    netz += bau.netzschritte
    boms: dict[str, dict[str, Any]] = {}
    with contextlib.ExitStack() as stapel:
        arbeit = arbeitsordner if arbeitsordner is not None else Path(
            stapel.enter_context(tempfile.TemporaryDirectory(prefix="scanview-sbom-")))
        for a in bau.artefakte:
            roh = erzeuge_roh(a, worktree, werkzeuge, nachweise=nachweise, arbeitsordner=arbeit)
            netz += roh.netzschritte
            pruefe_abdeckung(roh.bom, a, ausnahmen)
            boms[f"scanview-{a.name}-{version}.cdx.json"] = nachbearbeiten(
                roh.bom, a, ersteller, f"urn:scanview:sbom:{a.name}:{version}", version=version,
                belege=roh.belege, unvollstaendig=roh.unvollstaendig, jetzt=zeit)
        # Gesamt-SBOM = Produkt: nur die Artefakt-SBOMs, nicht die Werkzeug-Images.
        gesamt = zusammenfuehren(list(boms.values()), version, ersteller=ersteller, jetzt=zeit)
        if any(a.name == "import" for a in bau.artefakte):
            boms[f"scanview-{BILDER}-{version}.cdx.json"] = erzeuge_bilder(
                worktree, werkzeuge, version=version, ersteller=ersteller,
                arbeitsordner=arbeit, jetzt=zeit)
    boms[f"scanview-{version}.cdx.json"] = gesamt
    ausgabe.mkdir(parents=True)
    befunde: dict[str, Any] = {}
    zeilen: list[str] = []
    for name, bom in boms.items():
        pfad = ausgabe / name
        _schreibe_json(pfad, bom)
        befunde[name] = validieren(pfad, werkzeuge)
        luecken = befunde[name]["sbomqs"].get("pflichtluecken", {})
        aggregate = sorted({c["aggregate"] for c in bom.get("compositions", [])})
        zeilen.append(f"{name}: {len(bom.get('components', []))} Komponenten, "
                      f"compositions {'/'.join(aggregate)}, sbomqs-Pflichtlücken "
                      f"{sum(luecken.values())} in {len(luecken)} Feldern")
    _schreibe_json(ausgabe / "werkzeugbefunde.json", befunde)
    _schreibe_json(ausgabe / "netzschritte.json",
                   [{"befehl": s.befehl, "cwd": s.cwd, "zeit": s.zeit} for s in netz])
    namen = [a.name for a in bau.artefakte]
    if "import" in namen:
        namen.append(BILDER)
    return SbomErgebnis(
        bau=bau, sboms={n: ausgabe / f"scanview-{n}-{version}.cdx.json" for n in namen},
        gesamt=ausgabe / f"scanview-{version}.cdx.json",
        werkzeugbefunde=ausgabe / "werkzeugbefunde.json", netzschritte=netz, zeilen=zeilen)


def pruefe_bindung(bom: dict[str, Any], artefakt: Artefakt, version: str) -> None:
    """Gehört das SBOM zu genau diesem gebauten Artefakt? `serialNumber` muss
    die aus Artefakt-SHA-256 und Version abgeleitete SBOM-URI sein, die Hauptkomponente
    Version, SHA-256, SHA-512 und Dateinamen des Artefakts tragen. Ein SBOM aus einem anderen
    Bau (anderer Commit, andere Version) → `sbom-bindung`."""
    regel = "sbom-bindung"
    soll = "urn:uuid:" + str(uuid.uuid5(_NAMENSRAUM, f"{artefakt.sha256}:{version}"))
    if bom.get("serialNumber") != soll:
        raise NachweisFehler(regel, f"{artefakt.name}: serialNumber {bom.get('serialNumber')} "
                                    f"passt nicht zu Artefakt-SHA-256 und Version {version}")
    haupt = bom.get("metadata", {}).get("component") if isinstance(bom.get("metadata"),
                                                                   dict) else None
    if not isinstance(haupt, dict):
        raise NachweisFehler(regel, f"{artefakt.name}: Hauptkomponente fehlt")
    if haupt.get("version") != version:
        raise NachweisFehler(regel, f"{artefakt.name}: Hauptkomponente trägt Version "
                                    f"{haupt.get('version')}, erwartet {version}")
    hashes = {h.get("alg"): h.get("content") for h in haupt.get("hashes", [])
              if isinstance(h, dict)}
    for alg, wert in (("SHA-256", artefakt.sha256), ("SHA-512", artefakt.sha512)):
        if hashes.get(alg) != wert:
            raise NachweisFehler(regel, f"{artefakt.name}: {alg} der Hauptkomponente weicht vom "
                                        "gebauten Artefakt ab")
    if artefakt.pfad.name not in _eigenschaften(haupt, _BSI + "filename"):
        raise NachweisFehler(regel, f"{artefakt.name}: Dateiname {artefakt.pfad.name} fehlt "
                                    "in der Hauptkomponente")
