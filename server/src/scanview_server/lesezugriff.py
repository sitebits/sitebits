"""Lesezugriffsprotokoll: wer hat wann welches Projekt gelesen (Tabelle `lesezugriffe`).

Zweck ist allein die Nachvollziehbarkeit des Need-to-know und die Aufklärung von
Sicherheitsvorfällen — keine Leistungs- oder Verhaltenskontrolle. Die Einträge sind
personenbezogen und deshalb datensparsam, aggregiert, unveränderlich und befristet:

- Inhalt: Konto (`nutzer_id`, Name wird erst bei der Anzeige über `users` aufgelöst, ein
  anonymisiertes Konto erscheint als „Gelöschtes Konto n“), Zeit, Projekt, Art, Objekt, Kanal
  (`web`/`api`) und bei Token-Zugriff nur der Token-NAME. Nie IP, User-Agent, E-Mail, Sitzungs-
  Token oder Token-Wert/-Hash.
- Aggregiert: eine Zeile je Konto, Projekt, Art, Objekt, Kanal, Token-Name und Zeitfenster
  (`FENSTER_S`); `zeit` ist der erste Zugriff im Fenster. Einzelne Range-Anfragen werden nicht
  gezählt. Ein prozessweiter `Merker` hält die schon geschriebenen Schlüssel des laufenden
  Fensters, damit `/api/authz` im heißen Pfad nur beim ersten Zugriff je Fenster schreibt.
- Unveränderlich: Trigger (Migration 0007) verbieten UPDATE und Ersetzen; gelöscht wird nur in
  `aufraeumen` nach der Aufbewahrungsfrist `SCANVIEW_LESEZUGRIFFE_TAGE` (0 = Protokoll aus).

Abgrenzung zu `protokoll` (`ereignisse`): dort stehen Änderungen, sie werden nie gelöscht und
gehen in den Git-Export; hier stehen Lesezugriffe, sie werden nach der Frist gelöscht und
gelangen nie in den Git-Export.

Erfasst wird nur nach erfolgreicher Rechteprüfung, nie bei 401, 403 oder 404. Bewusst nicht
erfasst: die Projektliste `/api/v1/projekte` (nur Karten, kein Projektinhalt) und das globale
Admin-Protokoll `/api/v1/protokoll`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .protokoll import SEITE_MAX, SEITE_STANDARD

if TYPE_CHECKING:
    from fastapi import Request

    from .zugriff import Zugriff

log = logging.getLogger("scanview.lesezugriff")
FENSTER_S = 15 * 60
ARTEN = ("oeffnen", "paket", "export", "api")
KANAELE = ("web", "api")
AUFRAEUMEN_ABSTAND_S = 3600
_MERKER_MAX = 20_000


@dataclass(frozen=True)
class Lesezugriff:
    id: int
    zeit: int
    nutzer_id: int
    nutzer_name: str | None
    kanal: str
    token: str
    project_id: str
    art: str
    objekt: str

    def als_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "zeit": self.zeit, "nutzerId": self.nutzer_id,
            "nutzer": self.nutzer_name, "kanal": self.kanal, "token": self.token or None,
            "projekt": self.project_id, "art": self.art, "objekt": self.objekt,
        }


class Merker:
    """Schon geschriebene Schlüssel des laufenden Fensters (threadsicher, begrenzt) und der
    Zeitpunkt des letzten Aufräumens. Verlust (Neustart, Begrenzung) ist harmlos: der nächste
    Zugriff versucht ein `INSERT OR IGNORE`, das die UNIQUE-Regel ohnehin abweist."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fenster = -1
        self._schluessel: set[tuple[object, ...]] = set()
        self._aufgeraeumt = 0

    def bekannt(self, fenster: int, schluessel: tuple[object, ...]) -> bool:
        with self._lock:
            return fenster == self._fenster and schluessel in self._schluessel

    def merken(self, fenster: int, schluessel: tuple[object, ...]) -> None:
        with self._lock:
            if fenster != self._fenster or len(self._schluessel) >= _MERKER_MAX:
                self._fenster = fenster
                self._schluessel = set()
            self._schluessel.add(schluessel)

    def aufraeumen_faellig(self, jetzt: int) -> bool:
        with self._lock:
            if jetzt - self._aufgeraeumt < AUFRAEUMEN_ABSTAND_S:
                return False
            self._aufgeraeumt = jetzt
            return True


def melde_start(tage: int, geloescht: int) -> None:
    """Eine Logzeile beim Dienststart: wirksamer Wert und Zahl der beim Aufräumen gelöschten
    Einträge. Bei Frist 0 als WARNING — das Protokoll ist abgeschaltet und vorhandene Einträge
    sind unwiderruflich gelöscht (vorher den JSON-Lines-Export sichern)."""
    if tage == 0:
        log.warning(
            "Lesezugriffsprotokoll ist abgeschaltet (SCANVIEW_LESEZUGRIFFE_TAGE=0); "
            "%d vorhandene Einträge wurden gelöscht",
            geloescht,
        )
    else:
        log.info(
            "Lesezugriffsprotokoll: an, Aufbewahrung %d Tage, %d abgelaufene Einträge gelöscht",
            tage, geloescht,
        )


def _jetzt(now: int | None = None) -> int:
    return int(time.time()) if now is None else now


