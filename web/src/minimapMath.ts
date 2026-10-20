// Reine Minikarten-Logik (Vitest): Welt ↔ Draufsicht-Pixel (Kantenbezug, Zeile 0 = größtes Y) ↔ Canvas.
import type { Topview, Vec3 } from './manifest';

export interface MapFit {
  /** Canvas-Pixel je Draufsicht-Pixel. */
  scale: number;
  offsetX: number;
  offsetY: number;
}

export function worldToTopview(t: Topview, x: number, y: number): [number, number] {
  return [(x - t.upperLeftWorld[0]) / t.pixelSizeM, (t.upperLeftWorld[1] - y) / t.pixelSizeM];
}

export function topviewToWorld(t: Topview, px: number, py: number): [number, number] {
  return [t.upperLeftWorld[0] + px * t.pixelSizeM, t.upperLeftWorld[1] - py * t.pixelSizeM];
}

/** Draufsicht ganz in den Canvas einpassen, zentriert, Seitenverhältnis erhalten. */
export function fitTopview(t: Topview, w: number, h: number): MapFit {
  const scale = Math.min(w / t.widthPx, h / t.heightPx);
  return { scale, offsetX: (w - t.widthPx * scale) / 2, offsetY: (h - t.heightPx * scale) / 2 };
}

export function worldToCanvas(t: Topview, f: MapFit, x: number, y: number): [number, number] {
  const [px, py] = worldToTopview(t, x, y);
  return [f.offsetX + px * f.scale, f.offsetY + py * f.scale];
}

export function canvasToWorld(t: Topview, f: MapFit, cx: number, cy: number): [number, number] {
  return topviewToWorld(t, (cx - f.offsetX) / f.scale, (cy - f.offsetY) / f.scale);
}

/** Ersatzrahmen ohne topview.png (Paket ohne `overview`): Umriss aus cloud.boundsWorld, 512 px lange Kante. */
export function boundsTopview(bounds: readonly [Vec3, Vec3]): Topview {
  const [lo, hi] = bounds;
  const ex = Math.max(hi[0] - lo[0], 1e-3);
  const ey = Math.max(hi[1] - lo[1], 1e-3);
  const s = Math.max(ex, ey) / 512;
  return {
    url: '',
    widthPx: Math.max(1, Math.round(ex / s)),
    heightPx: Math.max(1, Math.round(ey / s)),
    pixelSizeM: s,
    upperLeftWorld: [lo[0], hi[1]],
    pixelAnchor: 'edge',
    zMaxWorld: null,
  };
}

/**
 * Kartenrahmen, der alle Punkte enthält (Standpunkte des Referenzpakets Kirche liegen teils außerhalb der
 * zugeschnittenen Wolke und damit außerhalb der Draufsicht). Liegen alle Punkte innen, kommt ``t``
 * unverändert zurück. Sonst wächst der Rahmen nur auf den überschrittenen Seiten um ganze Pixel bis
 * ``marginM`` hinter den äußersten Punkt; Pixelgröße bleibt, ``url`` leer (Rahmen, kein Bild).
 */
export function frameFor(t: Topview, pts: readonly (readonly [number, number])[], marginM = 1.5): Topview {
  const s = t.pixelSizeM;
  const [x0, y1] = t.upperLeftWorld;
  const x1 = x0 + t.widthPx * s;
  const y0 = y1 - t.heightPx * s;
  const px = (d: number): number => (d > 0 ? Math.ceil(d / s - 1e-9) : 0);
  let left = 0;
  let right = 0;
  let top = 0;
  let bottom = 0;
  for (const [x, y] of pts) {
    if (x < x0) left = Math.max(left, px(x0 - (x - marginM)));
    if (x > x1) right = Math.max(right, px(x + marginM - x1));
    if (y > y1) top = Math.max(top, px(y + marginM - y1));
    if (y < y0) bottom = Math.max(bottom, px(y0 - (y - marginM)));
  }
  if (left + right + top + bottom === 0) return t;
  return {
    url: '',
    widthPx: t.widthPx + left + right,
    heightPx: t.heightPx + top + bottom,
    pixelSizeM: s,
    upperLeftWorld: [x0 - left * s, y1 + top * s],
    pixelAnchor: 'edge',
    zMaxWorld: t.zMaxWorld,
  };
}
