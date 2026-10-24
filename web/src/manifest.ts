// Schema 1 und 2 → Innenmodell Project. Beide Formen von project.json werden
// zu einem gemeinsamen Project mit frame + layers[] gelesen; die Bequemlichkeitsverweise cloud
// (primäre Punktwolken-Ebene) und stations (Panorama-Ebene) sind dasselbe Objekt wie in layers[]
// oder null. Ebenen werden aus EXPLIZITEN Feldern gebaut (kein Spread der Quelle): fremde
// Felder wie source, readerPointCount, invalidDropped, per-Ebenen-conversion oder verify erreichen
// das Innenmodell nie. cloud.offset ist nur gesetzt, wenn der tatsächliche LAS-Offset vom
// gerundeten originWorld abweicht; conversion (Projektebene) beschreibt Laufzeit/Speicherbedarf und
// den gewählten Konvertierungsmodus des Imports.
import { leseGenauigkeit } from './genauigkeit';
import type { Genauigkeit } from './genauigkeit';

export type Vec3 = [number, number, number];

/**
 * Draufsicht für die Minikarte, geschrieben von `scanview-import overview`. Additiv zu
 * Schema 1. Pixel (c, r) deckt x ∈ [x0 + c·s, x0 + (c+1)·s], y ∈ [y0 − (r+1)·s, y0 − r·s].
 */
export interface Topview {
  url: string;
  widthPx: number;
  heightPx: number;
  pixelSizeM: number;
  upperLeftWorld: [number, number];
  pixelAnchor: 'edge';
  zMaxWorld: number | null;
}

/** Herkunft einer Punktwolken-Ebene: Pflicht bei Schema 2, für Schema 1 fest „scan". */
export interface Provenance {
  kind: 'scan' | 'photogrammetry' | 'other';
  interpolated: boolean;
  scaleVerified: boolean;
  scaleSource: string;
  /** Genauigkeitsnachweis (F15, `scanview-import genauigkeit`); fehlt bei Ebenen ohne Nachweis. */
  accuracy?: Genauigkeit;
}

/** Gemeinsamer Bezugsrahmen: Koordinatensystem, Einheit, Weltursprung. */
export interface Frame {
  crs: { wkt: string | null; verticalDatum: string | null; declaredBy: string };
  unit: 'm';
  originWorld: Vec3;
  /** Quasigeoidhöhe ζ (ellipsoidisch minus NHN) am Ursprung, vom Import (hoehen.undulation, F23). */
  undulation?: Undulation;
}

export interface Undulation {
  atOriginM: number;
  maxDeviationM: number;
  grid: string;
}

/** Optional und additiv: Unvollständiges gilt als fehlend (dann keine Zweithöhe), statt das Paket abzulehnen. */
function parseUndulation(raw: unknown): Undulation | undefined {
  const u = raw as Record<string, unknown> | null | undefined;
  if (!u || typeof u !== 'object') return undefined;
  const { atOriginM: a, maxDeviationM: d, grid: g } = u;
  if (typeof a !== 'number' || !Number.isFinite(a) || typeof d !== 'number' || !Number.isFinite(d) || d < 0) return undefined;
  if (typeof g !== 'string') return undefined;
  return { atOriginM: a, maxDeviationM: d, grid: g };
}

export interface PointcloudLayer {
  id: string;
  kind: 'pointcloud';
  title: string;
  /** false = beim Start ausgeblendet (Voreinstellung ohne ly=); fehlt = sichtbar. */
  startVisible?: boolean;
  url: string;
  pointCount: number;
  boundsWorld: [Vec3, Vec3];
  attributes: string[];
  scale: number;
  /** Nur vorhanden, wenn der LAS-Offset ≠ originWorld ist. */
  offset?: Vec3;
  /**
   * true = jeder Punkt trägt PointSourceId = Scanindex + 1 (nur mit bestandenem verify).
   * Fehlt bei Altpaketen → im Panorama kein Scanfilter.
   */
  pointSourceIsScan?: boolean;
  /** Klassen-Histogramm der ausgelieferten Punkte (F30), Schlüssel = Klassenwert als Text. */
  classes?: Record<string, number>;
  provenance: Provenance;
}

