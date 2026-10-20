// Debug-Modus: schaltbar über `?debug=1`/`?debug=0` (nur für
// diesen Aufruf) oder dauerhaft über `localStorage['sv-debug']` (Portal-Einstellungen →
// „Erweitert", seiten.ts). Bewusst ohne Browser-Vollzugriff: jede Browser-API ist einzeln
// abgesichert (typeof-Prüfung oder try/catch), damit die Datei unter Vitest (environment: 'node',
// kein jsdom) ohne Mock läuft — siehe tests/debug.test.ts.

const KEY = 'sv-debug';

declare const __SCANVIEW_VERSION__: string | undefined;

/**
 * localStorage kann fehlen oder werfen (privates Fenster, Kontingent, Node/Tests) — nie eine
 * Ausnahme an den Aufrufer weiterreichen (Muster wie layoutStore.ts::browserStorage). Der direkte
 * Zugriff auf den bloßen Bezeichner `localStorage` (statt `window.localStorage`) ist Absicht: in
 * Node ist er undeklariert und wirft beim Lesen ein catchbares ReferenceError, kein Absturz.
 */
export function sicherLocalStorage(): Storage | null {
  try {
    return localStorage;
  } catch {
    return null;
  }
}

/** Ebenso abgesichert wie sicherLocalStorage() — als Default für debugAn() ohne DOM (Node/Tests). */
function ortSicher(): Pick<Location, 'search'> {
  try {
    return location;
  } catch {
    return { search: '' };
  }
}

/**
 * An bei `?debug=1` oder `localStorage['sv-debug'] === '1'`. `?debug=0` schaltet Debug für diesen
 * Aufruf aus, unabhängig vom gespeicherten Wert (kein Löschen — dafür setzeDebug()).
 */