def erfassen(
    conn: sqlite3.Connection, *, nutzer_id: int, project_id: str, art: str, kanal: str,
    token_name: str | None, objekt: str = "", now: int | None = None,
    merker: Merker | None = None, tage: int | None = None,
) -> None:
    """Hält einen Lesezugriff fest (höchstens eine Zeile je Schlüssel und Fenster).

    Mit `merker` schreibt nur der erste Zugriff je Schlüssel und Fenster; mit `merker` und
    `tage` räumt der Aufruf höchstens einmal je Stunde abgelaufene Einträge weg, damit auch ein
    lange laufender Dienst nicht nur beim Start löscht."""
    if art not in ARTEN:
        raise ValueError(f"Lesezugriffsart {art!r} unbekannt.")
    if kanal not in KANAELE:
        raise ValueError(f"Kanal {kanal!r} unbekannt.")
    jetzt = _jetzt(now)
    fenster = jetzt // FENSTER_S * FENSTER_S
    name = token_name or ""
    schluessel = (nutzer_id, project_id, art, objekt, kanal, name)
    if merker is not None:
        if tage is not None and merker.aufraeumen_faellig(jetzt):
            aufraeumen(conn, tage, now=jetzt)
        if merker.bekannt(fenster, schluessel):
            return
    conn.execute(
        "INSERT OR IGNORE INTO lesezugriffe (zeit, fenster, nutzer_id, kanal, token_name,"
        " project_id, art, objekt) VALUES (?,?,?,?,?,?,?,?)",
        (jetzt, fenster, nutzer_id, kanal, name, project_id, art, objekt),
    )
    if merker is not None:
        merker.merken(fenster, schluessel)


def erfasse_nutzer(
    request: Request, conn: sqlite3.Connection, nutzer_id: int, project_id: str, art: str,
    objekt: str = "",
) -> None:
    """Kanal und Token-Name aus der Anfrage; nichts, wenn das Protokoll abgeschaltet ist."""
    # Lazy: `web` importiert `konten`, und das ruft `aufraeumen` hier — sonst ein Importzyklus.
    from .web import kanal, token_name

    tage: int = request.app.state.settings.lesezugriffe_tage
    if tage == 0:
        return
    erfassen(
        conn, nutzer_id=nutzer_id, project_id=project_id, art=art, kanal=kanal(request),
        token_name=token_name(request), objekt=objekt,
        merker=request.app.state.lesemerker, tage=tage,
    )


def erfasse_anfrage(
    request: Request, conn: sqlite3.Connection, z: Zugriff, art: str, objekt: str = "",
) -> None:
    """Lesezugriff des geprüften Nutzers auf das Projekt der Zugriffsprüfung `z`."""
    erfasse_nutzer(request, conn, z.nutzer.id, z.karte.id, art, objekt)


def aufraeumen(conn: sqlite3.Connection, tage: int, now: int | None = None) -> int:
    """Löscht Einträge, die älter sind als die Aufbewahrungsfrist — der einzige Löschweg."""
    return conn.execute(
        "DELETE FROM lesezugriffe WHERE zeit < ?", (_jetzt(now) - tage * 86400,),
    ).rowcount


def _zeile(r: sqlite3.Row) -> Lesezugriff:
    return Lesezugriff(
        id=int(r["id"]), zeit=int(r["zeit"]), nutzer_id=int(r["nutzer_id"]),
        nutzer_name=r["nutzer_name"], kanal=r["kanal"], token=r["token_name"],
        project_id=r["project_id"], art=r["art"], objekt=r["objekt"],
    )


# Feste SQL-Texte ohne zusammengesetzte Teile: ein nicht gesetzter Filter ist NULL und schließt
# nichts aus; alle Werte sind ?-Parameter.


def lesen(
    conn: sqlite3.Connection, *, project_id: str | None = None, nutzer_id: int | None = None,
    vor: int | None = None, limit: int = SEITE_STANDARD,
) -> list[Lesezugriff]:
    """Neueste zuerst, seitenweise über die ID (nur für Admins — Sichtbarkeit regeln die Routen)."""
    rows = conn.execute(
        "SELECT l.*, u.name AS nutzer_name FROM lesezugriffe l"
        " LEFT JOIN users u ON u.id = l.nutzer_id"
        " WHERE (?1 IS NULL OR l.project_id = ?1) AND (?2 IS NULL OR l.nutzer_id = ?2)"
        " AND (?3 IS NULL OR l.id < ?3) ORDER BY l.id DESC LIMIT ?4",
        (project_id, nutzer_id, vor, max(1, min(limit, SEITE_MAX))),
    )
    return [_zeile(r) for r in rows.fetchall()]


def projekte(
    conn: sqlite3.Connection, *, project_id: str | None = None, nutzer_id: int | None = None,
) -> set[str]:
    """Die Projekte, die im Ergebnis von `jsonl` mit denselben Filtern vorkommen."""
    rows = conn.execute(
        "SELECT DISTINCT l.project_id FROM lesezugriffe l"
        " WHERE (?1 IS NULL OR l.project_id = ?1) AND (?2 IS NULL OR l.nutzer_id = ?2)",
        (project_id, nutzer_id),
    )
    return {r[0] for r in rows}


def jsonl(
    conn: sqlite3.Connection, *, project_id: str | None = None, nutzer_id: int | None = None,
) -> str:
    """Export als JSON Lines: alle passenden Einträge, älteste zuerst (Auskunft nach Art. 15
    DSGVO: gefiltert nach Konto)."""
    rows = conn.execute(
        "SELECT l.*, u.name AS nutzer_name FROM lesezugriffe l"
        " LEFT JOIN users u ON u.id = l.nutzer_id"
        " WHERE (?1 IS NULL OR l.project_id = ?1) AND (?2 IS NULL OR l.nutzer_id = ?2)"
        " ORDER BY l.id ASC",
        (project_id, nutzer_id),
    ).fetchall()
    return "".join(json.dumps(_zeile(r).als_json(), ensure_ascii=False) + "\n" for r in rows)
