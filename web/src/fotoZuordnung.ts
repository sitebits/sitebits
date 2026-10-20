// Reine, serverseitig nachbildbare Zuordnung. Tiefenquelle ist austauschbar, keine GPU-/DOM-Abhängigkeit.
import type { OrientedImage } from './images';
import type { Vec3 } from './manifest';
import { verdecktDurchWolke } from './imageMath';

export interface FotoProjektion { x: number; y: number; abstand: number; mmProPx: number }
export interface FotoKandidat { bild: OrientedImage; pixel: (FotoProjektion | null)[]; winkel: number }
export interface FotoTreffer extends FotoKandidat { sichtbar: number[]; ungeprueft: number[]; abdeckung: number; mmProPx: number }
export type Tiefenquelle = (bild: OrientedImage, pixel: FotoProjektion, index: number) => number | null;

export type SuchTiefenquelle = (bild: OrientedImage, pixel: FotoProjektion, index: number, signal?: AbortSignal) => number | null | Promise<number | null>;

/** Inverse Weltpose, Blick −Z; 2 % Sicherheitsrand. mm/px als Strahlabstand/f. */
export function projiziereFoto(bild: OrientedImage, punkt: Vec3, rand: number | null = 0.02): FotoProjektion | null {
  const t = bild.pose.translation;
  const vx = punkt[0] - t[0], vy = punkt[1] - t[1], vz = punkt[2] - t[2];
  const [w, qx, qy, qz] = bild.pose.rotationQuat;
  const x = -qx, y = -qy, z = -qz;
  const cx = y * vz - z * vy, cy = z * vx - x * vz, cz = x * vy - y * vx;
  const px = vx + 2 * (w * cx + y * cz - z * cy);
  const py = vy + 2 * (w * cy + z * cx - x * cz);
  const pz = vz + 2 * (w * cz + x * cy - y * cx);
  const abstand = Math.hypot(vx, vy, vz);
  if (!(pz < 0) || !(abstand <= 200)) return null;
  const { width, height, focalPx } = bild.intrinsics;
  const u = width / 2 + focalPx * px / -pz;
  const v = height / 2 - focalPx * py / -pz;
  if (rand !== null && !(u >= width * rand && u <= width * (1 - rand) && v >= height * rand && v <= height * (1 - rand))) return null;
  return { x: u, y: v, abstand, mmProPx: abstand / focalPx * 1000 };
}

export function fotoVorfilter(bilder: readonly OrientedImage[], punkte: readonly Vec3[], normale?: Vec3): FotoKandidat[] {
  return bilder.flatMap((bild) => {
    const pixel = punkte.map((p) => projiziereFoto(bild, p));
    if (!pixel.some(Boolean)) return [];
    const p = punkte[0]!;
    const d = bild.pose.translation.map((x, i) => x - p[i]!) as Vec3;
    const winkel = normale ? 1 - Math.abs(d.reduce((s, x, i) => s + x * normale[i]!, 0)) / (Math.hypot(...d) * Math.hypot(...normale) || 1) : 0;
    return [{ bild, pixel, winkel }];
  });
}

export function pruefeFoto(k: FotoKandidat, tiefe: Tiefenquelle): FotoTreffer | null {
  const sichtbar: number[] = [], ungeprueft: number[] = [];
  let mm = 0;
  k.pixel.forEach((p, i) => {
    if (!p) return;
    const d = tiefe(k.bild, p, i);
    // Eine Lücke im Pick ist kein Sichtbarkeitsnachweis. Nur bestätigte
    // Stichproben zählen für Treffer und Abdeckung (auch bei GPU-/LOD-Lücken).
    if (d === null || !Number.isFinite(d)) { ungeprueft.push(i); return; }
    if (verdecktDurchWolke(p.abstand, d)) return;
    sichtbar.push(i);
    mm += p.mmProPx;
  });
  return sichtbar.length >= mindestSichtbar(k.pixel.length)
    ? { ...k, sichtbar, ungeprueft, abdeckung: sichtbar.length / k.pixel.length, mmProPx: mm / sichtbar.length } : null;
}

/** Mindestzahl sichtbarer Stichproben, ab der ein Foto als Treffer zählt. Punkt: 1. Bereich: 10 % der Stichproben
 *  (aufgerundet). Grund: Auf dünn abgetasteten Flächen rutscht einzelner Bildschirm-Pick durch eine Lücke auf die
 *  Fläche dahinter; diese eine Stichprobe machte sonst Kameras zum Treffer, die die gewählte Fläche gar nicht sehen
 *  (Würfelwand: Kameras hinter dem Quader „1 von 25 Stellen“). Bewusst strenger als „≥ 1“ (Stand 16.10.2026). */
