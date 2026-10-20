// Meshes liegen unter threeObjects und brauchen deshalb ein eigenes Budget und Ressourcen-Lebenszyklus.
import {
  AmbientLight, Box3, BufferGeometry, CompressedTexture, DirectionalLight, Group, Material,
  Mesh, MeshBasicMaterial, MeshLambertMaterial, Object3D, Texture,
} from 'three';
import { ktx2EingebettetPlugin } from './ktx2Eingebettet';
import { vormerkenMeshFeatureEdges } from './meshPickMath';
import type { Plane } from 'three';
import type Instance from '@giro3d/giro3d/core/Instance.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/examples/jsm/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import type { MeshHandle } from './layers';
import type { MeshLayer, Vec3 } from './manifest';
import { resolveUrl } from './manifest';
import { GRENZEN } from './geraet';
import { leseBegrenzt, MeshDateiZuGross, meshRootTransform, mipsVerwerfen, shouldLoadFull, stageBytes } from './meshMath';
import type { MeshSicht } from './meshMath';

/** 'mobil' = Mobilstufe statt Vollstufe (Touch-Geräte): gezeichnet und verdeckend, aber kein Messfang — vereinfacht. */
export type MeshStage = 'none' | 'preview' | 'mobil' | 'full';
export type MeshTextur = 'ok' | 'fehlt' | 'keine';
export type MeshFailure = 'not-ktx2' | 'no-compressed-texture-support' | 'budget' | 'datei-zu-gross' | 'load-error';
export interface MeshLayerHandle extends MeshHandle {
  readonly stage: MeshStage;
  readonly failure: MeshFailure | null;
  /** Textur der gezeichneten Stufe: gebunden, deklariert aber nicht gebunden, oder keine deklariert. */
  readonly textur: MeshTextur;
  readonly bytes: number;
  readonly worldBox: [Vec3, Vec3] | null;
  readonly visible: boolean;
  readonly shading: boolean;
  readonly pickMeshes: readonly Mesh[];
  /** Was gerade sichtbar gezeichnet wird (Vorschau oder Vollstufe) — verdeckt, misst aber nie. */
  readonly occluderMeshes: readonly Mesh[];
  requestFull(): void;
  setMeasuring(on: boolean): void;
  readonly clippingPlaneCounts: number[];
  readonly shadingMs: number[];
  setClippingPlanes(planes: Plane[]): void;
  setPanoramaOverlay(alpha: number): void;
  setShading(on: boolean): void;
  update(cameraWorld: Vec3, sicht?: MeshSicht): void;
  dispose(): void;
  onChange(cb: () => void): void;
}

interface GltfJson {
  images?: { mimeType?: string; uri?: string }[];
  materials?: Record<string, unknown>[];
  buffers?: { uri?: string }[];
}
// three typisiert `instanceof Mesh` als Mesh<any>; der Wächter gibt die tatsächlichen Typen zurück.
const isMesh = (o: Object3D): o is Mesh<BufferGeometry, Material | Material[]> => o instanceof Mesh;
class MeshTextureError extends Error {}
class MeshBudgetError extends Error {}

