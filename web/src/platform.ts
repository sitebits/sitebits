import { anfrageStatistik, debugAn, fpsAusFrames, zaehleFehler } from './debug';
import type { AnfrageEintrag } from './debug';
import { zeigeDebugOverlay } from './debugOverlay';
import type { DebugWerte } from './debugOverlay';
import { GRENZEN, TOUCH } from './geraet';

// Viewer-Naht zur Plattform — die einzige Kopplungsstelle zwischen dem bestehenden Viewer
// und der Plattform (Portal + Server). main.ts fragt beim Start genau einmal
// GET /api/sitzung ab (Server-Vertrag: 200 JSON {dienst:"scanview",nutzer:{...}} | 401 JSON
// {dienst:"scanview",fehler:...}) und entscheidet danach:
//  - kein JSON, kein dienst==="scanview" (z. B. die nginx-Demo, die auf unbekannte Pfade mit
//    index.html/200 antwortet) → Einzelmodus, unverändertes Verhalten; `sicher`, weil eine Antwort
//    ihn belegt.
//  - Netzfehler, Zeitüberschreitung, kaputtes JSON, Serverfehler (5xx) oder der Plattform-Dienst mit
//    unerwartetem Status → ebenfalls Einzelmodus, aber nur als Rückfall (`sicher: false`): Es kann
//    trotzdem die Plattform dahinterstecken; was je Konto getrennt sein muss (lokale Entwürfe), entfällt.
//  - 200 mit Dienstkennung → Plattformmodus, sofern ein Paket (?p=) gewählt ist; sonst zur Startseite.
//  - 401 mit Dienstkennung → zur Anmeldung, mit Rücksprung auf die aktuelle Ansicht.
//  - 200 mit `gast` (Nur-Lese-Link, F28) für genau das Paket aus ?p= → Einzelmodus mit `gast`: keine
//    Plattformwege (POIs, Messungen, Befunde, Statistik), bereinigtes Manifest vom Server. Mit Konto zählt
//    der Gast nur, wenn die Adresse `gast=1` trägt (die Teilen-Seite leitet so weiter).
// erkenneModus()/zeigeZurueck() bleiben absichtlich klein und ohne Zustand; starteDebug()
// (Debug-Overlay) hängt weiter unten separat an — main.ts ruft beide nacheinander auf.

/** Gast eines Nur-Lese-Links: Projekt, Name des Links (nur Anzeige), Ablauf in Unix-Sekunden. */
export interface GastInfo { projekt: string; name: string; ablauf: number }

export type Modus =
  | { art: 'einzeln'; sicher: boolean; gast?: GastInfo }
  | { art: 'plattform'; name: string; nutzerId: number | null }
  | { art: 'weiterleiten'; ziel: string };

const EINZELN: Modus = { art: 'einzeln', sicher: true };
const RUECKFALL: Modus = { art: 'einzeln', sicher: false };

/** Anfrage-Timeout (Regel: 3 s) — danach gilt der Dienst als nicht erreichbar → Einzelmodus (Rückfall). */
const TIMEOUT_MS = 3000;

function istJson(contentType: string | null): boolean {
  return (contentType ?? '').split(';')[0]?.trim().toLowerCase() === 'application/json';
}

/** Antworten ohne Dienstkennung, die ein echter Einzelbetrieb liefert (Datei, Demo mit 401+index.html, keine
 *  Route). Jeder andere Status — etwa 403/429 eines Vorschaltfilters — ist kein sicherer Beleg für den Einzelmodus. */
const EINZELN_STATUS = new Set([200, 401, 404]);

/**
 * Erkennt anhand von GET /api/sitzung, ob der Viewer einzeln (kein Plattform-Server dahinter) oder
 * eingebettet in die Plattform läuft. `fetchFn`/`loc` sind für Tests austauschbar (siehe
 * tests/platform.test.ts) — der eigentliche Aufruf in main.ts lässt beide Parameter weg.
 */
