"""Ersatzprüfung für Eigenbauten: Schwachstellen aus OSV.dev statt trivy.

trivy kennt für selbst gebauten C-Code (purl `pkg:github/…`) keine Schwachstellendaten; auch
`pkg:conan` nimmt es nur scheinbar an (Gegenprobe 07.10.2026: zstd 1.3.0 ohne Treffer, obwohl
CVE-2019-11922 bekannt ist). OSV.dev führt Schwachstellen im Ökosystem GIT als Commit-Bereiche
und Tag-Listen der Quell-Repositorys. Beide Wege sind nötig: OSV-2022-15 (zstd) trifft die
Abfrage nach Tag `v1.4.9`, aber nicht die nach dessen Commit. Abgefragt wird deshalb je
Komponente der Commit **und** der Tag aus `eigenbau.toml`; die Funde werden vereinigt.

Zwei getrennte Schritte wie bei trivy:

1. `aktualisieren` — Netzschritt (mit `db-aktualisieren`): fragt OSV je Eigenbau-Komponente
   ab und schreibt `osv-stand.json` in den Scan-Cache. Vorher eine **Gegenprobe** mit einem
   bekannt verwundbaren Stand (zstd v1.3.7 → CVE-2019-11922), je per Commit und per Tag; meldet
   OSV dort auf einem der Wege nichts, ist die Abfrage blind und der Schritt bricht ab
   (`osv-kontrolle`).
2. `lade_stand` — ohne Netz in der Scan-Stufe: Stand höchstens 24 h alt (wie die trivy-DB), nicht
   aus der Zukunft, mit bestandener Gegenprobe.

Wirkung im Scan (`scan.scanne_alle`): Eine Komponente gilt als ersatzweise geprüft, wenn ihre
bom-ref **und** ihre PURL genau so im Stand stehen; sie bleibt in der Abdeckung als ungescannt
sichtbar (`ersatz`), fällt aber aus `ungescannt_pflicht`. Jeder OSV-Fund wird ein Treffer der
Schwere „hoch“ (OSV-Einträge aus OSS-Fuzz tragen oft keine Bewertung — fail-closed); freigeben
kann ihn nur eine gültige VEX-Aussage (`vex`, Lockdatei `nachweise/eigenbau.toml`)."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .fehler import NachweisFehler
from .scan import Treffer
from .werkzeuge import gefilterte_umgebung, git_befehl

OSV_URL = "https://api.osv.dev/v1/query"
OSV_STAND = "osv-stand.json"
HOECHSTALTER = timedelta(hours=24)
#: Präfix der bom-refs von Eigenbauten (`sbom.eigenbau_einfuegen`).
EIGENBAU_PRAEFIX = "eigenbau:"
#: Gegenprobe: zstd v1.3.7 ist von CVE-2019-11922 betroffen (OSV kennt es per Commit und Tag).
KONTROLLE_REPO = "https://github.com/facebook/zstd"
KONTROLLE_COMMIT = "21cd8a9d95a321f1fe256dc837e388bbc168fdbf"
KONTROLLE_TAG = "v1.3.7"
KONTROLLE_ERWARTET = "CVE-2019-11922"
_SEITEN_HOECHSTENS = 50

Holen = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Abfrage:
    """Eine Eigenbau-Komponente, wie sie im SBOM steht: bom-ref, PURL (`pkg:github/o/r@commit`),
    Commit, Quell-Repository (vcs-Referenz) und Git-Tag (Eigenschaft `scanview:git-tag`)."""

    bom_ref: str
    purl: str
    commit: str
    repo: str
    tag: str


@dataclass(frozen=True)
class Fund:
    id: str
    aliases: tuple[str, ...]
    modified: str


@dataclass(frozen=True)
class OsvStand:
    """Geladener Stand; `sha256` über `osv-stand.json` (für Scan-Ergebnis und Manifest — der
    Cache ist wie bei der trivy-DB Vertrauensgrenze, lokale Manipulation ist Restrisiko)."""

    zeit: datetime
    funde: dict[Abfrage, tuple[Fund, ...]]
    sha256: str = ""


TagCommit = Callable[[str, str], "str | None"]


def _tag_commit_git(repo: str, tag: str) -> str | None:
    """Commit, auf den `tag` in `repo` zeigt (annotierte Tags aufgelöst), per `git ls-remote` —
    Teil des Netzschritts. `None`, wenn es den Tag nicht gibt."""
    try:
        r = subprocess.run(git_befehl("ls-remote", "--tags", repo, f"refs/tags/{tag}",
                                      f"refs/tags/{tag}^{{}}"),
                           capture_output=True, text=True, check=False, timeout=60,
                           stdin=subprocess.DEVNULL, env=gefilterte_umgebung())
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NachweisFehler("osv-tag", f"{repo}: git ls-remote nicht ausführbar ({e})") from None
    if r.returncode != 0:
        raise NachweisFehler("osv-tag", f"{repo}: git ls-remote fehlgeschlagen "
                                        f"({r.stderr.strip()[:200]})")
    refs: dict[str, str] = {}
    for zeile in r.stdout.splitlines():
        sha, _, ref = zeile.partition("\t")
        if ref:
            refs[ref] = sha
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    return commit if commit and re.fullmatch(r"[0-9a-f]{40}", commit) else None


# --- Abfragen ------------------------------------------------------------------------------------

def _commit_aus_purl(purl: str) -> str | None:
    if not purl.startswith("pkg:github/"):
        return None
    commit = purl.rsplit("@", 1)[-1] if "@" in purl else ""
    return commit if len(commit) == 40 and all(c in "0123456789abcdef" for c in commit) else None


def _komponenten(liste: object) -> Iterable[dict[str, Any]]:
    for k in liste if isinstance(liste, list) else []:
        if isinstance(k, dict):
            yield k
            yield from _komponenten(k.get("components"))


def abfragen_aus_bom(bom: dict[str, Any]) -> list[Abfrage]:
    """Alle Eigenbau-Komponenten eines SBOMs (bom-ref `eigenbau:…`, PURL `pkg:github/…@<commit>`).
    Eine Eigenbau-Komponente ohne solche PURL ist ein Fehler — sie ließe sich nicht abfragen."""
    ergebnis: list[Abfrage] = []
    for k in _komponenten(bom.get("components")):
        ref = k.get("bom-ref")
        if not isinstance(ref, str) or not ref.startswith(EIGENBAU_PRAEFIX):
            continue
        purl = k.get("purl")
        commit = _commit_aus_purl(purl) if isinstance(purl, str) else None
        if commit is None or not isinstance(purl, str):
            raise NachweisFehler("osv-abfrage", f"{ref}: keine PURL pkg:github/…@<voller Commit>")
        repo = next((r.get("url") for r in k.get("externalReferences", [])
                     if isinstance(r, dict) and r.get("type") == "vcs"), None)
        tag = next((p.get("value") for p in k.get("properties", [])
                    if isinstance(p, dict) and p.get("name") == "scanview:git-tag"), None)
        if not isinstance(repo, str) or not repo.startswith("https://github.com/") \
                or not isinstance(tag, str) or not tag:
            raise NachweisFehler("osv-abfrage", f"{ref}: vcs-Referenz (github.com) oder "
                                                "scanview:git-tag fehlt")
        ergebnis.append(Abfrage(ref, purl, commit, repo, tag))
    return ergebnis


def abfragen_aus_eigenbau(eintraege: Iterable[Any]) -> list[Abfrage]:
    """Abfragen aus `eigenbau.toml` (`sbom.lade_eigenbau`) — dieselben bom-refs/PURLs, die
    `sbom.eigenbau_einfuegen` schreibt."""
    from .sbom import eigenbau_refs  # zur Laufzeit: sbom ist groß und braucht osv nicht

    return [Abfrage(ref, purl, commit, repo, tag) for e in eintraege
            for ref, purl, commit, repo, tag in eigenbau_refs(e)]


def stand_falls_noetig(sboms: Iterable[Path], cache: Path, uhr: datetime) -> OsvStand | None:
    """OSV-Stand laden, wenn eines der SBOMs Eigenbauten enthält (sonst `None`, kein Stand
    nötig). Unlesbare SBOMs meldet erst der Scan."""
    for pfad in sboms:
        try:
            bom = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(bom, dict) and abfragen_aus_bom(bom):
            return lade_stand(cache, uhr)
    return None


# --- Netzschritt ---------------------------------------------------------------------------------

def _post(nutzlast: dict[str, Any]) -> dict[str, Any]:
    anfrage = urllib.request.Request(
        OSV_URL, data=json.dumps(nutzlast).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "scanview-nachweise"})
    try:
        with urllib.request.urlopen(anfrage, timeout=60) as antwort:  # feste https-URL (OSV_URL)
            daten = json.loads(antwort.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise NachweisFehler("osv-abfrage",
                             f"OSV nicht erreichbar oder Antwort kaputt: {e}") from None
    if not isinstance(daten, dict):
        raise NachweisFehler("osv-abfrage", "OSV-Antwort ist kein Objekt")
    return daten


def _seiten(anfrage: dict[str, Any], holen: Holen) -> list[Fund]:
    """Alle OSV-Einträge zu einer Anfrage (mit Folgeseiten)."""
    funde: list[Fund] = []
    nutzlast = dict(anfrage)
    for _ in range(_SEITEN_HOECHSTENS):
        daten = holen(nutzlast)
        vulns = daten.get("vulns", [])
        if not isinstance(vulns, list):
            raise NachweisFehler("osv-abfrage", f"{anfrage}: `vulns` ist keine Liste")
        for v in vulns:
            if not isinstance(v, dict) or not isinstance(v.get("id"), str):
                raise NachweisFehler("osv-abfrage", f"{anfrage}: Eintrag ohne id")
            aliases = v.get("aliases", [])
            funde.append(Fund(v["id"], tuple(a for a in aliases if isinstance(a, str))
                              if isinstance(aliases, list) else (), str(v.get("modified", ""))))
        token = daten.get("next_page_token")
        if not token:
            return funde
        nutzlast = {**anfrage, "page_token": token}
    raise NachweisFehler("osv-abfrage", f"{anfrage}: mehr als {_SEITEN_HOECHSTENS} Seiten")


def _per_commit(commit: str, holen: Holen) -> list[Fund]:
    return _seiten({"commit": commit}, holen)


def _per_tag(repo: str, tag: str, holen: Holen) -> list[Fund]:
    return _seiten({"package": {"name": repo, "ecosystem": "GIT"}, "version": tag}, holen)


def _vereinigt(*listen: list[Fund]) -> tuple[Fund, ...]:
    """Funde je ID zusammenführen: Aliase aller Antworten vereinigt (sonst ginge ein Alias, den nur
    die Tag- oder nur die Commit-Antwort nennt, verloren), `modified` der jüngste Wert."""
    aliase: dict[str, set[str]] = {}
    geaendert: dict[str, str] = {}
    for f in (f for liste in listen for f in liste):
        aliase.setdefault(f.id, set()).update(f.aliases)
        geaendert[f.id] = max(geaendert.get(f.id, ""), f.modified)
    return tuple(Fund(i, tuple(sorted(aliase[i] - {i})), geaendert[i]) for i in sorted(aliase))


def _getroffen(funde: Iterable[Fund]) -> bool:
    return any(KONTROLLE_ERWARTET in (f.id, *f.aliases) for f in funde)


def aktualisieren(abfragen: Iterable[Abfrage], cache: Path, *, holen: Holen = _post,
                  uhr: Callable[[], datetime] = lambda: datetime.now(UTC),
                  tag_commit: TagCommit = _tag_commit_git) -> OsvStand | None:
    """Netzschritt: Gegenprobe, Tag → Commit je Abfrage (`git ls-remote`, muss dem erklärten
    Commit entsprechen), dann OSV je Abfrage (Commit und Tag); schreibt `osv-stand.json` in
    `cache`. Ohne Eigenbauten kein Netzschritt (`None`)."""
    eintraege = sorted(set(abfragen), key=lambda a: a.bom_ref)
    if not eintraege:
        return None
    for a in eintraege:
        aufgeloest = tag_commit(a.repo, a.tag)
        if aufgeloest != a.commit:
            raise NachweisFehler("osv-tag", f"{a.bom_ref}: Tag {a.tag} in {a.repo} zeigt auf "
                                            f"{aufgeloest or 'nichts'}, erklärt ist {a.commit} — "
                                            "eigenbau.toml korrigieren")
    per_commit = _per_commit(KONTROLLE_COMMIT, holen)
    per_tag = _per_tag(KONTROLLE_REPO, KONTROLLE_TAG, holen)
    for weg, liste in (("Commit", per_commit), ("Tag", per_tag)):
        if not _getroffen(liste):
            raise NachweisFehler("osv-kontrolle", f"Gegenprobe: OSV meldet für zstd "
                                                  f"{KONTROLLE_TAG} per {weg} kein "
                                                  f"{KONTROLLE_ERWARTET} — Abfrage blind, kein "
                                                  "Ersatz für trivy")
    kontrolle = _vereinigt(per_commit, per_tag)
    zeit = uhr()
    funde = {a: _vereinigt(_per_commit(a.commit, holen), _per_tag(a.repo, a.tag, holen))
             for a in eintraege}
    kennungen = sorted({k for f in kontrolle for k in (f.id, *f.aliases)})
    inhalt = json.dumps({
        "zeit": zeit.isoformat(timespec="seconds"), "url": OSV_URL,
        "kontrolle": {"commit": KONTROLLE_COMMIT, "tag": KONTROLLE_TAG,
                      "erwartet": KONTROLLE_ERWARTET, "gefunden": kennungen},
        "abfragen": [{"bom_ref": a.bom_ref, "purl": a.purl, "commit": a.commit,
                      "repo": a.repo, "tag": a.tag, "tag_commit": a.commit,
                      "funde": [{"id": f.id, "aliases": list(f.aliases), "modified": f.modified}
                                for f in fs]} for a, fs in funde.items()],
    }, indent=2, ensure_ascii=False) + "\n"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / OSV_STAND).write_text(inhalt, encoding="utf-8")
    return OsvStand(zeit, funde, hashlib.sha256(inhalt.encode()).hexdigest())


# --- Ohne Netz -----------------------------------------------------------------------------------

def lade_stand(cache: Path, uhr: datetime) -> OsvStand:
    """`osv-stand.json` laden und prüfen: ≤ 24 h, nicht aus der Zukunft, Gegenprobe bestanden."""
    regel = "osv-stand"
    pfad = cache / OSV_STAND
    try:
        roh_bytes = pfad.read_bytes()
        daten = json.loads(roh_bytes.decode("utf-8"))
        zeit = datetime.fromisoformat(daten["zeit"])
        kontrolle = daten["kontrolle"]
        roh = daten["abfragen"]
    except (OSError, ValueError, KeyError, TypeError):
        raise NachweisFehler(regel, f"{pfad} fehlt oder ist unlesbar — erst `db-aktualisieren` "
                                    "(lädt auch den OSV-Stand)") from None
    if zeit.tzinfo is None:
        raise NachweisFehler(regel, f"{pfad}: Zeit ohne Zeitzone")
    alter = uhr - zeit
    if alter > HOECHSTALTER:
        raise NachweisFehler(regel, f"OSV-Stand ist {alter.total_seconds() / 3600:.1f} h alt, "
                                    "erlaubt sind 24 h — erneut `db-aktualisieren`")
    if alter < timedelta(0):
        raise NachweisFehler(regel, f"OSV-Stand-Zeit {zeit.isoformat()} liegt in der Zukunft")
    gefunden = kontrolle.get("gefunden") if isinstance(kontrolle, dict) else None
    if not isinstance(kontrolle, dict) or kontrolle.get("commit") != KONTROLLE_COMMIT \
            or kontrolle.get("tag") != KONTROLLE_TAG \
            or kontrolle.get("erwartet") != KONTROLLE_ERWARTET \
            or not isinstance(gefunden, list) or not all(isinstance(g, str) for g in gefunden) \
            or KONTROLLE_ERWARTET not in gefunden:
        raise NachweisFehler(regel, f"{pfad}: Gegenprobe fehlt oder nicht bestanden")
    funde: dict[Abfrage, tuple[Fund, ...]] = {}
    try:
        for e in roh:
            a = Abfrage(str(e["bom_ref"]), str(e["purl"]), str(e["commit"]), str(e["repo"]),
                        str(e["tag"]))
            if _commit_aus_purl(a.purl) != a.commit or not a.bom_ref.startswith(EIGENBAU_PRAEFIX) \
                    or e.get("tag_commit") != a.commit:
                raise ValueError(f"{a.bom_ref}: PURL, Präfix oder Tag-Prüfung passt nicht")
            funde[a] = tuple(Fund(str(f["id"]), tuple(map(str, f.get("aliases", []))),
                                  str(f.get("modified", ""))) for f in e["funde"])
    except (KeyError, TypeError, ValueError) as fehler:
        raise NachweisFehler(regel, f"{pfad}: Abfrage unlesbar ({fehler})") from None
    return OsvStand(zeit, funde, hashlib.sha256(roh_bytes).hexdigest())


def _abgedeckt(bom: dict[str, Any], stand: OsvStand | None) -> list[Abfrage]:
    if stand is None:
        return []
    abfragen = abfragen_aus_bom(bom)
    refs = [a.bom_ref for a in abfragen]
    doppelt = sorted({r for r in refs if refs.count(r) > 1})
    if doppelt:
        raise NachweisFehler("osv-abfrage", f"Eigenbau-bom-ref doppelt im SBOM: {doppelt}")
    bekannt = set(stand.funde)
    return [a for a in abfragen if a in bekannt]


def ersatz_fuer(bom: dict[str, Any], stand: OsvStand | None) -> dict[str, str]:
    """bom-ref → PURL der Eigenbau-Komponenten dieses SBOMs, die der Stand genau so (bom-ref,
    PURL, Commit, Repository, Tag) abdeckt; `scan.abdeckung` prüft die PURL je Komponente noch
    einmal. Andere bleiben ungeprüft (→ `scan-abdeckung`). Doppelte Eigenbau-Refs → Abbruch."""
    return {a.bom_ref: a.purl for a in _abgedeckt(bom, stand)}


def treffer(bom: dict[str, Any], stand: OsvStand | None, artefakt: str) -> list[Treffer]:
    """OSV-Funde der abgedeckten Eigenbau-Komponenten als Treffer (Schwere „hoch“, mit Aliasen —
    VEX gleicht ID und Aliase ab)."""
    if stand is None:
        return []
    return [Treffer(f.id, a.purl, a.commit, "hoch", None, artefakt=artefakt, bom_ref=a.bom_ref,
                    aliase=f.aliases)
            for a in sorted(_abgedeckt(bom, stand), key=lambda a: a.bom_ref)
            for f in stand.funde[a]]


def stand_bericht(stand: OsvStand) -> dict[str, Any]:
    return {"zeit": stand.zeit.isoformat(), "url": OSV_URL, "sha256": stand.sha256,
            "abfragen": {a.bom_ref: [f.id for f in fs] for a, fs in stand.funde.items()}}