/** Vor GLTFLoader prüfen: PNG/JPEG benötigen blob:-Bilder, die die CSP bewusst nicht erlaubt. */
export function prepareMeshGlb(buffer: ArrayBuffer, withoutTextures = false): ArrayBuffer {
  const header = new DataView(buffer);
  if (buffer.byteLength < 20 || header.getUint32(0, true) !== 0x46546c67 || header.getUint32(4, true) !== 2 ||
      header.getUint32(8, true) !== buffer.byteLength || header.getUint32(16, true) !== 0x4e4f534a)
    throw new Error('Ungültiges GLB');
  const length = header.getUint32(12, true);
  const json = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, length))) as GltfJson;
  for (const image of json.images ?? []) {
    if (image.mimeType !== 'image/ktx2' && !(image.mimeType === undefined && image.uri?.endsWith('.ktx2')))
      throw new MeshTextureError('Mesh-Textur nicht KTX2 – Ebene nicht ladbar');
    // Import Weg A liefert eingebettete Bilder; fremde URLs werden nicht nachgeladen.
    if (image.uri) throw new Error('Mesh-Texturen müssen im GLB eingebettet sein');
  }
  if (json.buffers?.some((b) => b.uri)) throw new Error('Mesh-Geometrie muss im GLB eingebettet sein');
  if (!withoutTextures) return buffer;
  // Ohne GPU-Kompression bleibt auch bei älteren texturierten Vorschauen die Geometrie benutzbar.
  json.materials = (json.materials ?? []).map(() => ({ extensions: { KHR_materials_unlit: {} } }));
  const encoded = new TextEncoder().encode(JSON.stringify(json));
  const padded = Math.ceil(encoded.length / 4) * 4;
  const rest = new Uint8Array(buffer, 20 + length);
  const result = new ArrayBuffer(20 + padded + rest.length);
  const view = new DataView(result);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, result.byteLength, true);
  view.setUint32(12, padded, true); view.setUint32(16, 0x4e4f534a, true);
  new Uint8Array(result, 20, padded).fill(32);
  new Uint8Array(result, 20, encoded.length).set(encoded);
  new Uint8Array(result, 20 + padded).set(rest);
  return result;
}

const budgets = new WeakMap<Instance, Map<object, number>>();
/** Vorschau ist klein (≤ ~1 MB); hängt der Server, darf sie den Viewer-Start nicht aufhalten. */
export const PREVIEW_TIMEOUT_MS = 20_000;

/** three.js wendet Lichter szenenweit an, egal wo sie hängen — deshalb eine Lichtgruppe je
 *  Instanz, sichtbar solange mindestens eine Mesh-Ebene schattiert. Die Sonne zielt auf ihr eigenes
 *  Ziel in derselben Gruppe, die Richtung hängt also nicht von der Lage zum Weltursprung ab. */
const sharedLights = new WeakMap<Instance, { group: Group; users: Set<object>; shaded: Set<object> }>();
function lightsFor(instance: Instance, position: Vec3, rotationX: number): { group: Group; users: Set<object>; shaded: Set<object> } {
  let l = sharedLights.get(instance);
  if (!l) {
    const group = new Group(); group.name = 'scanview-mesh-licht';
    group.position.set(...position); group.rotation.x = rotationX;
    const sun = new DirectionalLight(0xffffff, 1.5); sun.position.set(1, 2, 3);
    group.add(sun, sun.target, new AmbientLight(0xffffff, 0.5));
    group.visible = false; instance.threeObjects.add(group); group.updateMatrixWorld(true);
    l = { group, users: new Set(), shaded: new Set() };
    sharedLights.set(instance, l);
  }
  return l;
}
type Stage = { scene: Group; geometry: Set<BufferGeometry>; textures: Set<Texture>; materials: Set<Material>; bytes: number; textur?: MeshTextur };
const materialsOf = (m: Mesh): Material[] => Array.isArray(m.material) ? m.material : [m.material];