export const mindestSichtbar = (stichproben: number): number => (stichproben <= 1 ? 1 : Math.max(1, Math.ceil(stichproben * 0.1)));

export const sortiereFotos = (treffer: FotoTreffer[]): FotoTreffer[] => treffer.sort((a, b) =>
  b.abdeckung - a.abdeckung || a.mmProPx - b.mmProPx || a.winkel - b.winkel || a.bild.id.localeCompare(b.bild.id));

export const sortiereKandidaten = (k: FotoKandidat[]): FotoKandidat[] => k.sort((a, b) => {
  const anzahl = (x: FotoKandidat): number => x.pixel.filter(Boolean).length;
  const mm = (x: FotoKandidat): number => Math.min(...x.pixel.flatMap((p) => p ? [p.mmProPx] : []));
  return anzahl(b) - anzahl(a) || mm(a) - mm(b) || a.winkel - b.winkel || a.bild.id.localeCompare(b.bild.id);
});

export type Bildpunkt = readonly [number, number];
export function imPolygon(p: Bildpunkt, polygon: readonly Bildpunkt[]): boolean {
  let innen = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i]!, b = polygon[j]!;
    if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]) innen = !innen;
  }
  return innen;
}

/** Bis zu 25 gleichmäßig verteilte Rasterstellen innerhalb des Bildschirm-Polygons. */
export function polygonRaster(polygon: readonly Bildpunkt[]): Bildpunkt[] {
  if (polygon.length < 3) return [];
  const xs = polygon.map((p) => p[0]), ys = polygon.map((p) => p[1]);
  const x = Math.min(...xs), y = Math.min(...ys), w = Math.max(...xs) - x, h = Math.max(...ys) - y;
  if (!(w > 0 && h > 0)) return [];
  const out: Bildpunkt[] = [];
  for (let j = 0; j < 5; j++) for (let i = 0; i < 5; i++) {
    const p: Bildpunkt = [x + (i + 0.5) / 5 * w, y + (j + 0.5) / 5 * h];
    if (imPolygon(p, polygon)) out.push(p);
  }
  return out;
}

/** Maximal 300 Kameras; auch innerhalb einer Kamera portionieren. Abbruch liefert keine Teilliste. */
export async function sucheFotos(o: {
  kandidaten: FotoKandidat[]; tiefe: SuchTiefenquelle; signal: AbortSignal;
  pause(): Promise<void>; portion: number; budgetMs?: number; jetzt?(): number; fortschritt(fertig: number, gesamt: number): void;
}): Promise<FotoTreffer[]> {
  const kandidaten = sortiereKandidaten([...o.kandidaten]).slice(0, 300);
  const ergebnis: FotoTreffer[] = [];
  let picks = 0;
  const jetzt = o.jetzt ?? (() => performance.now());
  let beginn = jetzt();
  const abbruch = (): void => { o.signal.throwIfAborted(); };
  for (const [index, k] of kandidaten.entries()) {
    abbruch();
    const tiefen = new Map<number, number | null>();
    for (const [i, p] of k.pixel.entries()) {
      if (!p) continue;
      abbruch();
      tiefen.set(i, await o.tiefe(k.bild, p, i, o.signal));
      abbruch();
      if (++picks >= o.portion || jetzt() - beginn >= (o.budgetMs ?? Infinity)) {
        o.fortschritt(index, kandidaten.length); await o.pause(); abbruch(); picks = 0; beginn = jetzt();
      }
    }
    const t = pruefeFoto(k, (_b, _p, i) => tiefen.get(i) ?? null);
    if (t) ergebnis.push(t);
    o.fortschritt(index + 1, kandidaten.length);
  }
  return sortiereFotos(ergebnis);
}

/** Konvexe Bildschirmhülle einer Schnittbox (acht projizierte Ecken). */
export function bildHuelle(punkte: readonly Bildpunkt[]): Bildpunkt[] {
  const p = [...punkte].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const kreuz = (a: Bildpunkt, b: Bildpunkt, c: Bildpunkt): number => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const teil = (liste: Bildpunkt[]): Bildpunkt[] => {
    const out: Bildpunkt[] = [];
    for (const x of liste) { while (out.length > 1 && kreuz(out[out.length - 2]!, out[out.length - 1]!, x) <= 0) out.pop(); out.push(x); }
    return out.slice(0, -1);
  };
  return p.length < 3 ? p : [...teil(p), ...teil([...p].reverse())];
}