export interface PanoramasLayer {
  id: string;
  kind: 'panoramas';
  title: string;
  /** false = beim Start ausgeblendet (Voreinstellung ohne ly=); fehlt = sichtbar. */
  startVisible?: boolean;
  url: string;
  count: number;
  pointcloudLayer: string;
}

export interface MeshLayer {
  id: string;
  kind: 'mesh';
  title: string;
  /** false = beim Start ausgeblendet (Voreinstellung ohne ly=); fehlt = sichtbar. */
  startVisible?: boolean;
  format: 'gltf';
  url: string;
  previewUrl: string;
  previewColor: 'vertex' | 'texture';
  triangleCount: number;
  boundsWorld: [Vec3, Vec3];
  positionStepM: number;
  provenance: Provenance;
  /** Kleinere Vollstufe für Touch-Geräte (vereinfacht, ETC1S ≤ 2048 px); fehlt bei älteren Paketen. */
  mobile?: { url: string; sizeBytes: number; triangleCount: number };
}

/** Gaussian-Sichtschicht: lokale Mittelpunkte relativ zu frame.originWorld, niemals messbar. */
export interface SplatsLayer {
  id: string;
  kind: 'splats';
  title: string;
  format: 'ply' | 'spz' | 'rad';
  url: string;
  splatCount: number;
  sizeBytes: number;
  boundsWorld: [Vec3, Vec3];
  shDegree: 0 | 1 | 2 | 3;
  chunkCount?: number;
  lodMethod?: 'bhatt-lod';
  /** Rahmenbeleg gegen die Punktwolke — oder `'keine Wolke'` bei einem Paket ohne Punktwolke
   * (dann sind Lage und Maßstab nicht belegt, der Viewer sagt es an der Ebene). */
  frameCheck: { ok: true; samples: number; p50M: number; p95M: number } | typeof KEINE_WOLKE;
  /** false = beim Start ausgeblendet (Voreinstellung ohne ly=); fehlt = sichtbar. */
  startVisible?: boolean;
}

/** frameCheck einer Splat-Ebene in einem Paket ohne Punktwolke (`scanview-import neu-ohne-wolke`). */
export const KEINE_WOLKE = 'keine Wolke';

/** Orientierte Fotos (images.json); Inhalt lädt images.ts. */
export interface ImagesLayer {
  id: string;
  kind: 'images';
  title: string;
  /** false = beim Start ausgeblendet (Voreinstellung ohne ly=); fehlt = sichtbar. */
  startVisible?: boolean;
  url: string;
  count: number;
}

/** Jede andere Ebenenart (z. B. 3D-Tiles) — kein Fehler. */
export interface UnsupportedLayer {
  id: string;
  kind: string;
  title: string;
  /** false = beim Start ausgeblendet (Voreinstellung ohne ly=); fehlt = sichtbar. */
  startVisible?: boolean;
  unsupported: true;
}

export type Layer = PointcloudLayer | PanoramasLayer | MeshLayer | SplatsLayer | ImagesLayer | UnsupportedLayer;

export interface Project {
  schemaVersion: 1 | 2;
  projectId: string;
  revision: number;
  title: string;
  frame: Frame;
  layers: Layer[];
  /** Bequemlichkeit (L7): = frame.originWorld / frame.crs */
  originWorld: Vec3;
  crs: Frame['crs'];
  /** Primäre Punktwolke (erste Ebene kind 'pointcloud') oder null; dasselbe Objekt wie in layers. */
  cloud: PointcloudLayer | null;
  meshes: MeshLayer[];
  splats: SplatsLayer[];
  /** Panorama-Ebene (erste kind 'panoramas') oder null. */
  stations: PanoramasLayer | null;
  /** Fotos-Ebene (erste kind 'images') oder null. */
  images: ImagesLayer | null;
  initialView: { target: Vec3; distance: number; layers?: string[]; position?: Vec3 };
  initialViewDeclared: boolean;
  conversion?: { seconds: number; peakRssMb: number; mode?: 'direct' | 'staged' };
  topview?: Topview;
}

