// Gekachelte Mesh-Ebene (3D Tiles 1.1): Giro3D-Tiles3D-Entity im Paketrahmen.
//   - Darstellung: Renderer (3d-tiles-renderer) wählt Kacheln nach Bildfehler (errorTarget) und hält sie in einem
//     LRU-Speicher; Budget und Handy-Stufe stehen in geraet.ts.
//   - Messen: nicht über das, was der Renderer gerade hält, sondern über den Blattabruf (tilesPick/tilesBlatt).
//   - Ausgeliefert wird nur vom eigenen Ursprung: Decoder (KTX2/Basis, Meshopt) kommen aus `<wasm>/…`, nicht von
//     unpkg (Giro3D-Vorgabe); Draco gehört nicht zum Vertrag, sein Pfad zeigt ins Leere statt ins Netz.
//   - Kein Verfeinern über den Renderer erzwungen (REPLACE wartet auf alle Geschwister, im Spike unzuverlässig).
import type { Plane } from 'three';
import { Matrix4 } from 'three';
import type { BufferGeometry, Material, Mesh, Object3D } from 'three';
import Tiles3D from '@giro3d/giro3d/entities/Tiles3D.js';
import type Instance from '@giro3d/giro3d/core/Instance.js';
import type { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { KTX2Loader } from 'three/examples/jsm/loaders/KTX2Loader.js';
import { GRENZEN, kachelBudget } from './geraet';
import type { Grenzen, KachelBudget } from './geraet';
import { ktx2EingebettetPlugin } from './ktx2Eingebettet';
import { istMesh, KachelMaterialien } from './kachelMaterial';
import type { MeshHandle } from './layers';
import type { Vec3, TilesLayer } from './manifest';
import { resolveUrl } from './manifest';
import type { SchattenUniform } from './sonneSchatten';
import { BlattSpeicher, holeBytes } from './tilesBlatt';
import type { LadeBytes } from './tilesBlatt';
import type { KachelMessEbene } from './tilesPick';
import { pruefeCrs, pruefeLage, pruefeTileset, TilesetFehler } from './tilesMath';
import type { Tileset, TilesKnoten } from './tilesMath';

/** Wartezeit auf die Wurzelkachel (Giro3D `add` wartet darauf); danach gilt die Ebene als nicht ladbar. */
export const WURZEL_TIMEOUT_MS = 30_000;
export interface KachelStatistik {
  geladen: number; sichtbar: number; sichtbareDreiecke: number; lruBytes: number; lruObjekte: number;
  blaetterGeladen: number; blattBytes: number; laedt: boolean;
}

export interface TilesLayerHandle extends MeshHandle, KachelMessEbene {
  readonly kind: 'mesh-tiles';
  readonly failure: string | null;
  readonly worldBox: [Vec3, Vec3] | null;
  readonly clippingPlaneCounts: number[];
  readonly budget: KachelBudget;
  setClippingPlanes(planes: Plane[]): void;
  setPanoramaOverlay(alpha: number): void;
  /** Live-Sonne (sonne.ts): beleuchtet, wirft und empfängt Schatten. null = aus. */
  setSonne(s: SchattenUniform | null): void;
  statistik(): KachelStatistik;
  dispose(): void;
  onChange(cb: () => void): void;
}

type KachelMesh = Mesh<BufferGeometry, Material | Material[]>;
const dreieckeVon = (m: KachelMesh): number => Math.floor((m.geometry.index?.count ?? m.geometry.attributes.position?.count ?? 0) / 3);

/** Absolute Adresse relativ zur Seite (Ursprung des Viewers); `resolveUrl` hängt relative Pfade an die Paketbasis. */
const absolut = (u: string): string => new URL(u, location.href).href;

export async function createTilesLayer(o: {
  instance: Instance; layer: TilesLayer; frameWkt: string | null; pkgBase: string; wasmBase: string;
  notify: (text: string) => void; grenzen?: Grenzen; lade?: LadeBytes;
}): Promise<TilesLayerHandle> {
  const { instance, layer, notify } = o;
  const budget = kachelBudget(o.grenzen ?? GRENZEN);
  const url = absolut(resolveUrl(o.pkgBase, layer.url));
  // 1. Vertrag prüfen, bevor irgendetwas geladen wird: Rahmen, Hierarchie, Lage.
  const antwort = await fetch(url, { credentials: 'same-origin' });
  if (!antwort.ok) { void antwort.body?.cancel().catch(() => undefined); throw new TilesetFehler(`tileset.json: HTTP ${antwort.status}`); }
  const tileset: Tileset = pruefeTileset(await antwort.json(), { crs: layer.crs, heightSystem: layer.heightSystem }, new Matrix4());
  pruefeCrs(layer.crs, o.frameWkt);
  pruefeLage(tileset.weltBox, layer.boundsWorld);
  if (tileset.wurzel.uri === null) throw new TilesetFehler('tileset.json: Wurzelkachel ohne Inhalt wird nicht unterstützt.');

  const basis = new URL('.', url).href;
  const wasm = absolut(o.wasmBase);
  const ent = new Tiles3D({
    url, errorTarget: budget.errorTarget,
    // Eigener Ursprung statt unpkg.com (CSP/Egress). Draco ist nicht Teil des Vertrags: kein Decoder ausgeliefert.
    dracoDecoderPath: `${wasm}draco/`, ktx2DecoderPath: `${wasm}basis/`,
  });
  const tiles = ent.tiles;
  tiles.lruCache.maxBytesSize = budget.maxBytes; tiles.lruCache.minBytesSize = budget.minBytes;
  // Eingebettete KTX2-Texturen ohne blob:-URL (die CSP erlaubt sie nicht), wie bei den GLB-Meshes.
  const gltf = tiles.manager.getHandler('kachel.glb') as GLTFLoader | null;
  const ktx2 = (gltf as unknown as { ktx2Loader?: KTX2Loader | null } | null)?.ktx2Loader;
  if (gltf && ktx2) gltf.register(ktx2EingebettetPlugin(ktx2));

  const material = new KachelMaterialien(() => ({ planes: ent.clippingPlanes, opacity: ent.opacity }));
  const listeners: (() => void)[] = [];
  const geladen = new Set<Object3D>();
  let fehlerGemeldet = false; let disposed = false;
  const changed = (): void => { instance.notifyChange(ent); listeners.forEach((cb) => cb()); };
  tiles.addEventListener('load-model', (e) => { geladen.add(e.scene); material.uebernehmen(e.scene); });
  tiles.addEventListener('dispose-model', (e) => { geladen.delete(e.scene); material.verwerfen(e.scene); });
  tiles.addEventListener('load-error', (e) => {
    if (disposed || fehlerGemeldet) return;
    fehlerGemeldet = true; notify(`Gekachelte Ebene ${layer.title}: Kachel nicht ladbar – Ansicht unvollständig`);
    console.warn('Kachel nicht ladbar', e.url, e.error);
  });

  // Giro3D `add` wartet auf die erste Kachel; hängt der Server, darf der Start nicht ewig stehen.
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([instance.add(ent), new Promise<never>((_, nein) => {
      timer = setTimeout(() => nein(new Error('Wurzelkachel nicht rechtzeitig geladen')), WURZEL_TIMEOUT_MS);
    })]);
  } catch (e) { try { instance.remove(ent); } catch { /* war nie angemeldet */ } ent.dispose(); throw e; }
  finally { clearTimeout(timer); }

  const blaetter = new BlattSpeicher({ lade: o.lade ?? holeBytes });
  let desiredVisible = true; let overlay = 0;
  const gezeichnet = (): KachelMesh[] => {
    const meshes: KachelMesh[] = [];
    if (!ent.visible || ent.opacity <= 0) return meshes;
    tiles.group.traverseVisible((obj) => {
      if (!istMesh(obj)) return;
      const m = Array.isArray(obj.material) ? obj.material : [obj.material];
      if (m.some((x) => x.visible)) meshes.push(obj);
    });
    return meshes;
  };
  const weltBox: [Vec3, Vec3] = tileset.weltBox;
  const handle: TilesLayerHandle = {
    kind: 'mesh-tiles', id: layer.id, tileset, blaetter, budget,
    get failure() { return fehlerGemeldet ? 'load-error' : null; },
    get visible() { return ent.visible; },
    get opacity() { return ent.opacity; },
    get worldBox() { return weltBox; },
    get occluderMeshes() { return gezeichnet(); },
    get clippingPlaneCounts() {
      const counts: number[] = [];
      for (const m of gezeichnet()) for (const x of Array.isArray(m.material) ? m.material : [m.material]) counts.push(x.clippingPlanes?.length ?? 0);
      return counts;
    },
    blattUrl(k: TilesKnoten): string { return new URL(k.uri!, basis).href; },
    setVisible(on) { desiredVisible = on; ent.visible = on && overlay < 1; changed(); },
    setPanoramaOverlay(alpha) {
      overlay = alpha; const sichtbar = desiredVisible && overlay < 1;
      if (ent.visible !== sichtbar) { ent.visible = sichtbar; changed(); }
    },
    setOpacity(x) { ent.opacity = Math.max(0, Math.min(1, x)); for (const s of geladen) material.auffrischen(s); changed(); },
    setClippingPlanes(planes) {
      ent.clippingPlanes = planes; for (const s of geladen) material.auffrischen(s); changed();
    },
    setSonne(s) {
      if (s === null && !material.mitSonne) return;
      material.setSonne(s, geladen);
      // Tiles3D setzt castShadow/receiveShadow auch auf später geladene Kacheln.
      ent.castShadow = ent.receiveShadow = s !== null;
      changed();
    },
    statistik() {
      let sichtbar = 0, dreiecke = 0;
      for (const m of gezeichnet()) { sichtbar++; dreiecke += dreieckeVon(m); }
      const lru = tiles.lruCache as unknown as { cachedBytes: number; itemSet: { size: number } };
      return { geladen: geladen.size, sichtbar, sichtbareDreiecke: dreiecke, lruBytes: lru.cachedBytes, lruObjekte: lru.itemSet.size,
        blaetterGeladen: blaetter.anzahl, blattBytes: blaetter.belegt, laedt: ent.loading };
    },
    dispose() {
      if (disposed) return; disposed = true;
      try { instance.remove(ent); } catch { /* schon entfernt */ }
      ent.dispose(); blaetter.leeren(); geladen.clear(); listeners.length = 0;
    },
    onChange(cb) { listeners.push(cb); },
  };
  return handle;
}