function collect(scene: Group): Stage {
  const geometry = new Set<BufferGeometry>();
  const materials = new Set<Material>();
  const textures = new Set<Texture>();
  scene.traverse((o) => {
    if (!(isMesh(o))) return;
    geometry.add(o.geometry);
    for (const m of materialsOf(o)) {
      materials.add(m);
      for (const value of Object.values(m)) if (value instanceof Texture) textures.add(value);
    }
  });
  // Interleaved Attribute teilen sich häufig denselben Arraypuffer; nur einmal zählen.
  const arrays = new Set<ArrayBufferLike>();
  for (const g of geometry) {
    for (const a of Object.values(g.attributes)) arrays.add(('data' in a ? a.data.array : a.array).buffer);
    if (g.index) arrays.add(g.index.array.buffer);
  }
  let bytes = stageBytes({ attributeBytes: [...arrays].reduce((n, a) => n + a.byteLength, 0), indexBytes: 0,
    textures: [...textures].map((t) => ({ width: (t.image as { width: number }).width,
      height: (t.image as { height: number }).height, compressed: t instanceof CompressedTexture, mipmaps: t.mipmaps.length > 1 })) });
  // Tatsächliche Mipmap-Puffer sind genauer als die 1-Byte/Pixel-Näherung (ETC1S/Block-Rundung).
  for (const t of textures) if (t instanceof CompressedTexture) {
    bytes -= stageBytes({ attributeBytes: 0, indexBytes: 0, textures: [{ width: t.image.width, height: t.image.height,
      compressed: true, mipmaps: t.mipmaps.length > 1 }] });
    bytes += t.mipmaps.reduce((n, m) => n + m.data.byteLength, 0);
  }
  return { scene, geometry, textures, materials, bytes };
}
function discard(s: Stage): void {
  s.scene.removeFromParent();
  for (const g of s.geometry) g.dispose();
  for (const m of s.materials) m.dispose();
  for (const t of s.textures) { t.dispose(); (t.image as { close?: () => void } | null)?.close?.(); }
}