/** IDs von Ebenen: kurz, kleingeschrieben, ohne Sonderzeichen außer Bindestrich. */
export const LAYER_ID_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;

export class ManifestError extends Error {}

const isVec3 = (v: unknown): v is Vec3 =>
  Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === 'number' && Number.isFinite(x));

function parseProjectId(m: Record<string, unknown>): string {
  if (typeof m.projectId !== 'string' || m.projectId === '') throw new ManifestError('projectId fehlt.');
  return m.projectId;
}

function parseRevision(m: Record<string, unknown>): number {
  if (typeof m.revision !== 'number' || !Number.isFinite(m.revision))
    throw new ManifestError('revision fehlt oder ist keine Zahl.');
  return m.revision;
}

function parseInitialView(m: Record<string, unknown>): Project['initialView'] {
  if (m.initialView === undefined) return { target: [0, 0, 0], distance: 1 };
  const iv = m.initialView as { target?: unknown; distance?: unknown; layers?: unknown; position?: unknown } | null;
  if (!iv || !isVec3(iv.target)) throw new ManifestError('initialView ist ungültig.');
  if (typeof iv.distance !== 'number' || !Number.isFinite(iv.distance) || iv.distance <= 0)
    throw new ManifestError('initialView.distance muss positiv und endlich sein.');
  if (iv.layers !== undefined && (!Array.isArray(iv.layers) || !iv.layers.every((id) => typeof id === 'string') ||
      new Set(iv.layers).size !== iv.layers.length)) throw new ManifestError('initialView.layers ungültig.');
  if (iv.position !== undefined && (!isVec3(iv.position) || iv.position.every((v, i) => v === (iv.target as Vec3)[i])))
    throw new ManifestError('initialView.position ungültig.');
  return { target: iv.target, distance: iv.distance, ...(iv.position !== undefined ? { position: iv.position as Vec3 } : {}), ...(iv.layers !== undefined ? { layers: iv.layers as string[] } : {}) };
}

function parseConversion(raw: unknown): Project['conversion'] | undefined {
  if (raw === undefined) return undefined;
  const c = raw as { seconds?: unknown; peakRssMb?: unknown; mode?: unknown } | null;
  if (!c || typeof c.seconds !== 'number' || typeof c.peakRssMb !== 'number')
    throw new ManifestError('conversion ist ungültig.');
  if (c.mode !== undefined && c.mode !== 'direct' && c.mode !== 'staged')
    throw new ManifestError(`conversion.mode "${String(c.mode)}" wird nicht unterstützt (erwartet direct/staged).`);
  const result: NonNullable<Project['conversion']> = { seconds: c.seconds, peakRssMb: c.peakRssMb };
  if (c.mode !== undefined) result.mode = c.mode;
  return result;
}

function parseTopview(raw: unknown): Topview | undefined {
  if (raw === undefined) return undefined;
  const t = raw as Record<string, unknown> | null;
  const posInt = (x: unknown): boolean => Number.isInteger(x) && (x as number) > 0;
  const finite = (x: unknown): boolean => typeof x === 'number' && Number.isFinite(x);
  if (
    !t ||
    typeof t.url !== 'string' ||
    t.url === '' ||
    !posInt(t.widthPx) ||
    !posInt(t.heightPx) ||
    !finite(t.pixelSizeM) ||
    !((t.pixelSizeM as number) > 0) ||
    !Array.isArray(t.upperLeftWorld) ||
    t.upperLeftWorld.length !== 2 ||
    !t.upperLeftWorld.every(finite) ||
    t.pixelAnchor !== 'edge'
  )
    throw new ManifestError('topview ist ungültig.');
  const ul = t.upperLeftWorld as unknown[];
  return {
    url: t.url,
    widthPx: t.widthPx as number,
    heightPx: t.heightPx as number,
    pixelSizeM: t.pixelSizeM as number,
    upperLeftWorld: [ul[0] as number, ul[1] as number],
    pixelAnchor: 'edge',
    zMaxWorld: (t.zMaxWorld as number | null | undefined) ?? null,
  };
}

