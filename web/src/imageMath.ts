// Reine Geometrie der Fotos-Ebene (Vitest): Blickpyramiden und Bildschirm-Treffer.
// Kamera-KS wie images.ts: −Z Blick, +Y oben, +X rechts. Puffer relativ zu einem Ursprung, damit
// UTM-Beträge (5 800 000 m) in Float32 nicht auf Dezimeter gerundet werden.
import type { Vec3 } from './manifest';
import type { OrientedImage } from './images';

/** Trefferradius für Pyramidenspitzen in Bildschirmpixeln. */
export const IMAGE_PICK_PX = 12;

function rotate(q: readonly [number, number, number, number], v: Vec3): Vec3 {
  const [w, x, y, z] = q;
  // v' = v + 2w(q×v) + 2 q×(q×v)
  const cx = y * v[2] - z * v[1], cy = z * v[0] - x * v[2], cz = x * v[1] - y * v[0];
  const ccx = y * cz - z * cy, ccy = z * cx - x * cz, ccz = x * cy - y * cx;
  return [v[0] + 2 * (w * cx + ccx), v[1] + 2 * (w * cy + ccy), v[2] + 2 * (w * cz + ccz)];
}

/** Vier Ecken der Grundfläche in `tiefeM` vor der Kamera, Weltkoordinaten (Float64).
 *  Reihenfolge: unten links, unten rechts, oben rechts, oben links. */
export function cameraFrustumCorners(img: OrientedImage, tiefeM: number): Vec3[] {
  const { focalPx, width, height } = img.intrinsics;
  const hx = (width / 2 / focalPx) * tiefeM;
  const hy = (height / 2 / focalPx) * tiefeM;
  const t = img.pose.translation;
  return ([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]] as const).map(([x, y]) => {
    const r = rotate(img.pose.rotationQuat, [x, y, -tiefeM]);
    return [t[0] + r[0], t[1] + r[1], t[2] + r[2]] as Vec3;
  });
}

/** Je Kamera 8 Liniensegmente (4 Kanten Spitze→Ecke, 4 Kanten der Grundfläche), relativ zu `origin`. */
export function pyramidSegments(images: readonly OrientedImage[], tiefeM: number, origin: Vec3): Float32Array {
  const out = new Float32Array(images.length * 8 * 2 * 3);
  let k = 0;
  const put = (p: Vec3): void => {
    out[k++] = p[0] - origin[0];
    out[k++] = p[1] - origin[1];
    out[k++] = p[2] - origin[2];
  };
  for (const img of images) {
    const c = cameraFrustumCorners(img, tiefeM);
    const apex = img.pose.translation;
    for (let i = 0; i < 4; i++) {
      put(apex);
      put(c[i]!);
    }
    for (let i = 0; i < 4; i++) {
      put(c[i]!);
      put(c[(i + 1) % 4]!);
    }
  }
  return out;
}

export interface ScreenPoint {
  id: string;
  x: number;
  y: number;
  behind: boolean;
  /** Abstand Kamera → Pyramidenspitze [m] (für die Verdeckungsprüfung). */
  tiefeM?: number;
}

/**
 * Nächster Punkt innerhalb `maxPx`; Gleichstand → kleinere ID (deterministisch). `sichtbar` (optional,
 * teuer: ein Pick-Rendering je Aufruf) wird nur für Kandidaten im Fangradius gefragt, nächster zuerst;
 * der erste sichtbare gewinnt.
 */
export function pickNearest(
  points: readonly ScreenPoint[], click: { x: number; y: number }, maxPx = IMAGE_PICK_PX,
  sichtbar?: (p: ScreenPoint) => boolean,
): string | null {
  const kandidaten = points
    .filter((p) => !p.behind)
    .map((p) => ({ p, d: Math.hypot(p.x - click.x, p.y - click.y) }))
    .filter((k) => k.d <= maxPx)
    .sort((a, b) => a.d - b.d || (a.p.id < b.p.id ? -1 : a.p.id > b.p.id ? 1 : 0));
  for (const { p } of kandidaten) if (!sichtbar || sichtbar(p)) return p.id;
  return null;
}

