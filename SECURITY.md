# Security policy

This policy describes how to report security vulnerabilities in scanview, how we handle them and
when we disclose them (coordinated vulnerability disclosure). A short German version is at the
end.

## Supported versions

Security fixes are provided for the latest minor version (one maintenance line). Details,
deadlines and the support period are in
[`docs/public/support-und-patches.md`](docs/public/support-und-patches.md).

| Version | Security fixes |
|---|---|
| 0.11.x | yes |
| older | no, please update to the latest version |

## Reporting a vulnerability

Please report vulnerabilities **confidentially** by email to **security@sitebits.de**,
not through public issues, forums or social media.

- **Encryption:** reports can be encrypted with OpenPGP to the key with the fingerprint
  `328C A76B 3CE9 D878 5F04  7714 2C00 B9A9 F342 5E48`
  ([download](https://sitebits.de/assets/security.asc),
  [alternative download](https://souverain-systems.de/andre-souverain-systems.asc)). The key is issued to the
  addresses of Stiebitz Souverain Systems UG (haftungsbeschränkt), which operates the reporting
  mailbox. Please check the fingerprint before use.
- **Language:** English or German.
- **Helpful details:** affected version or commit, affected component (viewer, portal, platform
  server, MCP server, installation files), a description of the impact, steps to reproduce and,
  if available, a suggested fix.
- **Anonymous reports** are possible; we only need a sender address for questions and for
  coordinating disclosure.

## What you can expect from us

| Step | Deadline |
|---|---|
| Acknowledgement of receipt | within 5 working days |
| Initial assessment (confirmed or not, preliminary severity, next steps) | within 10 working days |
| Fix | by severity: critical ≤ 7 days, high ≤ 30 days, medium ≤ 60 days, low at the latest with the next minor version (from confirmation) |
| Status updates | on request and with every significant change |

We rate severity using the current version of CVSS and the actual exploitability in a typical
installation. Every reported vulnerability receives an identifier in the findings register
([`docs/public/befunde.md`](docs/public/befunde.md)).

## Coordinated disclosure

- We publish a vulnerability as soon as a fix is available, but no later than **90 days** after
  acknowledging receipt.
- If a fix demonstrably takes longer, we extend the deadline **once by up to 90 days** in
  agreement with the reporter.
- If a vulnerability is being actively exploited, we inform operators and users earlier, even if
  the fix is still pending, and name mitigating measures.
- Fixed vulnerabilities are listed in the "Sicherheit" (security) section of the
  [`CHANGELOG.md`](CHANGELOG.md) with severity, affected versions and remedy. We request a CVE
  identifier when the vulnerability affects published versions.
- Please publish yourself only after this deadline or after our approval.

## Acknowledgements

Anyone who reports a vulnerability responsibly is named, if they wish, in the security notes and
the CHANGELOG, by name or pseudonym. There is no bug bounty programme.

## Scope

**In scope** is the source code of this repository in a supported version: viewer and portal
(`web/`), platform server (`server/`), MCP server (`mcp-server/`), the installation and operation
files (`deploy/platform/`) and the release chain (`nachweise/`).

**Out of scope:**

- Tests against installations you do not own or for which you have no explicit permission from
  the operator. Please test only against your own installation (guide:
  [`docs/public/betrieb.md`](docs/public/betrieb.md)).
- Attacks on availability (load and flooding attacks), social engineering, physical attacks.
- Vulnerabilities in third-party components that are already publicly known and for which the
  vendor has a fix; a note that scanview uses an affected version is enough.
- Findings that only occur in outdated or unsupported browsers.

Anyone who follows this policy, acts in good faith, does not access or modify third-party data
and coordinates disclosure need not fear legal action from us.

## Further information

- Security architecture and secure defaults:
  [`docs/public/sicherheitsarchitektur.md`](docs/public/sicherheitsarchitektur.md)
- How releases are checked and signed: [`docs/public/release.md`](docs/public/release.md)

## Kurzfassung auf Deutsch

Sicherheitskorrekturen gibt es für die neueste Minor-Version, derzeit **0.11.x**; ältere
Versionen werden nicht unterstützt. Bitte auf die neueste Version aktualisieren. Einzelheiten
stehen unter [Support und Patches](docs/public/support-und-patches.md).

Sicherheitslücken bitte **vertraulich** per E-Mail an **security@sitebits.de** melden,
nicht über öffentliche Issues. Meldungen auf Deutsch oder Englisch, auf Wunsch anonym oder mit
OpenPGP verschlüsselt ([Schlüssel herunterladen](https://sitebits.de/assets/security.asc),
Fingerabdruck wie oben; er lautet auf die Adressen der Stiebitz
Souverain Systems UG, die das Meldepostfach betreibt). Wir bestätigen den Eingang innerhalb von 5 Arbeitstagen, schätzen innerhalb
von 10 Arbeitstagen ein und beheben nach Schwere (kritisch ≤ 7 Tage, hoch ≤ 30, mittel ≤ 60,
niedrig mit der nächsten Minor-Version). Offengelegt wird mit der Korrektur, spätestens nach
90 Tagen (einmalig um bis zu 90 Tage verlängerbar). Wer sich an diese Richtlinie hält und in gutem
Glauben handelt, muss keine rechtlichen Schritte befürchten. Es gilt die englische Fassung oben.
