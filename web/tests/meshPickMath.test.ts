import { expect, it } from 'vitest';
import { berechneKanten, kopiereKantenTeile } from '../src/kantenDaten';
import { BoxGeometry, BufferGeometry, Float32BufferAttribute, Group, Matrix4, Mesh, PerspectiveCamera, PlaneGeometry, Ray, Vector3 } from 'three';
import { featureEdges, meshFeatureEdges, prepareMeshFeatureEdges, localRay, snapMeshHit, vormerkenMeshFeatureEdges, worldPoint } from '../src/meshPickMath';
const root = new Matrix4().makeRotationX(Math.PI / 2).setPosition(500000, 5800000, 100);
it('UTM-Strahl lokal und zurück: Flächentreffer ≤0,1 mm', () => {
  const r = new Ray(new Vector3(500000.25, 5799998, 100.1), new Vector3(0, 1, 0));
  const p = localRay(r, root).at(1.5, new Vector3());
  expect(worldPoint(p, root).distanceTo(new Vector3(500000.25, 5799999.5, 100.1))).toBeLessThan(0.0001);
});
it('verschweißter Würfel: 12 Merkmalskanten, keine Flächendiagonalen', () => {
  expect(featureEdges(new BoxGeometry(1, 1, 1))).toHaveLength(12);
});
it('ebene Fläche: keine Merkmalskanten', () => {
  const g = new BufferGeometry();
  g.setAttribute('position', new Float32BufferAttribute([0,0,0, 1,0,0, 1,1,0, 0,1,0], 3));
  g.setIndex([0,1,2, 0,2,3]); expect(featureEdges(g)).toHaveLength(0);
});
it.each([90, 120])('Kante %s° erkannt', (angle) => {
  const a = angle * Math.PI / 180; const g = new BufferGeometry();
  g.setAttribute('position', new Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0, 0,-Math.cos(a),Math.sin(a)], 3));
  g.setIndex([0,1,2, 1,0,3]); expect(featureEdges(g)).toHaveLength(1);
});
const camera = new PerspectiveCamera(45, 1, 0.01, 100);
camera.position.set(0, 0, 3); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
const screen = (p: Vector3): [number, number] => { const v = p.clone().project(camera); return [(v.x + 1) * 500, (1 - v.y) * 500]; };
const options = { matrixWorld: new Matrix4(), camera, width: 1000, height: 1000, edges: true };
it('Vertex-Fang exakt, kleinerer Index bei Gleichstand', () => {
  const g = new BoxGeometry(1,1,1); const p = new Vector3().fromBufferAttribute(g.getAttribute('position'), 0); const [x,y] = screen(p);
  const result = snapMeshHit({ ...options, geometry: g, triangle: 0, hitLocal: p.clone().addScalar(1e-5), x, y });
  expect(result.kind).toBe('vertex'); expect(result.point.toArray()).toEqual(p.toArray()); expect(result.index).toBe(0);
});
it('Kantenfang ≤0,1 mm auf echter Kante', () => {
  const g = new BoxGeometry(1,1,1); const p = new Vector3(0.5,0.1,0.5); const [x,y] = screen(p);
  const result = snapMeshHit({ ...options, geometry: g, triangle: 0, hitLocal: p, x, y });
  expect(result.kind).toBe('edge'); expect(result.point.distanceTo(p)).toBeLessThan(0.0001);
});

it.each([90,120])('Fangpunkt auf der %s°-Merkmalskante bleibt ≤0,1 mm', (angle) => {
 const a=angle*Math.PI/180,g=new BufferGeometry();
 g.setAttribute('position',new Float32BufferAttribute([0,0,0,1,0,0,0,1,0,0,-Math.cos(a),Math.sin(a)],3));g.setIndex([0,1,2,1,0,3]);
 const p=new Vector3(.5,0,0),[x,y]=screen(p);
 const snapped=snapMeshHit({...options,geometry:g,triangle:0,hitLocal:new Vector3(.5,.005,0),x,y});
 expect(snapped.kind).toBe('edge');expect(snapped.point.distanceTo(p)).toBeLessThan(.0001);
});
it('ebene Fläche: Fang schaltet keine gewöhnliche Diagonale ein', () => {
 const g=new PlaneGeometry(1,1),p=new Vector3(.1,.1,0),[x,y]=screen(p);
 expect(snapMeshHit({...options,geometry:g,triangle:0,hitLocal:p,x,y}).kind).toBe('none');
});

it('Merkmalskanten werden über getrennte Material-Primitiven hinweg verschweißt', () => {
 const a=new BufferGeometry(),b=new BufferGeometry();
 a.setAttribute('position',new Float32BufferAttribute([0,0,0,1,0,0,0,1,0],3));
 b.setAttribute('position',new Float32BufferAttribute([1,0,0,0,0,0,0,0,1],3));
 const ma=new Mesh(a),mb=new Mesh(b),root=new Group();root.position.set(500000,5800000,100);root.add(ma,mb);root.updateMatrixWorld(true);
 expect(featureEdges(a)).toHaveLength(0);expect(featureEdges(b)).toHaveLength(0);
 prepareMeshFeatureEdges([ma,mb],root.matrixWorld);
 expect(meshFeatureEdges(ma)).toEqual([{a:0,b:1}]);expect(meshFeatureEdges(mb)).toEqual([{a:0,b:1}]);
});