/** crs-Block von Schema 1 (`m.crs`) und Schema 2 (`m.frame.crs`) — dieselben drei Felder. */
function parseCrs(raw: unknown, where: string): Frame['crs'] {
  const c = raw as Record<string, unknown> | null;
  if (!c || typeof c !== 'object' || !('wkt' in c)) throw new ManifestError(`${where} fehlt.`);
  return {
    wkt: c.wkt as string | null,
    verticalDatum: c.verticalDatum as string | null,
    declaredBy: c.declaredBy as string,
  };
}

/**
 * Kernfelder einer Punktwolken-Ebene, gemeinsam für Schema-1-`cloud` und Schema-2-Ebenen
 * `kind: 'pointcloud'`. `where` ist der Meldungspräfix: `cloud` bei Schema 1 (hält die
 * bestehenden Meldungen wörtlich gleich), `Ebene ${id}` bei Schema 2.
 */
function parseCloudFields(c: unknown, where: string): Omit<PointcloudLayer, 'id' | 'kind' | 'title' | 'provenance'> {
  const cc = c as Partial<PointcloudLayer> | null;
  if (!cc || typeof cc.url !== 'string') throw new ManifestError(`${where}.url fehlt.`);
  if (typeof cc.pointCount !== 'number' || !Number.isFinite(cc.pointCount) || cc.pointCount < 0)
    throw new ManifestError(`${where}.pointCount fehlt oder ist keine Zahl.`);
  if (!Array.isArray(cc.attributes) || !cc.attributes.every((a) => typeof a === 'string'))
    throw new ManifestError(`${where}.attributes fehlt oder ist keine Liste von Namen.`);
  if (!Array.isArray(cc.boundsWorld) || !isVec3(cc.boundsWorld[0]) || !isVec3(cc.boundsWorld[1]))
    throw new ManifestError(`${where}.boundsWorld ist ungültig.`);
  if (cc.offset !== undefined && !isVec3(cc.offset))
    throw new ManifestError(`${where}.offset ist gesetzt, hat aber nicht drei Zahlen.`);
  if (cc.pointSourceIsScan !== undefined && typeof cc.pointSourceIsScan !== 'boolean')
    throw new ManifestError(`${where}.pointSourceIsScan ist gesetzt, aber kein Wahrheitswert.`);
  const klassenOk = (x: unknown): x is Record<string, number> =>
    typeof x === 'object' && x !== null && !Array.isArray(x) &&
    Object.entries(x).every(([k, n]) => /^\d{1,3}$/.test(k) && Number(k) <= 255 && Number.isInteger(n) && (n as number) >= 0);
  if (cc.classes !== undefined && !klassenOk(cc.classes))
    throw new ManifestError(`${where}.classes ist gesetzt, aber kein Histogramm Klasse → Punktzahl.`);
  const fields: Omit<PointcloudLayer, 'id' | 'kind' | 'title' | 'provenance'> = {
    url: cc.url,
    pointCount: cc.pointCount,
    boundsWorld: cc.boundsWorld as [Vec3, Vec3],
    attributes: cc.attributes as string[],
    scale: cc.scale as number,
  };
  if (cc.offset !== undefined) fields.offset = cc.offset as Vec3;
  if (cc.pointSourceIsScan !== undefined) fields.pointSourceIsScan = cc.pointSourceIsScan;
  if (cc.classes !== undefined) fields.classes = cc.classes;
  return fields;
}

