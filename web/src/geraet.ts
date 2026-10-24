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
}

export const DESKTOP: Grenzen = { punkte: 1_000_000, meshBytes: 512 * MIB, panoBytes: 256 * MIB, meshMipsVerwerfen: 0, meshDateiBytes: Number.POSITIVE_INFINITY, meshMobil: false, schattenKarte: 4096 };
export const TOUCH: Grenzen = { punkte: 500_000, meshBytes: 256 * MIB, panoBytes: 64 * MIB, meshMipsVerwerfen: 1, meshDateiBytes: 128 * MIB, meshMobil: true, schattenKarte: 2048 };

export function istTouch(mm: ((q: string) => { matches: boolean }) | undefined = globalThis.matchMedia?.bind(globalThis)): boolean {
  try {
    return mm?.('(pointer: coarse)').matches ?? false;
  } catch {
    return false;
  }
}

export const GRENZEN: Grenzen = istTouch() ? TOUCH : DESKTOP;
