// Drehpunkt im Orbit als Daten (rein, Vitest): Belegung der Maustasten, Erkennen einer Drehung aus zwei Posen und
// die Sichtbarkeit des Markers. Die Darstellung hängt in drehpunktMarker.ts.
import { MOUSE, TOUCH } from 'three';
import type { OrbitLinks } from './bedienEinstellungen';

type V3 = readonly [number, number, number];

/** Wie lange der Marker nach dem letzten Anlass (Setzen, Drehschritt) steht, bevor er ausblendet. */
export const DREHPUNKT_RUHE_MS = 800;

/**
 * Maustasten der Orbit-Steuerung. „drehen“ (Vorgabe, wie CloudCompare/Potree): links dreht um den Drehpunkt,
 * rechts verschiebt. „verschieben“ ist die Belegung bis 0.3.0 (MapControls): links verschiebt, rechts dreht.
 * Umschalt/Strg + links nimmt in three.js jeweils die andere Geste; die Mitte zoomt immer.
 */
export function mausTasten(links: OrbitLinks): { LEFT: MOUSE; MIDDLE: MOUSE; RIGHT: MOUSE } {
  return links === 'drehen'
    ? { LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.PAN }
    : { LEFT: MOUSE.PAN, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.ROTATE };
}

/**
 * Finger der Orbit-Steuerung, passend zur Maus: ein Finger wie links ziehen, zwei Finger zoomen und nehmen die
 * andere Geste. „drehen“ entspricht Sketchfab (ein Finger dreht, zwei zoomen und verschieben); „verschieben“ ist die
 * Vorgabe der MapControls (ein Finger verschiebt, zwei zoomen und drehen).
 */
export function fingerGesten(links: OrbitLinks): { ONE: TOUCH; TWO: TOUCH } {
  return links === 'drehen'
    ? { ONE: TOUCH.ROTATE, TWO: TOUCH.DOLLY_PAN }
    : { ONE: TOUCH.PAN, TWO: TOUCH.DOLLY_ROTATE };
}

const dist = (a: V3, b: V3): number => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/**
 * Reine Drehung zwischen zwei Posen: Drehpunkt und Abstand Kamera–Drehpunkt bleiben, die Kamera bewegt sich.
 * Verschieben bewegt den Drehpunkt, Zoomen ändert den Abstand. Toleranz relativ zum Abstand, weil die Szene in
 * Weltkoordinaten (Hunderttausende Meter) läuft.
 */
export function istDrehung(a: { kamera: V3; ziel: V3 }, b: { kamera: V3; ziel: V3 }): boolean {
  const r = dist(a.kamera, a.ziel);
  const eps = Math.max(1e-6, r * 1e-6);
  if (dist(a.ziel, b.ziel) > eps) return false;
  if (Math.abs(dist(b.kamera, b.ziel) - r) > eps) return false;
  return dist(a.kamera, b.kamera) > eps;
}

/** Marker sichtbar: seit dem letzten Anlass (Zeitpunkt in ms, null = nie) ist die Ruhezeit nicht um. */
export function drehpunktSichtbar(letzterAnlass: number | null, jetzt: number): boolean {
  return letzterAnlass !== null && jetzt - letzterAnlass < DREHPUNKT_RUHE_MS;
}