export async function erkenneModus(
  fetchFn: typeof fetch = fetch,
  loc: Pick<Location, 'pathname' | 'search' | 'hash'> = location,
): Promise<Modus> {
  let res: Response;
  let body: unknown;
  try {
    res = await fetchFn('/api/sitzung', { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (res.status >= 500) return RUECKFALL;
    if (!istJson(res.headers.get('content-type'))) return EINZELN_STATUS.has(res.status) ? EINZELN : RUECKFALL;
    body = await res.json();
  } catch {
    // Netzfehler, Timeout (AbortError) oder ungültiges JSON: Einzelmodus nur als Rückfall.
    return RUECKFALL;
  }
  const dienst = body && typeof body === 'object' ? (body as { dienst?: unknown }).dienst : undefined;
  if (dienst !== 'scanview') return EINZELN_STATUS.has(res.status) ? EINZELN : RUECKFALL;

  if (res.status === 401) {
    // Hash mitnehmen: Kamera/Standpunkt/Messungen leben im URL-Hash (hash.ts) — ohne
    // ihn würde die Anmeldung zwar zurück auf dasselbe Paket springen, aber die Ansicht verlieren.
    // Das Portal (portal/weiter.ts::zielPfad) gibt den Hash beim eigentlichen Rücksprung ebenfalls
    // durch.
    const ziel = `/anmelden?weiter=${encodeURIComponent(loc.pathname + loc.search + loc.hash)}`;
    return { art: 'weiterleiten', ziel };
  }
  if (res.status === 200) {
    const suche = new URLSearchParams(loc.search);
    const p = suche.get('p');
    if (!p) return { art: 'weiterleiten', ziel: '/' };
    const nutzer = (body as { nutzer?: { name?: unknown; id?: unknown } | null }).nutzer;
    const gast = gastAus((body as { gast?: unknown }).gast);
    if (gast && paketId(p) === gast.projekt && (!nutzer || suche.get('gast') === '1')) {
      return { art: 'einzeln', sicher: false, gast };
    }
    // Nur Gast, aber ein anderes Paket: wie ohne Sitzung zur Anmeldung.
    if (!nutzer) return { art: 'weiterleiten', ziel: `/anmelden?weiter=${encodeURIComponent(loc.pathname + loc.search + loc.hash)}` };
    const name = typeof nutzer?.name === 'string' ? nutzer.name : '';
    // Eigene Konto-ID (eigene POIs erkennen); fehlt sie oder ist sie unbrauchbar (älterer Server) → null.
    const id = nutzer?.id;
    const nutzerId = typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? id : null;
    return { art: 'plattform', name, nutzerId };
  }
  return RUECKFALL;
}

const PAKET_RE = /^\/data\/([a-z0-9][a-z0-9-]{0,63})\/?$/;

/** Projekt-ID aus ?p=/data/<id>/ — sonst null. */
export function paketId(p: string): string | null {
  return PAKET_RE.exec(p)?.[1] ?? null;
}

function gastAus(roh: unknown): GastInfo | null {
  if (!roh || typeof roh !== 'object') return null;
  const g = roh as Record<string, unknown>;
  if (typeof g.projekt !== 'string' || typeof g.name !== 'string' || typeof g.ablauf !== 'number' || !Number.isFinite(g.ablauf)) return null;
  return { projekt: g.projekt, name: g.name, ablauf: g.ablauf };
}

/** Gast-Ansicht: das bereinigte Manifest (nur Wolke und Meshes) kommt vom Server statt aus dem Paket;
 *  alle anderen Abrufe (Ebenen-Dateien unter /data/<id>/) laufen unverändert. Für loadManifest(base, …). */
export function gastFetch(fetchFn: typeof fetch = fetch): typeof fetch {
  return (eingabe, init) => {
    const url = typeof eingabe === 'string' ? eingabe : eingabe instanceof URL ? eingabe.href : eingabe.url;
    return /\/project\.json(?:[?#]|$)/.test(url)
      ? fetchFn('/api/teilen/manifest', { credentials: 'same-origin' })
      : fetchFn(eingabe, init);
  };
}

/** Gast-Ansicht: kleine, dauerhafte Kennzeichnung „Nur-Lese-Ansicht … gültig bis …“ (kein Zurück zum Portal). */
export function zeigeGastHinweis(parent: HTMLElement, gast: GastInfo): void {
  const bis = new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeZone: 'Europe/Berlin' }).format(new Date(gast.ablauf * 1000));
  const d = document.createElement('div');
  d.className = 'sv-gast';
  d.textContent = `Nur-Lese-Ansicht · gültig bis ${bis}`;
  d.title = 'Freigegeben per Link: nur Punktwolke und Meshes, ohne Messungen anderer, Befunde oder Fotos.';
  parent.append(d);
}

/**
 * Plattformmodus: Link „← Projekte“ zurück zum Portal (/) einhängen. Wird main.ts als erstes
 * Body-Kind übergeben (`zeigeZurueck(document.body)`) — die Platzierung/das Aussehen kommen
 * ausschließlich aus der Klasse `sv-zurueck` (styles/toolbar.css), kein Inline-Style hier.
 */
export function zeigeZurueck(parent: HTMLElement): void {
  const a = document.createElement('a');
  a.href = '/';
  a.className = 'sv-zurueck';
  a.textContent = '← Projekte';
  parent.prepend(a);
}

// --- Debug-Overlay --------------------------------------------------------------------------
// window.__scanview wird erst ganz am Ende von main.ts::start() gesetzt (nach jedem await) — die
// Werte-Quelle liest es deshalb bei jedem Tick frisch und liefert bis dahin Nullwerte, statt beim
// Anhängen des Overlays (das schon vor der Punktwolke passiert) einmalig danach zu greifen.

type ScanviewFenster = {
  __scanview?: {
    fps?: { frames: number };
    viewer?: { cloud?: { displayedPointCount?: number } };
    meshes?: { stage: string; textur: string; bytes?: number }[];
  };
};

function fensterQuelle(): ScanviewFenster['__scanview'] {
  return (window as unknown as ScanviewFenster).__scanview;
}

/** FPS aus dem Framezähler (main.ts, after-render): Delta über die echte vergangene Zeit — siehe
 *  fpsAusFrames() in debug.ts (Giro3D rendert nur bei Bedarf, kein eigenes requestAnimationFrame). */
function fpsQuelle(): () => number {
  let letzterFrames = 0;
  let letzteZeit = performance.now();
  return () => {
    const frames = fensterQuelle()?.fps?.frames;
    if (frames === undefined) return 0;
    const jetzt = performance.now();
    const wert = fpsAusFrames(frames - letzterFrames, jetzt - letzteZeit);
    letzterFrames = frames;
    letzteZeit = jetzt;
    return wert;
  };
}

function istDatenPfad(name: string): boolean {
  try {
    return new URL(name, location.href).pathname.includes('/data/');
  } catch {
    return false;
  }
}

/**
 * PerformanceObserver auf /data/-Ressourcen (Punktwolke, Panoramen — nicht die eigenen JS/WASM-
 * Bundles oder Portal-Aufrufe). Resource-Timing liefert Einträge erst bei Abschluss (kein eigenes
 * Start-Ereignis) — anfrageStatistik() in debug.ts macht daraus die Näherung „im letzten Fenster
 * fertig geworden" für anfragenLaufend/letzteMs; beide beziehen sich auf `eintraege`, das unten
 * nach 60s getrimmt wird.
 *
 * anfragenFehler ist davon getrennt: ein sitzungskumulativer Zähler
 * (zaehleFehler(), debug.ts), der bei jedem Observer-Tick nur mit den NEU beobachteten Einträgen
 * gefüttert wird — nie mit `eintraege` selbst. Würde er aus `eintraege` neu berechnet, verlöre er
 * ältere Fehler, sobald deren Einträge aus dem 60s-Fenster herausgetrimmt werden.
 */
function anfrageQuelle(): () => Pick<DebugWerte, 'anfragenLaufend' | 'anfragenFehler' | 'letzteMs'> {
  const eintraege: AnfrageEintrag[] = [];
  let fehlerGesamt = 0;
  if (typeof PerformanceObserver !== 'undefined') {
    try {
      const obs = new PerformanceObserver((liste) => {
        const neu: AnfrageEintrag[] = [];
        for (const e of liste.getEntries()) {
          if (!istDatenPfad(e.name)) continue;
          const r = e as PerformanceResourceTiming;
          neu.push({ endeMs: r.responseEnd, dauerMs: r.duration, responseStatus: r.responseStatus });
        }
        eintraege.push(...neu);
        fehlerGesamt = zaehleFehler(fehlerGesamt, neu);
      });
      obs.observe({ type: 'resource', buffered: true });
    } catch {
      // PerformanceObserver mit type:'resource' nicht unterstützt (sehr alte Browser) — die
      // Anfrage-Werte bleiben dann dauerhaft 0/null, FPS/Punkte funktionieren trotzdem.
    }
  }
  return () => {
    const jetzt = performance.now();
    // Sitzungen können sehr lange laufen (ganzer Arbeitstag im Viewer) — alles älter als 60 s
    // verwerfen, sonst wächst das Array unbegrenzt. fehlerGesamt ist davon NICHT betroffen (s. o.).
    while (eintraege.length > 0 && jetzt - eintraege[0]!.endeMs > 60_000) eintraege.shift();
    return { ...anfrageStatistik(eintraege, jetzt), anfragenFehler: fehlerGesamt };
  };
}

/** Mesh-Stufe und Grafikspeicher je Ebene, Gerätegrenzen — für Gerätetests (iPhone 15.10.). */
function debugDetails(s: ReturnType<typeof fensterQuelle>): string {
  const meshes = (s?.meshes ?? []).map((m) => `Mesh ${m.stage}/${m.textur} ${Math.round((m.bytes ?? 0) / 2 ** 20)} MB`);
  return [...meshes, `Gerät ${GRENZEN === TOUCH ? 'Touch' : 'Desktop'}`].join(' · ');
}

/**
 * Hängt das Debug-Overlay an, wenn `an` (Vorgabe debugAn(); der Schalter „Leistungsanzeige“ im Einstellungsdialog
 * übergibt seinen Stand) — sowohl im Einzel- als auch im Plattformmodus (Debug
 * soll auch lokal helfen). Wird von main.ts direkt nach der Weiterleitungs-Prüfung
 * aufgerufen (main.ts bleibt sonst unverändert).
 */
export function starteDebug(parent: HTMLElement, an: boolean = debugAn()): () => void {
  if (!an) return () => undefined;
  const fps = fpsQuelle();
  const anfragen = anfrageQuelle();
  return zeigeDebugOverlay(parent, () => ({
    fps: fps(),
    punkte: fensterQuelle()?.viewer?.cloud?.displayedPointCount ?? 0,
    ...anfragen(),
    details: debugDetails(fensterQuelle()),
  }));
}
