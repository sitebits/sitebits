// Reine Viewer-Logik ohne Giro3D-Import (in Vitest testbar): Farbmodus-Auswahl, Rahmungsabstand,
// Umrechnung Welt ↔ lokales Paket-KS.
import type { Vec3 } from './manifest';

export type ColorMode = 'rgb' | 'intensity' | 'elevation' | 'classification';

/**
 * Höhenskala viridis (fünf Stützstellen, tief → hoch). Die Helligkeit steigt streng monoton, damit die
 * Reihenfolge der Höhen auch bei Farbsehschwäche und in Graustufen erhalten bleibt. Dieselben Farben
 * nutzt der Balken der Höhenlegende (styles/befund.css).
 */
export const VIRIDIS = ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'] as const;

/** Relative Leuchtdichte einer Farbe #rrggbb nach WCAG 2.1 (0 = Schwarz … 1 = Weiß). */
export function relativeLuminanz(hex: string): number {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) throw new Error(`Keine Farbe der Form #rrggbb: ${hex}`);
  const kanal = (h: string): number => {
    const c = parseInt(h, 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * kanal(m[1]!) + 0.7152 * kanal(m[2]!) + 0.0722 * kanal(m[3]!);
}

const HOEHE_FORMAT = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Höhe in Metern für die Legende: deutsch, eine Nachkommastelle, ohne „-0,0“. */
export function hoeheText(m: number): string {
  const r = Math.round(m * 10) / 10;
  return HOEHE_FORMAT.format(r === 0 ? 0 : r);
}

/** Giro3D-Attributname je Farbmodus (gemessen an COPCSource, Giro3D 2.0.4). */
export const ATTRIBUTE_OF: Record<ColorMode, string> = { rgb: 'Color', intensity: 'Intensity', elevation: 'Z', classification: 'Classification' };

/**
 * Farbmodi, die tatsächlich Daten haben. Giro3D meldet für jedes LAS-Punktformat 7 'Color' und
 * 'Intensity', auch wenn die Quelle sie nie geliefert hat (dann stehen dort Nullen) — maßgeblich
 * ist deshalb zusätzlich cloud.attributes aus dem Manifest. Höhe braucht nur 'Z'.
 */
export function availableColorModes(manifestAttributes: readonly string[], supported: readonly string[]): ColorMode[] {
  const inManifest: Record<ColorMode, boolean> = {
    rgb: manifestAttributes.includes('rgb'),
    intensity: manifestAttributes.includes('intensity'),
    elevation: true,
    // F30: nur, wenn der Import Klassen außer 0/1 gefunden hat (cloud.attributes „classification“).
    classification: manifestAttributes.includes('classification'),
  };
  return (Object.keys(ATTRIBUTE_OF) as ColorMode[]).filter((k) => inManifest[k] && supported.includes(ATTRIBUTE_OF[k]));
}

/** Abstand, aus dem die Hüllkugel der Bounding-Box vollständig in das vertikale Sichtfeld passt. */
export function fitDistance(bounds: readonly [Vec3, Vec3], verticalFovDeg: number): number {
  const [lo, hi] = bounds;
  const radius = Math.max(Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2, 1);
  return radius / Math.sin((verticalFovDeg * Math.PI) / 360);
}

/** Welt → lokales Paket-KS (Ursprung originWorld, nicht der LAS-Offset cloud.offset). */
export const worldToLocal = (origin: Vec3, p: Vec3): Vec3 => [p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]];
export const localToWorld = (origin: Vec3, p: Vec3): Vec3 => [p[0] + origin[0], p[1] + origin[1], p[2] + origin[2]];

/** Grenzen des Near-Plane-Bodens in Metern (siehe nearPlaneFor). */
export const NEAR_MIN_M = 0.01;
export const NEAR_MAX_M = 2;

