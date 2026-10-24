// Meshes liegen unter threeObjects und brauchen deshalb ein eigenes Budget und Ressourcen-Lebenszyklus.
import {
  AmbientLight, Box3, BufferAttribute, BufferGeometry, CompressedTexture, DirectionalLight, Group,
  LineBasicMaterial, LineSegments, Material, Mesh, MeshBasicMaterial, MeshLambertMaterial, MeshNormalMaterial,
  Object3D, Texture,
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
import { sonnenMaterial } from './sonneSchatten';
import type { SchattenUniform } from './sonneSchatten';
import { drahtBytes, drahtIndex, leseBegrenzt, MeshDateiZuGross, meshRootTransform, mipsVerwerfen, shouldLoadFull, stageBytes } from './meshMath';
import type { MeshFlaeche, MeshSicht } from './meshMath';

/** 'mobil' = Mobilstufe statt Vollstufe (Touch-Geräte): gezeichnet und verdeckend, aber kein Messfang — vereinfacht. */
export type MeshStage = 'none' | 'preview' | 'mobil' | 'full';
export type MeshTextur = 'ok' | 'fehlt' | 'keine';
export type MeshFailure = 'not-ktx2' | 'no-compressed-texture-support' | 'budget' | 'datei-zu-gross' | 'load-error';
/** Darstellung der Fläche (wie Sketchfab „Model Inspector“): Fototextur, neutrales Grau zur Formprüfung,
 *  Normalen als Farbe (Orientierung/Rauschen sichtbar), oder ausgeblendet (nur Drahtgitter). Licht
 *  („Schattierung“) wirkt auf die Textur; Grau ist immer beleuchtet (sonst eine flache Silhouette). Ohne Normalen im
 *  Paket schattieren Grau/Normalen flach im Shader — kein Zusatzspeicher, auch bei 4 Mio. Dreiecken. */
export type { MeshFlaeche };
const GRAU = 0xb8bcc0;
const DRAHT_FARBE = 0x8fa3b3;
const DRAHT_DECKKRAFT = 0.35;

const dreieckeVon = (g: BufferGeometry): number => Math.floor((g.index?.count ?? g.attributes.position?.count ?? 0) / 3);
export interface MeshLayerHandle extends MeshHandle {
  readonly stage: MeshStage;
  readonly failure: MeshFailure | null;
  /** Textur der gezeichneten Stufe: gebunden, deklariert aber nicht gebunden, oder keine deklariert. */
  readonly textur: MeshTextur;
  readonly bytes: number;
  readonly worldBox: [Vec3, Vec3] | null;
  readonly visible: boolean;
  readonly shading: boolean;
  readonly flaeche: MeshFlaeche;
  readonly draht: boolean;
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
  setFlaeche(f: MeshFlaeche): void;
  /** Drahtgitter über der Fläche (bei Fläche „aus“ allein). Kostet 24 Byte je Dreieck im Mesh-Budget. */
  setDraht(on: boolean): void;
  /** Live-Sonne (sonne.ts): beleuchtete Materialien mit präzisen Schattenkoordinaten, wirft und empfängt
   *  Schatten; die feste Lichtgruppe ist dann aus. null = aus. */
  setSonne(s: SchattenUniform | null): void;
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
type Lichter = { group: Group; users: Set<object>; shaded: Set<object>; sonne: boolean };
const sharedLights = new WeakMap<Instance, Lichter>();
function lightsFor(instance: Instance, position: Vec3, rotationX: number): Lichter {
  let l = sharedLights.get(instance);
  if (!l) {
    const group = new Group(); group.name = 'scanview-mesh-licht';
    group.position.set(...position); group.rotation.x = rotationX;
    const sun = new DirectionalLight(0xffffff, 1.5); sun.position.set(1, 2, 3);
    group.add(sun, sun.target, new AmbientLight(0xffffff, 0.5));
    group.visible = false; instance.threeObjects.add(group); group.updateMatrixWorld(true);
    l = { group, users: new Set(), shaded: new Set(), sonne: false };
    sharedLights.set(instance, l);
  }
  return l;
}
type Stage = { scene: Group; geometry: Set<BufferGeometry>; textures: Set<Texture>; materials: Set<Material>; bytes: number; textur?: MeshTextur;
  /** Drahtgitter je Mesh: LineSegments als Kind, teilt die Positionen, eigener Linienindex. */
  draht?: Map<Mesh, LineSegments> };
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
  return { scene, geometry, textures, materials, bytes: bytesOf(geometry, textures) };
}
/** Grafikspeicher aus Geometrie und Texturen. Getrennt von collect(), weil abgeleitete Materialien (Grau, Normalen)
 *  die Textur nicht tragen, sie aber auf der Grafikkarte bleibt — apply() zählt die Texturen der Stufe immer mit. */
function bytesOf(geometry: Set<BufferGeometry>, textures: Set<Texture>): number {
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
  return bytes;
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
  // Mit Live-Sonne bleibt die feste Lichtgruppe aus: sonst zwei Sonnen, eine davon ohne Schatten.
  const lightsUpdate = (): void => { lights.group.visible = lights.shaded.size > 0 && !lights.sonne; };
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
  let flaeche: MeshFlaeche = 'textur'; let draht = false;
  let sonne: SchattenUniform | null = null;
  /** Schattierung ohne nachgerechnete Normalen (flach), weil sie nicht ins Budget passten. */
  let flachZwang = false;
  const drahtMaterial = new LineBasicMaterial({ color: DRAHT_FARBE, transparent: true, depthWrite: false });
  const original = new Map<Mesh, MeshBasicMaterial[]>();
  /** Abgeleitete Materialien je Originalmaterial und Variante; landen in s.materials, discard entsorgt sie. */
  const varianten = new Map<MeshBasicMaterial, Map<string, Material>>();
  const changed = (): void => { root.updateMatrixWorld(true); instance.notifyChange(); listeners.forEach((cb) => cb()); };
  const used = (): number => [...budget.values()].reduce((a, b) => a + b, 0);
  const fail = (why: MeshFailure, text: string): void => { failure = why; notify(text); changed(); };
  // Grau/Normalen brauchen keine Normalen: ohne Attribut schattieren sie flach im Shader (kein Budget, Haus C 4 Mio. Dreiecke).
  const brauchtNormalen = (sh = shaded || sonne !== null): boolean => sh && !flachZwang;
  const lichtSetzen = (): void => {
    if (shaded || flaeche === 'grau') lights.shaded.add(root); else lights.shaded.delete(root);
    lightsUpdate();
  };
  const fehlendeNormalen = (s: Stage): number =>
    [...s.geometry].reduce((n, g) => n + (g.attributes.normal ? 0 : (g.attributes.position?.count ?? 0) * 12), 0);
  const drahtBedarf = (s: Stage): number => {
    let n = 0; s.scene.traverse((obj) => { if (isMesh(obj) && !s.draht?.has(obj)) n += drahtBytes(dreieckeVon(obj.geometry)); });
    return n;
  };
  const variante = (s: Stage, m: MeshBasicMaterial, flach: boolean): Material => {
    const art = flaeche === 'grau' ? 'grau' : flaeche === 'normalen' ? 'normalen'
      : (shaded || sonne) && flaeche === 'textur' ? 'licht' : 'basis';
    if (art === 'basis') return m;
    // Unter der Sonne eigene Varianten (gepatchter Shader); die ohne Sonne bleiben für das Ausschalten liegen.
    const mitSonne = sonne !== null && art !== 'normalen';
    const key = `${art}${flach ? '-flach' : ''}${mitSonne ? '-sonne' : ''}`;
    let je = varianten.get(m);
    if (!je) { je = new Map(); varianten.set(m, je); }
    let v = je.get(key);
    if (!v) {
      const seite = { side: m.side };
      if (art === 'licht') v = new MeshLambertMaterial({ map: m.map, vertexColors: m.vertexColors, color: m.color,
        flatShading: flach, ...seite, alphaTest: m.alphaTest });
      else if (art === 'grau') v = new MeshLambertMaterial({ color: GRAU, flatShading: flach, ...seite });
      else v = new MeshNormalMaterial({ flatShading: flach, ...seite });
      if (mitSonne && sonne) sonnenMaterial(v, sonne);
      je.set(key, v); s.materials.add(v);
    }
    return v;
  };
  const drahtEntfernen = (s: Stage): void => {
    for (const line of s.draht?.values() ?? []) {
      // Positionen gehören dem Mesh: vor dispose() lösen, sonst löscht three.js auch dessen GPU-Puffer.
      line.removeFromParent(); line.geometry.deleteAttribute('position'); line.geometry.dispose();
    }
    s.draht?.clear();
  };
  const apply = (s: Stage): void => {
    let ms = 0;
    const meshes: Mesh<BufferGeometry, Material | Material[]>[] = [];
    s.scene.traverse((obj) => { if (isMesh(obj)) meshes.push(obj); });
    for (const obj of meshes) {
      if (brauchtNormalen() && !obj.geometry.attributes.normal) {
        const start = performance.now(); obj.geometry.computeVertexNormals(); ms += performance.now() - start;
      }
      const basic = original.get(obj)!;
      const materials = basic.map((m) => {
        const mat = variante(s, m, !obj.geometry.attributes.normal);
        mat.opacity = opacity; mat.transparent = opacity < 1; mat.depthWrite = opacity >= 1;
        mat.visible = flaeche !== 'aus';
        // Drahtgitter liegt sonst in derselben Tiefe wie die Fläche und flimmert (Z-Fighting).
        mat.polygonOffset = draht; mat.polygonOffsetFactor = 1; mat.polygonOffsetUnits = 1;
        mat.clippingPlanes = planes; mat.needsUpdate = true;
        return mat;
      });
      // glTF-Primitiven ohne Gruppen brauchen ein Einzelmaterial. Ein Array ohne
      // geometry.groups zeichnet und raycastet in three.js kein einziges Dreieck.
      obj.material = materials.length === 1 && (obj.geometry as BufferGeometry).groups.length === 0 ? materials[0]! : materials;
      // In apply() statt beim Laden: die Vollstufe kommt später und muss ebenso werfen.
      obj.castShadow = obj.receiveShadow = sonne !== null;
      if (draht && !s.draht?.has(obj)) {
        const g = new BufferGeometry();
        g.setAttribute('position', obj.geometry.attributes.position!);
        g.setIndex(new BufferAttribute(drahtIndex(obj.geometry.index?.array ?? null, obj.geometry.attributes.position?.count ?? 0), 1));
        const line = new LineSegments(g, drahtMaterial); line.name = 'scanview-mesh-draht';
        line.raycast = () => undefined; // Messen und Verdecken laufen über die Fläche, nie über die Linien.
        obj.add(line); (s.draht ??= new Map()).set(obj, line);
      }
    }
    if (!draht) drahtEntfernen(s);
    drahtMaterial.opacity = DRAHT_DECKKRAFT * opacity; drahtMaterial.clippingPlanes = planes; drahtMaterial.needsUpdate = true;
    if (ms > 0) shadingMs.push(ms);
    // Normalen und Drahtgitter gehören zum Budget, auch wenn sie erst nachträglich entstehen.
    const jetzt = collect(s.scene);
    s.bytes = bytesOf(jetzt.geometry, new Set([...s.textures, ...jetzt.textures])) +
      [...s.draht?.keys() ?? []].reduce((n, m) => n + drahtBytes(dreieckeVon(m.geometry)), 0);
    budget.set(s, s.bytes);
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
      const normals = brauchtNormalen() ? fehlendeNormalen(s) : 0;
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
    budget.delete(s); drahtEntfernen(s);
    s.scene.traverse((obj) => { if (isMesh(obj)) {
      for (const m of original.get(obj) ?? []) varianten.delete(m);
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
      if (draht && used() + drahtBedarf(s) > GRENZEN.meshBytes) { draht = false; notify('Drahtgitter überschreitet das Grafikbudget – aus'); }
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
    get shading() { return shaded; }, get flaeche() { return flaeche; }, get draht() { return draht; },
    // Deckkraft 0 oder Fläche „aus“ (nur Drahtgitter): unsichtbar, also weder messbar noch verdeckend
    // (three.js prüft beim Raycast material.visible nicht).
    get pickMeshes() {
      const meshes: Mesh[] = [];
      if (stage === 'full' && root.visible && opacity > 0 && flaeche !== 'aus') current?.scene.traverseVisible((obj) => { if (isMesh(obj)) meshes.push(obj); });
      return meshes;
    },
    get occluderMeshes() {
      const meshes: Mesh[] = [];
      if (root.visible && opacity > 0 && flaeche !== 'aus') current?.scene.traverseVisible((obj) => { if (isMesh(obj)) meshes.push(obj); });
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
      // Schattierung und Sonne brauchen dieselben Normalen: Zwang gilt, solange eins von beiden an ist.
      const war = flachZwang;
      flachZwang = (on || sonne !== null) && current !== null && (war || used() + fehlendeNormalen(current) > GRENZEN.meshBytes);
      if (flachZwang && !war) notify('Schattierung flach – Normalen passen nicht ins Grafikbudget');
      shaded = on; lichtSetzen();
      if (current) apply(current); changed();
    },
    setFlaeche(f) {
      if (flaeche === f) return;
      flaeche = f; lichtSetzen();
      if (current) apply(current); changed();
    },
    setSonne(s) {
      if (sonne === s) return;
      const an = s !== null;
      const war = flachZwang;
      flachZwang = (an || shaded) && current !== null && (war || used() + fehlendeNormalen(current) > GRENZEN.meshBytes);
      if (flachZwang && !war) notify('Sonne: Schattierung flach – Normalen passen nicht ins Grafikbudget');
      sonne = s; lights.sonne = an; lightsUpdate();
      if (current) apply(current); changed();
    },
    setDraht(on) {
      if (draht === on) return;
      if (on && current && used() + drahtBedarf(current) > GRENZEN.meshBytes) { notify('Drahtgitter überschreitet das Grafikbudget'); return; }
      draht = on;
      if (current) apply(current); changed();
    },
    update(camera, sicht) { if (measuring || shouldLoadFull(camera, layer.boundsWorld, sicht)) requestFull(); },
    dispose() {
      if (disposed) return; disposed = true; abort.abort();
      if (current) removeStage(current); current = null; stage = 'none';
      root.removeFromParent(); ktx2.dispose(); drahtMaterial.dispose(); listeners.length = 0;
      lights.shaded.delete(root); lights.users.delete(root); lightsUpdate();
      if (lights.users.size === 0) { lights.group.removeFromParent(); sharedLights.delete(instance); }
      instance.notifyChange();
    },
    onChange(cb) { listeners.push(cb); },
  };
  return handle;
}
