import { describe, expect, it } from 'vitest';
import { BackSide, BufferAttribute, BufferGeometry, DoubleSide, FrontSide, InterleavedBuffer, InterleavedBufferAttribute,
  Mesh, MeshBasicMaterial, Raycaster, Vector3 } from 'three';
import type { Intersection, Object3D, Side } from 'three';
import { strahlSchnitte } from '../src/meshStrahl';

// Zufall mit festem Startwert: Fehlschläge lassen sich nachstellen.
const zufall = (s: number) => () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32; };

/** Kugelförmige Wolke aus Dreiecken: kleine Dreiecke in Kugelschale, dazu große quer durch. */
function geometrie(r: () => number, n: number, art: 'index' | 'ohneIndex' | 'u16' | 'verschraenkt' | 'normalisiert'): BufferGeometry {
  const vs = n * 3; const xyz: number[] = [];
  for (let t = 0; t < n; t++) {
    const cx = (r() - 0.5) * 20, cy = (r() - 0.5) * 20, cz = (r() - 0.5) * 20; const g = t % 50 === 0 ? 8 : 0.8;
    for (let k = 0; k < 3; k++) xyz.push(cx + (r() - 0.5) * g, cy + (r() - 0.5) * g, cz + (r() - 0.5) * g);
  }
  const g = new BufferGeometry();
  if (art === 'u16' || art === 'normalisiert') {
    const q = new Uint16Array(vs * 3); for (let i = 0; i < q.length; i++) q[i] = Math.round((xyz[i]! + 15) / 30 * 65535);
    g.setAttribute('position', new BufferAttribute(q, 3, art === 'normalisiert'));
  } else if (art === 'verschraenkt') {
    const f = new Float32Array(vs * 5); for (let v = 0; v < vs; v++) f.set([xyz[v*3]!, xyz[v*3+1]!, xyz[v*3+2]!], v * 5 + 1);
    g.setAttribute('position', new InterleavedBufferAttribute(new InterleavedBuffer(f, 5), 3, 1));
  } else g.setAttribute('position', new BufferAttribute(new Float32Array(xyz), 3));
  if (art !== 'ohneIndex') {
    // Vertices teilweise geteilt, Reihenfolge gemischt.
    const idx = new Uint32Array(vs); for (let i = 0; i < vs; i++) idx[i] = r() < 0.2 ? Math.floor(r() * vs) : i;
    g.setIndex(new BufferAttribute(idx, 1));
  }
  return g;
}

const kurz = (hs: Intersection[]) => hs.map((h) => [h.distance, h.point.x, h.point.y, h.point.z, h.faceIndex, h.object.id, h.face?.a, h.face?.b, h.face?.c, h.face?.materialIndex]);

function vergleiche(objekte: Object3D[], r: () => number, strahlen: number, near = 0, far = Infinity): number {
  let treffer = 0;
  for (let s = 0; s < strahlen; s++) {
    const o = new Vector3((r() - 0.5) * 60, (r() - 0.5) * 60, (r() - 0.5) * 60);
    const d = new Vector3((r() - 0.5) * 20, (r() - 0.5) * 20, (r() - 0.5) * 20).sub(o);
    // Achsparallele Strahlen gezielt mitprüfen (Richtung 0 in einer Achse).
    if (s % 7 === 0) d.x = 0; if (s % 11 === 0) { d.y = 0; d.z = 0; d.x = d.x || 1; }
    const rc = new Raycaster(o, d.normalize(), near, far);
    const soll = rc.intersectObjects(objekte, false); const ist = strahlSchnitte(rc, objekte);
    expect(kurz(ist)).toEqual(kurz(soll));
    treffer += soll.length;
  }
  return treffer;
}

describe('strahlSchnitte', () => {
  for (const art of ['index', 'ohneIndex', 'u16', 'verschraenkt', 'normalisiert'] as const) {
    for (const side of [FrontSide, BackSide, DoubleSide] as Side[]) {
      it(`bitgleich mit three: ${art}, side ${side}`, () => {
        const r = zufall(7 + side);
        const m = new Mesh(geometrie(r, 3000, art), new MeshBasicMaterial({ side }));
        m.position.set(1.5, -2, 0.25); m.rotation.set(0.3, -0.7, 1.1); m.scale.set(1.2, 0.9, 1.1);
        if (art === 'u16' || art === 'normalisiert') m.scale.multiplyScalar(art === 'u16' ? 30 / 65535 : 30);
        m.updateMatrixWorld(true);
        expect(vergleiche([m], r, 300)).toBeGreaterThan(100);
      });
    }
  }

  it('Gruppen mit Materialfeld, drawRange, near/far und mehrere Meshes', () => {
    const r = zufall(42);
    const g = geometrie(r, 4000, 'index');
    g.addGroup(0, 3000, 0); g.addGroup(3000, 4500, 1); g.addGroup(7500, 4500, 2); g.addGroup(4, 900, 0);
    g.setDrawRange(300, 10500);
    const a = new Mesh(g, [new MeshBasicMaterial({ side: FrontSide }), new MeshBasicMaterial({ side: BackSide }), new MeshBasicMaterial({ side: DoubleSide })]);
    const b = new Mesh(geometrie(r, 2000, 'ohneIndex'), new MeshBasicMaterial({ side: DoubleSide }));
    b.position.set(3, 0, -1); b.updateMatrixWorld(true); a.updateMatrixWorld(true);
    expect(vergleiche([a, b], r, 200)).toBeGreaterThan(50);
    expect(vergleiche([a, b], r, 200, 25, 45)).toBeGreaterThan(0);
  });

  it('Geometrie geändert: Blöcke werden neu gerechnet', () => {
    const r = zufall(3);
    const m = new Mesh(geometrie(r, 1000, 'index'), new MeshBasicMaterial({ side: DoubleSide }));
    m.updateMatrixWorld(true);
    vergleiche([m], r, 20);
    const pos = m.geometry.attributes.position as BufferAttribute;
    for (let i = 0; i < pos.count; i++) pos.setX(i, pos.getX(i) + 5);
    pos.needsUpdate = true; m.geometry.computeBoundingSphere();
    expect(vergleiche([m], r, 200)).toBeGreaterThan(20);
  });

  it('Ebenenmaske und Nicht-Meshes wie three', () => {
    const r = zufall(9);
    const a = new Mesh(geometrie(r, 500, 'index'), new MeshBasicMaterial({ side: DoubleSide }));
    a.layers.set(2); a.updateMatrixWorld(true);
    const rc = new Raycaster(new Vector3(0, 0, -50), new Vector3(0, 0, 1));
    expect(strahlSchnitte(rc, [a])).toEqual([]);
    rc.layers.enable(2);
    expect(kurz(strahlSchnitte(rc, [a]))).toEqual(kurz(rc.intersectObjects([a], false)));
  });
});
