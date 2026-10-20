"""Markdown-Fassungen der Compliance-Register (privat und öffentlich), erzeugt statt gepflegt.

Deterministisch: dieselben Register ergeben byte-gleiche Ausgaben (keine Zeitstempel, Reihenfolge
wie in der TOML-Datei), damit die erzeugten Dateien im Repo nur bei echten Änderungen abweichen."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from .archiv import PRUEFUNGEN, PRUEFUNGSNAME
from .register import STATUS, Anwendbarkeit, Befund, Kontrolle, Register

INTERN = "intern (Vault), auf Anfrage"
KEIN_ERGEBNIS = "—"
UNLESBAR = "unlesbar"

_BEFEHL = "`scanview-nachweise register darstellen`"


def _zelle(text: str) -> str:
    """Text für eine Markdown-Tabellenzelle: Pipes escapen, Zeilenumbrüche zu Leerzeichen."""
    return " ".join(text.replace("\\", "\\\\").replace("|", "\\|").split())


def _zeile(zellen: Iterable[str]) -> str:
    return "| " + " | ".join(_zelle(z) for z in zellen) + " |"


def _tabelle(kopf: Sequence[str], zeilen: Iterable[Sequence[str]]) -> list[str]:
    return [_zeile(kopf), "|" + "---|" * len(kopf), *(_zeile(z) for z in zeilen)]


def _ja_nein(wert: bool) -> str:
    return "ja" if wert else "nein"


def matrix_md(
    kontrollen: Sequence[Kontrolle],
    ergebnisse: Mapping[str, str] | None,
    oeffentlich: bool,
) -> str:
    """Kontrollmatrix als Markdown. `ergebnisse` ordnet Kontroll-IDs das Release-Ergebnis zu
    (Nachweis-ID aus dem Manifest); ohne Eintrag steht „—“. In der öffentlichen Fassung erscheinen
    Zeilen mit `oeffentlich = false` nur mit Kennung, Quelle, Titel und Status — Begründung,
    Prüfhandlung und Sollwert werden durch „intern (Vault), auf Anfrage“ ersetzt. Zeilen mit
    `oeffentlich_zeile = false` fehlen dort ganz — ihre Kennung und ihr Titel allein würden eine
    Lücke offenlegen."""
    ergebnisse = ergebnisse or {}
    kopf = ["ID", "Quelle", "Titel", "Art", "Anwendbar", "Prüfhandlung", "Sollwert",
            "Release-Voraussetzung", "Nachweis", "ISO 27001:2022 Annex A", "Ergebnis"]

    zeilen = []
    weggelassen = False
    for k in kontrollen:
        if oeffentlich and not k.oeffentlich_zeile:
            weggelassen = True
            continue
        verbergen = oeffentlich and not k.oeffentlich
        anwendbar = _ja_nein(k.anwendbar)
        if k.begruendung.strip() and not verbergen:
            anwendbar += " — " + k.begruendung
        zeilen.append([
            k.id, k.quelle, k.titel, k.art, anwendbar,
            INTERN if verbergen else k.pruefhandlung,
            INTERN if verbergen else k.sollwert,
            _ja_nein(k.release_voraussetzung),
            " + ".join(f"`{s}`" for s in k.schluessel_teile) or "—",
            ", ".join(k.iso) or "—",
            ergebnisse.get(k.id, KEIN_ERGEBNIS),
        ])

    herkunft = ("dem internen Register `docs/compliance/kontrollmatrix.toml`" if oeffentlich
                else "`docs/compliance/kontrollmatrix.toml`")
    teile = [
        "# Kontrollmatrix" + (" (öffentliche Fassung)" if oeffentlich else ""),
        "",
        f"> Erzeugt aus {herkunft} mit {_BEFEHL} — nicht von Hand bearbeiten.",
        "",
        "Je Anforderung: Anwendbarkeit, Prüfhandlung und Sollwert; die Spalte „Ergebnis“ trägt je "
        "Release die Nachweis-ID aus dem signierten Release-Manifest. „offen bis …“ ist zulässig "
        "und prüffähig, erfüllt aber keine Release-Voraussetzung. Die ISO-27001-Spalte ist nur "
        "eine Zuordnung, keine eigene Prüfung.",
        "",
        "Art: `pflicht` = rechtliche Anforderung (hier: behandelt, als gälte sie), `freiwillig` = "
        "selbst gewählter Maßstab, `kunde` = Kundenanforderung.",
    ]
    if oeffentlich:
        teile += [
            "",
            f"Zeilen mit „{INTERN}“ enthalten sicherheitsrelevante Prüfdetails; sie liegen in der "
            "internen Dokumentation und werden Prüfern auf Anfrage vorgelegt.",
        ]
    if weggelassen:
        teile += [
            "",
            "Geplante Maßnahmen, deren Bezeichnung allein Rückschlüsse auf den Betrieb zuließe, "
            "stehen nur in der internen Fassung; sie werden Prüfern ebenfalls auf Anfrage "
            "vorgelegt.",
        ]
    teile += ["", *_tabelle(kopf, zeilen)]
    return "\n".join(teile) + "\n"


def anwendbarkeit_md(register: Anwendbarkeit) -> str:
    """Anwendbarkeitsentscheidung (privat) als Markdown."""
    teile = [
        "# Anwendbarkeit (Cyber Resilience Act und Umfeld)",
        "",
        f"> Erzeugt aus `docs/compliance/anwendbarkeit.toml` mit {_BEFEHL} — nicht von Hand "
        "bearbeiten.",
        "",
        f"- **Entschieden am:** {register.entschieden_am}",
        f"- **Status:** {register.status}",
        "",
        "## Betriebs- und Vertriebsmodelle",
        "",
        *_tabelle(
            ["Modell", "Produktgrenze", "Rechtsperson", "Bereitstellung seit", "Einordnung"],
            ([m.name, m.produktgrenze, m.rechtsperson, m.seit, m.einordnung]
             for m in register.modelle),
        ),
    ]
    if register.hinweise:
        teile += ["", "## Hinweise", "", *(f"- {' '.join(h.split())}" for h in register.hinweise)]
    teile += ["", "## Offene Fragen (Anwalt)"]
    for m in register.modelle:
        teile += ["", f"### {m.name}", "", *(f"- {' '.join(f.split())}" for f in m.offene_fragen)]
    return "\n".join(teile) + "\n"


def anwendbarkeit_oeffentlich_md(register: Anwendbarkeit) -> str:
    """Öffentliche Kurzfassung der Anwendbarkeitsentscheidung: Datum, Status und je Modell
    Produktgrenze, Bereitstellung und Einordnung. Rechtsperson, interne Hinweise und die Fragen
    an den Anwalt bleiben in der privaten Fassung."""
    teile = [
        "# Anwendbarkeit des Cyber Resilience Act (öffentliche Fassung)",
        "",
        "> Erzeugt aus dem internen Register `docs/compliance/anwendbarkeit.toml` mit "
        f"{_BEFEHL} — nicht von Hand bearbeiten.",
        "",
        f"- **Entschieden am:** {register.entschieden_am}",
        f"- **Status:** {register.status}",
        "",
        "Ob und in welcher Rolle die Pflichten der Verordnung (EU) 2024/2847 (Cyber Resilience "
        "Act) für scanview gelten, hängt vom Betriebs- und Vertriebsmodell ab und ist noch nicht "
        "abschließend rechtlich geklärt. Bis dahin wird gehandelt, als gälten die "
        "Herstellerpflichten einschließlich der Meldepflichten nach Art. 14: Meldeweg, "
        "Schwachstellenbehandlung, "
        "Sicherheitsupdates und technische Dokumentation sind Voraussetzung jedes Releases "
        "(siehe `docs/public/kontrollmatrix.md`).",
        "",
        "## Betriebs- und Vertriebsmodelle",
        "",
        *_tabelle(
            ["Modell", "Produktgrenze", "Bereitstellung seit", "Einordnung"],
            ([m.name, m.produktgrenze, m.seit, m.einordnung] for m in register.modelle),
        ),
    ]
    return "\n".join(teile) + "\n"


def _betrifft(b: Befund) -> str:
    return ", ".join(b.artefakte) if b.artefakte else "alle"


def befunde_md(befunde: Sequence[Befund], oeffentlich: bool) -> str:
    """Befundregister als Markdown. Privat: alle Felder. Öffentlich: je Befund nur ID, Schwere,
    Kategorie und Status — kein Titel, keine Quelle, keine Stände, kein Retest, damit offene
    Punkte keinen Angriffspfad verraten und keine Instanz- oder Kundenbezüge nach
    außen gehen."""
    zaehlung = ", ".join(f"{s}: {sum(b.status == s for b in befunde)}" for s in STATUS)
    if oeffentlich:
        kopf = ["ID", "Schwere", "Kategorie", "Status", "Betrifft"]
        zeilen = [[b.id, b.schwere, b.kategorie, b.status, _betrifft(b)] for b in befunde]
        herkunft = "dem internen Register `docs/compliance/befunde.toml`"
    else:
        kopf = ["ID", "Quelle", "Titel", "Schwere", "Kategorie", "Status", "Betrifft",
                "Betroffene Stände",
                "Entdeckt", "Fix-Commit", "Retest", "Akzeptanz (Begründung, Frist)"]
        zeilen = []
        for b in befunde:
            akzeptanz = "—"
            if b.status == "akzeptiert":
                frist = b.frist.isoformat() if b.frist else "—"
                akzeptanz = f"{b.akzeptanz_begruendung} (Frist {frist}, Freigabe Andre)"
            zeilen.append([
                b.id, b.quelle, b.titel, b.schwere, b.kategorie, b.status,
                _betrifft(b) + (f" ({b.artefakte_begruendung})" if b.artefakte_begruendung
                                else ""),
                ", ".join(b.staende),
                b.entdeckt.isoformat(),
                " + ".join(f"`{c}`" for c in b.fix_commit.split("+")) if b.fix_commit else "—",
                f"`{b.retest}`" if b.retest else "—",
                akzeptanz,
            ])
        herkunft = "`docs/compliance/befunde.toml`"
    teile = [
        "# Befundregister" + (" (öffentliche Fassung)" if oeffentlich else ""),
        "",
        f"> Erzeugt aus {herkunft} mit {_BEFEHL} — nicht von Hand bearbeiten.",
        "",
        "Jeder eigene und fremde Sicherheitsbefund mit stabiler ID. Release-Schwelle: kein offener "
        "Befund ≥ hoch; offene mittlere Befunde blockieren, wenn sie Authentifizierung, Rechte, "
        "Projekttrennung oder Datenabfluss betreffen, übrige mittlere nach mehr als 60 Tagen. "
        "Ein Befund, der nur nicht ausgelieferte Artefakte betrifft (Spalte „Betrifft“), blockiert "
        "das Release nicht und wird dort informativ aufgeführt.",
    ]
    if oeffentlich:
        teile += [
            "",
            "Diese Fassung nennt je Befund nur Kennung, Schwere, Kategorie und Status. "
            "Beschreibung, Fundstelle und Nachweis der Behebung liegen in der internen "
            "Dokumentation und werden Prüfern auf Anfrage vorgelegt.",
        ]
    teile += ["", f"Stand: {len(befunde)} Befunde — {zaehlung}.", "", *_tabelle(kopf, zeilen)]
    return "\n".join(teile) + "\n"


_BETRIEB_PRAEFIX = "betrieb:"


def _kurzergebnis(daten: object) -> str:
    """Ergebnis eines Betriebsnachweises in einem Wort: `ergebnis` (Update-Protokoll, Wochenscan),
    sonst `gate` (DAST). Ein Nachweis ohne dieses Feld ist nur „vorhanden“ — das sagt nichts über
    bestanden oder blockiert, bewertet wird hier nichts hinzu; ein Erzeuger, der ein Urteil hat,
    schreibt es nach `ergebnis`. Kein JSON-Objekt → „unlesbar“."""
    if not isinstance(daten, dict):
        return UNLESBAR
    for feld in ("ergebnis", "gate"):
        wert = daten.get(feld)
        if isinstance(wert, str) and wert.strip():
            return wert.strip()
    return "vorhanden"


def betriebsergebnisse(kontrollen: Sequence[Kontrolle], archiv: Path) -> dict[str, str]:
    """Je Kontrolle mit `betrieb:<art>`-Schlüssel der jüngste Nachweis je Instanz aus
    `<archiv>/pruefungen/<datum>[.<lauf>]-<art>[-<instanz>]/ergebnis.json` (Datum, Ergebnis,
    Pfad); ohne Nachweis „—“. Kontrollen ohne `betrieb:`-Schlüssel erscheinen nicht."""
    # (Art, Instanz) → ((Datum, Lauf), Ordnername, Kurzergebnis)
    jüngste: dict[tuple[str, str], tuple[tuple[str, int], str, str]] = {}
    ordner = archiv / PRUEFUNGEN
    if ordner.is_dir():
        for eintrag in sorted(ordner.iterdir()):
            treffer = PRUEFUNGSNAME.fullmatch(eintrag.name)
            datei = eintrag / "ergebnis.json"
            if not treffer or not eintrag.is_dir():
                continue
            datum, lauf, art, instanz = treffer.groups()
            instanz = instanz or ""
            # Reihenfolge nach Datum, dann Laufnummer numerisch (lexikalisch läge .10 vor .2).
            stand = (datum, int(lauf or 1))
            # Fehlend oder unlesbar zählt als jüngster Stand: sonst rückte still ein älteres
            # Ergebnis nach.
            try:
                daten: object = json.loads(datei.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                daten = None
            bisher = jüngste.get((art, instanz))
            if bisher is None or stand >= bisher[0]:
                jüngste[(art, instanz)] = (stand, eintrag.name, _kurzergebnis(daten))
    ergebnisse: dict[str, str] = {}
    for k in kontrollen:
        arten = [s[len(_BETRIEB_PRAEFIX):] for s in k.schluessel_teile
                 if s.startswith(_BETRIEB_PRAEFIX)]
        if not arten:
            continue
        angaben = []
        for art in arten:
            for (a, instanz), ((datum, _lauf), name, ergebnis) in sorted(jüngste.items()):
                if a == art:
                    angaben.append((f"{instanz}: " if instanz else "")
                                   + f"{datum} {ergebnis} (`{PRUEFUNGEN}/{name}`)")
        ergebnisse[k.id] = "; ".join(angaben) or KEIN_ERGEBNIS
    return ergebnisse


def betrieb_md(kontrollen: Sequence[Kontrolle], ergebnisse: Mapping[str, str]) -> str:
    """Betriebsnachweise (privat): je Kontrolle mit `betrieb:`-Schlüssel der jüngste Stand je
    Instanz aus dem Archiv. Eigene Datei, weil der Archivstand nicht im Release-Commit stehen
    kann, den die Darstellungsprüfung des Releases byte-genau vergleicht."""
    zeilen = [[k.id, k.titel, " + ".join(f"`{s}`" for s in k.schluessel_teile),
               ergebnisse.get(k.id, KEIN_ERGEBNIS)]
              for k in kontrollen if k.id in ergebnisse]
    teile = [
        "# Betriebsnachweise",
        "",
        "> Erzeugt aus `docs/compliance/kontrollmatrix.toml` und dem Nachweis-Archiv mit "
        "`scanview-nachweise register darstellen --betrieb <archiv>` — nicht von Hand bearbeiten.",
        "",
        "Jüngster Nachweis je Instanz unter `pruefungen/<Datum>[.<Lauf>]-<Art>[-<Instanz>]/` des "
        "Archivs (weitere Läufe eines Tages tragen eine Laufnummer); "
        "„—“ heißt: kein Nachweis vorhanden, es wird nichts behauptet. Der jüngste Stand kann ein "
        "blockierendes Ergebnis sein; ein späterer Lauf ersetzt ihn erst, wenn sein Nachweis im "
        "Archiv liegt. Die Kontrollmatrix "
        "selbst trägt keine Betriebsergebnisse.",
        "",
        *_tabelle(["ID", "Titel", "Nachweis", "Jüngster Stand je Instanz"], zeilen),
    ]
    return "\n".join(teile) + "\n"


#: Erzeugte Fassungen (Repo-relativ) — privat unter docs/compliance, öffentlich unter docs/public.
AUSGABEN = {
    "anwendbarkeit": Path("docs/compliance/anwendbarkeit.md"),
    "anwendbarkeit_oeffentlich": Path("docs/public/anwendbarkeit.md"),
    "matrix_privat": Path("docs/compliance/kontrollmatrix.md"),
    "matrix_oeffentlich": Path("docs/public/kontrollmatrix.md"),
    "befunde_privat": Path("docs/compliance/befunde.md"),
    "befunde_oeffentlich": Path("docs/public/befunde.md"),
}


BETRIEB_AUSGABE = Path("docs/compliance/betriebsnachweise.md")


def fassungen(r: Register) -> dict[Path, str]:
    """Alle Markdown-Fassungen der Register (Repo-relativer Pfad → Inhalt)."""
    return {
        AUSGABEN["anwendbarkeit"]: anwendbarkeit_md(r.anwendbarkeit),
        AUSGABEN["anwendbarkeit_oeffentlich"]: anwendbarkeit_oeffentlich_md(r.anwendbarkeit),
        AUSGABEN["matrix_privat"]: matrix_md(r.kontrollen, None, oeffentlich=False),
        AUSGABEN["matrix_oeffentlich"]: matrix_md(r.kontrollen, None, oeffentlich=True),
        AUSGABEN["befunde_privat"]: befunde_md(r.befunde, oeffentlich=False),
        AUSGABEN["befunde_oeffentlich"]: befunde_md(r.befunde, oeffentlich=True),
    }


def abweichungen(wurzel: Path, inhalte: Mapping[Path, str]) -> list[str]:
    """Repo-relative Pfade, deren committete Fassung fehlt oder vom erzeugten Inhalt abweicht
    (byte-genau, UTF-8, `\\n`) — Drift zwischen TOML und Markdown."""
    abweichend: list[str] = []
    for relativ, inhalt in inhalte.items():
        try:
            ist = (wurzel / relativ).read_bytes()
        except OSError:
            abweichend.append(f"{relativ.as_posix()} (fehlt)")
            continue
        if ist != inhalt.encode("utf-8"):
            abweichend.append(relativ.as_posix())
    return abweichend