// Referenz: die frühere Fassung (Text-Schlüssel), unverändert übernommen — die neue muss dasselbe liefern.
function referenzKanten(meshes: Mesh[], rootMatrix: Matrix4): { a: number; b: number }[][] {
  const inverse = rootMatrix.clone().invert(); const vertices = new Map<string, number>();
  const edges = new Map<string, { normal: Vector3; feature: boolean; refs: { m: number; a: number; b: number }[] }>();
  meshes.forEach((mesh, mi) => {
    const pos = mesh.geometry.getAttribute('position'), index = mesh.geometry.index;
    const transform = inverse.clone().multiply(mesh.matrixWorld); const welded = new Uint32Array(pos.count);
    const point = (i: number): Vector3 => new Vector3().fromBufferAttribute(pos, i).applyMatrix4(transform);
    for (let i = 0; i < pos.count; i++) { const p = point(i), key = `${p.x},${p.y},${p.z}`; let id = vertices.get(key); if (id === undefined) { id = vertices.size; vertices.set(key, id); } welded[i] = id; }
    for (let t = 0; t < (index?.count ?? pos.count); t += 3) {
      const ids = [0, 1, 2].map((j) => index ? index.getX(t + j) : t + j);
      const normal = point(ids[1]!).sub(point(ids[0]!)).cross(point(ids[2]!).sub(point(ids[0]!))).normalize();
      if (normal.lengthSq() === 0) continue;
      for (let j = 0; j < 3; j++) {
        const a = ids[j]!, b = ids[(j + 1) % 3]!, wa = welded[a]!, wb = welded[b]!;
        const key = `${Math.min(wa, wb)}:${Math.max(wa, wb)}`, ref = { m: mi, a: Math.min(a, b), b: Math.max(a, b) }, prev = edges.get(key);
        if (prev) { prev.feature ||= prev.normal.dot(normal) <= Math.cos(30 * Math.PI / 180) + 1e-12; prev.refs.push(ref); } else edges.set(key, { normal, feature: false, refs: [ref] });
      }
    }
  });
  const out: { a: number; b: number }[][] = meshes.map(() => []);
  for (const e of edges.values()) if (e.feature) for (const r of e.refs) out[r.m]!.push({ a: r.a, b: r.b });
  return out.map((l) => { l.sort((x, y) => x.a - y.a || x.b - y.b); return l.filter((e, i) => i === 0 || e.a !== l[i - 1]!.a || e.b !== l[i - 1]!.b); });
}

it('Neufassung der Merkmalskanten liefert dasselbe wie die frühere (Zufallsnetze, Nähte, entartete Flächen)', () => {
  let s = 7; const zufall = (): number => { s = (s * 16807) % 2147483647; return s / 2147483647; };
  for (let runde = 0; runde < 5; runde++) {
    const root = new Group(); root.position.set(365000, 5806000, 30); root.rotation.z = 0.3 * runde;
    const raster = Array.from({ length: 40 }, () => [Math.round(zufall() * 6), Math.round(zufall() * 6), Math.round(zufall() * 3)]);
    const meshes: Mesh[] = [];
    for (let k = 0; k < 3; k++) {
      const g = new BufferGeometry();
      // Gemeinsame Rasterpunkte erzeugen Nähte zwischen den Teilen; doppelte Punkte innerhalb eines Teils auch.
      g.setAttribute('position', new Float32BufferAttribute(raster.flat(), 3));
      const idx: number[] = []; for (let f = 0; f < 60; f++) idx.push(...[0, 1, 2].map(() => Math.floor(zufall() * raster.length)));
      g.setIndex(idx); const m = new Mesh(g); root.add(m); meshes.push(m);
    }
    root.updateMatrixWorld(true);
    prepareMeshFeatureEdges(meshes, root.matrixWorld);
    const referenz = referenzKanten(meshes, root.matrixWorld);
    expect(meshes.map((m) => meshFeatureEdges(m))).toEqual(referenz);
    const inverse = root.matrixWorld.clone().invert();
    const teile = meshes.map((m) => ({ pos: m.geometry.getAttribute('position'), index: m.geometry.index,
      matrix: inverse.clone().multiply(m.matrixWorld).elements }));
    expect(berechneKanten({ teile: kopiereKantenTeile(teile), geschweisst: false }))
      .toEqual(referenz.map((liste) => Uint32Array.from(liste.flatMap((e) => [e.a, e.b]))));
    expect(meshes.reduce((n, m) => n + meshFeatureEdges(m).length, 0)).toBeGreaterThan(20); // nicht trivial leer
  }
});

it('vorgemerkte Gruppe rechnet erst beim ersten Bedarf', () => {
  const a = new BufferGeometry(), b = new BufferGeometry();
  a.setAttribute('position', new Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  b.setAttribute('position', new Float32BufferAttribute([1, 0, 0, 0, 0, 0, 0, 0, 1], 3));
  const ma = new Mesh(a), mb = new Mesh(b), root = new Group(); root.add(ma, mb); root.updateMatrixWorld(true);
  vormerkenMeshFeatureEdges([ma, mb], root.matrixWorld);
  expect(meshFeatureEdges(mb)).toEqual([{ a: 0, b: 1 }]);
  expect(meshFeatureEdges(ma)).toEqual([{ a: 0, b: 1 }]);
});
