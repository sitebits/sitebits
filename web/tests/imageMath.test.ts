import { describe, expect, it } from 'vitest';
import { Matrix4, Quaternion, Vector3 } from 'three';
import { cameraFrustumCorners, einpassen, fotoFov, kachelEbene, nachGroesse, pickNearest, verdecktDurchWolke, pyramidSegments, sichtbareKacheln, stufeFuer, zoomUm } from '../src/imageMath';
import { parseImages } from '../src/images';
import { bild, imagesJson } from './images.test';

const quat = (q: number[]): Quaternion => new Quaternion(q[1], q[2], q[3], q[0]);

describe('Blickpyramiden', () => {
  it('Spitze in der Kamera, Grundfläche in Blickrichtung (−Z der Kamera), Seitenverhältnis wie das Foto', () => {
    // Blick nach unten (Nadir): Kamera −Z = Welt −Z → Einheitsdrehung [1, 0, 0, 0].
    const img = parseImages(imagesJson()).images[0]!;
    const c = cameraFrustumCorners(img, 2);
    const t = img.pose.translation;
    for (const p of c) expect(p[2]).toBeCloseTo(t[2] - 2, 9);
    const breite = Math.abs(c[1]![0] - c[0]![0]);
    const hoehe = Math.abs(c[2]![1] - c[1]![1]);
    expect(breite / hoehe).toBeCloseTo(3700 / 2080, 6); // Differenz zweier UTM-Beträge
    expect(breite).toBeCloseTo(2 * (3700 / 2 / 2800) * 2, 6);
  });
  it('Hochformat bleibt Hochformat', () => {
    const img = parseImages(imagesJson([bild('F0009', { intrinsics: { focalPx: 2800, width: 2080, height: 3700 } })])).images[0]!;
    const c = cameraFrustumCorners(img, 1);
    expect(Math.abs(c[1]![0] - c[0]![0])).toBeLessThan(Math.abs(c[2]![1] - c[1]![1]));
  });
  it('Ecken stimmen mit der allgemeinen Pose überein (beliebige Drehung)', () => {
    const q = new Quaternion().setFromAxisAngle(new Vector3(1, 2, 3).normalize(), 0.7);
    const img = parseImages(imagesJson([bild('F0001', { pose: { translation: [10, 20, 30], rotationQuat: [q.w, q.x, q.y, q.z] } })])).images[0]!;
    const c = cameraFrustumCorners(img, 1.5);
    const m = new Matrix4().compose(new Vector3(10, 20, 30), quat([q.w, q.x, q.y, q.z]), new Vector3(1, 1, 1));
    const lokal = new Vector3(-(3700 / 2) / 2800 * 1.5, -(2080 / 2) / 2800 * 1.5, -1.5).applyMatrix4(m);
    expect(c[0]![0]).toBeCloseTo(lokal.x, 9);
    expect(c[0]![1]).toBeCloseTo(lokal.y, 9);
    expect(c[0]![2]).toBeCloseTo(lokal.z, 9);
  });
  it('3000 Kameras in einer Geometrie: 8 Segmente je Kamera, relativ zum Ursprung (Float32-tauglich)', () => {
    const images = Array.from({ length: 3000 }, (_, i) => bild(`F${String(i + 1).padStart(4, '0')}`, {
      pose: { translation: [400000 + i, 5800000 + i, 30], rotationQuat: [1, 0, 0, 0] } }));
    const set = parseImages({ ...imagesJson(images), groups: [{ id: 'Nadir', title: 'Nadir', count: 3000 }] });
    const origin: [number, number, number] = [400000, 5800000, 30];
    const buf = pyramidSegments(set.images, 1, origin);
    expect(buf).toBeInstanceOf(Float32Array);
    expect(buf.length).toBe(3000 * 8 * 2 * 3);
    // Spitze der ersten Kamera = Ursprung → 0
    expect([buf[0], buf[1], buf[2]]).toEqual([0, 0, 0]);
    expect(Math.max(...Array.from(buf.subarray(0, 48)).map(Math.abs))).toBeLessThan(5);
  });
});

describe('Treffer per Bildschirmabstand', () => {
  it('nächste statt erste, Gleichstand → kleinere ID, hinter der Kamera nie', () => {
    const m = [
      { id: 'F0003', x: 100, y: 100, behind: false },
      { id: 'F0001', x: 108, y: 100, behind: false },
      { id: 'F0002', x: 103, y: 100, behind: false },
      { id: 'F0000', x: 103, y: 100, behind: true },
    ];
    expect(pickNearest(m, { x: 104, y: 100 })).toBe('F0002');
    expect(pickNearest([{ id: 'F0005', x: 10, y: 0, behind: false }, { id: 'F0004', x: -10, y: 0, behind: false }], { x: 0, y: 0 })).toBe('F0004');
    expect(pickNearest(m, { x: 300, y: 300 })).toBeNull();
  });
});


