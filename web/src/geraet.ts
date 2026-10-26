// Grenzen je Gerät. Touch-Geräte (Handy, Tablet: `pointer: coarse`) bekommen kleinere Budgets — auf dem
// iPhone beendete Safari den Tab (15.10., Pilotpaket: ~410 MB geladen, Mesh-Vollstufe ~485 MB Grafikspeicher,
// JS-Heap ~620 MB, dazu Panoramen). Desktop bleibt wie bisher. Ohne Browser (Vitest, node) gilt Desktop.

const MIB = 2 ** 20;

export interface Grenzen {
  /** Startwert Punktbudget (Regler im Panel „Ansicht“ bleibt frei). */
  punkte: number;
  /** Grafikspeicher aller Mesh-Stufen zusammen. */
  meshBytes: number;
  /** Panorama-Würfeltexturen. */
  panoBytes: number;
  /** So viele schärfste Mipmap-Stufen der Mesh-Texturen verwerfen (1 = halbe Kantenlänge, ¼ Speicher). */
  meshMipsVerwerfen: number;
  /**
   * Größte Vollstufen-Datei (GLB), die überhaupt geladen wird. Das Budget oben greift erst nach dem Entpacken;
   * eine Datei von 390 MB brachte Safari auf dem iPhone schon vorher zum Abbruch (Datei + Entpacken > 3 GB, 17.10.).
   */
  meshDateiBytes: number;
  /** Mobilstufe eines Pakets (`mobileUrl`) statt der Vollstufe laden, wenn vorhanden. */
  meshMobil: boolean;
  /** Kantenlänge der Schattenkarte der Live-Sonne. three 0.180 legt RGBA8 (gepackte Tiefe) + Tiefenpuffer an:
   *  4096² ≈ 128 MiB, 2048² ≈ 32 MiB Grafikspeicher — außerhalb von meshBytes, nur solange die Sonne an ist (sonne.ts gibt sie beim Ausschalten frei). */
  schattenKarte: number;
  /**
   * Gekachelte Meshes (tilesLayer.ts): Bildfehler [px], ab dem der Renderer eine Kachel verfeinert (errorTarget).
   * Größer = gröber, weniger Kacheln und Dreiecke. STARTWERTE, am Gerät nicht gemessen: Desktop 8 = Vorgabe von
   * Giro3D (im Spike mit dem Testdatensatz geladen und bedienbar), Touch 16 = doppelt so grob, zugleich die Vorgabe
   * von 3d-tiles-renderer. Die iPhone-Messung (Leistung, Speicher) steht aus — dann hier nachziehen.
   */
  kachelFehlerPx: number;
  /**
   * Grafikspeicher der geladenen Kacheln (LRU des Renderers, Obergrenze, `maxBytesSize`). Gleich meshBytes: dieselbe
   * Grenze, die auf dem iPhone gemessen wurde (Safari-Abbruch bei ~485 MB Mesh, 15.10.). Der Renderer wirft nur
   * unbenutzte Kacheln hinaus — sichtbare bleiben, also begrenzt auf dem Handy kachelFehlerPx die sichtbare Menge.
   */
  kachelBytes: number;
}

export const DESKTOP: Grenzen = { punkte: 1_000_000, meshBytes: 512 * MIB, panoBytes: 256 * MIB, meshMipsVerwerfen: 0, meshDateiBytes: Number.POSITIVE_INFINITY, meshMobil: false, schattenKarte: 4096, kachelFehlerPx: 8, kachelBytes: 512 * MIB };
export const TOUCH: Grenzen = { punkte: 500_000, meshBytes: 256 * MIB, panoBytes: 64 * MIB, meshMipsVerwerfen: 1, meshDateiBytes: 128 * MIB, meshMobil: true, schattenKarte: 2048, kachelFehlerPx: 16, kachelBytes: 256 * MIB };

export function istTouch(mm: ((q: string) => { matches: boolean }) | undefined = globalThis.matchMedia?.bind(globalThis)): boolean {
  try {
    return mm?.('(pointer: coarse)').matches ?? false;
  } catch {
    return false;
  }
}

export const GRENZEN: Grenzen = istTouch() ? TOUCH : DESKTOP;

/** Anteil, auf den der Renderer beim Aufräumen zurückgeht (Renderer-Vorgabe: 0,3/0,4 GiB = 75 %). */
export const KACHEL_MIN_ANTEIL = 0.75;

export interface KachelBudget { errorTarget: number; maxBytes: number; minBytes: number }

/** Renderer-Einstellungen der gekachelten Meshes aus den Gerätegrenzen (Begründung und Messstand: Grenzen). */
export function kachelBudget(g: Pick<Grenzen, 'kachelFehlerPx' | 'kachelBytes'> = GRENZEN): KachelBudget {
  return { errorTarget: g.kachelFehlerPx, maxBytes: g.kachelBytes, minBytes: Math.floor(g.kachelBytes * KACHEL_MIN_ANTEIL) };
}
