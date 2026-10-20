# scanview-server

Anmeldung (Passkey/Passwort), Rechte und Projektkarten für die scanview-Plattform. FastAPI +
SQLite, ein Dienst je Instanz, lauscht nur auf `127.0.0.1:8803`.

Einrichten: `uv sync --locked`. Start: `uv run scanview-server serve`.

Umgebungsvariablen (systemd `EnvironmentFile`): `SCANVIEW_DATA` (Paketwurzel, nur lesend),
`SCANVIEW_STATE` (SQLite-Datenbank), `SCANVIEW_ORIGIN` (z. B. `https://scanview.example.de`),
`SCANVIEW_RP_ID` (WebAuthn Relying-Party-ID, i. d. R. der Hostname), `SCANVIEW_RP_NAME`
(Anzeigename, Default `scanview`), `SCANVIEW_COOKIE_SECURE` (`1`/`0`, Default `1`).
