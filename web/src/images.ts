// Orientierte Fotos: images.json lesen (Schema 1). Reine Logik ohne Giro3D/three (Vitest).
//
// Bildkonvention wie stations.ts: Pose im Welt-KS, Lochkamera blickt entlang −Z, +Y oben, Pixelzeile 0
// oben, Hauptpunkt in der Bildmitte (der Import entzerrt auf genau diese Lochkamera). Intrinsik in
// Pixeln der vollen entzerrten Auflösung; Stufen sind lange Kanten (512/2048/4096), Kacheln optional.
// 404 auf images.json ist kein Fehler: Ohne freigegebene Anonymisierung sehen nur Bearbeiter+ die
// Fotos (Server-Tor), der Viewer blendet die Ebene dann aus.
import type { Project } from './manifest';
import { resolveUrl } from './manifest';
import type { Pose } from './stations';
import { isPose } from './stations';

export const IMAGE_ID_RE = /^F[0-9]{4,6}$/;
const REV_RE = /^[0-9a-f]{16}$/;
const MAX_LEVEL = 4096;

export interface ImageIntrinsics {
  focalPx: number;
  width: number;
  height: number;
}

export interface ImageTiles {
  size: number;
  levels: number;
}

export interface OrientedImage {
  id: string;
  label: string;
  group: string;
  pose: Pose;
  intrinsics: ImageIntrinsics;
  /** Lange Kante je Stufe, aufsteigend; die letzte ist die schärfste Einzeldatei. */
  levels: number[];
  tiles: ImageTiles | null;
  /** Inhalts-Revision (16 Hex) — Dateien unter fotos/<id>/<rev>/; fehlt bei Paketen vor M7. */
  rev: string | null;
}

export interface ImageGroup {
  id: string;
  title: string;
  count: number;
}

export interface ImageSet {
  groups: ImageGroup[];
  anonymization: { required: boolean; status: string };
  images: OrientedImage[];
}

export class ImagesError extends Error {}

const positiveInt = (x: unknown): x is number => Number.isSafeInteger(x) && (x as number) > 0;
const positive = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x) && x > 0;

function parseImage(raw: unknown, groups: Set<string>): OrientedImage {
  const b = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const id = b.id;
  if (typeof id !== 'string' || !IMAGE_ID_RE.test(id)) throw new ImagesError(`images.json: ungültige Foto-ID ${JSON.stringify(id)}.`);
  if (!isPose(b.pose)) throw new ImagesError(`images.json: Foto ${id} ohne gültige Pose.`);
  const i = (b.intrinsics ?? {}) as Record<string, unknown>;
  if (!positive(i.focalPx) || !positiveInt(i.width) || !positiveInt(i.height))
    throw new ImagesError(`images.json: Foto ${id} mit ungültiger Intrinsik.`);
  const levels = b.levels;
  if (!Array.isArray(levels) || levels.length === 0 || !levels.every(positiveInt) ||
      levels.some((l, k) => k > 0 && (l as number) <= (levels[k - 1] as number)) || (levels.at(-1) as number) > MAX_LEVEL)
    throw new ImagesError(`images.json: Foto ${id} mit ungültigen Stufen.`);
  let tiles: ImageTiles | null = null;
  if (b.tiles != null) {
    const t = b.tiles as Record<string, unknown>;
    if (!positiveInt(t.size) || !positiveInt(t.levels)) throw new ImagesError(`images.json: Foto ${id} mit ungültigen Kacheln.`);
    tiles = { size: t.size, levels: t.levels };
  }
  if (b.rev != null && (typeof b.rev !== 'string' || !REV_RE.test(b.rev)))
    throw new ImagesError(`images.json: Foto ${id} mit ungültiger Revision.`);
  const group = typeof b.group === 'string' ? b.group : '';
  if (!groups.has(group)) throw new ImagesError(`images.json: Foto ${id} in unbekannter Gruppe ${JSON.stringify(group)}.`);
  return {
    id, label: typeof b.label === 'string' ? b.label : id, group, pose: b.pose,
    intrinsics: { focalPx: i.focalPx, width: i.width, height: i.height }, levels: levels as number[], tiles,
    rev: typeof b.rev === 'string' ? b.rev : null,
  };
}

export function parseImages(json: unknown): ImageSet {
  const d = json as Record<string, unknown> | null;
  if (!d || typeof d !== 'object') throw new ImagesError('images.json ist kein Objekt.');
  if (d.schemaVersion !== 1) throw new ImagesError(`images.json: Schema-Version ${String(d.schemaVersion)} wird nicht unterstützt (erwartet 1).`);
  if (!Array.isArray(d.groups) || !Array.isArray(d.images)) throw new ImagesError('images.json: Listen "groups"/"images" fehlen.');
  const groups: ImageGroup[] = d.groups.map((g: unknown) => {
    const x = (g && typeof g === 'object' ? g : {}) as Record<string, unknown>;
    if (typeof x.id !== 'string') throw new ImagesError('images.json: Gruppe ohne ID.');
    return { id: x.id, title: typeof x.title === 'string' ? x.title : x.id, count: Number.isSafeInteger(x.count) ? (x.count as number) : 0 };
  });
  const ids = new Set(groups.map((g) => g.id));
  const seen = new Set<string>();
  const images = d.images.map((raw: unknown) => {
    const img = parseImage(raw, ids);
    if (seen.has(img.id)) throw new ImagesError(`images.json: Foto-ID ${img.id} doppelt.`);
    seen.add(img.id);
    return img;
  });
  const a = (d.anonymization ?? {}) as Record<string, unknown>;
  return {
    groups,
    anonymization: { required: a.required !== false, status: typeof a.status === 'string' ? a.status : 'unreviewed' },
    images,
  };
}

// Mit Revision liegt jeder Inhaltsstand unter eigener URL: der Browser-Cache (max-age 1 h) zeigt
// nach einem Neu-Import nie das alte Bild unter derselben fortlaufenden ID (Review-Minor M7).
const ordner = (img: OrientedImage): string => (img.rev ? `fotos/${img.id}/${img.rev}` : `fotos/${img.id}`);

export function levelUrl(pkgBase: string, img: OrientedImage, level: number): string {
  return resolveUrl(pkgBase, `${ordner(img)}/${level}.jpg`);
}

export function tileUrl(pkgBase: string, img: OrientedImage, z: number, x: number, y: number): string {
  return resolveUrl(pkgBase, `${ordner(img)}/tiles/${z}/${x}_${y}.jpg`);
}

/** Lädt images.json; 404 → null (Ebene für diese Rolle nicht freigegeben), sonst Fehler → ImagesError. */
export async function loadImages(
  pkgBase: string,
  layer: Pick<NonNullable<Project['images']>, 'url'>,
  fetchFn: typeof fetch = fetch,
): Promise<ImageSet | null> {
  const url = resolveUrl(pkgBase, layer.url);
  let json: unknown;
  try {
    const res = await fetchFn(url);
    if (res.status === 404) {
      void res.body?.cancel().catch(() => undefined);
      return null;
    }
    if (!res.ok) {
      void res.body?.cancel().catch(() => undefined);
      throw new Error(`Status ${res.status}`);
    }
    json = await res.json();
  } catch (e) {
    throw new ImagesError(`Fotos nicht lesbar (${url}: ${e instanceof Error ? e.message : String(e)}).`);
  }
  return parseImages(json);
}
