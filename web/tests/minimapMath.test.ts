import { describe, expect, it } from 'vitest';
import {
  boundsTopview,
  canvasToWorld,
  fitTopview,
  frameFor,
  topviewToWorld,
  worldToCanvas,
  worldToTopview,
} from '../src/minimapMath';
import type { Topview } from '../src/manifest';

const t: Topview = {
  url: 'topview.png', widthPx: 800, heightPx: 600, pixelSizeM: 0.05,
  upperLeftWorld: [1000, 2030], pixelAnchor: 'edge', zMaxWorld: null,
};

describe('Draufsicht-Georeferenz', () => {
  it('obere linke Ecke ist Pixelkante (0, 0), Zeilen laufen nach Süden', () => {
    expect(worldToTopview(t, 1000, 2030)).toEqual([0, 0]);
    const [c1, r1] = worldToTopview(t, 1000.05, 2029.95);
    expect(c1).toBeCloseTo(1, 9);
    expect(r1).toBeCloseTo(1, 9);
    const [, row] = worldToTopview(t, 1000, 2000);
    expect(row).toBeCloseTo(600, 9);
  });
  it('hin und zurück', () => {
    const [x, y] = topviewToWorld(t, ...worldToTopview(t, 1012.345, 2011.111));
    expect(x).toBeCloseTo(1012.345, 9);
    expect(y).toBeCloseTo(2011.111, 9);
  });
  it('Einpassen zentriert und erhält das Seitenverhältnis', () => {
    const f = fitTopview(t, 280, 210);
    expect(f.scale).toBeCloseTo(0.35, 12);
    expect(f.offsetX).toBeCloseTo(0, 12);
    const g = fitTopview(t, 400, 210);
    expect(g.offsetX).toBeCloseTo((400 - 800 * 0.35) / 2, 12);
  });
  it('Canvas ↔ Welt', () => {
    const f = fitTopview(t, 280, 210);
    const [cx, cy] = worldToCanvas(t, f, 1020, 2015);
    const [x, y] = canvasToWorld(t, f, cx, cy);
    expect(x).toBeCloseTo(1020, 9);
    expect(y).toBeCloseTo(2015, 9);
  });
  it('Ersatzrahmen aus boundsWorld ohne topview.png', () => {
    const b = boundsTopview([[0, 0, 0], [40, 30, 5]]);
    expect(b.url).toBe('');
    expect(b.widthPx).toBe(512);
    expect(b.heightPx).toBe(384);
    expect(b.upperLeftWorld).toEqual([0, 30]);
  });
});

describe('frameFor — Standpunkte außerhalb der Draufsicht', () => {
  it('alle Punkte innen → Rahmen unverändert', () => {
    expect(frameFor(t, [[1010, 2010], [1039, 2001]])).toBe(t);
    expect(frameFor(t, [])).toBe(t);
  });
  it('Punkt außerhalb liegt nach dem Einpassen im Canvas', () => {
    const f = frameFor(t, [[1010, 2010], [1055, 1990]]);
    const fit = fitTopview(f, 280, 210);
    const [cx, cy] = worldToCanvas(f, fit, 1055, 1990);
    expect(cx).toBeGreaterThanOrEqual(0);
    expect(cx).toBeLessThanOrEqual(280);
    expect(cy).toBeGreaterThanOrEqual(0);
    expect(cy).toBeLessThanOrEqual(210);
  });
  it('wächst nur auf den überschrittenen Seiten, mit Rand, im Pixelraster', () => {
    const f = frameFor(t, [[1055, 1990]], 1.5); // rechts (x > 1040) und unten (y < 2000)
    expect(f.upperLeftWorld).toEqual([1000, 2030]);
    expect(f.pixelSizeM).toBe(0.05);
    expect(f.url).toBe('');
    expect(f.widthPx).toBe(Math.ceil((1056.5 - 1000) / 0.05 - 1e-9));
    expect(f.heightPx).toBe(Math.ceil((2030 - 1988.5) / 0.05 - 1e-9));
    const g = frameFor(t, [[990, 2035]], 1.5); // links und oben
    expect(g.upperLeftWorld[0]).toBeCloseTo(988.5, 9);
    expect(g.upperLeftWorld[1]).toBeCloseTo(2036.5, 9);
    expect(g.widthPx).toBe(800 + 230);
    expect(g.heightPx).toBe(600 + 130);
  });
});
