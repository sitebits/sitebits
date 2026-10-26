# siteBits

**English** · [Deutsch](README.de.md)

Source: [GitHub](https://github.com/sitebits/sitebits) · [Issues](https://github.com/sitebits/sitebits/issues) · [Releases](https://github.com/sitebits/sitebits/releases) · [Code map](https://sitebits.de/code/)

siteBits shows laser scans in the browser: point clouds, panoramas of the scan stations,
measurements and pins, served from a self-hosted platform with invitations, passkey sign-in and
per-project permissions. Scan data is prepared once into project packages and then opens in the
browser without additional software.

The user interface is currently in German; English is planned as the second language.

Code identifiers (packages, commands, environment variables, paths) still use the working name
`scanview`.

## Features

- **Viewer:** COPC point clouds with levels of detail, jumping between stations with panoramas,
  measuring (distance, height, area, polyline), pins with notes, top view and minimap.
- **Platform:** accounts by invitation only, passkey sign-in (password as fallback), roles viewer,
  reporter and editor per project, administration in the portal, edit history, export as CSV and
  JSON.
- **API and tools:** REST API under `/api/v1/…` with an OpenAPI description, personal API tokens
  and an MCP server through which AI assistants can work with the permissions of the user's own
  account.

## Architecture

```
Browser ──HTTPS──> Caddy ──┬─ /api, /auth ──────> scanview-server (Python, SQLite, loopback only)
                           ├─ /data/<project> ──> access check per file, then delivery
                           └─ portal, viewer ───> static web bundle
MCP server (user side) ──HTTPS + API token──> /api/v1
```

| Directory | Contents |
|---|---|
| `web/` | Viewer and portal (TypeScript, Vite, Three.js/Giro3D) |
| `server/` | Platform server (Python, FastAPI, SQLite) |
| `mcp-server/` | MCP server for AI assistants (Python) |
| `deploy/platform/` | Installation and operation: systemd units, Caddy configuration, scripts |
| `nachweise/` | Release chain: SBOM, vulnerability scan, signing, public export |
| `docs/` | [Architecture](docs/architektur.md) and [decisions](docs/entscheidungen/README.md) (ADRs) |
| `docs/public/` | Product documents (security, operation, support, release process) |

The documents under `docs/` are in German. Components, package format and data flows:
[architecture](docs/architektur.md); trust boundaries and threats:
[security architecture](docs/public/sicherheitsarchitektur.md); reasons behind the main
decisions: [decisions](docs/entscheidungen/README.md).

## Quick start (development)

Requirements: Node.js ≥ 22.12 with npm, Python 3.11 or 3.12, [uv](https://docs.astral.sh/uv/).

```bash
# Viewer and portal (from the repository root)
cd web
npm ci
npm run typecheck && npm run lint && npm test
npm run dev                      # http://localhost:5173

# Platform server (second terminal); packages under <data-dir>/<projectId>/
cd server
uv sync --locked
uv run pytest
SCANVIEW_DATA=<data-dir> SCANVIEW_STATE=<state-dir> \
SCANVIEW_ORIGIN=http://localhost:5173 SCANVIEW_RP_ID=localhost \
  uv run scanview-server serve    # 127.0.0.1:8803, the Vite dev server proxies /api and /auth
SCANVIEW_DATA=… SCANVIEW_STATE=… SCANVIEW_ORIGIN=… SCANVIEW_RP_ID=… \
  uv run scanview-server einladen --name Test --email test@example.org --admin

# MCP server and release chain (from the repository root)
(cd mcp-server && uv sync --locked && uv run pytest)
(cd nachweise && uv sync --locked && uv run pytest)
```

Importing point clouds, spherical panoramas and photos: [import guide](import/README.md).

Working with mesh layers: [layers, framing and limits](docs/public/mesh-ebenen.md).

Sun and shadows in the viewer and the sun report (German): [sun guide](docs/public/sonne.md).

Setting up a server: [operations guide](docs/public/betrieb.md).

## Information under Annex II of the Cyber Resilience Act

| Item | |
|---|---|
| **Manufacturer** | Stiebitz Souverain Systems UG (haftungsbeschränkt), Potsdam, Germany, Local Court (Amtsgericht) Potsdam HRB 42359 P. The postal address will be added when the product is placed on the market; until then via the contact point. |
| **Contact point for vulnerabilities** | security@sitebits.de, reporting process and deadlines in [`SECURITY.md`](SECURITY.md) |
| **Product** | siteBits, version 0.12.0 (viewer/portal, platform server and MCP server carry the same version; `GET /api/v1/version` shows the installed version to signed-in users) |
| **Intended use** | Viewing, measuring and jointly working on laser scan projects in the browser on a self-hosted platform, accessible only to invited users with permission for the project |
| **Environment** | Linux server with Caddy as reverse proxy and HTTPS; current browsers with WebGL 2 and WebAssembly |
| **Security notes** | Operate over HTTPS only; apply the hardening in the [operations guide](docs/public/betrieb.md#härtung); share invitation links and API tokens only over confidential channels; create API tokens with the smallest scope needed; with the MCP server, tool results go to the provider of the language model (see `mcp-server/README.md`); measured values depend on the accuracy of the scan data and do not replace an official survey |
| **Security updates** | free of charge as a signed patch release of the latest minor version; deadlines and upgrade path in [support and patches](docs/public/support-und-patches.md), notes per version in the [CHANGELOG](CHANGELOG.md) |
| **End of support** | until the legal classification is settled: latest version, no end date promised; the end date will be set when the product is placed on the market and stated here ([support and patches](docs/public/support-und-patches.md#supportzeitraum)) |
| **Software bill of materials (SBOM)** | per release and shipped artefact in CycloneDX 1.6 following BSI TR-03183-2 (`scanview-web-<version>.cdx.json`, `scanview-server-<version>.cdx.json`, `scanview-mcp-<version>.cdx.json`), published with the release artefacts |
| **Instructions** | Installation, update, backup, deleting data and removing the installation: [operations guide](docs/public/betrieb.md) |
| **Legal classification** | [Applicability](docs/public/anwendbarkeit.md), [control matrix](docs/public/kontrollmatrix.md) |

## Security

Please report vulnerabilities confidentially as described in [`SECURITY.md`](SECURITY.md). How
releases are checked, signed and made traceable is described in the
[release process](docs/public/release.md); open and resolved findings are listed in the
[findings register](docs/public/befunde.md).
Answers for security questionnaires from clients, each with evidence and openly stated gaps:
[answer template](docs/public/sicherheitsfragebogen.md) (German).

## Licence

Copyright © 2026 Stiebitz Souverain Systems UG (haftungsbeschränkt). Author: Andre Stiebitz.

siteBits is licensed under the European Union Public Licence v. 1.2 (`EUPL-1.2`), see
[`LICENSE`](LICENSE). Anyone who distributes siteBits or a modified version, or makes it
available as a network service, must provide the source code of that version (EUPL Articles 1
and 5). Licences of third-party components: [`docs/THIRD_PARTY.md`](docs/THIRD_PARTY.md).
Notes on contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md).

**Origin and scope of rights.** Most of the code was written with AI tools under human direction.
Andre Stiebitz set the goals, chose and arranged the solutions, steered the work and checked every
change ([`AI-USAGE.md`](AI-USAGE.md)). The licence applies to the extent that copyright or other
rights in the code exist.

**Support:** apart from the security updates described above (Cyber Resilience Act table), help,
fixes and new features are voluntary; there is no entitlement to them unless agreed in a contract.

```text
⣿⣿⣻⢽⢯⣯⢯⡷⣯⣻⡺⣝⡷⡽⣮⣳⣝⣮⣳⣝⢮⡳⣝⢮⡳⡵⣝⣽⢽⢽⢽⢽⡽⣽⣻⣻⣟⣟⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣽⣽⢾⣽⣺⡵⣫⣞⢽⣽⢽⣝⡮⣗⡽⣪⣟⣿
⣿⣺⣯⣯⢷⣻⣽⢯⡷⣷⣻⣯⣿⣻⣗⣯⢾⣺⣞⡾⣽⣽⣺⣳⡽⣽⡺⡽⣯⣿⣽⣳⢿⣽⣯⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣟⣷⣯⣿⣿⣽⣾⣽⡾⣾⣻⣽⣻
⣿⣿⣻⣾⡿⣯⣿⢿⣻⣿⣽⣾⣯⣷⣯⣿⣯⣿⣺⣯⣷⡿⣽⢷⣟⣷⣟⣿⣗⣯⣿⣿⣿⣷⣿⣽⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣻⣿⣯⣿⣳⣷⣿⣾
⣟⡷⡿⡿⡿⡿⡿⣿⢿⡷⡿⡿⢿⠿⠿⡟⡿⢟⢿⠿⠷⢿⠿⡿⢿⡻⡿⡻⠿⠿⠿⢿⠿⢿⠿⡿⡿⡿⡿⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⡿⣿⡻⡿⡽⡿⡽⡯⣟⢿⢽⢯⢿⢽⡻⣞⢿
⡲⡪⡺⡲⡕⡗⡟⡞⡼⡨⣊⢊⡂⢅⠑⠤⡁⡅⠕⡈⢌⠂⠅⢂⠂⠂⡌⡘⠸⠌⠮⡤⢬⢔⡴⣔⢴⢴⣼⣤⣑⣈⡂⡣⢓⠭⢣⢣⢑⢨⠐⡁⢒⢐⠨⠨⠀⢂⡁⣖⠼⡔⡎⡖⡕⡕⡖⢆⢇⢎⢖⢕
⠂⠕⠐⡐⢈⠂⡑⢈⠢⠑⡀⠂⠌⡂⠅⡁⠀⠠⠑⠄⠅⡂⢑⠠⠑⠁⠂⠄⠡⠈⠌⡘⠌⡊⠜⢘⠨⠪⠪⠪⢙⠕⠠⠂⠢⠡⠁⠊⠄⠡⠁⠂⠂⠀⡈⢀⢎⠣⡱⢘⠘⠌⡊⠔⠨⢂⠅⡑⠐⡐⠡⠁
⠀⠀⠁⠀⠀⠀⠀⠀⠀⠀⠀⠁⠀⠄⠀⠀⠁⠀⠀⠈⠀⠀⠀⠀⠈⠀⠀⠐⠀⠠⠀⠀⠀⡀⠈⠀⠈⠈⠈⠀⡀⠁⡁⠄⡢⠀⡂⡈⠀⡁⠀⠂⠆⠅⠊⡐⠠⠁⡐⠀⢈⠐⠀⠌⠐⠀⠠⠀⠀⠀⢀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⠀⠐⠀⠁⠀⠀⠀⠄⠀⢁⠐⠀⠠⠀⠐⠈⢀
```

Picture: © 2026 André Stiebitz · [CC BY 4.0](LICENSES/CC-BY-4.0.txt) · [spleen/bilder/glienicker-see.txt](spleen/bilder/glienicker-see.txt)

> A map is not the territory it represents, but, if correct, it has a similar structure to the territory, which accounts for its usefulness.
>
> — Alfred Korzybski, Science and Sanity, 1933
