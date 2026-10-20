import { describe, expect, it, vi } from 'vitest';
import { bildHuelle, fotoVorfilter, mindestSichtbar, polygonRaster, projiziereFoto, pruefeFoto, sortiereFotos, sucheFotos } from '../src/fotoZuordnung';
import type { OrientedImage } from '../src/images';
import type { Vec3 } from '../src/manifest';

// Messwert für Vitest-Reporter; keine zeitabhängige Schranke auf gemeinsam genutzten Testrechnern.
declare module 'vitest' { interface TaskMeta { fotoVorfilterMs?: number } }

const kamera = (n: number, z = 10): OrientedImage => ({
  id: `F${String(n).padStart(4, '0')}`, label: `Foto ${n}`, group: n % 2 ? 'wand-a' : 'wand-b',
  pose: { translation: [0, 0, z], rotationQuat: [1, 0, 0, 0] },
  intrinsics: { width: 1000, height: 800, focalPx: 1000 }, levels: [512], tiles: null, rev: null,
});
const auswerten = (bilder: OrientedImage[], punkte: Vec3[], tiefe: (b: OrientedImage) => number | null) =>
  sortiereFotos(fotoVorfilter(bilder, punkte).flatMap((k) => { const t = pruefeFoto(k, tiefe); return t ? [t] : []; }));

describe('Fotos zu einer Oberfläche', () => {
  it('Lochkamera: Hauptpunkt, Pixelrichtung, Pose und große Weltkoordinaten', () => {
    const k = kamera(1);
    expect(projiziereFoto(k, [0, 0, 0])).toEqual({ x: 500, y: 400, abstand: 10, mmProPx: 10 });
    expect(projiziereFoto(k, [1, 1, 0])).toMatchObject({ x: 600, y: 300 });
    k.pose.translation = [400000, 5800000, 10];
    // 90° um Y: Blick nach −X, Bild-X zeigt nach −Z.
    k.pose.rotationQuat = [Math.SQRT1_2, 0, Math.SQRT1_2, 0];
    const p = projiziereFoto(k, [399990, 5800000, 9]);
    expect(p?.x).toBeCloseTo(600, 8); expect(p?.y).toBeCloseTo(400, 8);
  });
  it('verwirft Rückseite, >200 m und 2-%-Rand, nicht den gültigen Rand', () => {
    const k = kamera(1);
    expect(projiziereFoto(k, [0, 0, 11])).toBeNull();
    expect(projiziereFoto(k, [0, 0, -191])).toBeNull();
    expect(projiziereFoto(k, [0, 0, -190])).not.toBeNull();
    expect(projiziereFoto(k, [-4.81, 0, 0])).toBeNull();
    expect(projiziereFoto(k, [10, 0, 0], null)?.x).toBe(1500); // Umriss außerhalb des Fotos wird geometrisch weitergeführt.
    expect(projiziereFoto(k, [-4.8, 0, 0])?.x).toBeCloseTo(20);
    expect(projiziereFoto(k, [NaN, 0, 0])).toBeNull();
  });
  it('Würfelwand: acht Kameras, zwei hinter Zwischenwand, eine abgewandt → fünf nach mm/px', () => {
    const bilder = Array.from({ length: 8 }, (_, i) => kamera(i + 1, i + 4));
    bilder[7]!.pose.rotationQuat = [0, 0, 1, 0];
    // Vorderfläche des synthetischen Würfels bei z=0, Zwischenwand 2 m vor Kameras 2 und 6.
    const tiefe = (b: OrientedImage): number => b.pose.translation[2] - ([2, 6].includes(Number(b.id.slice(1))) ? 2 : 0);
    const t = auswerten(bilder, [[0, 0, 0]], tiefe);
    expect(t.map((x) => x.bild.id)).toEqual(['F0001', 'F0003', 'F0004', 'F0005', 'F0007']);
    expect(t.map((x) => x.mmProPx)).toEqual([4, 6, 7, 8, 10]);
    // Negativkontrolle: Wand entfernen → beide zuvor verdeckten Kameras kommen zurück.
    expect(auswerten(bilder, [[0, 0, 0]], (b) => b.pose.translation[2])).toHaveLength(7);
    expect(auswerten(bilder, [[0, 0, 0]], () => null)).toEqual([]);
  });
  it('Bereich über zwei Wände liefert beide Gruppen und zählt nur sichtbare Stichproben', () => {
    const bilder = [kamera(1), kamera(2)]; bilder[1]!.pose.translation[0] = 8;
    const t = auswerten(bilder, [[0, 0, 0], [8, 0, 0]], () => 10);
    expect(new Set(t.map((t) => t.bild.group))).toEqual(new Set(['wand-a', 'wand-b']));
    expect(t.map((t) => t.abdeckung)).toEqual([0.5, 0.5]);
    expect(t.map((t) => t.ungeprueft)).toEqual([[], []]);
  });
  it('Verdeckungstoleranz in Metern und Prozent; fehlende Tiefe bleibt explizit ungeprüft', () => {
    const k = fotoVorfilter([kamera(1)], [[0, 0, 0]])[0]!;
    expect(pruefeFoto(k, () => 9.7)?.sichtbar).toEqual([0]);
    expect(pruefeFoto(k, () => 9.69)).toBeNull();
    expect(pruefeFoto(k, () => null)).toBeNull();
    expect(pruefeFoto(k, () => NaN)).toBeNull();
    const gemischt = fotoVorfilter([kamera(1)], [[0, 0, 0], [1, 0, 0]])[0]!;
    expect(pruefeFoto(gemischt, (_b, _p, i) => i ? null : 10)).toMatchObject({ sichtbar: [0], ungeprueft: [1], abdeckung: 0.5, mmProPx: 10 });
    const fern = fotoVorfilter([kamera(1, 100)], [[0, 0, 0]])[0]!;
    expect(pruefeFoto(fern, () => 98)?.sichtbar).toEqual([0]);
    expect(pruefeFoto(fern, () => 97.9)).toBeNull();
  });
  it('Sortierung: Abdeckung vor Auflösung vor Blickwinkel; deterministische ID bei Gleichstand', () => {
    const k = fotoVorfilter([kamera(2), kamera(1)], [[0, 0, 0], [1, 0, 0]]);
    const alle = k.map((k) => pruefeFoto(k, () => 10)!);
    const wenig = pruefeFoto({ ...k[1]!, bild: kamera(3, 1) }, (_b, _p, i) => i ? 0 : 10)!;
    expect(sortiereFotos([wenig, { ...alle[0]!, winkel: 0.5 }, alle[1]!]).map((x) => x.bild.id)).toEqual(['F0001', 'F0002', 'F0003']);
  });
  it('Raster bleibt im konkaven Polygon, höchstens 25 Punkte und leere Entartung', () => {
    const p = polygonRaster([[0, 0], [10, 0], [10, 5], [5, 5], [5, 10], [0, 10]]);
    expect(p.length).toBeGreaterThan(10); expect(p.length).toBeLessThanOrEqual(25);
    expect(p.every(([x, y]) => x < 5 || y < 5)).toBe(true);
    expect(polygonRaster([[0, 0], [1, 0], [2, 0]])).toEqual([]);
    expect(polygonRaster([])).toEqual([]);
  });
  it('portioniert innerhalb einer Kamera, begrenzt auf 300 und bricht ohne weitere Picks ab', async () => {
    const controller = new AbortController();
    const kandidaten = fotoVorfilter(Array.from({ length: 305 }, (_, i) => kamera(i + 1)), [[0, 0, 0], [1, 0, 0]]);
    const tiefe = vi.fn(() => 10);
    const pause = vi.fn(() => Promise.resolve());
    const t = await sucheFotos({ kandidaten, signal: controller.signal, tiefe, pause, portion: 2, fortschritt: vi.fn() });
    expect(t).toHaveLength(300); expect(tiefe).toHaveBeenCalledTimes(600); expect(pause).toHaveBeenCalledTimes(300);
    tiefe.mockClear();
    await expect(sucheFotos({ kandidaten, signal: controller.signal, tiefe, portion: 1, fortschritt: vi.fn(), pause: () => { controller.abort(); return Promise.resolve(); } })).rejects.toThrow();
    expect(tiefe).toHaveBeenCalledTimes(1);
  });
  it('Benchmark: geometrischer Vorfilter 2.746 × 25 (ohne GPU)', ({ task }) => {
    const bilder = Array.from({ length: 2746 }, (_, i) => kamera(i + 1, 10 + i % 20));
    const punkte: Vec3[] = Array.from({ length: 25 }, (_, i) => [(i % 5 - 2) * 0.1, (Math.floor(i / 5) - 2) * 0.1, 0]);
    fotoVorfilter(bilder, punkte); // JIT vor der Messung aufwärmen.
    const start = performance.now(); const k = fotoVorfilter(bilder, punkte); const ms = performance.now() - start;
    task.meta.fotoVorfilterMs = ms;
    expect(k).toHaveLength(2746);
    expect(k.reduce((n, k) => n + k.pixel.filter(Boolean).length, 0)).toBe(68650);
  });
});

