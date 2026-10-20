// Scanfilter im Panorama: „was man sieht, misst man“ — im Panorama zählen nur
// Punkte des eigenen Scans der Station (PointSourceId = Scanindex + 1). Reine Logik ohne
// three.js/Giro3D (Vitest); die Anbindung an die Strahlsuche steht in rayPick.ts.
import { chooseFrontSurface, coarseFallback, depthAlong, frontCluster, pixelOffset, surfaceGapM } from './pickMath';
import type { PointChunk, Ray, Vec3 } from './pickMath';

/**
 * PickInfo.scanFilter: 'on' = nur Punkte des eigenen Scans; 'off-legacy' = Paket ohne
 * cloud.pointSourceIsScan (Altpaket); 'off-unknown' = Station ohne pointSourceId
 * (Carry-over). Im Orbit gibt es keinen Filter und keinen Vermerk.
 */
export type ScanFilterMode = 'on' | 'off-legacy' | 'off-unknown';
export type ScanFilter = { mode: 'on'; pointSourceId: number } | { mode: 'off-legacy' } | { mode: 'off-unknown' };

/** Dauerhinweis beim Betreten des Panoramas, wenn nicht gefiltert werden kann. */
export const SCAN_FILTER_HINT = 'Paket ohne Scan-Zuordnung — Messpunkte können aus anderen Scans stammen';
/** Paket mit Scan-Zuordnung, aber diese Station hat keine gültige pointSourceId (z. B. Carry-over). */
export const STATION_FILTER_HINT = 'Standpunkt ohne Scan-Zuordnung — Messpunkte können aus anderen Scans stammen';

/** Text des Dauerhinweises je Modus; null = kein Hinweis (Filter an). */
export function scanFilterHint(mode: ScanFilterMode): string | null {
  if (mode === 'off-legacy') return SCAN_FILTER_HINT;
  if (mode === 'off-unknown') return STATION_FILTER_HINT;
  return null;
}

/** Gültige Scan-ID: ganze Zahl 1 … 65535 (LAS PointSourceId ist uint16, 0 = unbekannt). */
export function validPointSourceId(x: unknown): x is number {
  return typeof x === 'number' && Number.isInteger(x) && x >= 1 && x <= 65535;
}

/**
 * Aktivierung: nur mit cloud.pointSourceIsScan === true UND einer gültigen Scan-ID der
 * Station. ``station`` null = Orbit → null (kein Filter, kein Vermerk).
 */
export function scanFilterFor(
  pointSourceIsScan: boolean | undefined,
  station: { pointSourceId: number | null } | null,
): ScanFilter | null {
  if (!station) return null;
  if (pointSourceIsScan !== true) return { mode: 'off-legacy' };
  if (!validPointSourceId(station.pointSourceId)) return { mode: 'off-unknown' };
  return { mode: 'on', pointSourceId: station.pointSourceId };
}

/** Lagetoleranz zwischen angezeigtem (Float32, lokal) und dekodiertem Punkt [m]. */
const HIT_MATCH_M = 0.001;

/**
 * Grober Kandidat (Giro3D-Pick: Knoten + Punktindex + Weltlage) gegen die Dekodierung desselben
 * Knotens mit PointSourceId prüfen: 'own' nur, wenn die ID stimmt **und** der dekodierte Punkt am
 * Index dort liegt, wo der Pick ihn gemeldet hat (sonst passen Anzeige und Dekodierung nicht
 * zusammen → 'unverified', der Kandidat fällt weg). Nie wird ein ungeprüfter Punkt zu 'own'.
 */
export type HitVerdict = 'own' | 'foreign' | 'unverified';

export function classifyHit(
  chunk: PointChunk | null,
  index: number,
  point: Vec3,
  sourceId: number,
  tolM: number = HIT_MATCH_M,
): HitVerdict {
  if (!chunk?.ids || !Number.isInteger(index) || index < 0 || index >= chunk.ids.length) return 'unverified';
  const i = 3 * index;
  if (i + 2 >= chunk.positions.length) return 'unverified';
  const dx = chunk.origin[0] + chunk.positions[i]! - point[0];
  const dy = chunk.origin[1] + chunk.positions[i + 1]! - point[1];
  const dz = chunk.origin[2] + chunk.positions[i + 2]! - point[2];
  if (dx * dx + dy * dy + dz * dz > tolM * tolM) return 'unverified';
  return chunk.ids[index] === sourceId ? 'own' : 'foreign';
}

/** Suchfenster hinter dem hintersten Cursor-Kandidaten in Vielfachen der Flächenlücke. */
export const FINE_BACK_GAPS = 3;