/**
 * Untergrenze der Near-Plane abhängig vom Abstand Kamera–Drehpunkt: dist/100, geklemmt auf
 * 1 cm … 2 m. Nah am Objekt (Zoom auf wenige cm) bleibt nichts weggeschnitten, bei langen
 * Sichtachsen bleibt das Verhältnis far/near — und damit die Auflösung der 16-bit-Tiefentextur
 * für EDL — begrenzt. 2 m entspricht der Giro3D-Vorgabe (DEFAULT_MIN_NEAR_PLANE).
 */
export function nearPlaneFor(distanceToTarget: number): number {
  if (!Number.isFinite(distanceToTarget)) return NEAR_MAX_M;
  return Math.min(NEAR_MAX_M, Math.max(NEAR_MIN_M, distanceToTarget / 100));
}

/** Grenze der Neigung beim Drehen um das Ziel: nie ganz senkrecht, sonst kippt die Blickrichtung. */
export const ORBIT_NEIGUNG_MAX_GRAD = 85;
/** Kleinster Abstand der Kamera zum Ziel beim schrittweisen Heranzoomen. */
export const ORBIT_ABSTAND_MIN_M = 0.5;
const GRAD = Math.PI / 180;

/**
 * Dreht die Kamera in festen Schritten um das Ziel (Z oben): dYawGrad um die senkrechte Achse (positiv =
 * gegen den Uhrzeigersinn von oben gesehen), dPitchGrad hebt die Kamera an. Der Abstand bleibt, die Neigung
 * der Kamera über dem Ziel wird auf ±85° begrenzt — eine schon steilere Ausgangslage bleibt erhalten (sie wird
 * nur nicht noch steiler). Kamera im Ziel oder ungültige Schritte: unverändert.
 */
export function orbitSchritt(cam: Vec3, tgt: Vec3, dYawGrad: number, dPitchGrad: number): Vec3 {
  const d: Vec3 = [cam[0] - tgt[0], cam[1] - tgt[1], cam[2] - tgt[2]];
  const r = Math.hypot(d[0], d[1], d[2]);
  if (!(r > 0) || !Number.isFinite(dYawGrad) || !Number.isFinite(dPitchGrad)) return [cam[0], cam[1], cam[2]];
  const yaw = Math.atan2(d[1], d[0]) + dYawGrad * GRAD;
  const alt = Math.atan2(d[2], Math.hypot(d[0], d[1]));
  const max = Math.max(ORBIT_NEIGUNG_MAX_GRAD * GRAD, Math.abs(alt));
  const pitch = Math.min(max, Math.max(-max, alt + dPitchGrad * GRAD));
  return [
    tgt[0] + r * Math.cos(pitch) * Math.cos(yaw),
    tgt[1] + r * Math.cos(pitch) * Math.sin(yaw),
    tgt[2] + r * Math.sin(pitch),
  ];
}

/** Kamera auf der Geraden zum Ziel, Abstand × faktor, mindestens 0,5 m bzw. `minAbstand` (Zoom-Stopp vor einer
 *  Mesh-Oberfläche, zoomStopp.ts) — liegt sie schon näher, schiebt Heranzoomen sie nicht hinaus. Kamera im Ziel oder
 *  Faktor ≤ 0: unverändert. */
export function orbitZoom(cam: Vec3, tgt: Vec3, faktor: number, minAbstand = 0): Vec3 {
  const d: Vec3 = [cam[0] - tgt[0], cam[1] - tgt[1], cam[2] - tgt[2]];
  const r = Math.hypot(d[0], d[1], d[2]);
  if (!(r > 0) || !(faktor > 0) || !Number.isFinite(faktor)) return [cam[0], cam[1], cam[2]];
  const k = Math.max(Math.min(r, Math.max(ORBIT_ABSTAND_MIN_M, minAbstand)), r * faktor) / r;
  return [tgt[0] + d[0] * k, tgt[1] + d[1] * k, tgt[2] + d[2] * k];
}