export function debugAn(
  loc: Pick<Location, 'search'> = ortSicher(),
  speicher: Pick<Storage, 'getItem'> | null = sicherLocalStorage(),
): boolean {
  const param = new URLSearchParams(loc.search).get('debug');
  if (param === '0') return false;
  if (param === '1') return true;
  try {
    return speicher?.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

/** Dauerhaft an/aus (Portal-Einstellungen → „Erweitert"). Speicher kann voll/gesperrt sein — dann
 *  kein Absturz, Debug bleibt für diese Sitzung ggf. nur über ?debug=1 aktivierbar. */
export function setzeDebug(an: boolean): void {
  const s = sicherLocalStorage();
  if (!s) return;
  try {
    if (an) s.setItem(KEY, '1');
    else s.removeItem(KEY);
  } catch {
    // Kontingent voll o. ä. — bewusst kein Fehler nach außen.
  }
}

/** Ausführliches Protokoll in der Browser-Konsole (F12), nur wenn debugAn(). */
export function dlog(bereich: string, ...daten: unknown[]): void {
  if (!debugAn()) return;
  console.debug('[scanview:' + bereich + ']', ...daten);
}

function webglRenderer(): string {
  try {
    if (typeof document === 'undefined') return 'nicht verfügbar (kein DOM)';
    const gl = document.createElement('canvas').getContext('webgl');
    if (!gl) return 'kein WebGL';
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : 'unbekannt (kein WEBGL_debug_renderer_info)';
  } catch {
    return 'nicht verfügbar';
  }
}

/**
 * Diagnose-Momentaufnahme für den Knopf „Diagnose kopieren" (Portal, seiten.ts) — bewusst ohne
 * alles, was eine Person identifiziert: kein Name, keine E-Mail, kein Cookie/Token. Läuft auch
 * ohne DOM (Vitest), deshalb jedes Feld einzeln über typeof abgesichert statt eines direkten
 * Browser-API-Zugriffs.
 */
export function diagnose(): Record<string, string | number | boolean> {
  const version = typeof __SCANVIEW_VERSION__ !== 'undefined' ? __SCANVIEW_VERSION__ : 'dev';
  return {
    version,
    modus: import.meta.env.MODE,
    userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : 'unbekannt',
    webglRenderer: webglRenderer(),
    bildschirm: typeof screen !== 'undefined' ? `${screen.width}×${screen.height}` : 'unbekannt',
    devicePixelRatio: typeof devicePixelRatio !== 'undefined' ? devicePixelRatio : 1,
    zeit: new Date().toISOString(),
  };
}

// --- Anfrage-Statistik für das Debug-Overlay (debugOverlay.ts, verdrahtet in platform.ts) -------
// Reine Auswertung von /data/-Resource-Timing-Einträgen: ein PerformanceObserver liefert Einträge
// erst bei Abschluss (kein eigenes Start-Ereignis) — „anfragenLaufend" ist deshalb eine Näherung
// (wie viele im Fenster vor `jetzt` fertig wurden), keine echte Parallelitäts-Zählung. Getrennt von
// der Observer-Verdrahtung (die braucht PerformanceObserver/window, hier nur Arithmetik) — so
// bleibt die Zählweise ohne DOM testbar.
//
// anfragenLaufend/letzteMs bleiben fensterbasiert (anfrageStatistik() bekommt in
// platform.ts::anfrageQuelle() nur das Anzeige-Array, das dort nach 60s getrimmt wird — sonst
// wüchse es unbegrenzt). anfragenFehler MUSS dagegen sitzungskumulativ sein und darf von diesem
// Trimmen nicht betroffen sein — dafür zaehleFehler(): eine reine Reduktions-Funktion, die der
// Aufrufer bei jedem PerformanceObserver-Tick nur mit den NEU beobachteten Einträgen füttert
// (nie mit dem ganzen, ggf. schon getrimmten Anzeige-Array), sodass ein einmal gezählter Fehler
// nie wieder verschwinden kann.

export interface AnfrageEintrag {
  /** performance.now()-Zeitpunkt, zu dem die Antwort fertig war. */
  endeMs: number;
  /** Antwortdauer in ms. */
  dauerMs: number;
  /** HTTP-Status, falls der Browser ihn preisgibt (Resource Timing Level 2, i. d. R. nur
   *  Chromium); sonst 0 laut Spezifikation — zählt dann nicht als Fehler. */
  responseStatus: number;
}

export interface AnfrageStatistik {
  anfragenLaufend: number;
  letzteMs: number | null;
}

export function anfrageStatistik(
  eintraege: readonly AnfrageEintrag[],
  jetzt: number,
  fensterMs = 1000,
): AnfrageStatistik {
  const imFenster = eintraege.filter((e) => e.endeMs <= jetzt && jetzt - e.endeMs <= fensterMs);
  const letzter = eintraege.reduce<AnfrageEintrag | null>(
    (a, b) => (a === null || b.endeMs > a.endeMs ? b : a),
    null,
  );
  return {
    anfragenLaufend: imFenster.length,
    letzteMs: letzter ? letzter.dauerMs : null,
  };
}

/**
 * Sitzungskumulativer Fehlerzähler: `bisher` ist die bisherige Summe, `neue` sind
 * ausschließlich die seit dem letzten Aufruf frisch beobachteten Einträge (so liefert sie ein
 * PerformanceObserver-Callback von Natur aus — nur die seit dem letzten Callback neu
 * abgeschlossenen Ressourcen). Der Aufrufer (platform.ts) hält `bisher` in einer Variablen und
 * schreibt sie bei jedem Tick fort; ein bereits gezähltes 60s-altes Fehlerereignis bleibt darin
 * erhalten, auch wenn das separate Anzeige-Array (anfrageStatistik) es längst verworfen hat.
 */
export function zaehleFehler(
  bisher: number,
  neue: readonly Pick<AnfrageEintrag, 'responseStatus'>[],
): number {
  return bisher + neue.filter((e) => e.responseStatus >= 400).length;
}

/** FPS aus dem Framezähler in window.__scanview.fps.frames (main.ts, after-render): Delta über die
 *  echte vergangene Zeit statt eigenem requestAnimationFrame — Giro3D rendert nur bei Bedarf, ein
 *  rAF-Zähler zeigte bei stehendem Bild fälschlich ~60 statt der wahren (niedrigen) Renderrate. */
export function fpsAusFrames(deltaFrames: number, deltaMs: number): number {
  if (deltaMs <= 0 || deltaFrames <= 0) return 0;
  return (deltaFrames * 1000) / deltaMs;
}
