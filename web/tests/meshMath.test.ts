import { describe, expect, it } from 'vitest';
import { datenSparen, gltfToWorld, leseBegrenzt, MeshDateiZuGross, meshRootTransform, shouldLoadFull, stageBytes } from '../src/meshMath';
import type { Vec3 } from '../src/manifest';
it('Mesh-Testfall: glTF-Bounds landen auf Weltbounds (±0,2 mm)', () => {
  const o: Vec3 = [3, 3, 1];
  const a = gltfToWorld(o, [-4.3119, -3.3137, -4.1114]);
  const b = gltfToWorld(o, [4.6884, 2.9629, 4.8889]);
  const soll = [[-1.311872, -1.88887, -2.313722], [7.688344, 7.111371, 3.96288]];
  for (let k = 0; k < 3; k++) {
    expect(Math.abs(Math.min(a[k]!, b[k]!) - soll[0]![k]!)).toBeLessThan(2e-4);
    expect(Math.abs(Math.max(a[k]!, b[k]!) - soll[1]![k]!)).toBeLessThan(2e-4);
  }
});
it('Wurzel dreht +π/2 um X und erhält den Ursprung', () => {
  expect(meshRootTransform([1, 2, 3])).toEqual({ position: [1, 2, 3], rotationX: Math.PI / 2 });
});
it('lädt ausschließlich innerhalb der Raumdiagonale', () => {
  const b: [Vec3, Vec3] = [[0, 0, 0], [3, 4, 0]];
  expect(shouldLoadFull([1.5, 2, 6], b)).toBe(false);
  expect(shouldLoadFull([1.5, 2, 5], b)).toBe(false);
  expect(shouldLoadFull([1.5, 2, 4.9], b)).toBe(true);
});
it('Budget: komprimierte 4096²-Mipmaps und RGBA-Rückfall', () => {
  const t = { width: 4096, height: 4096, mipmaps: true };
  expect(stageBytes({ attributeBytes: 0, indexBytes: 0, textures: [{ ...t, compressed: true }] })).toBe(Math.round(4096 ** 2 * 4 / 3));
  expect(stageBytes({ attributeBytes: 0, indexBytes: 0, textures: [{ ...t, compressed: false }] })).toBe(Math.round(4096 ** 2 * 4 * 4 / 3));
  expect(stageBytes({ attributeBytes: 10, indexBytes: 20, textures: [] })).toBe(30);
});
it('Vollstufe nach Bildgröße: ab einem Viertel der Bildhöhe, nicht bei Datensparen', () => {
  // Würfel 2 m Kante, Diagonale √12 ≈ 3,46 m; Mitte im Ursprung.
  const b: [Vec3, Vec3] = [[-1, -1, -1], [1, 1, 1]];
  const sicht = { fovGrad: 50, sparsam: false };
  // 2·asin(1,732/a) / 50° ≥ 0,25 ⇔ a ≤ 1,732 / sin(6,25°) ≈ 15,9 m
  expect(shouldLoadFull([0, 0, 15.5], b, sicht)).toBe(true);
  expect(shouldLoadFull([0, 0, 16.5], b, sicht)).toBe(false);
  expect(shouldLoadFull([0, 0, 15.5], b, { ...sicht, sparsam: true })).toBe(false);
  expect(shouldLoadFull([0, 0, 3], b, { ...sicht, sparsam: true })).toBe(true);
  expect(shouldLoadFull([0, 0, 15.5], b, { fovGrad: 0, sparsam: false })).toBe(false);
  expect(shouldLoadFull([0, 0, 15.5], b)).toBe(false);
});
it('Datensparen: saveData oder langsame Verbindung; ohne API kein Sparen', () => {
  expect(datenSparen({})).toBe(false);
  expect(datenSparen({ connection: { saveData: true } })).toBe(true);
  expect(datenSparen({ connection: { effectiveType: '3g' } })).toBe(true);
  expect(datenSparen({ connection: { effectiveType: '4g', saveData: false } })).toBe(false);
});

describe('leseBegrenzt', () => {
  const strom = (teile: number[]) => new ReadableStream<Uint8Array>({
    start(c) { teile.forEach((n, i) => c.enqueue(new Uint8Array(n).fill(i + 1))); c.close(); },
  });
  it('liest unter der Grenze vollständig und in Reihenfolge', async () => {
    let abgebrochen = false;
    const b = new Uint8Array(await leseBegrenzt(strom([3, 2]), 5, 10, () => { abgebrochen = true; }));
    expect([...b]).toEqual([1, 1, 1, 2, 2]);
    expect(abgebrochen).toBe(false);
  });
  it('bricht ohne Lesen ab, wenn die gemeldete Länge zu groß ist', async () => {
    let abgebrochen = false;
    await expect(leseBegrenzt(strom([1]), 409_000_000, 128 * 2 ** 20, () => { abgebrochen = true; }))
      .rejects.toMatchObject({ bytes: 409_000_000 });
    expect(abgebrochen).toBe(true);
  });
  it('bricht beim Überschreiten ab, wenn keine Länge gemeldet ist', async () => {
    let abgebrochen = false;
    const fehler = await leseBegrenzt(strom([4, 4, 4]), null, 6, () => { abgebrochen = true; }).catch((e: unknown) => e);
    expect(fehler).toBeInstanceOf(MeshDateiZuGross);
    expect((fehler as MeshDateiZuGross).bytes).toBe(8);
    expect(abgebrochen).toBe(true);
  });
});
