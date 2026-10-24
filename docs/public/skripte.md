# Skripte statt Werkzeuge

siteBits lässt sich aus Skripten steuern, mit derselben API v1 und denselben Rechten wie im
Browser. Für Agenten (Sprachmodelle mit Ausführungsumgebung) ist das oft der bessere Weg als
viele einzelne Werkzeugaufrufe.

## Warum

Ein Agent, der Werkzeuge einzeln aufruft, schickt jedes Zwischenergebnis durch das Modell:
die Projektliste, jeden Auswertungsstand, jedes vollständige Ergebnis. Das kostet Tokens und
Zeit, und jede Zwischenantwort ist eine Gelegenheit, sich zu verlesen. Schreibt der Agent
stattdessen ein kurzes Skript gegen eine typisierte Bibliothek, bleiben die Zwischendaten in
seiner Ausführungsumgebung, und ins Modell kommt nur die Ausgabe, etwa drei rote Zeilen statt
dreier vollständiger Prüfergebnisse. Sprachmodelle schreiben solchen Code zuverlässig, und die
Typen zeigen ihnen, welche Felder es gibt.

Der MCP-Server bleibt der einfache Anschluss für Assistenten ohne eigene Ausführungsumgebung.
Beide Wege rufen dieselbe API auf, mit denselben Rechten und derselben Regel „KI schlägt vor,
Mensch prüft“.

## Was es gibt

- **OpenAPI-Beschreibung** der API v1: [`api/openapi.json`](api/openapi.json). Sie enthält genau
  die Operationen, die ein persönliches API-Token erreicht. Die Verwaltung (Konten, Rechte,
  Einladungen, Projekteinstellungen, Nur-Lese-Links) bleibt dem Browser vorbehalten und fehlt
  darin. Die Datei wird aus dem Server erzeugt, ein Test bricht ab, sobald sie nicht mehr zur API
  passt.
- **Client `sitebits`** (`clients/sitebits/`): TypeScript ohne Laufzeit-Abhängigkeiten, nur
  `fetch`. Je Operation gibt es eine typisierte Funktion, die Typen sind aus der OpenAPI-Datei
  erzeugt. Fehler kommen als `SitebitsFehler` mit HTTP-Status und dem Satz der Instanz. Läuft
  ohne Bauschritt mit Node ≥ 22.18 und mit Deno.
- **Beispiele** (`clients/sitebits/beispiele/`):
  - `rote-zeilen.ts`: startet in allen Projekten, in denen das Konto es darf, die Lieferprüfung
    und gibt nur die Zeilen mit dem Urteil „nicht erfüllt“ aus.
  - `pois-csv.ts <projekt>`: POIs eines Projekts als CSV.
  - `auswertung-warten.ts <projekt>`: Auswertung starten, auf das Ende warten, Urteil ausgeben.

## Wie

1. Im Portal unter Einstellungen → API-Zugänge ein Token anlegen. Für einen Agenten „für einen
   KI-Agenten“ ankreuzen: Was er schreibt, wird dann ein Vorschlag, den ein Mensch annimmt.
2. Adresse und Token als Umgebungsvariablen setzen, nicht als Argument (sonst stehen sie in der
   Prozessliste):

   ```sh
   export SITEBITS_URL=https://bits.example.org
   export SITEBITS_TOKEN=sv_…
   node clients/sitebits/beispiele/rote-zeilen.ts
   ```

3. Eigene Skripte importieren den Client:

   ```ts
   import { Sitebits } from './clients/sitebits/src/index.ts';

   const sb = new Sitebits({ basis: process.env.SITEBITS_URL!, token: process.env.SITEBITS_TOKEN! });
   for (const p of (await sb.projekte()).projekte) {
     const offen = (await sb.pois(p.id)).pois.filter(x => x.stand?.status === 'offen');
     console.log(`${p.titel}: ${offen.length} offene Befunde`);
   }
   ```

Ein Agent bekommt am besten den Client, die erzeugten Typen (`src/schema.d.ts`) und ein Token in
seine eigene Ausführungsumgebung. Er schreibt das Skript, führt es dort aus und gibt nur das
Ergebnis weiter.

## Grenzen

- Ein Skript sieht genau, was das Konto des Tokens sieht. Lese-Tokens schreiben nicht, ein Token
  für einen KI-Agenten nimmt keine Vorschläge an und gibt keine Berichte frei.
- Je Token gilt eine Drosselung der Anfragen pro Minute. Schleifen über viele Projekte sollten
  warten statt drängeln; `aufAuswertungWarten` fragt standardmäßig alle 2 s.
- Antworten ohne beschriebenes Schema (z. B. Paketinhalt, Faden, Verlauf) sind im Client als
  `unknown` typisiert: erst prüfen, dann verwenden.
- Die Instanz führt keine fremden Skripte aus. Das Skript läuft immer beim Nutzer oder in der
  Ausführungsumgebung seines Agenten.
