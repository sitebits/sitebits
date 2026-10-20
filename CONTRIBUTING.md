# Contributing to scanview

**English** · [Deutsch](CONTRIBUTING.de.md)

scanview is licensed under the **European Union Public Licence v. 1.2** (`EUPL-1.2`, see `LICENSE`).
Copyright © 2026 Stiebitz Souverain Systems UG (haftungsbeschränkt). Author: Andre Stiebitz.

Public repository: [sitebits/sitebits](https://github.com/sitebits/sitebits). Report bugs and
suggest features through [Issues](https://github.com/sitebits/sitebits/issues). Report security
vulnerabilities confidentially as described in [SECURITY.md](SECURITY.md).

## Developer Certificate of Origin

Contributions (code, documentation, tests) are accepted under the EUPL-1.2 without a separate
transfer of rights. Every commit carries the line

```
Signed-off-by: First Last <address@example.org>
```

(`git commit -s`). With it, the contributor confirms the
[Developer Certificate of Origin 1.1](https://developercertificate.org/): they have the right to
submit the contribution under the project's licence. Only a human may sign off, never an AI tool;
how AI assistance is labelled is described in [`AI-USAGE.md`](AI-USAGE.md).

Code identifiers, comments and commit messages are in German. Issues and contributions in
English are welcome.

## Third-party components

Licences of the libraries and tools used: `docs/THIRD_PARTY.md`; the packages shipped in the
viewer are listed in `THIRD_PARTY_LICENSES.txt` at build time.

## What becomes public

For each release (and each security hotfix) the public repository receives a snapshot of the
source code, generated from a fixed commit of the development repository. Which files belong to
it is decided solely by `veroeffentlichung/positivliste.txt`; every new or changed file is
confirmed individually before publication.

- **Public:** source code of the viewer, platform server and MCP server including tests, build
  configuration and lockfiles, installation and operation files of the platform, the release
  chain (`nachweise/`), licence texts and the product documents under `docs/public/`.
- **Stays private:** working notes and handovers, decision records with personal data, details
  from security assessments, operational details of individual installations, and customer and
  project data. Anyone who wants to describe any of this publicly writes a separate, cleaned-up
  version under `docs/public/`.
- **Checks before every publication:** no symlinks or submodules, images and binaries only with an
  approved hash (`veroeffentlichung/freigaben.txt`), a secret scan (gitleaks), a search for
  customer, project, host and user names, and the snapshot must build on its own and pass its
  tests.
- Test fixtures contain synthetic data only.

## What belongs in the repository

The development repository contains the product: source code, tests, build and operation files,
product documents (`docs/architektur.md`, `docs/entscheidungen/`, `docs/public/`) and the
permanent evidence (compliance register, audit reports; not public).

**No working notes:** handovers (`HANDOFF*`), inboxes (`INBOX*`), session and implementation logs
(`*.ledger*`, `.superpowers/`, `docs/superpowers/`), drafts of specs, plans, decision records and
acceptance reports, and screenshots of customer data are not checked in here. They live in a
separate, private working repository. Load-bearing decisions go into `docs/entscheidungen/` as a
shortened, factual ADR. The public export aborts if a working note ends up in the positive list.

Commit subjects describe the change to the product in plain terms.

Public contributions are paused for now (see above); a way to accept contributions with proof of
origin will follow when needed.
