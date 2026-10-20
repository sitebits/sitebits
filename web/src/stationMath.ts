// Reine Standpunkt-Logik (Vitest): Markerposition, nächster Standpunkt, Nachbarn, Bildschirm-Treffer.
import type { Vec3 } from './manifest';
import type { Station } from './stations';

/** Nachbar-Bodenmarken im Panorama-Modus: höchstens so weit und so viele. */
export const NEIGHBOUR_MAX_M = 25;
export const NEIGHBOUR_MAX_COUNT = 8;
/** Gibt es innerhalb von NEIGHBOUR_MAX_M keinen Nachbarn (weit auseinanderliegende Panorama-Serien,
 *  z. B. Kugelpanoramen im Gehen), gelten die nächsten so vielen bis zu dieser Entfernung — sonst säße man fest. */
export const NEIGHBOUR_FALLBACK_COUNT = 3;
export const NEIGHBOUR_FALLBACK_M = 100;
/** Doppelklick im Panorama: höchstens so viel Grad neben der Klickrichtung. */
export const DIRECTION_MAX_DEG = 15;
/** Trefferradius für Marker in Bildschirmpixeln. */
export const MARKER_PICK_PX = 24;

const dist3 = (a: Vec3, b: Vec3): number => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/** Am Boden, wenn groundZ belegt ist, sonst auf Scannerhöhe (nichts raten). */
export function markerPosition(s: Station): Vec3 {
  const [x, y, z] = s.pose.translation;
  return [x, y, s.groundZ ?? z];
}

/** Nächster Standpunkt in der Draufsicht (XY) — Minikarte. */
export function nearestStation(
  stations: readonly Station[],
  p: readonly [number, number],
  opts: { requirePanorama?: boolean } = {},
): Station | null {
  let best: Station | null = null;
  let bestD = Infinity;
  for (const s of stations) {
    if (opts.requirePanorama && !s.panorama) continue;
    const d = Math.hypot(s.pose.translation[0] - p[0], s.pose.translation[1] - p[1]);
    if (d < bestD) {
      best = s;
      bestD = d;
    }
  }
  return best;
}

/** Nächster Standpunkt im Raum (Scannerzentrum) — POI-Sprung. */
export function nearestStation3d(
  stations: readonly Station[],
  p: Vec3,
  opts: { requirePanorama?: boolean; maxDistM?: number } = {},
): Station | null {
  let best: Station | null = null;
  let bestD = opts.maxDistM ?? Infinity;
  for (const s of stations) {
    if (opts.requirePanorama && !s.panorama) continue;
    const d = dist3(s.pose.translation, p);
    if (d <= bestD) {
      best = s;
      bestD = d;
    }
  }
  return best;
}

/** Nachbarn mit Panorama, nach 3D-Abstand sortiert, ohne den Standpunkt selbst. */
export function neighbours(
  stations: readonly Station[],
  id: string,
  maxDistM = NEIGHBOUR_MAX_M,
  maxCount = NEIGHBOUR_MAX_COUNT,
): Station[] {
  const self = stations.find((s) => s.id === id);
  if (!self) return [];
  const sortiert = stations
    .filter((s) => s.id !== id && s.panorama !== null)
    .map((s) => ({ s, d: dist3(s.pose.translation, self.pose.translation) }))
    .sort((a, b) => a.d - b.d);
  const nah = sortiert.filter(({ d }) => d <= maxDistM).slice(0, maxCount);
  if (nah.length > 0 || maxDistM !== NEIGHBOUR_MAX_M) return nah.map(({ s }) => s);
  return sortiert
    .filter(({ d }) => d <= NEIGHBOUR_FALLBACK_M)
    .slice(0, NEIGHBOUR_FALLBACK_COUNT)
    .map(({ s }) => s);
}

/** Nachbar, dessen Marke höchstens `maxDeg` neben der Richtung `dir` (vom Standpunkt aus) liegt;
 *  der kleinste Winkel gewinnt. Für den Doppelklick im Panorama. */
export function neighbourInDirection(
  stations: readonly Station[],
  fromId: string,
  dir: Vec3,
  maxDeg = DIRECTION_MAX_DEG,
): Station | null {
  const self = stations.find((s) => s.id === fromId);
  const laenge = Math.hypot(dir[0], dir[1], dir[2]);
  if (!self || laenge === 0) return null;
  const t = self.pose.translation;
  let best: Station | null = null;
  let bestDeg = maxDeg;
  for (const n of neighbours(stations, fromId)) {
    const m = markerPosition(n);
    const v: Vec3 = [m[0] - t[0], m[1] - t[1], m[2] - t[2]];
    const lv = Math.hypot(v[0], v[1], v[2]);
    if (lv === 0) continue;
    const cos = (v[0] * dir[0] + v[1] * dir[1] + v[2] * dir[2]) / (lv * laenge);
    const deg = (Math.acos(Math.min(1, Math.max(-1, cos))) * 180) / Math.PI;
    if (deg <= bestDeg) {
      best = n;
      bestDeg = deg;
    }
  }
  return best;
}

export interface ScreenMarker {
  id: string;
  x: number;
  y: number;
  /** Hinter der Kamera bzw. außerhalb near/far — nicht klickbar. */
  behind: boolean;
}

/** Marker unter dem Klick: nächster sichtbarer innerhalb maxPx, sonst null. */
export function pickScreenMarker(
  markers: readonly ScreenMarker[],
  click: { x: number; y: number },
  maxPx = MARKER_PICK_PX,
): string | null {
  let best: string | null = null;
  let bestD = maxPx;
  for (const m of markers) {
    if (m.behind) continue;
    const d = Math.hypot(m.x - click.x, m.y - click.y);
    if (d <= bestD) {
      best = m.id;
      bestD = d;
    }
  }
  return best;
}
