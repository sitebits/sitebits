"""Bearbeitungsprotokoll: append-only Tabelle `ereignisse`.

Geschrieben wird nur über `ereignis()` — in derselben Transaktion wie die Änderung selbst
(db.transaktion). UPDATE/DELETE verbieten SQLite-Trigger (Migration 0003), auch direkter
SQL-Zugriff kann nichts umschreiben. `INSERT OR REPLACE` (löscht intern erst die Konfliktzeile)
fängt ein eigener Trigger `ereignisse_kein_ersetzen` ab, der jede Einfügung mit schon vorhandener
id verbietet — unabhängig von `PRAGMA recursive_triggers`, also auch in der sqlite3-Shell oder
über ein bloßes `sqlite3.connect` (Final-Fix C/D; `db.connect` setzt das Pragma zusätzlich, dann
griffe auch der DELETE-Trigger). Datenschutz: Nutzer-ID statt Name im Datensatz (die
Anzeige löst über `users` auf), keine IP, kein User-Agent und nie E-Mail-Adressen, Namen, Tokens
oder Token-Hashes in vorher/nachher/objekt_id — die Tabelle wird nie gelöscht.
Ausnahme (Plan, Global Constraints): `token.erstellt`/`token.widerrufen` (apitoken.py) tragen in
`nachher` den vom Nutzer gewählten Token-NAMEN — nie den Token-Wert oder dessen Hash.
Seit Migration 0005 hält Spalte `token_name` bei per API-Token ausgelösten Schreibvorgängen
(kanal='api') nur den Namen des Tokens (nie Wert oder Hash) — welches Gerät/Skript geschrieben
hat, ohne das Konto weiter zu identifizieren als über `nutzer_id` ohnehin.

Abgrenzung zu `lesezugriff` (Tabelle `lesezugriffe`): hier stehen Änderungen — nie gelöscht, im
Git-Export; dort stehen Lesezugriffe — nach der Aufbewahrungsfrist gelöscht, nie im Git-Export.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

KANAELE = ("web", "cli", "api")
_ART_RE = re.compile(r"[a-z]+\.[a-z_]+")
SEITE_STANDARD = 50
SEITE_MAX = 200
_SELECT = (
    "SELECT e.*, u.name AS nutzer_name FROM ereignisse e LEFT JOIN users u ON u.id = e.nutzer_id"
)


@dataclass(frozen=True)
class Ereignis:
    id: int
    zeit: int
    nutzer_id: int | None
    nutzer_name: str | None
    kanal: str
    project_id: str | None
    art: str
    objekt_id: str | None
    vorher: Any
    nachher: Any
    # Name des API-Tokens, das die Änderung ausgelöst hat (kanal='api') — nie der
    # Token-Wert oder -Hash, siehe apitoken.py.
    token: str | None = None

    def als_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "zeit": self.zeit, "nutzerId": self.nutzer_id,
            "nutzer": self.nutzer_name, "kanal": self.kanal, "projekt": self.project_id,
            "art": self.art, "objekt": self.objekt_id, "vorher": self.vorher,
            "nachher": self.nachher, "token": self.token,
        }


def _json(wert: Any) -> str | None:
    if wert is None:
        return None
    return json.dumps(wert, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ereignis(
    conn: sqlite3.Connection, *, kanal: str, art: str, nutzer_id: int | None = None,
    project_id: str | None = None, objekt_id: str | None = None, vorher: Any = None,
    nachher: Any = None, token_name: str | None = None, now: int | None = None,
) -> int:
    if kanal not in KANAELE:
        raise ValueError(f"Kanal {kanal!r} unbekannt.")
    if not _ART_RE.fullmatch(art):
        raise ValueError(f"Ereignisart {art!r} ungültig.")
    zeit = int(time.time()) if now is None else now
    cur = conn.execute(
        "INSERT INTO ereignisse (zeit, nutzer_id, kanal, project_id, art, objekt_id, vorher,"
        " nachher, token_name) VALUES (?,?,?,?,?,?,?,?,?)",
        (zeit, nutzer_id, kanal, project_id, art, objekt_id, _json(vorher), _json(nachher),
         token_name),
    )
    if cur.lastrowid is None:
        raise RuntimeError("Ereignis ohne ID")
    return int(cur.lastrowid)


def _zeile(r: sqlite3.Row) -> Ereignis:
    return Ereignis(
        id=int(r["id"]), zeit=int(r["zeit"]), nutzer_id=r["nutzer_id"],
        nutzer_name=r["nutzer_name"], kanal=r["kanal"], project_id=r["project_id"],
        art=r["art"], objekt_id=r["objekt_id"],
        vorher=None if r["vorher"] is None else json.loads(r["vorher"]),
        nachher=None if r["nachher"] is None else json.loads(r["nachher"]),
        token=r["token_name"],
    )


# Befund-Faden: Ereignisse interner Einträge (eintrag.angelegt/.zurueckgezogen mit intern=true in
# `nachher`) — im SQL ausgeblendet, damit die Seitengröße und `weiter` stimmen.
_OHNE_INTERN = "NOT (e.art LIKE 'eintrag.%' AND json_extract(e.nachher, '$.intern') = 1)"


def _bedingungen(
    project_id: str | None, vor: int | None, arten: tuple[str, ...] | None,
    ohne_intern: bool = False,
) -> tuple[str, list[Any]]:
    teile: list[str] = []
    werte: list[Any] = []
    if ohne_intern:
        teile.append(_OHNE_INTERN)
    if project_id is not None:
        teile.append("e.project_id = ?")
        werte.append(project_id)
    if vor is not None:
        teile.append("e.id < ?")
        werte.append(vor)
    if arten:
        teile.append("(" + " OR ".join("e.art LIKE ?" for _ in arten) + ")")
        werte.extend(f"{a}.%" for a in arten)
    return (" WHERE " + " AND ".join(teile)) if teile else "", werte


def lesen(
    conn: sqlite3.Connection, *, project_id: str | None = None, vor: int | None = None,
    limit: int = SEITE_STANDARD, arten: tuple[str, ...] | None = None,
    ohne_intern: bool = False,
) -> list[Ereignis]:
    """Neueste zuerst, seitenweise über die ID. project_id=None liest alles (nur Admin).
    `ohne_intern`: Ereignisse interner Faden-Einträge weglassen (Nicht-Bauherrenseite)."""
    grenze = max(1, min(limit, SEITE_MAX))
    wo, werte = _bedingungen(project_id, vor, arten, ohne_intern)
    rows = conn.execute(f"{_SELECT}{wo} ORDER BY e.id DESC LIMIT ?", (*werte, grenze))
    return [_zeile(r) for r in rows.fetchall()]


def jsonl(
    conn: sqlite3.Connection, *, project_id: str | None = None,
    arten: tuple[str, ...] | None = None, ohne_intern: bool = False,
) -> str:
    """Export als JSON Lines: alle passenden Ereignisse, älteste zuerst."""
    wo, werte = _bedingungen(project_id, None, arten, ohne_intern)
    rows = conn.execute(f"{_SELECT}{wo} ORDER BY e.id ASC", werte).fetchall()
    return "".join(json.dumps(_zeile(r).als_json(), ensure_ascii=False) + "\n" for r in rows)
