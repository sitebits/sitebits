// Einstellungen der Bedienung (Drehen, Empfindlichkeit, Sichtfeld, Tempo, Bewegung …) als Daten. Rein (Vitest):
// Laden prüft jedes Feld einzeln, klemmt Zahlen auf ihre Grenzen und fällt bei Fremdem auf die Vorgabe zurück.
export type Wahl = 'halten' | 'umschalten';
export interface BedienEinstellungen {
  schema: 1;
  drehenInSchritten: 'auto' | 'an' | 'aus'; // auto = an bei reduzierter Bewegung
  drehSchrittGrad: number;                   // 5–90
  empfindlichkeit: number;                   // 0,1–5
  invertY: boolean;
  sichtfeldGrad: number;                     // 70–100
  tempo: number;                             // m/s, 0,2–50
  vignette: boolean;
  bewegungReduzieren: 'system' | 'an' | 'aus';
  zielen: Wahl;
  schnell: Wahl;
  hoeheHalten: boolean;
  fadenkreuzPx: number;                      // 16–64
  hinweiseImmer: boolean;                    // Tastenhinweise auch nach dreimaliger Ausführung zeigen
  orbitLinks: OrbitLinks;                    // linke Maustaste im Orbit: drehen (Vorgabe) oder verschieben (bis 0.3.0)
}
export type OrbitLinks = 'drehen' | 'verschieben';
export const EINSTELLUNGEN_SCHLUESSEL = 'sv-bedienung';

export const GRENZEN = {
  drehSchrittGrad: { min: 5, max: 90 },
  empfindlichkeit: { min: 0.1, max: 5 },
  sichtfeldGrad: { min: 70, max: 100 },
  tempo: { min: 0.2, max: 50 },
  fadenkreuzPx: { min: 16, max: 64 },
} as const;

export const vorgabeEinstellungen = (): BedienEinstellungen => ({
  schema: 1,
  drehenInSchritten: 'auto',
  drehSchrittGrad: 15,
  empfindlichkeit: 1,
  invertY: false,
  sichtfeldGrad: 80,
  tempo: 2,
  vignette: true,
  bewegungReduzieren: 'system',
  zielen: 'halten',
  schnell: 'halten',
  hoeheHalten: false,
  fadenkreuzPx: 24,
  hinweiseImmer: false,
  orbitLinks: 'drehen',
});

const zahl = (x: unknown, g: { min: number; max: number }, vorgabe: number): number =>
  typeof x === 'number' && Number.isFinite(x) ? Math.min(g.max, Math.max(g.min, x)) : vorgabe;
const wahl = <T extends string>(x: unknown, erlaubt: readonly T[], vorgabe: T): T =>
  erlaubt.includes(x as T) ? (x as T) : vorgabe;
const bool = (x: unknown, vorgabe: boolean): boolean => (typeof x === 'boolean' ? x : vorgabe);

export function ladeEinstellungen(raw: string | null): BedienEinstellungen {
  const v = vorgabeEinstellungen();
  let roh: unknown;
  try { roh = raw === null ? null : JSON.parse(raw); } catch { return v; }
  if (typeof roh !== 'object' || roh === null || (roh as { schema?: unknown }).schema !== 1) return v;
  const r = roh as Record<string, unknown>;
  return {
    schema: 1,
    drehenInSchritten: wahl(r.drehenInSchritten, ['auto', 'an', 'aus'], v.drehenInSchritten),
    drehSchrittGrad: zahl(r.drehSchrittGrad, GRENZEN.drehSchrittGrad, v.drehSchrittGrad),
    empfindlichkeit: zahl(r.empfindlichkeit, GRENZEN.empfindlichkeit, v.empfindlichkeit),
    invertY: bool(r.invertY, v.invertY),
    sichtfeldGrad: zahl(r.sichtfeldGrad, GRENZEN.sichtfeldGrad, v.sichtfeldGrad),
    tempo: zahl(r.tempo, GRENZEN.tempo, v.tempo),
    vignette: bool(r.vignette, v.vignette),
    bewegungReduzieren: wahl(r.bewegungReduzieren, ['system', 'an', 'aus'], v.bewegungReduzieren),
    zielen: wahl(r.zielen, ['halten', 'umschalten'], v.zielen),
    schnell: wahl(r.schnell, ['halten', 'umschalten'], v.schnell),
    hoeheHalten: bool(r.hoeheHalten, v.hoeheHalten),
    fadenkreuzPx: zahl(r.fadenkreuzPx, GRENZEN.fadenkreuzPx, v.fadenkreuzPx),
    hinweiseImmer: bool(r.hinweiseImmer, v.hinweiseImmer),
    orbitLinks: wahl(r.orbitLinks, ['drehen', 'verschieben'], v.orbitLinks),
  };
}

export const speichereEinstellungen = (e: BedienEinstellungen): string => JSON.stringify(e);

export const bewegungReduziert = (e: BedienEinstellungen, system: boolean): boolean =>
  e.bewegungReduzieren === 'system' ? system : e.bewegungReduzieren === 'an';

export const drehenInSchritten = (e: BedienEinstellungen, system: boolean): boolean =>
  e.drehenInSchritten === 'auto' ? bewegungReduziert(e, system) : e.drehenInSchritten === 'an';