/** Provenienz einer Punktwolken-Ebene (Schema 2 Pflicht). */
function parseProvenance(raw: unknown, where: string): Provenance {
  const p = raw as Record<string, unknown> | null;
  if (!p || typeof p !== 'object') throw new ManifestError(`${where}: provenance fehlt.`);
  if (p.kind !== 'scan' && p.kind !== 'photogrammetry' && p.kind !== 'other')
    throw new ManifestError(`${where}: provenance.kind "${String(p.kind)}" wird nicht unterstützt.`);
  if (typeof p.interpolated !== 'boolean') throw new ManifestError(`${where}: provenance.interpolated ist kein Wahrheitswert.`);
  if (typeof p.scaleVerified !== 'boolean') throw new ManifestError(`${where}: provenance.scaleVerified ist kein Wahrheitswert.`);
  if (typeof p.scaleSource !== 'string') throw new ManifestError(`${where}: provenance.scaleSource fehlt oder ist kein Text.`);
  const accuracy = leseGenauigkeit(p.accuracy);
  if (typeof accuracy === 'string') throw new ManifestError(`${where}: provenance.${accuracy}.`);
  return { kind: p.kind, interpolated: p.interpolated, scaleVerified: p.scaleVerified, scaleSource: p.scaleSource,
    ...(accuracy ? { accuracy } : {}) };
}

interface ProjectBase {
  schemaVersion: 1 | 2;
  projectId: string;
  revision: number;
  title: string;
  frame: Frame;
  layers: Layer[];
  initialView: Project['initialView'];
  initialViewDeclared: boolean;
  conversion?: Project['conversion'];
  topview?: Topview;
}

/** Baut das fertige Project: cloud/stations sind dieselben Objekte wie in layers. */
function finalize(base: ProjectBase): Project {
  if (base.initialView.layers?.some((id) => !base.layers.some((l) => l.id === id && !('unsupported' in l))))
    throw new ManifestError('initialView.layers verweist auf keine unterstützte Ebene.');
  const cloud = base.layers.find((l): l is PointcloudLayer => l.kind === 'pointcloud') ?? null;
  const stations = base.layers.find((l): l is PanoramasLayer => l.kind === 'panoramas') ?? null;
  const images = base.layers.find((l): l is ImagesLayer => !('unsupported' in l) && l.kind === 'images') ?? null;
  const project: Project = {
    schemaVersion: base.schemaVersion,
    projectId: base.projectId,
    revision: base.revision,
    title: base.title,
    frame: base.frame,
    layers: base.layers,
    originWorld: base.frame.originWorld,
    crs: base.frame.crs,
    cloud,
    meshes: base.layers.filter((l): l is MeshLayer => !('unsupported' in l) && l.kind === 'mesh'),
    stations,
    images,
    splats: base.layers.filter((l): l is SplatsLayer => !('unsupported' in l) && l.kind === 'splats'),
    initialView: base.initialView,
    initialViewDeclared: base.initialViewDeclared,
  };
  if (base.conversion !== undefined) project.conversion = base.conversion;
  if (base.topview !== undefined) project.topview = base.topview;
  return project;
}

/** Schema 1: eine Punktwolke `cloud`, optionale Standpunkte `stations`. */
function parseV1(m: Record<string, unknown>): Project {
  const projectId = parseProjectId(m);
  const revision = parseRevision(m);
  if (!isVec3(m.originWorld)) throw new ManifestError('originWorld fehlt oder hat nicht drei Zahlen.');
  const cloudFields = parseCloudFields(m.cloud, 'cloud');
  const crs = parseCrs(m.crs, 'crs');
  const initialView = parseInitialView(m);
  const stationsRaw = m.stations as { url?: unknown; count?: unknown } | null;
  if (!stationsRaw || typeof stationsRaw.url !== 'string' || typeof stationsRaw.count !== 'number')
    throw new ManifestError('stations ist ungültig.');
  const conversion = parseConversion(m.conversion);
  const topview = parseTopview(m.topview);

  const cloud: PointcloudLayer = {
    id: 'punkte',
    kind: 'pointcloud',
    title: 'Punktwolke',
    ...cloudFields,
    provenance: { kind: 'scan', interpolated: false, scaleVerified: true, scaleSource: 'scanner' },
  };
  const layers: Layer[] = [cloud];
  if ((stationsRaw.count as number) > 0) {
    layers.push({
      id: 'standpunkte',
      kind: 'panoramas',
      title: 'Standpunkte',
      url: stationsRaw.url as string,
      count: stationsRaw.count as number,
      pointcloudLayer: 'punkte',
    });
  }

  const frame: Frame = { crs, unit: 'm', originWorld: m.originWorld as Vec3 };

  return finalize({
    schemaVersion: 1,
    projectId,
    revision,
    title: projectId,
    frame,
    layers,
    initialView,
    initialViewDeclared: m.initialView !== undefined,
    conversion,
    topview,
  });
}

