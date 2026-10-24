"""Antwortmodelle der API v1 — nur Beschreibung, kein Verhalten.

Die Routen liefern ihre Antworten weiter selbst als `JSONResponse`; diese Modelle stehen nur in
`responses={…}` der Routen und damit in der OpenAPI-Beschreibung (`openapi_v1`). FastAPI
validiert oder filtert damit nichts. Damit die Beschreibung nicht von der Wirklichkeit abweicht,
prüft `tests/test_api_modelle.py` echte Antworten gegen diese Modelle — eigene Felder streng
(`extra="forbid"`), fremde Inhalte (Paketdaten, Prüferausgabe) offen.

Feldnamen genau wie in den Antworten (gemischt deutsch/camelCase, wie gewachsen).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Streng(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Offen(BaseModel):
    model_config = ConfigDict(extra="allow")


# --- Fehler ---------------------------------------------------------------------------------

class Fehler(_Streng):
    """Jede Fehlerantwort (4xx): Dienstkennung und ein Satz für Menschen."""

    dienst: str
    fehler: str
    aktuell: Any = Field(
        default=None,
        description="Nur bei 409 nach Bearbeitungskonflikt: der aktuelle Stand des Objekts.",
    )


# --- Sitzung und Version --------------------------------------------------------------------

class SitzungNutzer(_Streng):
    id: int
    name: str
    email: str
    admin: bool


class Sitzung(_Streng):
    dienst: str
    nutzer: SitzungNutzer
    import_: bool = Field(alias="import", description=(
        "Darf dieses Konto Uploads und neue Projekte anlegen? Nur Admins, nur mit eingeschaltetem "
        "Import (SCANVIEW_IMPORT=1)."))
    quelltext_url: str | None = Field(description="Quelltext-Link der Instanz oder null")


class Version(_Streng):
    version: str


# --- Projekte -------------------------------------------------------------------------------

class Genauigkeit(_Streng):
    """Genauigkeitsnachweis einer Ebene (Punktwolke oder Mesh)."""

    ebene: str
    titel: str
    art: str
    zeilen: list[str]
    nachweis: dict[str, Any] | None = Field(description="Status und Kennzahlen, maschinenlesbar")


class Einstellungen(_Streng):
    vorlage: str | None = Field(description="null = Projekt noch nicht eingerichtet")
    rueckmeldungen: bool
    exportBearbeiter: bool
    befundFaeden: bool
    nurLeseLinks: bool


class Kategorie(_Streng):
    name: str
    aktiv: bool


class ProjektBasis(_Streng):
    """Projektkarte (Antwort auf PATCH /projekte/{pid})."""

    id: str
    titel: str
    beschreibung: str
    punkte: int | None
    standpunkte: int | None
    ausdehnung: list[float] | None = Field(description="Ausdehnung [x, y, z] in m")
    crs: str
    scandatum: str | None
    groesseBytes: int
    einheit: str = Field(description="Koordinaten- und Höhensystem wie im CSV-Kopf")
    draufsicht: bool
    rolle: str = Field(description="Eigene Rolle im Projekt")
    genauigkeit: list[Genauigkeit]
    version: int = Field(description="Version der Metadaten (für PATCH)")
    einstellungen: Einstellungen
    befund_faeden: bool


class Projektkarte(ProjektBasis):
    """Eintrag der Projektliste."""

    ueberfaellig_meine: int = Field(description="Überfällige Befunde, für die ich zuständig bin")


class Projektliste(_Streng):
    projekte: list[Projektkarte]


class Projekt(ProjektBasis):
    """Ein Projekt mit seinen Kategorien."""

    kategorien: list[Kategorie]


# --- POIs und Messungen ---------------------------------------------------------------------

class Punktquelle(_Streng):
    source: str = Field(description="pointcloud oder mesh")
    layerId: str


class PoiDaten(_Streng):
    title: str
    category: str
    note: str
    position: list[float] = Field(description="Weltkoordinaten [x, y, z] in m")
    station: str | None
    positionSource: Punktquelle | None = None


class Zustaendig(_Streng):
    gewerk_id: int | None
    nutzer_id: int | None


class Stand(_Streng):
    """Stand eines Befunds (nur wenn das Projekt Befund-Fäden nutzt)."""

    status: str
    zustaendig: Zustaendig | None
    frist: str | None
    version: int


class _Eintrag(_Streng):
    id: str
    version: int
    paketRevision: int | None
    erstelltVon: int | None
    erstelltAm: int
    geaendertVon: int | None
    geaendertVonName: str | None
    geaendertAm: int
    erstelltVonName: str | None
    herkunft: str = Field(description="mensch oder ki")
    pruefstand: str = Field(description="gueltig, vorschlag oder verworfen")
    loeschenVorgeschlagen: int | None


class Poi(_Eintrag):
    daten: PoiDaten
    stand: Stand | None = None


class Messung(_Eintrag):
    daten: dict[str, Any] = Field(description="kind, points, ggf. areaM2, planarityM, "
                                              "pointSources")


class PoiListe(_Streng):
    pois: list[Poi]


class MessungListe(_Streng):
    messungen: list[Messung]


# --- Auswertungen ---------------------------------------------------------------------------

class Auswertung(_Streng):
    id: str
    typ: str
    optionen: dict[str, str]
    status: str = Field(description="wartend, laufend, fertig, fehlgeschlagen oder abgebrochen")
    erstelltAm: int
    erstelltVon: int | None
    erstelltVonName: str | None
    kanal: str
    begonnenAm: int | None
    beendetAm: int | None
    warteplatz: int | None
    urteil: str | None
    zaehlung: dict[str, int] | None
    fehler: str | None
    freigegeben: bool
    ergebnis: str | None = Field(description="Pfad des Ergebnisses, sobald fertig")
    bericht: str | None = Field(description="Pfad des Berichts, sobald fertig")


class AuswertungMitWorker(Auswertung):
    worker: str = Field(description="Zustand des Workers: aktiv, still oder aus")


class Auswertungsliste(_Streng):
    auswertungen: list[Auswertung]
    worker: str


class ErgebnisKatalog(_Offen):
    titel: str
    sha256: str


class ErgebnisGesamt(_Offen):
    urteil: str = Field(description="erfüllt, nicht erfüllt oder erfüllt, soweit prüfbar")
    zaehlung: dict[str, int]
    entschieden: int


class ErgebnisZeile(_Offen):
    id: str
    urteil: str = Field(description="erfüllt, nicht erfüllt oder nicht prüfbar")
    anforderung: str | None = None
    pruefart: str | None = None
    parameter: dict[str, Any] | None = None
    grund: str | None = None
    belege: list[str] | None = None


class Ergebnis(_Offen):
    """ergebnis.json des Prüfers (`auswerten_pruefen`); weitere Felder möglich."""

    katalog: ErgebnisKatalog
    gesamt: ErgebnisGesamt
    zeilen: list[ErgebnisZeile]


def antwort(modell: type[BaseModel], status: int = 200) -> dict[int | str, dict[str, Any]]:
    """`responses=` einer Route: nur Beschreibung der Erfolgsantwort."""
    return {status: {"model": modell}}


#: App-weit: jede 4xx-Antwort hat dieselbe Form (ersetzt FastAPIs eigenes 422-Schema, das
#: diese App nie liefert).
FEHLERANTWORTEN: dict[int | str, dict[str, Any]] = {
    "4XX": {"model": Fehler, "description": "Fehler"},
}

#: Für Routen mit eigener Antwortklasse (CSV, XLSX, JSON Lines, HTML). FastAPI hängt das Modell
#: aus `FEHLERANTWORTEN` unter den Medientyp der Antwortklasse — dort stünde dann `text/csv` mit
#: dem Fehler-Schema. Die Fehler kommen aber als JSON (`web.fehler`). Diese Fassung ersetzt „4XX“
#: auf Routenebene, ohne `model` (sonst käme der Medientyp der Antwortklasse wieder dazu), und
#: nennt `application/json` ausdrücklich. `tests/test_medientypen.py` prüft gesendete gegen
#: beschriebene Typen.
FEHLER_ALS_JSON: dict[int | str, dict[str, Any]] = {
    "4XX": {
        "description": "Fehler",
        "content": {"application/json": {
            "schema": {"$ref": f"#/components/schemas/{Fehler.__name__}"}}},
    },
}

#: Für HEAD-Routen: Fehler tragen nur Status und Kopfzeilen, nie einen Körper (HTTP). Deshalb
#: „4XX“ ohne `content` — sonst erwartete die Beschreibung ein JSON-Fehlerobjekt, das bei HEAD
#: nie ankommt.
FEHLER_OHNE_KOERPER: dict[int | str, dict[str, Any]] = {
    "4XX": {"description": "Fehler (HEAD: nur Status und Kopfzeilen, kein Körper)"},
}
