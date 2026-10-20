# 0004 Plattform-Server in Python mit SQLite

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Aus dem statischen Viewer wird eine gehostete Plattform mit Konten, Rechten je Projekt,
serverseitigen POIs und Messungen. Die Zahl der Nutzer ist klein, die großen Dateien liefert der
Webserver. Betrieben wird auf kleinen Linux-Hosts ohne Container-Plattform.

## Entscheidung

- Ein Dienst `scanview-server`: **FastAPI + uvicorn in einem Prozess**, gebunden an Loopback.
- Zustand in **SQLite** (stdlib `sqlite3`, WAL, `foreign_keys=ON`, kein ORM), Schemaänderungen als
  nummerierte SQL-Migrationen.
- Abhängigkeiten nur `fastapi`, `uvicorn`, `webauthn`, `argon2-cffi` (+ transitive, alle
  permissiv); Lockfile mit Hashes, Bau nur aus Wheels, fester Stichtag für neue Versionen.
- Betrieb als gehärtete systemd-Unit unter eigenem Systemnutzer ohne Netz nach außen.
- Verworfen: Node/Fastify (zweiter Stack), Authelia/Keycloak (Rechte je Projekt müssten trotzdem
  selbst gebaut werden), Redis/Postgres/Docker (nicht nötig in dieser Größe).

## Konsequenzen

- Einfacher Betrieb und Sicherung (`sqlite3 .backup`), keine weitere Datenbank.
- Ein Prozess heißt: In-Memory-Drosselungen gelten je Prozess und setzen bei Neustart zurück;
  mehrere Worker wären eine neue Entscheidung.
- Die Werkzeugkette (uv, ruff, mypy, pytest) ist dieselbe wie im Import-Werkzeug.