it('Schnittboxhülle begrenzt das Raster auf die Box statt auf das ganze Fenster', () => {
  const h = bildHuelle([[10, 10], [20, 10], [20, 20], [10, 20], [15, 15], [10, 10]]);
  expect(h).toHaveLength(4);
  const raster = polygonRaster(h);
  expect(raster).toHaveLength(25);
  expect(raster.every(([x, y]) => x > 10 && x < 20 && y > 10 && y < 20)).toBe(true);
});

it('zeitbegrenzte Portionen geben auch vor der maximalen Pickzahl den UI-Takt frei', async () => {
  let uhr = 0;
  const pause = vi.fn(() => Promise.resolve());
  const k = fotoVorfilter([kamera(1)], Array.from({ length: 8 }, (): Vec3 => [0, 0, 0]));
  const t = await sucheFotos({ kandidaten: k, signal: new AbortController().signal, tiefe: () => { uhr++; return 10; },
    portion: 64, budgetMs: 2, jetzt: () => uhr, pause, fortschritt: vi.fn() });
  expect(t[0]!.sichtbar).toHaveLength(8);
  expect(pause).toHaveBeenCalledTimes(4);
});

describe('mindestSichtbar', () => {
  it('Punkt braucht 1, Bereich 10 % der Stichproben (aufgerundet)', () => {
    expect([1, 2, 10, 11, 25, 100].map(mindestSichtbar)).toEqual([1, 1, 1, 2, 3, 10]);
  });
});
