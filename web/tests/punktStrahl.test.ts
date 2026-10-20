import { expect, it } from 'vitest';
import { Box3, BufferGeometry, Float32BufferAttribute, Group, Points, Ray, Vector3 } from 'three';
import { punktStrahl } from '../src/punktStrahl';

function punkte(werte: number[] | Float32Array): Points {
  const g = new BufferGeometry();
  g.setAttribute('position', new Float32BufferAttribute(werte, 3));
  return new Points(g);
}
function schnitt(root: Group, winkel = 0.001) {
  const lauf = punktStrahl(root, new Ray(new Vector3(), new Vector3(0, 0, -1)), winkel);
  let schritt = lauf.next();
  while (!schritt.done) schritt = lauf.next();
  return schritt.value;
}
it('unsichtbare Eltern/Knoten, Weltmatrix mit Drehung und nichtuniformer Skalierung, vorderster Treffer', () => {
  const root = new Group(), eltern = new Group();
  root.add(eltern); eltern.visible = false;
  eltern.position.set(0, -3, -1); eltern.rotation.y = Math.PI / 2; eltern.scale.set(2, 3, 4);
  const wand = punkte([1, 1, 0]); wand.visible = false; eltern.add(wand);
  root.add(punkte([0, 0, -5, 0, 0, 1]));
  expect(schnitt(root)?.point?.z).toBeCloseTo(-3);
  expect(schnitt(root)?.point?.length()).toBeCloseTo(3);
  eltern.position.z = -6;
  expect(schnitt(root)?.point?.z).toBe(-5);
});
it('Voxelgröße berücksichtigt Punktabstand; Fotowinkel verändert die Belegung nicht', () => {
  const root = new Group(), wand = punkte([0.019, 0, -3]); root.add(wand);
  expect(schnitt(root)?.point?.z).toBe(-3);
  wand.geometry.setAttribute('position', new Float32BufferAttribute([0.021, 0, -3], 3)); wand.geometry.boundingBox = null;
  expect(schnitt(root)?.point).toBeNull();
  expect(schnitt(root, 0.01)?.point).toBeNull();
  wand.geometry.setAttribute('position', new Float32BufferAttribute([0, 0, 3], 3)); wand.geometry.boundingBox = null;
  expect(schnitt(root)?.point).toBeNull();
  // 10-cm-Raster: Mittelpunkt zwischen vier Punkten wird trotz Abstand > 2 cm getroffen.
  root.clear(); root.add(punkte([-0.05, -0.05, -3, 0.05, -0.05, -3, -0.05, 0.05, -3, 0.05, 0.05, -3]));
  expect(schnitt(root)?.point?.z).toBe(-3);
});
it('Hüllquader verwirft entfernte Knoten ohne Positionen zu lesen; fehlende Geometrie bleibt unterscheidbar', () => {
  const root = new Group(); expect(schnitt(root)).toBeNull();
  const fern = punkte([100, 100, -3]); root.add(fern);
  fern.geometry.boundingBox = new Box3(new Vector3(99, 99, -4), new Vector3(101, 101, -2));
  fern.geometry.getAttribute('position').getX = () => { throw Error('Vorfilter fehlt'); };
  expect(schnitt(root)).toEqual({ point: null });
});
it('große Knoten geben während Quaderberechnung und Punkttest in Portionen ab', () => {
  const root = new Group(); root.add(punkte(Array.from({ length: 5000 }, () => [0, 0, -3]).flat()));
  const lauf = punktStrahl(root, new Ray(new Vector3(), new Vector3(0, 0, -1)), 0.001);
  let portionen = 0, schritt = lauf.next();
  while (!schritt.done) { portionen++; schritt = lauf.next(); }
  expect(portionen).toBeGreaterThanOrEqual(5);
  expect(schritt.value?.point?.z).toBe(-3);
});

// BOX aus e2e/fixtures/fotos/szene.py, einschließlich Unterseite.
function zufallsQuader(jeFlaeche = 10_000): Group {
  let seed = 7;
  const zufall = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
  const werte = new Float32Array(jeFlaeche * 6 * 3);
  let index = 0;
  for (let achse = 0; achse < 3; achse++) for (const seite of [0, 1]) {
    for (let i = 0; i < jeFlaeche; i++) {
      const p = [8 + 4 * zufall(), 9 + 2 * zufall(), 1.5 * zufall()];
      p[achse] = [[8, 12], [9, 11], [0, 1.5]][achse]![seite]!;
      werte.set(p, index); index += 3;
    }
  }
  const root = new Group(); root.add(punkte(werte)); return root;
}
function strahlTreffer(root: Group, kamera: Vector3, ziel: Vector3) {
  const abstand = kamera.distanceTo(ziel);
  const lauf = punktStrahl(root, new Ray(kamera, ziel.clone().sub(kamera).normalize()), 0.001, abstand + Math.max(0.3, 0.02 * abstand), abstand);
  let schritt = lauf.next();
  while (!schritt.done) schritt = lauf.next();
  return schritt.value?.point?.distanceTo(kamera) ?? null;
}
it('Zufallsquader: alle 25 Vorderflächen-Stichproben sind von hinten verdeckt', () => {
  const root = zufallsQuader(), kamera = new Vector3(10, 14, 0.75);
  const verdeckt = [];
  for (let j = 0; j < 5; j++) for (let i = 0; i < 5; i++) {
    const ziel = new Vector3(8.4 + i * 0.8, 9, 0.15 + j * 0.3);
    const tiefe = strahlTreffer(root, kamera, ziel);
    verdeckt.push(tiefe !== null && tiefe < kamera.distanceTo(ziel) - Math.max(0.3, 0.02 * kamera.distanceTo(ziel)));
  }
  expect(verdeckt).toEqual(Array<boolean>(25).fill(true));
});


