"""Prüfbericht nach der Berichts-Norm (Recherche land-forst-garten §5.2), angepasst an die Prüfung.

Die Aufnahme entfällt (fremde Lieferung). Pflicht bleiben Frage und Antwort auf Seite 1, die Kette
mit Versionen und Prüfsummen, offene Schwellen, „Was der Bericht nicht kann“, Datenschutz und der
Anhang zum Nachprüfen. ``pflichtteile`` prüft das vor dem Rendern (Prüfart „bericht“).
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path
from string import Template
from typing import Any

from ..berichtsatz import _tabelle
from .pruefarten import ERFUELLT, NICHT_ERFUELLT, NICHT_PRUEFBAR

VORLAGE = Template((Path(__file__).parent / "vorlage.html").read_text(encoding="utf-8"))
KLASSE = {ERFUELLT: "erfuellt", NICHT_ERFUELLT: "nicht-erfuellt", NICHT_PRUEFBAR: "nicht-pruefbar"}

GRENZEN = [
    "Geprüft werden Dateiköpfe, Dateilisten, Prüfsummen und Klassenstatistik. Ob Boden wirklich "
    "Boden ist, prüft siteBits nur mit Referenz-DGM des Auftraggebers und nur für die Klasse 2; "
    "die übrigen Klassen bleiben ungeprüft.",
    "Das Referenz-DGM hat eigene Genauigkeit und einen eigenen Stand (Bauten oder Aufschüttung "
    "nach der Befliegung), unter Gebäuden ist es interpoliert. Eine Abweichung je Pixel ist ein "
    "Hinweis auf einen Klassifizierungs- oder Höhenfehler, kein Beweis.",
    "Genauigkeit belegen nur unabhängige Kontrollpunkte; Passpunkte waren im Ausgleich und "
    "zählen nie. Die Höhe misst siteBits selbst an der Wolke (Median im Radius); die Lage "
    "rechnet es aus den Ist-Koordinaten der Liste nach — vom Messenden geliefert oder im "
    "siteBits-Viewer am Modell gepickt (dann trifft der Mensch, der pickt, die Marke). "
    "Die Kontrollpunkte selbst prüft siteBits nicht: Ihre Sollkoordinaten und \u03c3 sind Angaben "
    "des Messenden.",
    "Ein deklariertes Bezugssystem belegt nicht, dass die Koordinaten darin stimmen; es belegt "
    "nur, was der Dienstleister angibt.",
    "Formate ohne freien Leser (z. B. RCP, DWG) sind nur auf Vorhandensein geprüft.",
    "Anhänge (z. B. Registrierbericht) werden auf Vorhandensein und Lesbarkeit geprüft, nicht "
    "inhaltlich bewertet.",
    "Punktdichte zählt alle Punkte und Echos, sofern die Zeile keine Klassen vorgibt; eine "
    "Zelle gehört zur Auftragsfläche, wenn ihr Mittelpunkt innen liegt (Ränder unscharf um eine "
    "halbe Zelle). Gezählt wird die gelieferte Datei, eine ausgedünnte Wolke also ausgedünnt.",
    "Die Bodenauflösung gilt für die Bildmitte über der Bodenhöhe aus der Punktwolke; zum "
    "Bildrand und über Dächern weicht sie ab.",
    "Kein Abnahmeprotokoll im Rechtssinn und keine zugesicherte Eigenschaft; die Abnahme "
    "erklärt der Auftraggeber.",
]

DATENSCHUTZ = (
    "Gelesen wurden Dateiköpfe, Prüfsummen, die Punktkoordinaten und Klassenwerte der "
    "Punktwolken sowie von orientierten Fotos nur Lage, Blickrichtung und Brennweite "
    "(images.json). Bildinhalte und Personendaten wurden nicht ausgewertet und stehen nicht im "
    "Bericht. "
    "Die Prüfung lief ohne Netzverbindung am Arbeitsplatz; die Lieferung wurde nicht verändert. "
    "Ablageort und Löschfrist der Lieferung bestimmt der Auftraggeber."
)

ANHANG = [
    "ergebnis.json: jede Zeile mit Urteil, Grund, Belegen, Parametern, Versionen und den "
    "Prüfsummen aller gelesenen Dateien (maschinenlesbar)",
    "pruefsummen.sha256: Prüfsummen der gelesenen Dateien, nachrechenbar mit "
    "„sha256sum -c“ im Lieferordner",
    "lauf.json: Datum, Dauer, Prüfsumme des Ergebnisses",
    "Karten (PNG) je Flächenprüfung: Lücke rot, unter der Schwelle hellblau, erfüllt dunkelblau, "
    "außerhalb der Auftragsfläche grau",
]

# Inhalte, die vor dem Urteil feststehen; Frage, Antwort und Übersicht sichert die Vorlage.
PFLICHT = ("kette", "grenzen", "datenschutz", "anhang")
VORLAGE_PFLICHT = ("Frage:", "$antwort", "$vertrauen", "$uebersicht", "Was der Bericht nicht kann")


def gesamt(zeilen: list[dict[str, Any]]) -> dict[str, Any]:
    zaehlung = {u: sum(z["urteil"] == u for z in zeilen) for u in KLASSE}
    entschieden = zaehlung[ERFUELLT] + zaehlung[NICHT_ERFUELLT]
    if zaehlung[NICHT_ERFUELLT]:
        urteil = NICHT_ERFUELLT
    elif zaehlung[NICHT_PRUEFBAR]:
        urteil = "erfüllt, soweit prüfbar"
    else:
        urteil = ERFUELLT
    return {"urteil": urteil, "zaehlung": zaehlung, "entschieden": entschieden}


def ergebnis_hash(e: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(e, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _antwort(e: dict[str, Any]) -> str:
    g, n = e["gesamt"], len(e["zeilen"])
    z = g["zaehlung"]
    if g["urteil"] == NICHT_ERFUELLT:
        return f"Nein: {z[NICHT_ERFUELLT]} von {n} Anforderungen sind nicht erfüllt."
    if g["urteil"] == ERFUELLT:
        return f"Ja: alle {n} Anforderungen sind erfüllt."
    return (
        f"Soweit prüfbar ja: {z[ERFUELLT]} von {n} Anforderungen erfüllt, "
        f"{z[NICHT_PRUEFBAR]} nicht prüfbar."
    )


def _parameter(p: dict[str, Any]) -> str:
    if not p:
        return "keine"
    return html.escape(json.dumps(p, ensure_ascii=False, sort_keys=True))


def _urteil(u: str) -> str:
    return f'<span class="u-{KLASSE[u]}">{html.escape(u)}</span>'


def feste_teile(e: dict[str, Any]) -> dict[str, str]:
    """Pflichtteile, die vor dem Urteil feststehen: Kette, Grenzen, Datenschutz, Anhang."""
    vers = ", ".join(
        f"{html.escape(k)} {html.escape(v)}" for k, v in e["versionen"].items() if k != "git"
    )
    kat, lf = e["katalog"], e["lieferung"]
    kette = [
        ("Rechenstand (git)", f"<code>{html.escape(e['versionen']['git'])}</code>"),
        ("Bibliotheken", vers),
        (
            "Katalog",
            f"{html.escape(kat['datei'])} „{html.escape(kat['titel'])}“, "
            f"SHA-256 <code>{kat['sha256']}</code>",
        ),
        (
            "Lieferung",
            f"{lf['dateien']} Dateien"
            + (f", {len(lf['links'])} symbolische Links (nicht verfolgt)" if lf["links"] else ""),
        ),
        (
            "siteBits-Paket",
            html.escape(e["paket"]["projectId"]) if e["paket"] else "keins angegeben",
        ),
        (
            "Gelesene Dateien",
            "".join(
                f"{html.escape(r)}: <code>{h}</code><br>" for r, h in e["eingaenge_sha256"].items()
            )
            or "keine",
        ),
        (
            "Eingaben außerhalb der Lieferung",
            "".join(
                f"{html.escape(r)}: <code>{h}</code><br>"
                for r, h in e.get("eingaenge_extern_sha256", {}).items()
            )
            or "keine",
        ),
        ("Wiederholbarkeit", "gleiche Eingaben ergeben dieselbe Ergebnis-Prüfsumme"),
    ]
    return {
        "kette": _tabelle(kette),
        "grenzen": "".join(f"<li>{html.escape(g)}</li>" for g in GRENZEN),
        "datenschutz": html.escape(DATENSCHUTZ),
        "anhang": "".join(f"<li>{html.escape(a)}</li>" for a in ANHANG),
    }


def teile(e: dict[str, Any]) -> dict[str, str]:
    """Alle Textteile des Berichts: feste Teile, Antwort und Übersicht."""
    uebersicht = "".join(
        f"<tr><td>{html.escape(z['id'])}</td><td>{html.escape(z['anforderung'])}</td>"
        f"<td>{_urteil(z['urteil'])}</td><td>{html.escape(z['grund'])}</td></tr>"
        for z in e["zeilen"]
    )
    return {"antwort": html.escape(_antwort(e)), "uebersicht": uebersicht, **feste_teile(e)}


def pflichtteile(t: dict[str, str]) -> list[str]:
    """Fehlende oder leere Pflichtteile (leere Liste = alles da)."""
    fehlt = [k for k in PFLICHT if not t.get(k, "").strip()]
    return fehlt + [f"Vorlage: {m}" for m in VORLAGE_PFLICHT if m not in VORLAGE.template]


def _zeilen(e: dict[str, Any], karten: dict[str, bytes]) -> str:
    out = []
    for z in e["zeilen"]:
        belege = "".join(f"<li>{html.escape(b)}</li>" for b in z["belege"])
        bilder = "".join(
            f'<img class="karte" alt="Karte {html.escape(n)}" '
            f'src="data:image/png;base64,{base64.b64encode(karten[n]).decode()}">'
            for n in z.get("karten", [])
            if n in karten
        )
        out.append(
            f'<div class="zeile"><h3>{html.escape(z["id"])}: {html.escape(z["anforderung"])}</h3>'
            f"<p>{_urteil(z['urteil'])}: {html.escape(z['grund'])}</p>"
            f'<p class="leise">Prüfart „{html.escape(z["pruefart"])}“, Parameter: '
            f"{_parameter(z['parameter'])}</p>"
            + (f'<ul class="belege">{belege}</ul>' if belege else "")
            + bilder
            + "</div>"
        )
    return "".join(out)


def html_bericht(
    e: dict[str, Any],
    t: dict[str, str],
    sha: str,
    erstellt: str,
    karten: dict[str, bytes] | None = None,
) -> str:
    g = e["gesamt"]
    z = g["zaehlung"]
    if z[NICHT_PRUEFBAR]:
        vertrauen = "unvollständig"
        grund = f"({z[NICHT_PRUEFBAR]} Anforderungen ohne Prüfwerkzeug oder Grundlage)"
    else:
        vertrauen, grund = "belegt", "(jede Zeile aus den gelieferten Dateien entschieden)"
    return VORLAGE.substitute(
        titel=html.escape(e["katalog"]["titel"]),
        lieferung=html.escape(e["lieferung"]["name"]),
        katalog_datei=html.escape(e["katalog"]["datei"]),
        erstellt=html.escape(erstellt),
        git=html.escape(e["versionen"]["git"][:12]),
        vertrauen=vertrauen,
        vertrauen_grund=html.escape(grund),
        n_erfuellt=z[ERFUELLT],
        n_nicht_erfuellt=z[NICHT_ERFUELLT],
        n_nicht_pruefbar=z[NICHT_PRUEFBAR],
        zeilen=_zeilen(e, karten or {}),
        sha=sha,
        **t,
    )
