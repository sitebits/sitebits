import { describe, expect, it, vi } from 'vitest';
import { PlaneGeometry, BufferAttribute, Group, Matrix4, Mesh, MeshBasicMaterial, MeshLambertMaterial, MeshStandardMaterial, Plane, Texture, Vector3 } from 'three';
import { KachelMaterialien } from '../src/kachelMaterial';
import { schattenUniform } from '../src/sonneSchatten';

const kachel = (o: { normalen?: boolean; map?: Texture; opacity?: number } = {}): { szene: Group; mesh: Mesh; original: MeshStandardMaterial } => {
  const g = new PlaneGeometry(1, 1);
  if (!o.normalen) g.deleteAttribute('normal');
  const original = new MeshStandardMaterial({ map: o.map ?? null, opacity: o.opacity ?? 1, transparent: (o.opacity ?? 1) < 1 });
  const mesh = new Mesh(g, original); const szene = new Group(); szene.add(mesh);
  return { szene, mesh, original };
};
const zustand = { planes: null as Plane[] | null, opacity: 1 };
const neu = (): KachelMaterialien => { zustand.planes = null; zustand.opacity = 1; return new KachelMaterialien(() => zustand); };

describe('KachelMaterialien', () => {
  it('ersetzt PBR-Material durch unbeleuchtetes Basic-Material mit Textur und Seite des Originals', () => {
    const map = new Texture(); const { szene, mesh, original } = kachel({ map }); original.side = 2;
    neu().uebernehmen(szene);
    const m = mesh.material as MeshBasicMaterial;
    expect(m).toBeInstanceOf(MeshBasicMaterial); expect(m.map).toBe(map); expect(m.side).toBe(2);
    expect(mesh.castShadow).toBe(false);
  });
  it('Schnittebenen und Deckkraft des Entity gelten auch für die Ersatzmaterialien', () => {
    const { szene, mesh } = kachel(); const k = neu();
    zustand.planes = [new Plane(new Vector3(0, 0, 1), 0)]; zustand.opacity = 0.5;
    k.uebernehmen(szene);
    const m = mesh.material as MeshBasicMaterial;
    expect(m.clippingPlanes).toBe(zustand.planes); expect(m.opacity).toBe(0.5); expect(m.transparent).toBe(true);
    zustand.planes = []; zustand.opacity = 1; k.auffrischen(szene);
    expect(m.clippingPlanes).toEqual([]); expect(m.opacity).toBe(1); expect(m.transparent).toBe(false);
  });
  it('Ausgangsdeckkraft des Originals bleibt erhalten (Giro3D-Regel)', () => {
    const { szene, mesh } = kachel({ opacity: 0.4 }); const k = neu(); zustand.opacity = 0.5;
    k.uebernehmen(szene);
    expect((mesh.material as MeshBasicMaterial).opacity).toBeCloseTo(0.2, 12);
  });
  it('Sonne: beleuchtetes Material (flach ohne Normalen), wirft und empfängt Schatten; aus → wieder Basic', () => {
    const { szene, mesh } = kachel(); const k = neu(); const s = schattenUniform(() => new Matrix4());
    k.uebernehmen(szene); const basis = mesh.material;
    k.setSonne(s, [szene]);
    const l = mesh.material as MeshLambertMaterial;
    expect(l).toBeInstanceOf(MeshLambertMaterial); expect(l.flatShading).toBe(true);
    expect(mesh.castShadow && mesh.receiveShadow).toBe(true); expect(k.mitSonne).toBe(true);
    expect(typeof l.onBeforeCompile).toBe('function'); expect(l.customProgramCacheKey()).toBe('scanview-sonne-schatten');
    k.setSonne(null, [szene]);
    expect(mesh.material).toBe(basis); expect(mesh.castShadow).toBe(false);
    k.setSonne(s, [szene]); expect(mesh.material).toBe(l); // Variante wird wiederverwendet
  });
  it('Mit Normalen schattiert die Sonne glatt', () => {
    const { szene, mesh } = kachel({ normalen: true }); const k = neu();
    k.setSonne(schattenUniform(() => new Matrix4()), []); k.uebernehmen(szene);
    expect((mesh.material as MeshLambertMaterial).flatShading).toBe(false);
    expect(mesh.geometry.getAttribute('normal')).toBeInstanceOf(BufferAttribute);
  });
  it('verwerfen gibt Originale und Ersatzmaterialien frei, die Textur gehört dem Renderer', () => {
    const map = new Texture(); const tex = vi.spyOn(map, 'dispose');
    const { szene, mesh, original } = kachel({ map }); const k = neu();
    const od = vi.spyOn(original, 'dispose'); k.uebernehmen(szene);
    const basis = mesh.material as MeshBasicMaterial; const bd = vi.spyOn(basis, 'dispose');
    k.verwerfen(szene);
    expect(od).toHaveBeenCalled(); expect(bd).toHaveBeenCalled(); expect(tex).not.toHaveBeenCalled();
  });
});