it('Raster wird je Geometrie wiederverwendet und bei needsUpdate oder Attributtausch erneuert', () => {
  const root = new Group(), wand = punkte([0, 0, -3]); root.add(wand);
  const position = wand.geometry.getAttribute('position');
  const lesen = position.getX.bind(position);
  let zugriffe = 0;
  position.getX = (i: number) => { zugriffe++; return lesen(i); };
  expect(schnitt(root)?.point?.z).toBe(-3);
  const nachBau = zugriffe;
  expect(nachBau).toBeGreaterThan(0);
  expect(schnitt(root)?.point?.z).toBe(-3);
  expect(zugriffe).toBe(nachBau);
  position.setXYZ(0, 0, 0, -6); position.needsUpdate = true;
  // Der absichtlich veraltete Quader darf das neu zu bauende Raster nicht begrenzen.
  wand.geometry.boundingBox = new Box3(new Vector3(99, 99, 99), new Vector3(100, 100, 100));
  expect(schnitt(root)?.point?.z).toBe(-6);
  expect(zugriffe).toBeGreaterThan(nachBau);
  wand.geometry.setAttribute('position', new Float32BufferAttribute([0, 0, -4], 3));
  expect(schnitt(root)?.point?.z).toBe(-4);
  const zweiterKnoten = new Points(wand.geometry); zweiterKnoten.position.z = 2; root.add(zweiterKnoten);
  expect(schnitt(root)?.point?.z).toBe(-2);
});

it('DDA: positive/negative Richtungen, parallele Achsen, Ursprung innen, Ecktreffer und endliche Reichweite', () => {
  const root = new Group(); root.add(punkte([-1, -1, -1, 1, 1, 1]));
  for (const vorzeichen of [-1, 1]) {
    const kamera = new Vector3().setScalar(vorzeichen * 3), ziel = new Vector3().setScalar(-vorzeichen);
    expect(strahlTreffer(root, kamera, ziel)).not.toBeNull();
    const innen = new Vector3(), aussen = new Vector3().setScalar(vorzeichen);
    expect(strahlTreffer(root, innen, aussen)).not.toBeNull();
  }
  const wand = new Group(); wand.add(punkte([0, 0, -3]));
  for (const [far, erwartet] of [[2, null], [3.1, -3]] as const) {
    const lauf = punktStrahl(wand, new Ray(new Vector3(), new Vector3(0, 0, -1)), 0, far);
    let schritt = lauf.next(); while (!schritt.done) schritt = lauf.next();
    expect(schritt.value?.point?.z ?? null).toBe(erwartet);
  }
  expect(strahlTreffer(wand, new Vector3(1, 0, 0), new Vector3(1, 0, -4))).toBeNull();
});

it('Benchmark: eine Million Punkte und 200 Strahlen einschließlich kaltem Rasterbau unter einer Sekunde', () => {
  // Gleichmäßige Oberflächenabtastung für den Rechenbenchmark. Zufallslücken
  // werden separat mit unverändertem Seed und 10.000 Punkten je Fläche geprüft.
  const werte = new Float32Array(1_000_002 * 3);
  const lo = [8, 9, 0], laenge = [4, 2, 1.5];
  let index = 0;
  for (let achse = 0; achse < 3; achse++) for (const seite of [0, 1]) {
    const a = (achse + 1) % 3, b = (achse + 2) % 3;
    const spalten = Math.ceil(Math.sqrt(166_667 * laenge[a]! / laenge[b]!));
    const zeilen = Math.ceil(166_667 / spalten);
    for (let i = 0; i < 166_667; i++) {
      werte[index + achse] = lo[achse]! + seite * laenge[achse]!;
      werte[index + a] = lo[a]! + (i % spalten + 0.5) / spalten * laenge[a]!;
      werte[index + b] = lo[b]! + (Math.floor(i / spalten) + 0.5) / zeilen * laenge[b]!;
      index += 3;
    }
  }
  const root = new Group(); root.add(punkte(werte));
  const kamera = new Vector3(10, 14, 0.75);
  const ziele = Array.from({ length: 200 }, (_, i) => new Vector3(8.4 + (i % 20) * 3.2 / 19, 9, 0.15 + Math.floor(i / 20) * 1.2 / 9));
  const beginn = performance.now();
  const kalt = ziele.map((ziel) => strahlTreffer(root, kamera, ziel));
  const kaltMs = performance.now() - beginn;
  const warmBeginn = performance.now();
  const warm = ziele.map((ziel) => strahlTreffer(root, kamera, ziel));
  const warmMs = performance.now() - warmBeginn;
  console.info(`Voxel-Benchmark: 1000002 Punkte, 200 Strahlen, kalt inkl. Bau ${kaltMs.toFixed(2)} ms, warm ${warmMs.toFixed(2)} ms`);
  expect(kalt.every((t) => t !== null && t > 0 && t < 4)).toBe(true);
  expect(warm).toEqual(kalt);
  expect(kaltMs).toBeLessThan(1000);
  expect(warmMs).toBeLessThan(1000);
});
