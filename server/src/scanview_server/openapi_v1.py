"""OpenAPI-Beschreibung der API v1: Sichten und Erzeuger der öffentlichen Datei.

Eine Quelle für zwei Wege:
- `GET /api/v1/openapi.json` (routen_v1, nur angemeldet) zeigt je Anfrage die Sicht des Kontos.
- `oeffentliche_beschreibung` erzeugt offline die Datei für Skripte und Agenten
  (`docs/public/api/openapi.json`, daraus die TypeScript-Typen des Clients `sitebits`): genau die
  Operationen, die ein API-Token erreichen kann. Der Dienst selbst liefert keine öffentliche
  OpenAPI-Route aus (`openapi_url=None` in `web.create_app`).

Neu erzeugen (aus `server/`):
    uv run --locked python -m scanview_server.openapi_v1 ../docs/public/api/openapi.json
oder, samt TypeScript-Typen, in `clients/sitebits/`: `npm run typen`.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .web import LESEZUGRIFF_PFADE, LESEZUGRIFF_PROJEKT_RE, bearer_pfad

#: Der eine Befehl nach jeder API-Änderung: erzeugt OpenAPI-Datei und TypeScript-Typen des Clients.
ERZEUGER = "cd clients/sitebits && npm run typen"

#: Projektverwaltung über `zugriff.leitung`: nur per Sitzung im Browser, per Token immer 403
#: („Projektverwaltung nur im Browser, nicht per API-Token.“). Die Laufzeit-Sicht eines Tokens
#: nennt sie trotzdem (sie liegen auf Bearer-Pfaden); in die öffentliche Datei gehören sie nicht,
#: sonst hätte der Client Funktionen, die nie gelingen. `tests/test_openapi_datei.py` prüft gegen
#: die echte App, dass jede hier genannte Operation per Token abgelehnt wird und keine andere.
NUR_BROWSER: frozenset[tuple[str, str]] = frozenset({
    ("put", "/api/v1/projekte/{pid}/beteiligte/{uid}"),
    ("post", "/api/v1/projekte/{pid}/gewerke"),
    ("patch", "/api/v1/projekte/{pid}/gewerke/{gid}"),
    ("delete", "/api/v1/projekte/{pid}/gewerke/{gid}"),
    ("get", "/api/v1/projekte/{pid}/einladungen"),
    ("post", "/api/v1/projekte/{pid}/einladungen"),
    ("post", "/api/v1/projekte/{pid}/einladungen/{eid}/widerrufen"),
    ("patch", "/api/v1/projekte/{pid}/einstellungen"),
    ("put", "/api/v1/projekte/{pid}/kategorien"),
    ("post", "/api/v1/projekte/{pid}/einrichten"),
    ("get", "/api/v1/projekte/{pid}/teilen"),
    ("post", "/api/v1/projekte/{pid}/teilen"),
    ("delete", "/api/v1/projekte/{pid}/teilen/{lid}"),
})

_METHODEN = frozenset({"get", "put", "post", "delete", "patch", "head", "options", "trace"})


def ref_namen(node: Any) -> set[str]:
    """Alle Schema-Namen, auf die `$ref` in diesem Teilbaum verweist (rekursiv über dict/list)."""
    gefunden: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            gefunden.add(ref.rsplit("/", 1)[1])
        for v in node.values():
            gefunden |= ref_namen(v)
    elif isinstance(node, list):
        for v in node:
            gefunden |= ref_namen(v)
    return gefunden


def v1_schemas(pfade: dict[str, Any], alle: dict[str, Any]) -> dict[str, Any]:
    """Nur die Schemas, die von den v1-Pfaden erreichbar sind (transitiv über `$ref`) —
    `/auth/*`-Schemas (AnmeldenIn, TokenIn, …) sollen im v1-Dokument nicht auftauchen."""
    namen = ref_namen(pfade)
    rand = set(namen)
    while rand:
        weiter: set[str] = set()
        for name in rand:
            weiter |= ref_namen(alle.get(name, {}))
        rand = weiter - namen
        namen |= rand
    return {n: alle[n] for n in sorted(namen) if n in alle}


def sichtbare_pfade(pfade: dict[str, Any], ist_admin: bool) -> dict[str, Any]:
    """`/api/v1/admin/*`, das globale Protokoll (`/api/v1/protokoll`, `.../protokoll.jsonl`) und
    das Lesezugriffsprotokoll tauchen im OpenAPI-Dokument für Nicht-Admins nicht auf — sonst
    verrät das Dokument genau die Routennamen, die die 404-Antwort sonst verbirgt. Der
    projektbezogene Verlauf (`/api/v1/projekte/{pid}/protokoll…`) bleibt sichtbar, er ist nicht
    admin-exklusiv (Bearbeiter/Admin je Projekt)."""
    if ist_admin:
        return pfade
    return {
        k: v for k, v in pfade.items()
        if not k.startswith("/api/v1/admin/")
        and k not in ("/api/v1/protokoll", "/api/v1/protokoll.jsonl")
        and k not in LESEZUGRIFF_PFADE and not LESEZUGRIFF_PROJEKT_RE.match(k)
    }


def token_pfade(schema: dict[str, Any]) -> dict[str, Any]:
    """Pfade unter `/api/v1/`, an denen ein Bearer-Token anmelden darf (ohne Verwaltung,
    Statistik, Konto) — ohne Admin-Sicht, wie die Laufzeit-Sicht eines Tokens."""
    pfade = {k: v for k, v in schema.get("paths", {}).items()
             if k.startswith("/api/v1/") and bearer_pfad(k)}
    return sichtbare_pfade(pfade, ist_admin=False)


SICHERHEIT_TOKEN = {"bearerAuth": {"type": "http", "scheme": "bearer"}}


def oeffentliche_beschreibung(app: FastAPI) -> dict[str, Any]:
    """Öffentliche Beschreibung: Token-Sicht ohne die Operationen aus `NUR_BROWSER`. Baut nur
    neue Dicts — das zwischengespeicherte `app.openapi()` bleibt unverändert."""
    schema: dict[str, Any] = app.openapi()
    pfade: dict[str, Any] = {}
    for pfad, ops in token_pfade(schema).items():
        rest = {m: op for m, op in ops.items()
                if m not in _METHODEN or (m, pfad) not in NUR_BROWSER}
        if any(m in _METHODEN for m in rest):
            pfade[pfad] = rest
    komponenten = schema.get("components", {})
    return {
        "openapi": schema["openapi"],
        "info": {
            "title": "siteBits API",
            "version": "1",
            "description": ("Öffentliche API v1 einer siteBits-Instanz: alle Operationen, die "
                            "ein persönliches API-Token erreicht. Anmeldung per "
                            "`Authorization: Bearer <Token>`."),
        },
        "paths": pfade,
        "components": {
            "schemas": v1_schemas(pfade, komponenten.get("schemas", {})),
            "securitySchemes": dict(SICHERHEIT_TOKEN),
        },
        "security": [{"bearerAuth": []}],
    }


def als_text(beschreibung: dict[str, Any]) -> str:
    """Feste Form (sortierte Schlüssel, zwei Leerzeichen, UTF-8, Zeilenende) für die
    Drift-Prüfung: gleiche App → byte-gleiche Datei."""
    return json.dumps(beschreibung, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def erzeugen() -> str:
    """Die App mit leerer Wegwerf-Datenwurzel bauen und ihre öffentliche Beschreibung liefern."""
    from .config import Settings
    from .web import create_app

    with tempfile.TemporaryDirectory(prefix="sitebits-openapi-") as tmp:
        wurzel = Path(tmp)
        (wurzel / "data").mkdir()
        (wurzel / "state").mkdir()
        app = create_app(Settings(
            data_dir=wurzel / "data", state_dir=wurzel / "state", origin="https://beispiel.invalid",
            rp_id="beispiel.invalid", rp_name="siteBits",
        ))
        return als_text(oeffentliche_beschreibung(app))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m scanview_server.openapi_v1",
        description="Öffentliche OpenAPI-Beschreibung der API v1 erzeugen.")
    p.add_argument("ziel", type=Path, help="Zieldatei, z. B. ../docs/public/api/openapi.json")
    p.add_argument("--pruefen", action="store_true",
                   help="nichts schreiben, nur Abweichung melden (Exit 1)")
    args = p.parse_args(argv)
    text = erzeugen()
    ziel: Path = args.ziel
    if args.pruefen:
        alt = ziel.read_text(encoding="utf-8") if ziel.is_file() else None
        if alt != text:
            print(f"{ziel} passt nicht zur App. Neu erzeugen (Repo-Wurzel): {ERZEUGER}",
                  file=sys.stderr)
            return 1
        return 0
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
