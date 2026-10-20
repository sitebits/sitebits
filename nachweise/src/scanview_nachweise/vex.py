"""OpenVEX-Aussagen laden und das Scan-Gate.

Freigebend ist eine Aussage nur, wenn **alle** Regeln gelten — sonst gilt sie als **nicht
vorhanden** (der Treffer bleibt blockierend) und wird gemeldet:

- Status `fixed` oder `not_affected`; `affected` und `under_investigation` geben nie frei.
- `not_affected`: einer der fünf Begründungscodes der OpenVEX-Spezifikation **und** ein
  `impact_statement` mit mindestens 20 Zeichen (Freitext mit Beleg, z. B. Datei/Zeile).
- Zeit (`last_updated`, sonst `timestamp` der Aussage, sonst des Dokuments — Vererbung laut
  OpenVEX) höchstens 90 Tage alt und nicht in der Zukunft.
- `scanview.lock_hash` = aktueller SHA-256 der Lockdatei `scanview.lockdatei`, und diese
  Lockdatei gehört zum Artefakt, in dessen SBOM der Treffer steht (`LOCKDATEIEN`). Ändert sich
  die Abhängigkeit, verfällt die Aussage.
- Produktbezug: exakt die PURL (mit Version) des Treffers.
- Eigenbauten (bom-ref `eigenbau:…`, Treffer aus OSV): Lockdatei ist `nachweise/eigenbau.toml`
  statt der Paket-Lockdatei des Artefakts — die Aussage verfällt, sobald sich Quelle, Commit oder
  Dateien des Eigenbaus ändern.

Zusatzfelder stehen im Objekt `scanview` der OpenVEX-Aussage (`lock_hash`, `lockdatei`,
`pruefer`). trivy 0.74.0 liest Dokumente mit diesem Zusatzobjekt und wendet die Aussage an
(geprüft am 28.09.2026) — eine Nebendatei ist deshalb nicht nötig.

Komponenten aus den Container-Images des Imports (bom-ref `pdal|…`, `untwine|…`) haben keine
Lockdatei: Für sie ist keine VEX-Freigabe möglich, Treffer ≥ hoch blockieren immer; Behebung
über das Image selbst (Befunde B-LK-UNTWINE, B-SBOM-IMAGES und die Sammelbefunde
B-SCAN-IMG-*). Das Befund-Gate kommt hinzu (`scan_gate`)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .fehler import NachweisFehler
from .gate import befund_gate
from .register import Befund
from .scan import IMAGE_PRAEFIXE, Treffer, purl_schluessel

#: Die fünf Begründungscodes der OpenVEX-Spezifikation (OPENVEX-SPEC.md, „Status Justifications“).
OPENVEX_BEGRUENDUNGEN = (
    "component_not_present",
    "vulnerable_code_not_present",
    "vulnerable_code_not_in_execute_path",
    "vulnerable_code_cannot_be_controlled_by_adversary",
    "inline_mitigations_already_exist",
)
STATUS = ("not_affected", "affected", "fixed", "under_investigation")
FREIGEBEND = ("fixed", "not_affected")
VERFALL_TAGE = 90
BEGRUENDUNG_MINDESTENS = 20
#: Lockdatei je Release-Artefakt (Artefaktnamen wie `artefakte.baue_artefakte`). Eine PyPI-PURL
#: kann in mehreren Artefakten stecken — die Aussage gilt nur für das Artefakt ihrer Lockdatei.
LOCKDATEIEN = {"web": "web/package-lock.json", "server": "server/uv.lock",
               "mcp": "mcp-server/uv.lock", "import": "import/uv.lock"}
#: Lockdatei der Eigenbauten (`sbom.lade_eigenbau`); nur vorhanden, wenn es Eigenbauten gibt.
EIGENBAU_LOCKDATEI = "nachweise/eigenbau.toml"
_EIGENBAU_PRAEFIX = "eigenbau:"

_SCHWEREN = ("kritisch", "hoch", "mittel", "niedrig", "info")
_BLOCKIEREND = ("kritisch", "hoch")
_PURL_MIT_VERSION = re.compile(r"pkg:[a-z0-9.+-]+/[^@\s]+@[^@\s]+")
_LOCK_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_ZUSATZ = ("lock_hash", "lockdatei", "pruefer")


@dataclass(frozen=True)
class VexAussage:
    """Eine OpenVEX-Aussage. `produkte` sind PURLs mit Version, `zeit` die maßgebliche Zeit
    (UTC), `lock_hash`/`lockdatei`/`pruefer` aus dem Zusatzobjekt `scanview`."""

    schwachstelle: str
    produkte: tuple[str, ...]
    status: str
    justification: str | None
    impact_statement: str | None
    zeit: datetime
    lock_hash: str | None
    pruefer: str | None
    lockdatei: str | None = None


# --- Laden --------------------------------------------------------------------------------------

def _zeit(wert: object, wo: str) -> datetime:
    zeit = None
    if isinstance(wert, str):
        try:
            zeit = datetime.fromisoformat(wert)
        except ValueError:
            zeit = None
    if zeit is None or zeit.tzinfo is None:
        raise NachweisFehler("vex", f"{wo}: „{wert}“ ist kein Zeitstempel mit Zeitzone")
    return zeit.astimezone(UTC)


def _text(wert: object) -> str | None:
    return wert if isinstance(wert, str) else None


def _produkte(roh: object, wo: str) -> tuple[str, ...]:
    if not isinstance(roh, list) or not roh:
        raise NachweisFehler("vex", f"{wo}: `products` fehlt oder ist leer")
    purls: list[str] = []
    for p in roh:
        if not isinstance(p, dict):
            raise NachweisFehler("vex", f"{wo}: Produkt ist kein Objekt")
        if "subcomponents" in p:
            raise NachweisFehler("vex", f"{wo}: `subcomponents` nicht unterstützt — Produkt ist "
                                        "die betroffene Komponente selbst (PURL mit Version)")
        roh_ident = p.get("identifiers")
        ident: dict[str, object] = roh_ident if isinstance(roh_ident, dict) else {}
        purl = p.get("@id", ident.get("purl"))
        if not isinstance(purl, str) or _PURL_MIT_VERSION.fullmatch(purl) is None:
            raise NachweisFehler("vex", f"{wo}: Produkt „{purl}“ ist keine PURL mit Version")
        purls.append(purl)
    return tuple(purls)


def _zusatz(roh: object, wo: str) -> dict[str, str]:
    if roh is None:
        return {}
    if not isinstance(roh, dict):
        raise NachweisFehler("vex", f"{wo}: `scanview` muss ein Objekt sein")
    unbekannt = sorted(set(roh) - set(_ZUSATZ))
    if unbekannt:
        raise NachweisFehler("vex", f"{wo}: unbekannte Felder in `scanview`: "
                                    + ", ".join(unbekannt))
    for feld, wert in roh.items():
        if not isinstance(wert, str) or not wert.strip():
            raise NachweisFehler("vex", f"{wo}: scanview.{feld} muss Text sein")
    if "lock_hash" in roh and _LOCK_HASH.fullmatch(roh["lock_hash"]) is None:
        raise NachweisFehler("vex", f"{wo}: scanview.lock_hash muss „sha256:<64 hex>“ sein")
    if "lockdatei" in roh and roh["lockdatei"] not in (*LOCKDATEIEN.values(), EIGENBAU_LOCKDATEI):
        raise NachweisFehler("vex", f"{wo}: scanview.lockdatei „{roh['lockdatei']}“ ist keine "
                                    "der Lockdateien " + ", ".join(LOCKDATEIEN.values())
                                    + f", {EIGENBAU_LOCKDATEI}")
    return dict(roh)


def _aussage(roh: object, nr: int, dokumentzeit: datetime) -> VexAussage:
    wo = f"Aussage {nr}"
    if not isinstance(roh, dict):
        raise NachweisFehler("vex", f"{wo}: kein Objekt")
    schwachstelle = roh.get("vulnerability", {}).get("name") \
        if isinstance(roh.get("vulnerability"), dict) else None
    if not isinstance(schwachstelle, str) or not schwachstelle.strip():
        raise NachweisFehler("vex", f"{wo}: `vulnerability.name` fehlt")
    wo = f"Aussage {nr} ({schwachstelle})"
    status = roh.get("status")
    if status not in STATUS:
        raise NachweisFehler("vex", f"{wo}: Status „{status}“ ist keiner von "
                                    + ", ".join(STATUS))
    zeitfeld = next((f for f in ("last_updated", "timestamp") if f in roh), None)
    zeit = _zeit(roh[zeitfeld], f"{wo} {zeitfeld}") if zeitfeld else dokumentzeit
    zusatz = _zusatz(roh.get("scanview"), wo)
    return VexAussage(
        schwachstelle=schwachstelle, produkte=_produkte(roh.get("products"), wo), status=status,
        justification=_text(roh.get("justification")),
        impact_statement=_text(roh.get("impact_statement")), zeit=zeit,
        lock_hash=zusatz.get("lock_hash"), pruefer=zusatz.get("pruefer"),
        lockdatei=zusatz.get("lockdatei"))


def lade_vex(pfad: Path) -> list[VexAussage]:
    """OpenVEX-Dokument laden. Formfehler (Kopf, unbekannter Status, Produkt ohne PURL mit
    Version, kaputtes Zusatzobjekt) sind `NachweisFehler("vex")`; inhaltliche Regeln prüft erst
    das Gate (die Aussage gilt dann als nicht vorhanden)."""
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except OSError as e:
        raise NachweisFehler("vex", f"{pfad} nicht lesbar: {e.strerror}") from None
    except json.JSONDecodeError as e:
        raise NachweisFehler("vex", f"{pfad} ist kein gültiges JSON: {e}") from None
    if not isinstance(daten, dict):
        raise NachweisFehler("vex", f"{pfad}: oberste Ebene muss ein Objekt sein")
    kontext = daten.get("@context")
    if not isinstance(kontext, str) or not kontext.startswith("https://openvex.dev/ns/"):
        raise NachweisFehler("vex", f"{pfad}: @context ist kein OpenVEX-Kontext")
    for feld in ("@id", "author"):
        if not isinstance(daten.get(feld), str) or not daten[feld].strip():
            raise NachweisFehler("vex", f"{pfad}: Pflichtfeld {feld} fehlt")
    version = daten.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise NachweisFehler("vex", f"{pfad}: version muss eine ganze Zahl ≥ 1 sein")
    dokumentzeit = _zeit(daten.get("timestamp"), f"{pfad} timestamp")
    aussagen = daten.get("statements")
    if not isinstance(aussagen, list):
        raise NachweisFehler("vex", f"{pfad}: statements muss eine Liste sein")
    return [_aussage(a, nr, dokumentzeit) for nr, a in enumerate(aussagen, 1)]


# --- Gültigkeit -----------------------------------------------------------------------------------

def _grund_allgemein(a: VexAussage, heute: date, lock_hashes: dict[str, str] | None
                     ) -> str | None:
    """Warum die Aussage unabhängig vom Treffer nicht freigibt (`None` = gültig)."""
    if a.status not in FREIGEBEND:
        return f"Status {a.status} gibt nicht frei"
    if a.status == "not_affected":
        if a.justification not in OPENVEX_BEGRUENDUNGEN:
            return f"justification „{a.justification}“ ist kein OpenVEX-Begründungscode"
        if len((a.impact_statement or "").strip()) < BEGRUENDUNG_MINDESTENS:
            return (f"impact_statement fehlt oder hat weniger als {BEGRUENDUNG_MINDESTENS} "
                    "Zeichen")
    tag = a.zeit.date()
    if tag > heute:
        return f"Zeitstempel {tag.isoformat()} liegt in der Zukunft"
    if (heute - tag).days > VERFALL_TAGE:
        return f"älter als {VERFALL_TAGE} Tage (vom {tag.isoformat()})"
    if not a.pruefer:
        return "scanview.pruefer fehlt"
    if a.lockdatei is None or a.lock_hash is None:
        return "scanview.lockdatei/lock_hash fehlt"
    if lock_hashes is None or a.lockdatei not in lock_hashes:
        return f"kein aktueller Hash für {a.lockdatei} bekannt"
    if lock_hashes[a.lockdatei] != a.lock_hash:
        return f"lock_hash passt nicht mehr zu {a.lockdatei} (Abhängigkeit geändert)"
    return None


def ist_image_inhalt(t: Treffer) -> bool:
    return t.bom_ref is not None and t.bom_ref.startswith(IMAGE_PRAEFIXE)


def lockdatei_von(t: Treffer) -> str | None:
    """Lockdatei, an die eine Aussage zum Treffer gebunden sein muss: bei Eigenbauten
    `eigenbau.toml`, sonst die Paket-Lockdatei des Artefakts (Image-Inhalt: keine)."""
    if t.artefakt not in LOCKDATEIEN or ist_image_inhalt(t):
        return None
    if t.bom_ref is not None and t.bom_ref.startswith(_EIGENBAU_PRAEFIX):
        return EIGENBAU_LOCKDATEI
    return LOCKDATEIEN[t.artefakt]


def _betrifft(a: VexAussage, t: Treffer) -> bool:
    """Aussage bezieht sich auf den Treffer: gleiche Schwachstelle (ID oder Alias des Treffers),
    Produkt = PURL des Treffers."""
    return a.schwachstelle in (t.id, *t.aliase) and \
        purl_schluessel(t.paket) in {purl_schluessel(p) for p in a.produkte}


def _massgeblich(t: Treffer, vex: Iterable[VexAussage]) -> list[VexAussage]:
    """Die für den Treffer maßgeblichen Aussagen: OpenVEX — die jüngste Aussage je
    (Schwachstelle, Produkt) gilt, hier zusätzlich je Lockdatei des Artefakts. Aussagen ohne
    Lockdatei-Angabe gelten für jedes Artefakt und können daher auch zurückziehen. Mehrere gleich
    alte Aussagen werden alle geliefert (Gleichstand)."""
    eigene = lockdatei_von(t)
    kandidaten = [a for a in vex if _betrifft(a, t) and a.lockdatei in (None, eigene)]
    if not kandidaten:
        return []
    neueste = max(a.zeit for a in kandidaten)
    return [a for a in kandidaten if a.zeit == neueste]


def _frei(t: Treffer, vex: Iterable[VexAussage], heute: date,
          lock_hashes: dict[str, str] | None) -> bool:
    """Frei nur, wenn der Treffer zu einem Artefakt mit Lockdatei gehört (kein Image-Inhalt)
    und **jede** maßgebliche (jüngste) Aussage für sich freigibt — eine neuere
    `under_investigation`/`affected` oder ungültige Aussage zieht eine ältere Freigabe zurück,
    ein Gleichstand mit widersprüchlichem Status blockiert."""
    eigene = lockdatei_von(t)
    if eigene is None:
        return False
    massgeblich = _massgeblich(t, vex)
    return bool(massgeblich) and all(
        a.lockdatei == eigene and _grund_allgemein(a, heute, lock_hashes) is None
        for a in massgeblich)


def _pruefe_schwere(t: Treffer) -> None:
    if t.schwere not in _SCHWEREN:
        raise NachweisFehler("scan-schwere", f"{t.id}: unbekannte Schwere „{t.schwere}“")


def treffer_gate(treffer: Iterable[Treffer], vex: list[VexAussage], heute: date, *,
                 lock_hashes: dict[str, str] | None = None) -> list[str]:
    """Blockierende Treffer (≥ hoch ohne gültige freigebende Aussage), je Zeile
    `"<ID> (<schwere>) <PURL>: keine freigebende VEX-Aussage"`, gleiche Zeilen einmal."""
    zeilen: list[str] = []
    for t in treffer:
        _pruefe_schwere(t)
        if t.schwere in _BLOCKIEREND and not _frei(t, vex, heute, lock_hashes):
            zeile = f"{t.id} ({t.schwere}) {t.paket}: keine freigebende VEX-Aussage"
            if zeile not in zeilen:
                zeilen.append(zeile)
    return zeilen


def scan_gate(treffer: Iterable[Treffer], vex: list[VexAussage], befunde: Iterable[Befund],
              heute: date, *, lock_hashes: dict[str, str] | None = None) -> list[str]:
    """Treffer-Gate plus Befund-Gate. Leere Liste = freigegeben."""
    return [*treffer_gate(treffer, vex, heute, lock_hashes=lock_hashes),
            *befund_gate(befunde, heute)]


def befund_vorschlaege(treffer: Iterable[Treffer], vex: list[VexAussage],
                       befunde: Iterable[Befund], heute: date, *,
                       lock_hashes: dict[str, str] | None = None) -> list[str]:
    """Treffer „mittel“ ohne gültige freigebende Aussage → Vorschlag `B-SCAN-<ID>` (nicht still
    ignoriert). Image-Inhalt wird je Image zu einem Sammelvorschlag `B-SCAN-IMG-<IMAGE>`
    zusammengefasst. Schon eingetragene IDs entfallen."""
    vorhanden = {b.id for b in befunde}
    zeilen: list[str] = []
    je_image: dict[str, list[str]] = {}
    for t in treffer:
        _pruefe_schwere(t)
        if t.schwere != "mittel" or _frei(t, vex, heute, lock_hashes):
            continue
        if ist_image_inhalt(t):
            image = (t.bom_ref or "").split("|", 1)[0].removeprefix("image:")
            ids = je_image.setdefault(image, [])
            if t.id not in ids:
                ids.append(t.id)
            continue
        kennung = f"B-SCAN-{t.id}"
        zeile = (f"{kennung}: {t.id} (mittel) {t.paket} im Artefakt {t.artefakt} — keine "
                 "freigebende VEX-Aussage, als Befund eintragen")
        if kennung not in vorhanden and zeile not in zeilen:
            zeilen.append(zeile)
    for image, ids in je_image.items():
        kennung = f"B-SCAN-IMG-{image.upper()}"
        if kennung not in vorhanden:
            zeilen.append(f"{kennung}: {len(ids)} Treffer mittel im Image-Inhalt {image} "
                          f"({', '.join(ids)}) — als Sammelbefund eintragen")
    return zeilen


def vex_meldungen(vex: Iterable[VexAussage], heute: date, *,
                  lock_hashes: dict[str, str] | None = None,
                  treffer: Iterable[Treffer] | None = None) -> list[str]:
    """Aussagen, die nach den Regeln als nicht vorhanden gelten, mit Grund. Mit `treffer`
    zusätzlich Aussagen ohne Wirkung: kein passender Treffer, Lockdatei gehört zu keinem Artefakt
    der passenden Treffer, oder nur Image-Inhalt betroffen (nicht per VEX freigebbar)."""
    alle = list(treffer) if treffer is not None else None
    meldungen: list[str] = []
    for a in vex:
        kopf = f"{a.schwachstelle}: Aussage ({a.status}, {', '.join(a.produkte)})"
        grund = _grund_allgemein(a, heute, lock_hashes)
        if grund is not None:
            meldungen.append(f"{kopf} gilt als nicht vorhanden — {grund}")
            continue
        if alle is None:
            continue
        passend = [t for t in alle if _betrifft(a, t)]
        eigene = [t for t in passend if not ist_image_inhalt(t)]
        artefakte = sorted({t.artefakt or "?" for t in eigene})
        if not passend:
            meldungen.append(f"{kopf} ohne Wirkung — passt zu keinem Treffer")
        elif not eigene:
            meldungen.append(f"{kopf} ohne Wirkung — betrifft nur Image-Inhalt (nicht per VEX "
                             "freigebbar)")
        elif all(lockdatei_von(t) != a.lockdatei for t in eigene):
            meldungen.append(f"{kopf} ohne Wirkung — Lockdatei {a.lockdatei} gehört zu keinem "
                             f"Artefakt der Treffer ({', '.join(artefakte)})")
    return meldungen


def lock_hashes_aus(lesen: Callable[[str], bytes | None]) -> dict[str, str]:
    """SHA-256 aller Lockdateien (`LOCKDATEIEN`); `lesen` liefert den Inhalt je Pfad relativ zur
    Wurzel (`None`, wenn es die Datei nicht gibt) — aus einem Worktree oder einem Commit.
    Fehlt eine → `scan-lock`."""
    hashes: dict[str, str] = {}
    for pfad in LOCKDATEIEN.values():
        inhalt = lesen(pfad)
        if inhalt is None:
            raise NachweisFehler("scan-lock", f"{pfad}: nicht lesbar oder nicht vorhanden")
        hashes[pfad] = "sha256:" + hashlib.sha256(inhalt).hexdigest()
    eigenbau = lesen(EIGENBAU_LOCKDATEI)   # optional: ältere Stände haben keine Eigenbauten
    if eigenbau is not None:
        hashes[EIGENBAU_LOCKDATEI] = "sha256:" + hashlib.sha256(eigenbau).hexdigest()
    return hashes


def lock_hashes(wurzel: Path) -> dict[str, str]:
    """SHA-256 aller Lockdateien (`LOCKDATEIEN`) unter `wurzel` — im Release der eingefrorene
    Worktree; fehlt eine → `scan-lock`."""
    def lesen(pfad: str) -> bytes | None:
        try:
            return (wurzel / pfad).read_bytes()
        except OSError:
            return None

    return lock_hashes_aus(lesen)