/** Liegt die Punktwolke an der Bildschirmstelle der Spitze deutlich davor (Wand zwischen Betrachter und
 *  Aufnahmeort)? Spiel max(0,3 m, 2 %): Fotos entstehen oft dicht vor der Fläche. */
export function verdecktDurchWolke(spitzeM: number, wolkeM: number | null): boolean {
  return wolkeM !== null && wolkeM < spitzeM - Math.max(0.3, 0.02 * spitzeM);
}

/** Vertikales Sichtfeld (Grad), in das das ganze Foto passt, bei Fenster-Seitenverhältnis `aspect`. */
export function fotoFov(intr: { focalPx: number; width: number; height: number }, aspect: number): number {
  const halbV = intr.height / 2 / intr.focalPx;
  const halbH = intr.width / 2 / intr.focalPx;
  return (2 * Math.atan(Math.max(halbV, halbH / aspect)) * 180) / Math.PI;
}

/** Kleinste Stufe mit langer Kante ≥ `benoetigtPx`, sonst die größte. */
export function stufeFuer(levels: readonly number[], benoetigtPx: number): number {
  return levels.find((l) => l >= benoetigtPx) ?? levels[levels.length - 1]!;
}

/** Kachelebene z (0 = volle Auflösung), sobald mehr als die größte Einzelstufe gebraucht wird; sonst null. */
export function kachelEbene(
  img: { width: number; height: number; levels: readonly number[]; tiles: { size: number; levels: number } | null },
  benoetigtPx: number,
): number | null {
  if (!img.tiles || benoetigtPx <= img.levels[img.levels.length - 1]!) return null;
  const lang = Math.max(img.width, img.height);
  for (let z = img.tiles.levels - 1; z >= 0; z--) if (Math.ceil(lang / 2 ** z) >= benoetigtPx) return z;
  return 0;
}

/** Ansicht im Foto-Fenster: Bildschirm = (x, y) + scale · Bildpixel. */
export interface FotoAnsicht {
  scale: number;
  x: number;
  y: number;
}

/** Foto zentriert und vollständig ins Fenster einpassen. */
export function einpassen(bildB: number, bildH: number, fensterB: number, fensterH: number): FotoAnsicht {
  const scale = Math.min(fensterB / bildB, fensterH / bildH);
  return { scale, x: (fensterB - bildB * scale) / 2, y: (fensterH - bildH * scale) / 2 };
}

/**
 * Ansicht nach einer Größenänderung des Fensters: eingepasst bleibt eingepasst; sonst bleiben
 * Maßstab und der Bildpunkt in der Fenstermitte — außer das Foto wäre kleiner als eingepasst.
 */
export function nachGroesse(
  v: FotoAnsicht, alt: { b: number; h: number }, neu: { b: number; h: number },
  bildB: number, bildH: number, eingepasst: boolean,
): FotoAnsicht {
  const fit = einpassen(bildB, bildH, neu.b, neu.h);
  if (eingepasst || v.scale < fit.scale) return fit;
  return { scale: v.scale, x: v.x + (neu.b - alt.b) / 2, y: v.y + (neu.h - alt.h) / 2 };
}

/** Um den Faktor zoomen; der Bildpunkt unter (px, py) bleibt an seiner Stelle. */
export function zoomUm(v: FotoAnsicht, faktor: number, px: number, py: number): FotoAnsicht {
  const scale = v.scale * faktor;
  return { scale, x: px - (px - v.x) * faktor, y: py - (py - v.y) * faktor };
}

/** Kacheln (x, y) einer Ebene, die im Fenster sichtbar sind; `v` bildet Ebenenpixel ab. */
export function sichtbareKacheln(
  v: FotoAnsicht, fensterB: number, fensterH: number, ebeneB: number, ebeneH: number, groesse: number,
): [number, number][] {
  const x0 = Math.max(0, Math.floor(-v.x / v.scale / groesse));
  const y0 = Math.max(0, Math.floor(-v.y / v.scale / groesse));
  const x1 = Math.min(Math.ceil(ebeneB / groesse) - 1, Math.floor((fensterB - v.x) / v.scale / groesse));
  const y1 = Math.min(Math.ceil(ebeneH / groesse) - 1, Math.floor((fensterH - v.y) / v.scale / groesse));
  const out: [number, number][] = [];
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) out.push([x, y]);
  return out;
}