/**
 * Tiefenfenster der feinsten Stufe. Die Untergrenze legt die vorderste Fläche **aller** groben
 * Kandidaten fest (auch fremder: sie beginnt dadurch höchstens früher — eine Fläche, die der eigene
 * Scan gesehen hat, darf nie übersprungen werden). Die Obergrenze kommt vom vordersten Cursor-Kandidaten
 * (``nearDepth``, im Panorama mit Filter nur eigene). Gibt es keinen (fremder Verdecker unter dem
 * Cursor), darf der fremde Treffer die Suche nicht begrenzen: dann bis ``farDepth`` (Austritt aus der
 * Wolke) — die feinste Stufe hält an der ersten eigenen Fläche; aus dem Scannerzentrum ist das genau
 * die Fläche, die das Panorama zeigt.
 */
export function fineWindow(frontDepth: number, nearDepth: number | undefined, farDepth?: number): { minDepth: number; maxDepth: number } {
  const minDepth = frontDepth - surfaceGapM(frontDepth);
  // Wie bisher (Orbit unverändert): Lücke der Cursor-Tiefe, ab der tieferen der beiden Tiefen.
  const usual = (d: number): number => Math.max(frontDepth, d) + FINE_BACK_GAPS * surfaceGapM(d);
  if (nearDepth !== undefined) return { minDepth, maxDepth: usual(nearDepth) };
  return { minDepth, maxDepth: Math.max(usual(frontDepth), farDepth ?? 0) };
}

/**
 * Prüfphase der groben Kandidaten unter der Frist (Review 12c): dieselbe Frist wie die feinste Stufe ab
 * Klick. Was bis ``remainingMs`` nicht geurteilt ist (z. B. gedrosselter Fetch bei geleertem Cache),
 * gilt als 'unverified' und fällt weg — nie wird ein ungeprüfter Kandidat eigen. Fehler → 'unverified'.
 * Das Ergebnis ist eine Momentaufnahme; spätere Antworten ändern es nicht.
 */
export async function verdictsWithin<T>(
  items: readonly T[],
  judge: (item: T) => Promise<HitVerdict>,
  remainingMs: number,
): Promise<HitVerdict[]> {
  const out: HitVerdict[] = items.map(() => 'unverified');
  if (items.length === 0) return out;
  const all = Promise.all(
    items.map(async (item, i) => {
      try {
        out[i] = await judge(item);
      } catch (e) {
        console.warn('Scan-ID eines groben Kandidaten nicht lesbar:', e);
      }
    }),
  );
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, Math.max(0, remainingMs));
  });
  try {
    await Promise.race([all, deadline]);
  } finally {
    clearTimeout(timer);
  }
  return [...out];
}

export interface CoarsePlan {
  /** Grobe Kandidaten, die zählen (mit Filter: nur eigene), und davon die im Cursor-Kreis. */
  wide: Vec3[];
  near: Vec3[];
  foreign: number;
  unverified: number;
  /** null = gar kein grober Kandidat → kein Messpunkt. */
  front: {
    frontDepth: number;
    nearDepth?: number;
    /** Grober Ersatz (nur aus ``wide``, also mit Filter nie fremd). */
    coarse: Vec3 | null;
    minDepth: number;
    maxDepth: number;
  } | null;
}

/**
 * Grobe Stufe als reine Funktion: ``verdicts`` null = ohne Scanfilter (Orbit, Altpaket — unverändertes
 * Verhalten); sonst zählen nur 'own'. Die vorderste Fläche (Untergrenze) kommt mit Filter aus allen
 * Kandidaten, die Obergrenze aus eigenen Cursor-Kandidaten, ohne solche bis ``farDepth`` (fineWindow).
 */
export function planCoarse(
  points: readonly Vec3[],
  verdicts: readonly HitVerdict[] | null,
  ray: Ray,
  k: number,
  radiusPx: number,
  farDepth?: number,
): CoarsePlan {
  const self = (p: Vec3): Vec3 => p;
  const wide = verdicts ? points.filter((_, i) => verdicts[i] === 'own') : [...points];
  const foreign = verdicts ? verdicts.filter((x) => x === 'foreign').length : 0;
  const unverified = verdicts ? verdicts.filter((x) => x === 'unverified').length : 0;
  const near = wide.filter((p) => pixelOffset(ray, p, k) <= radiusPx);
  const front = frontCluster(verdicts ? points : wide, self, ray)[0];
  if (front === undefined) return { wide, near, foreign, unverified, front: null };
  const frontDepth = depthAlong(ray, front);
  const nearSeed = chooseFrontSurface(near, self, ray);
  const nearDepth = nearSeed ? depthAlong(ray, nearSeed) : undefined;
  // Grober Ersatz nur mit etwas unter dem Cursor und nie weiter als radiusPx daneben.
  const coarse = coarseFallback(wide, near, self, ray, k, radiusPx);
  const { minDepth, maxDepth } = fineWindow(frontDepth, nearDepth ?? (verdicts ? undefined : frontDepth), farDepth);
  return { wide, near, foreign, unverified, front: { frontDepth, ...(nearDepth !== undefined ? { nearDepth } : {}), coarse, minDepth, maxDepth } };
}
