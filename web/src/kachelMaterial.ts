// Materialien der gekachelten Meshes. GLTFLoader liefert PBR-Materialien, die ohne Szenenlicht schwarz bleiben;
// die GLB-Meshes (meshLayer.ts) zeichnen unbeleuchtet (MeshBasicMaterial, nur Textur/Farbe). Hier dasselbe für jede
// geladene Kachel — und mit Live-Sonne beleuchtete Materialien mit präzisen Schattenkoordinaten (sonneSchatten.ts).
// Die Ersatzmaterialien gehören dieser Klasse und werden mit der Kachel entsorgt; Texturen gehören dem Renderer.
import { Mesh, MeshBasicMaterial, MeshLambertMaterial } from 'three';
import type { BufferGeometry, Material, Object3D, Plane } from 'three';
import { sonnenMaterial } from './sonneSchatten';
import type { SchattenUniform } from './sonneSchatten';

/** Felder, die ein glTF-Material (PBR, Basic, Lambert) für die Ersatzmaterialien hergibt. */
type Quelle = Material & { map?: MeshBasicMaterial['map']; color?: MeshBasicMaterial['color']; vertexColors?: boolean; alphaTest: number };

interface Je { basis?: MeshBasicMaterial; licht?: { flach: boolean; uniform: SchattenUniform; material: Material } }

export interface KachelZustand { planes: Plane[] | null; opacity: number }

/** three typisiert `instanceof Mesh` als Mesh<any>; der Wächter gibt die tatsächlichen Typen zurück. */
export const istMesh = (o: Object3D): o is Mesh<BufferGeometry, Material | Material[]> => o instanceof Mesh;
const materialsOf = (m: Mesh<BufferGeometry, Material | Material[]>): Material[] => Array.isArray(m.material) ? m.material : [m.material as Material];

export class KachelMaterialien {
  private sonne: SchattenUniform | null = null;
  private readonly original = new WeakMap<Object3D, Material[]>();
  private readonly je = new WeakMap<Material, Je>();
  constructor(private readonly zustand: () => KachelZustand) {}

  get mitSonne(): boolean { return this.sonne !== null; }

  /** Neu geladene Kachel: Originalmaterialien merken und durch die gerade passenden ersetzen. */
  uebernehmen(szene: Object3D): void {
    szene.traverse((o) => {
      if (!istMesh(o)) return;
      if (!this.original.has(o)) this.original.set(o, materialsOf(o));
      this.zuweisen(o);
    });
  }

  /** Sonne an/aus: alle gehaltenen Kacheln neu zuweisen. */
  setSonne(s: SchattenUniform | null, szenen: Iterable<Object3D>): void {
    this.sonne = s;
    for (const szene of szenen) this.uebernehmen(szene);
  }

  /** Sichtbares Material neu mit Schnittebenen/Deckkraft versehen (nach Änderung am Entity). */
  auffrischen(szene: Object3D): void {
    szene.traverse((o) => { if (istMesh(o)) for (const m of materialsOf(o)) this.einstellen(m, this.quelleVon(m)); });
  }

  /** Kachel wird entsorgt: Originale und Ersatzmaterialien freigeben (die Texturen entsorgt der Renderer). */
  verwerfen(szene: Object3D): void {
    szene.traverse((o) => {
      if (!istMesh(o)) return;
      for (const orig of this.original.get(o) ?? []) {
        const je = this.je.get(orig);
        je?.basis?.dispose(); je?.licht?.material.dispose(); orig.dispose();
        this.je.delete(orig);
      }
      for (const m of materialsOf(o)) m.dispose();
      this.original.delete(o);
    });
  }

  private quelleVon(m: Material): Material | undefined {
    return m.userData.kachelOriginal as Material | undefined;
  }

  private zuweisen(o: Mesh<BufferGeometry, Material | Material[]>): void {
    const flach = !o.geometry.attributes.normal;
    const neu = (this.original.get(o) ?? []).map((orig) => {
      const q = orig as Quelle;
      let je = this.je.get(orig);
      if (!je) { je = {}; this.je.set(orig, je); }
      let m: Material;
      if (this.sonne) {
        if (!je.licht || je.licht.flach !== flach || je.licht.uniform !== this.sonne) {
          je.licht?.material.dispose();
          const v = new MeshLambertMaterial({ map: q.map ?? null, color: q.color, vertexColors: !!q.vertexColors && !q.map,
            side: q.side, alphaTest: q.alphaTest, flatShading: flach });
          je.licht = { flach, uniform: this.sonne, material: sonnenMaterial(v, this.sonne) };
        }
        m = je.licht.material;
      } else {
        je.basis ??= new MeshBasicMaterial({ map: q.map ?? null, color: q.color, vertexColors: !!q.vertexColors && !q.map,
          side: q.side, alphaTest: q.alphaTest });
        m = je.basis;
      }
      m.userData.kachelOriginal = orig;
      this.einstellen(m, orig);
      return m;
    });
    // Ein Array ohne geometry.groups zeichnet und raycastet in three.js nichts (siehe meshLayer.apply).
    o.material = neu.length === 1 && o.geometry.groups.length === 0 ? neu[0]! : neu;
    o.castShadow = o.receiveShadow = this.sonne !== null;
  }

  private einstellen(m: Material, orig: Material | undefined): void {
    const z = this.zustand();
    // Dieselben Felder wie Giro3D Tiles3D.setupMaterial: Schnittebenen des Entity, Deckkraft × Ausgangsdeckkraft.
    m.clippingPlanes = z.planes;
    const ausgang = orig ? (orig.userData.originalOpacity as number | undefined) ?? orig.opacity : 1;
    m.userData.originalOpacity = ausgang;
    const deckkraft = z.opacity * ausgang;
    const transparent = deckkraft < 1;
    m.opacity = deckkraft;
    if (m.transparent !== transparent) { m.transparent = transparent; m.needsUpdate = true; }
  }
}
