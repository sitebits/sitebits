import { describe, expect, it } from 'vitest';
import { Matrix4, OrthographicCamera, PerspectiveCamera, ShaderChunk, Vector3 } from 'three';
import { patchSchattenVertex, schattenAusSicht } from '../src/sonneSchatten';

// float32 wie auf der GPU: Matrixeinträge beim Hochladen gerundet, jedes Produkt und jede Summe gerundet.
const f = Math.fround;
const f32Matrix = (m: Matrix4): number[] => m.elements.map(f);
function f32Mal(e: number[], v: [number, number, number, number]): [number, number, number, number] {
  const r: number[] = [];
  for (let z = 0; z < 4; z++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s = f(s + f(e[k * 4 + z]! * v[k]!));
    r.push(s);
  }
  return r as [number, number, number, number];
}
const genau = (m: Matrix4, p: Vector3): Vector3 => p.clone().applyMatrix4(m);

/** Szene wie im Pilotprojekt: Ursprung UTM 33, Mesh-Wurzel gedreht (glTF Y oben), Sonne aus Südwesten 40° hoch. */
function szene() {
  const ursprung = new Vector3(365809, 5806702, 42);
  const modell = new Matrix4().makeRotationX(Math.PI / 2).setPosition(ursprung);
  const zentrum = ursprung.clone().add(new Vector3(0, -20, 0));
  const licht = new OrthographicCamera(-60, 60, 60, -60, 1, 400);
  const richtung = new Vector3(-0.6, -0.55, 0.58).normalize();
  licht.position.copy(zentrum).addScaledVector(richtung, 200); licht.lookAt(zentrum); licht.updateMatrixWorld(true);
  // three: shadow.matrix = Bias(0,5) × Projektion × Sicht des Lichts
  const schatten = new Matrix4().set(0.5, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0, 0, 0.5, 0.5, 0, 0, 0, 1)
    .multiply(licht.projectionMatrix).multiply(licht.matrixWorldInverse);
  const kamera = new PerspectiveCamera(50, 1.5, 0.1, 1000);
  kamera.position.copy(zentrum).add(new Vector3(30, -45, 25)); kamera.up.set(0, 0, 1); kamera.lookAt(zentrum);
  kamera.updateMatrixWorld(true);
  return { modell, schatten, kamera, kante: 120, tiefe: 399 };
}

describe('Schattenkoordinaten in UTM', () => {
  it('Sichtraum-Matrix (double) < 1 mm, three-Weg über Weltkoordinaten (float32) > 10 cm', () => {
    const { modell, schatten, kamera, kante, tiefe } = szene();
    const modellSicht = new Matrix4().multiplyMatrices(kamera.matrixWorldInverse, modell);
    const sichtSchatten = schattenAusSicht(schatten, kamera.matrixWorld);
    let naiv = 0; let neu = 0;
    // Gitter lokaler Punkte (glTF-Koordinaten des Meshes) mit krummen Werten
    for (let i = 0; i < 400; i++) {
      const lokal = new Vector3(((i * 7.31) % 60) - 30 + 0.123, (i * 0.137) % 12, ((i * 3.77) % 50) - 5 + 0.0371);
      const lv: [number, number, number, number] = [f(lokal.x), f(lokal.y), f(lokal.z), 1];
      const soll = genau(schatten, genau(modell, lokal));
      // three: worldPosition = modelMatrix * position; directionalShadowMatrix * worldPosition
      const welt = f32Mal(f32Matrix(modell), lv);
      const a = f32Mal(f32Matrix(schatten), welt);
      // neu: mvPosition = modelViewMatrix * position; svSchattenAusSicht * mvPosition
      const b = f32Mal(f32Matrix(sichtSchatten), f32Mal(f32Matrix(modellSicht), lv));
      const fehler = (v: number[]): number => Math.max(Math.abs(v[0]! - soll.x) * kante, Math.abs(v[1]! - soll.y) * kante, Math.abs(v[2]! - soll.z) * tiefe);
      naiv = Math.max(naiv, fehler(a)); neu = Math.max(neu, fehler(b));
    }
    expect(naiv).toBeGreaterThan(0.1);
    expect(neu).toBeLessThan(0.001);
  });
  it('Patch ersetzt genau die Directional-Schleife und lässt Punkt-/Spotlicht unberührt', () => {
    const p = patchSchattenVertex(ShaderChunk.shadowmap_vertex);
    expect(p).toContain('svSchattenAusSicht * vec4( mvPosition.xyz + svVersatz, 1.0 )');
    expect(p).not.toContain('directionalShadowMatrix[ i ] * shadowWorldPosition');
    expect(p).toContain('pointShadowMatrix[ i ] * shadowWorldPosition');
    expect(p).toContain('#pragma unroll_loop_start');
    expect(() => patchSchattenVertex('void main() {}')).toThrow(/three\.js geändert/);
  });
});