describe('Blick durch die Kamera und Stufenwahl', () => {
  const intr = { focalPx: 2800, width: 3700, height: 2080 };
  it('Sichtfeld so, dass das ganze Foto ins Fenster passt', () => {
    // Breites Fenster: Höhe begrenzt → vertikales FOV des Fotos.
    expect(fotoFov(intr, 2.5)).toBeCloseTo((2 * Math.atan(1040 / 2800) * 180) / Math.PI, 9);
    // Schmales Fenster: Breite begrenzt → vertikales FOV aus der Breite.
    const v = (2 * Math.atan((1850 / 2800) / 1.0) * 180) / Math.PI;
    expect(fotoFov(intr, 1.0)).toBeCloseTo(v, 9);
  });
  it('kleinste Stufe, die reicht; sonst die größte', () => {
    expect(stufeFuer([512, 2048, 3700], 400)).toBe(512);
    expect(stufeFuer([512, 2048, 3700], 513)).toBe(2048);
    expect(stufeFuer([512, 2048, 3700], 9000)).toBe(3700);
  });
  it('Kachelebene erst über der größten Stufe; z=0 ist volle Auflösung', () => {
    const img = { width: 7765, height: 4368, levels: [512, 2048, 4096], tiles: { size: 1024, levels: 2 } };
    expect(kachelEbene(img, 3000)).toBeNull();
    expect(kachelEbene(img, 4096)).toBeNull();
    expect(kachelEbene(img, 5000)).toBe(0);
    expect(kachelEbene({ ...img, tiles: null }, 9000)).toBeNull();
  });
});

describe('Foto-Fenster: Ansicht und Kacheln', () => {
  it('passt das Foto zentriert ein', () => {
    expect(einpassen(4000, 2000, 800, 600)).toEqual({ scale: 0.2, x: 0, y: 100 });
    expect(einpassen(2000, 4000, 800, 600)).toEqual({ scale: 0.15, x: 250, y: 0 });
  });
  it('zoomt um den Mauspunkt: der Bildpunkt darunter bleibt stehen', () => {
    const v = { scale: 0.2, x: 0, y: 100 };
    const z = zoomUm(v, 2, 400, 300);
    // Bildpunkt unter (400, 300) vorher und nachher gleich
    expect((400 - v.x) / v.scale).toBeCloseTo((400 - z.x) / z.scale, 9);
    expect((300 - v.y) / v.scale).toBeCloseTo((300 - z.y) / z.scale, 9);
    expect(z.scale).toBeCloseTo(0.4, 12);
  });
  it('liefert nur die sichtbaren Kacheln der Ebene', () => {
    // Ebene 2048 × 1024 mit 512er Kacheln, Ansicht zeigt Bildbereich x 600–1500, y 0–500.
    const k = sichtbareKacheln({ scale: 1, x: -600, y: 0 }, 900, 500, 2048, 1024, 512);
    expect(k).toEqual([[1, 0], [2, 0]]);
  });
});

describe('Foto-Fenster: Größenänderung', () => {
  it('eingepasst bleibt eingepasst', () => {
    const v = einpassen(4000, 3000, 400, 300);
    expect(nachGroesse(v, { b: 400, h: 300 }, { b: 800, h: 300 }, 4000, 3000, true)).toEqual(einpassen(4000, 3000, 800, 300));
  });
  it('gezoomt: Maßstab bleibt, die Fenstermitte zeigt weiter denselben Bildpunkt', () => {
    const v = zoomUm(einpassen(4000, 3000, 400, 300), 4, 100, 80);
    const n = nachGroesse(v, { b: 400, h: 300 }, { b: 600, h: 500 }, 4000, 3000, false);
    expect(n.scale).toBe(v.scale);
    const bildpunkt = (a: { scale: number; x: number; y: number }, b: number, h: number): [number, number] =>
      [(b / 2 - a.x) / a.scale, (h / 2 - a.y) / a.scale];
    expect(bildpunkt(n, 600, 500)).toEqual(bildpunkt(v, 400, 300));
  });
  it('gezoomt, aber kleiner als das neue Einpassen → einpassen statt Rand zeigen', () => {
    const v = zoomUm(einpassen(4000, 3000, 400, 300), 1.2, 200, 150);
    expect(nachGroesse(v, { b: 400, h: 300 }, { b: 1600, h: 1200 }, 4000, 3000, false)).toEqual(einpassen(4000, 3000, 1600, 1200));
  });
});

describe('Treffer nur auf sichtbare Pyramiden (M10)', () => {
  it('verdeckter nächster Kandidat → der nächste sichtbare gewinnt; keiner sichtbar → null', () => {
    const pts = [
      { id: 'F0001', x: 100, y: 100, behind: false, tiefeM: 20 },
      { id: 'F0002', x: 105, y: 100, behind: false, tiefeM: 8 },
    ];
    expect(pickNearest(pts, { x: 100, y: 100 })).toBe('F0001');
    expect(pickNearest(pts, { x: 100, y: 100 }, undefined, (p) => p.id !== 'F0001')).toBe('F0002');
    expect(pickNearest(pts, { x: 100, y: 100 }, undefined, () => false)).toBeNull();
  });
  it('Filter nur für Kandidaten im Fangradius (keine teure Prüfung für ferne Punkte)', () => {
    const geprueft: string[] = [];
    const pts = [{ id: 'nah', x: 0, y: 0, behind: false }, { id: 'fern', x: 500, y: 0, behind: false }];
    pickNearest(pts, { x: 0, y: 0 }, undefined, (p) => (geprueft.push(p.id), true));
    expect(geprueft).toEqual(['nah']);
  });
  it('verdeckt nur, wenn die Wolke deutlich vor der Spitze liegt', () => {
    expect(verdecktDurchWolke(20, null)).toBe(false); // keine Wolke an der Stelle
    expect(verdecktDurchWolke(20, 25)).toBe(false); // Fläche hinter der Kamera (Fassade)
    expect(verdecktDurchWolke(20, 19.8)).toBe(false); // innerhalb der Toleranz (0,4 m bei 20 m)
    expect(verdecktDurchWolke(20, 12)).toBe(true); // Wand dazwischen
    expect(verdecktDurchWolke(2, 1.8)).toBe(false); // nah: mindestens 0,3 m Spiel
    expect(verdecktDurchWolke(2, 1.5)).toBe(true);
  });
});