export async function createMeshLayer(o: {
  instance: Instance; layer: MeshLayer; originWorld: Vec3; pkgBase: string; wasmBase: string;
  notify: (text: string) => void;
}): Promise<MeshLayerHandle> {
  const { instance, layer, notify } = o;
  const ktx2 = new KTX2Loader().setTranscoderPath(`${o.wasmBase}basis/`).detectSupport(instance.renderer);
  const config = (ktx2 as unknown as { workerConfig: Record<string, boolean> }).workerConfig;
  const compressed = ['astcSupported', 'etc2Supported', 'etc1Supported', 'dxtSupported', 'bptcSupported', 'pvrtcSupported']
    .some((key) => config[key]);
  const loader = new GLTFLoader().setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder)
    .register(ktx2EingebettetPlugin(ktx2));
  const root = new Group(); root.name = `scanview-mesh-${layer.id}`;
  const t = meshRootTransform(o.originWorld);
  root.position.set(...t.position); root.rotation.x = t.rotationX;
  instance.threeObjects.add(root);
  const lights = lightsFor(instance, t.position, t.rotationX); lights.users.add(root);
  const lightsUpdate = (): void => { lights.group.visible = lights.shaded.size > 0; };
  const budget = budgets.get(instance) ?? new Map<object, number>(); budgets.set(instance, budget);
  const abort = new AbortController();
  const listeners: (() => void)[] = [];
  const shadingMs: number[] = [];
  let current: Stage | null = null;
  let stage: MeshStage = 'none';
  let failure: MeshFailure | null = compressed ? null : 'no-compressed-texture-support';
  let textur: MeshTextur = 'keine';
  let loading = false; let disposed = false; let measuring = false;
  let desiredVisible = true; let overlay = 0;
  let opacity = 1; let planes: Plane[] = []; let shaded = false;
  const original = new Map<Mesh, MeshBasicMaterial[]>();
  const lambert = new Map<MeshBasicMaterial, MeshLambertMaterial>();
  const changed = (): void => { root.updateMatrixWorld(true); instance.notifyChange(); listeners.forEach((cb) => cb()); };
  const used = (): number => [...budget.values()].reduce((a, b) => a + b, 0);
  const fail = (why: MeshFailure, text: string): void => { failure = why; notify(text); changed(); };
  const apply = (s: Stage): void => {
    let ms = 0;
    s.scene.traverse((obj) => {
      if (!(isMesh(obj))) return;
      if (shaded && !obj.geometry.attributes.normal) {
        const start = performance.now(); obj.geometry.computeVertexNormals(); ms += performance.now() - start;
      }
      const basic = original.get(obj)!;
      const materials = basic.map((m) => {
        let mat: MeshBasicMaterial | MeshLambertMaterial = m;
        if (shaded) {
          let lit = lambert.get(m);
          if (!lit) {
            lit = new MeshLambertMaterial({ map: m.map, vertexColors: m.vertexColors, color: m.color,
              side: m.side, alphaTest: m.alphaTest });
            lambert.set(m, lit); s.materials.add(lit);
          }
          mat = lit;
        }
        mat.opacity = opacity; mat.transparent = opacity < 1; mat.depthWrite = opacity >= 1;
        mat.clippingPlanes = planes; mat.needsUpdate = true;
        return mat;
      });
      // glTF-Primitiven ohne Gruppen brauchen ein Einzelmaterial. Ein Array ohne
      // geometry.groups zeichnet und raycastet in three.js kein einziges Dreieck.
      obj.material = materials.length === 1 && (obj.geometry as BufferGeometry).groups.length === 0 ? materials[0]! : materials;
    });
    if (ms > 0) shadingMs.push(ms);
    // Normalen gehören zum Budget, auch wenn sie erst beim Schattieren entstehen.
    s.bytes = collect(s.scene).bytes; budget.set(s, s.bytes);
  };
  const load = async (url: string, preview: boolean, mobil = false): Promise<Stage> => {
    const absolute = resolveUrl(o.pkgBase, url);
    // Vorschau mit Zeitlimit über Antwort und Körper; die Vollstufe (zig MB) lädt ohne Limit im Hintergrund.
    const limit = new AbortController();
    const stop = (): void => limit.abort();
    abort.signal.addEventListener('abort', stop);
    const timer = preview ? setTimeout(stop, PREVIEW_TIMEOUT_MS) : undefined;
    let buffer: ArrayBuffer;
    try {
      const response = await fetch(absolute, { signal: limit.signal });
      if (!response.ok) { void response.body?.cancel().catch(() => undefined); throw new Error(`HTTP ${response.status}`); }
      // Vollstufe auf Touch-Geräten nur bis GRENZEN.meshDateiBytes: sonst läuft der Speicher schon beim Laden über.
      const laenge = Number(response.headers.get('content-length'));
      buffer = preview || !Number.isFinite(GRENZEN.meshDateiBytes) ? await response.arrayBuffer()
        : await leseBegrenzt(response.body, laenge > 0 && !response.headers.get('content-encoding') ? laenge : null,
          GRENZEN.meshDateiBytes, stop);
    } finally { clearTimeout(timer); abort.signal.removeEventListener('abort', stop); }
    const data = prepareMeshGlb(buffer, preview && !compressed);
    const gltf = await loader.parseAsync(data, new URL('.', absolute).toString());
    // Touch-Geräte: schärfste Mipmap-Stufe(n) der Vollstufe verwerfen, bevor sie auf die Grafikkarte gehen.
    // Die Mobilstufe ist schon auf ≤ 2048 px gebaut; dort keine weitere Stufe verwerfen.
    if (!preview && !mobil && GRENZEN.meshMipsVerwerfen > 0) {
      gltf.scene.traverse((obj) => {
        if (!isMesh(obj)) return;
        for (const m of materialsOf(obj)) {
          const map = (m as MeshBasicMaterial).map;
          if (map instanceof CompressedTexture) mipsVerwerfen(map, GRENZEN.meshMipsVerwerfen);
        }
      });
    }
    const s = collect(gltf.scene);
    if (disposed) { discard(s); throw new Error('Mesh abgebaut'); }
    // GLTFLoader verwirft Texturfehler still (nur Vertexfarben): deklariert, aber nicht gebunden,
    // wird sichtbar gemeldet statt blass weiterzuzeichnen. Ohne Textur-Kompression lädt die
    // Vorschau bewusst ohne Texturen.
    const deklariert = !(preview && !compressed) && ((gltf.parser.json as GltfJson).materials ?? [])
      .some((m) => (m.pbrMetallicRoughness as { baseColorTexture?: unknown } | undefined)?.baseColorTexture !== undefined);
    const gebunden = [...s.materials].some((m) => (m as MeshBasicMaterial).map);
    s.textur = gebunden ? 'ok' : deklariert ? 'fehlt' : 'keine';
    try {
      for (const mat of s.materials) {
        const map = (mat as MeshBasicMaterial).map;
        if (map && !(map instanceof CompressedTexture)) throw new MeshTextureError('Mesh-Textur nicht KTX2 – Ebene nicht ladbar');
      }
      const normals = shaded ? [...s.geometry].reduce((n, g) => n + (g.attributes.normal ? 0 : (g.attributes.position?.count ?? 0) * 12), 0) : 0;
      if (used() + s.bytes + normals > GRENZEN.meshBytes) throw new MeshBudgetError('Mesh-Budget überschritten');
      s.scene.traverse((obj) => {
        if (!(isMesh(obj))) return;
        original.set(obj, materialsOf(obj).map((m) => {
          const source = m as MeshBasicMaterial;
          // Texturiert: Vertexfarben aus. Sie sind in scanview-Paketen nur eine grobe Kopie der
          // Texturfarben (Vorschau-Ersatz); glTF würde beide multiplizieren und doppelt abdunkeln.
          // Pakete ab 0.8.2 lassen COLOR_0 in texturierten Vollstufen weg, ältere nicht.
          const basic = new MeshBasicMaterial({ map: source.map, vertexColors: source.vertexColors && !source.map,
            color: source.color, side: source.side, alphaTest: source.alphaTest });
          s.materials.add(basic); return basic;
        }));
      });
      budget.set(s, s.bytes + normals);
      return s;
    } catch (e) { discard(s); throw e; }
  };
  const report = (e: unknown): void => {
    if (disposed) return;
    if (e instanceof MeshTextureError) fail('not-ktx2', e.message);
    else if (e instanceof MeshBudgetError) fail('budget', 'Mesh zu groß für das Grafikbudget – Vorschau bleibt');
    else if (e instanceof MeshDateiZuGross) {
      fail('datei-zu-gross', `Mesh in voller Auflösung zu groß für dieses Gerät (${Math.round(e.bytes / 2 ** 20)} MB) – Vorschau bleibt`);
    }
    else fail('load-error', 'Mesh nicht ladbar – Vorschau bleibt, falls vorhanden');
  };
  const texturMelden = (s: Stage): void => {
    textur = s.textur ?? 'keine';
    if (textur === 'fehlt') notify(`Mesh ${layer.title}: Textur nicht geladen – nur Vertexfarben`);
  };
  const removeStage = (s: Stage): void => {
    budget.delete(s);
    s.scene.traverse((obj) => { if (isMesh(obj)) {
      for (const m of original.get(obj) ?? []) lambert.delete(m);
      original.delete(obj);
    } }); discard(s);
  };
  const full = async (): Promise<void> => {
    loading = true;
    try {
      const mobil = GRENZEN.meshMobil && layer.mobile !== undefined;
      const s = await load(mobil ? layer.mobile!.url : layer.url, false, mobil);
      if (disposed) { removeStage(s); return; }
      if (current) removeStage(current);
      current = s; root.add(s.scene); apply(s);
      root.updateMatrixWorld(true);
      const pickMeshes: Mesh[] = []; s.scene.traverse((obj) => { if (isMesh(obj)) pickMeshes.push(obj); });
      // Kanten für den Messfang erst bei Bedarf (Messwerkzeug wärmt vor), nicht hier: blockierte sonst den Tab.
      if (!mobil) vormerkenMeshFeatureEdges(pickMeshes, root.matrixWorld);
      stage = mobil ? 'mobil' : 'full'; texturMelden(s); changed();
    } catch (e) { report(e); }
    finally { loading = false; }
  };
  const requestFull = (): void => {
    if (!disposed && root.visible && stage === 'preview' && failure === null && !loading) void full();
  };
  if (!compressed) notify('Grafikkarte ohne komprimierte Texturen – nur Vorschau des Meshes');
  try {
    current = await load(layer.previewUrl, true); root.add(current.scene); apply(current); stage = 'preview'; texturMelden(current); changed();
  } catch (e) { report(e); }
  const handle: MeshLayerHandle = {
    id: layer.id,
    get stage() { return stage; }, get failure() { return failure; }, get textur() { return textur; }, get bytes() { return current?.bytes ?? 0; },
    get disabled() { return stage === 'none' && failure !== null; },
    get shading() { return shaded; },
    // Deckkraft 0: unsichtbar, also weder messbar noch verdeckend.
    get pickMeshes() {
      const meshes: Mesh[] = [];
      if (stage === 'full' && root.visible && opacity > 0) current?.scene.traverseVisible((obj) => { if (isMesh(obj)) meshes.push(obj); });
      return meshes;
    },
    get occluderMeshes() {
      const meshes: Mesh[] = [];
      if (root.visible && opacity > 0) current?.scene.traverseVisible((obj) => { if (isMesh(obj)) meshes.push(obj); });
      return meshes;
    },
    requestFull,
    setMeasuring(on) { measuring = on; if (on) requestFull(); },
    get visible() { return root.visible; }, get shadingMs() { return shadingMs; },
    get worldBox(): [Vec3, Vec3] | null {
      if (!current) return null;
      root.updateMatrixWorld(true); const b = new Box3().setFromObject(current.scene);
      return [[b.min.x, b.min.y, b.min.z], [b.max.x, b.max.y, b.max.z]];
    },
    get clippingPlaneCounts() {
      const counts: number[] = [];
      current?.scene.traverse((obj) => { if (isMesh(obj)) counts.push(...materialsOf(obj).map((m) => m.clippingPlanes?.length ?? 0)); });
      return counts;
    },
    setVisible(on) { desiredVisible = on; root.visible = on && overlay < 1; if (measuring) requestFull(); changed(); },
    setPanoramaOverlay(alpha) {
      overlay = alpha; const visible = desiredVisible && overlay < 1;
      if (root.visible !== visible) { root.visible = visible; changed(); }
    },
    setOpacity(x) { opacity = Math.max(0, Math.min(1, x)); if (current) apply(current); changed(); },
    setClippingPlanes(p) { planes = p; if (current) apply(current); changed(); },
    setShading(on) {
      if (shaded === on) return;
      if (on && current) {
        const extra = [...current.geometry].reduce((n, g) => n + (g.attributes.normal ? 0 : (g.attributes.position?.count ?? 0) * 12), 0);
        if (used() + extra > GRENZEN.meshBytes) { notify('Schattierung überschreitet das Grafikbudget'); return; }
      }
      shaded = on;
      if (on) lights.shaded.add(root); else lights.shaded.delete(root);
      lightsUpdate();
      if (current) apply(current); changed();
    },
    update(camera, sicht) { if (measuring || shouldLoadFull(camera, layer.boundsWorld, sicht)) requestFull(); },
    dispose() {
      if (disposed) return; disposed = true; abort.abort();
      if (current) removeStage(current); current = null; stage = 'none';
      root.removeFromParent(); ktx2.dispose(); listeners.length = 0;
      lights.shaded.delete(root); lights.users.delete(root); lightsUpdate();
      if (lights.users.size === 0) { lights.group.removeFromParent(); sharedLights.delete(instance); }
      instance.notifyChange();
    },
    onChange(cb) { listeners.push(cb); },
  };
  return handle;
}