/** Schema 2: beliebige Ebenen, mindestens eine; unbekannte Arten → UnsupportedLayer. */
function parseV2(m: Record<string, unknown>): Project {
  const projectId = parseProjectId(m);
  const revision = parseRevision(m);
  const initialView = parseInitialView(m);
  const conversion = parseConversion(m.conversion);
  const topview = parseTopview(m.topview);

  const frameRaw = m.frame as Record<string, unknown> | null;
  if (!frameRaw || typeof frameRaw !== 'object') throw new ManifestError('frame fehlt.');
  const crs = parseCrs(frameRaw.crs, 'frame.crs');
  if (crs.wkt !== null && typeof crs.wkt !== 'string') throw new ManifestError('frame.crs.wkt ist weder Text noch null.');
  if (frameRaw.unit !== 'm')
    throw new ManifestError(`frame.unit "${String(frameRaw.unit)}" wird nicht unterstützt (erwartet m).`);
  if (!isVec3(frameRaw.originWorld)) throw new ManifestError('frame.originWorld fehlt oder hat nicht drei Zahlen.');
  const undulation = parseUndulation(frameRaw.undulation);
  const frame: Frame = { crs, unit: 'm', originWorld: frameRaw.originWorld, ...(undulation ? { undulation } : {}) };

  const layersRaw = m.layers;
  if (!Array.isArray(layersRaw) || layersRaw.length === 0) throw new ManifestError('layers fehlt oder ist leer.');

  const cloudIds = new Set<string>();
  for (const raw of layersRaw) {
    const l = raw as Record<string, unknown> | null;
    if (l && typeof l === 'object' && l.kind === 'pointcloud' && typeof l.id === 'string') cloudIds.add(l.id);
  }

  const seen = new Set<string>();
  const layers: Layer[] = layersRaw.map((raw): Layer => {
    const layer = parseLayerV2(raw);
    const sv = (raw as Record<string, unknown>).startVisible;
    if (sv === undefined) return layer;
    if (typeof sv !== 'boolean') throw new ManifestError(`Ebene ${layer.id}: startVisible ist kein Wahrheitswert.`);
    return { ...layer, startVisible: sv };
  });
  function parseLayerV2(raw: unknown): Layer {
    const l = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
    const id = l.id;
    if (typeof id !== 'string' || !LAYER_ID_RE.test(id))
      throw new ManifestError(`Ebenen-ID ${JSON.stringify(id)} ist ungültig.`);
    if (seen.has(id)) throw new ManifestError(`Ebenen-ID ${id} ist doppelt.`);
    seen.add(id);
    const title = typeof l.title === 'string' ? l.title : id;
    if (l.kind === 'pointcloud') {
      const fields = parseCloudFields(l, `Ebene ${id}`);
      const provenance = parseProvenance(l.provenance, `Ebene ${id}`);
      return { id, kind: 'pointcloud', title, ...fields, provenance };
    }
    if (l.kind === 'panoramas') {
      if (typeof l.url !== 'string') throw new ManifestError(`Ebene ${id}: url fehlt.`);
      if (typeof l.count !== 'number' || !Number.isFinite(l.count) || l.count < 0)
        throw new ManifestError(`Ebene ${id}: count fehlt oder ist keine Zahl.`);
      if (typeof l.pointcloudLayer !== 'string' || !cloudIds.has(l.pointcloudLayer))
        throw new ManifestError(
          `Ebene ${id}: pointcloudLayer "${String(l.pointcloudLayer)}" verweist auf keine vorhandene Punktwolken-Ebene.`,
        );
      return { id, kind: 'panoramas', title, url: l.url, count: l.count, pointcloudLayer: l.pointcloudLayer };
    }
    // Wie beim Mesh: erst ein erklärtes Format macht die Ebene prüfpflichtig; der alte Platzhalter ohne format
    // bleibt „nicht unterstützt“, der Rest des Pakets lädt.
    if (l.kind === 'splats' && l.format !== undefined) {
      const ohneWolke = l.frameCheck === KEINE_WOLKE && !cloudIds.size;
      const c = (ohneWolke ? { ok: true, samples: 1000, p50M: 0, p95M: 0 } : l.frameCheck) as
        Exclude<SplatsLayer['frameCheck'], typeof KEINE_WOLKE> | undefined;
      if (!['ply', 'spz', 'rad'].includes(l.format as string) || typeof l.url !== 'string' || !l.url || ![0, 1, 2, 3].includes(l.shDegree as number) ||
          (l.format === 'rad' ? (!Number.isSafeInteger(l.chunkCount) || (l.chunkCount as number) < 1 || l.lodMethod !== 'bhatt-lod') : (l.chunkCount !== undefined && l.chunkCount !== 1)) ||
          !Number.isSafeInteger(l.splatCount) || (l.splatCount as number) <= 0 ||
          !Number.isSafeInteger(l.sizeBytes) || (l.sizeBytes as number) <= 0 ||
          !Array.isArray(l.boundsWorld) || l.boundsWorld.length !== 2 ||
          !isVec3(l.boundsWorld[0]) || !isVec3(l.boundsWorld[1]) ||
          l.boundsWorld[0].some((v, i) => v > (l.boundsWorld as Vec3[])[1]![i]!) ||
          !c || c.ok !== true || !Number.isSafeInteger(c.samples) || c.samples < 1000 ||
          !Number.isFinite(c.p50M) || !Number.isFinite(c.p95M) || c.p50M < 0 || c.p95M < c.p50M)
        throw new ManifestError(`Ebene ${id}: Splat-Vertrag/Rahmenbeleg ungültig.`);
      return { id, kind: 'splats', title, format: l.format as SplatsLayer['format'], url: l.url,
        splatCount: l.splatCount as number, sizeBytes: l.sizeBytes as number,
        boundsWorld: l.boundsWorld as [Vec3, Vec3], shDegree: l.shDegree as SplatsLayer['shDegree'],
        ...(l.chunkCount !== undefined ? { chunkCount: l.chunkCount as number } : {}),
        ...(l.format === 'rad' ? { lodMethod: 'bhatt-lod' as const } : {}),
        frameCheck: ohneWolke ? KEINE_WOLKE : { ok: true, samples: c.samples, p50M: c.p50M, p95M: c.p95M } };
    }
    if (l.kind === 'mesh' && l.format === 'gltf') {
      const where = `Ebene ${id}`;
      if (typeof l.url !== 'string' || !l.url || typeof l.previewUrl !== 'string' || !l.previewUrl)
        throw new ManifestError(`${where}: url/previewUrl fehlen.`);
      if (!Number.isSafeInteger(l.triangleCount) || (l.triangleCount as number) < 0)
        throw new ManifestError(`${where}: triangleCount ungültig.`);
      if (!Array.isArray(l.boundsWorld) || l.boundsWorld.length !== 2 ||
          !isVec3(l.boundsWorld[0]) || !isVec3(l.boundsWorld[1]) ||
          l.boundsWorld[0].some((x, i) => x > (l.boundsWorld as Vec3[])[1]![i]!))
        throw new ManifestError(`${where}: boundsWorld ungültig.`);
      if (typeof l.positionStepM !== 'number' || !Number.isFinite(l.positionStepM) || l.positionStepM <= 0)
        throw new ManifestError(`${where}: positionStepM ungültig.`);
      if (l.previewColor !== undefined && l.previewColor !== 'vertex' && l.previewColor !== 'texture')
        throw new ManifestError(`${where}: previewColor ungültig.`);
      if (l.mobileUrl !== undefined && (typeof l.mobileUrl !== 'string' || !l.mobileUrl ||
          !Number.isSafeInteger(l.mobileSizeBytes) || (l.mobileSizeBytes as number) <= 0 ||
          !Number.isSafeInteger(l.mobileTriangleCount) || (l.mobileTriangleCount as number) < 0))
        throw new ManifestError(`${where}: mobileUrl/mobileSizeBytes/mobileTriangleCount ungültig.`);
      return { id, kind: 'mesh', title, format: 'gltf', url: l.url, previewUrl: l.previewUrl,
        previewColor: l.previewColor ?? 'texture', triangleCount: l.triangleCount as number,
        boundsWorld: l.boundsWorld as [Vec3, Vec3], positionStepM: l.positionStepM,
        provenance: parseProvenance(l.provenance, where),
        ...(typeof l.mobileUrl === 'string' ? { mobile: { url: l.mobileUrl, sizeBytes: l.mobileSizeBytes as number,
          triangleCount: l.mobileTriangleCount as number } } : {}) };
    }
    if (l.kind === 'images') {
      if (typeof l.url !== 'string' || !l.url) throw new ManifestError(`Ebene ${id}: url fehlt.`);
      if (!Number.isSafeInteger(l.count) || (l.count as number) < 0)
        throw new ManifestError(`Ebene ${id}: count fehlt oder ist ungültig.`);
      return { id, kind: 'images', title, url: l.url, count: l.count as number };
    }
    return { id, kind: typeof l.kind === 'string' ? l.kind : String(l.kind), title, unsupported: true };
  }

  if (layers.filter((l) => l.kind === 'splats' && !('unsupported' in l)).length > 1)
    throw new ManifestError('Höchstens eine Splat-Sichtschicht pro Paket.');

  return finalize({
    schemaVersion: 2,
    projectId,
    revision,
    title: typeof m.title === 'string' ? m.title : projectId,
    frame,
    layers,
    initialView,
    initialViewDeclared: m.initialView !== undefined,
    conversion,
    topview,
  });
}

export function parseManifest(json: unknown): Project {
  const m = json as { schemaVersion?: unknown } | null;
  if (!m || typeof m !== 'object') throw new ManifestError('project.json ist kein Objekt.');
  if (m.schemaVersion === 1) return parseV1(m as Record<string, unknown>);
  if (m.schemaVersion === 2) return parseV2(m as Record<string, unknown>);
  throw new ManifestError(`Schema-Version ${String(m.schemaVersion)} wird nicht unterstützt (erwartet 1 oder 2).`);
}

export function resolveUrl(pkgBase: string, rel: string): string {
  const base = pkgBase.endsWith('/') ? pkgBase : `${pkgBase}/`;
  return new URL(rel, base).toString();
}

/**
 * Lädt und prüft `<paket>/project.json` mit Meldungen, die ein Nutzer versteht (I3): Server nicht
 * erreichbar, kein Paket unter der Adresse (404 oder HTML statt JSON — z. B. ein falscher `?p=`,
 * auf den der Webserver mit der Viewer-Seite antwortet) oder ein ungültiges Manifest.
 */
export async function loadManifest(pkgBase: string, fetchFn: typeof fetch = fetch): Promise<Project> {
  const url = resolveUrl(pkgBase, 'project.json');
  const noPackage = (why: string): ManifestError =>
    new ManifestError(`Kein siteBits-Paket unter ${pkgBase} (${why}). Bitte den Link (?p=…) prüfen.`);
  let res: Response;
  try {
    res = await fetchFn(url);
  } catch {
    throw new ManifestError(`Server nicht erreichbar (${url}). Netzwerkverbindung prüfen und neu laden.`);
  }
  if (!res.ok) {
    void res.body?.cancel().catch(() => undefined);
    throw noPackage(res.status === 404 ? 'project.json nicht gefunden' : `project.json: Status ${res.status}`);
  }
  if (/text\/html/i.test(res.headers.get('content-type') ?? '')) {
    void res.body?.cancel().catch(() => undefined);
    throw noPackage('Server liefert eine HTML-Seite statt project.json');
  }
  let json: unknown;
  try {
    json = await res.json();
  } catch {
    throw noPackage('project.json ist kein gültiges JSON');
  }
  try {
    return parseManifest(json);
  } catch (e) {
    if (e instanceof ManifestError) throw new ManifestError(`project.json ungültig: ${e.message}`);
    throw e;
  }
}
