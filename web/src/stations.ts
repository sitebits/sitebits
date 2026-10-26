// Standpunkte: stations.json lesen, Schema 1 (ohne schemaVersion) und Schema 2
// (mit schemaVersion). Reine Logik ohne Giro3D/three (Vitest). Jede Station wird einzeln bewertet:
// ein ungültiges Panorama macht nur diese Station „ohne Panorama“ (panoramaIssue), der Rest bleibt
// nutzbar. Fehler, die das Ganze unlesbar machen (Version, IDs, Posen), werfen StationsError.
//
// Bildkonvention: Posen im Welt-KS, Lochkamera blickt entlang −Z, +Y oben, Pixelzeile 0
// oben, principalPoint in Pixeln; pixelWidth/pixelHeight = Pixelabstand in m, imageWidth/imageHeight
// = Pixelzahl. Bild-GUIDs sind kein Schlüssel (Referenzpaket Kirche: Platzhalter) — Seite → Bild über faceImages.
import type { Project, Vec3 } from './manifest';
import { resolveUrl } from './manifest';
import { validPointSourceId } from './scanFilter';

export type FaceName = 'px' | 'nx' | 'py' | 'ny' | 'pz' | 'nz';
export const FACES: readonly FaceName[] = ['px', 'nx', 'py', 'ny', 'pz', 'nz'];
/** Quaternion (w, x, y, z) wie im Vertrag. */
export type Quat = [number, number, number, number];

export interface Pose {
  translation: Vec3;
  rotationQuat: Quat;
}

export interface PinholeImage {
  face: FaceName;
  pose: Pose;
  focalLength: number;
  pixelWidth: number;
  pixelHeight: number;
  principalPoint: [number, number];
  imageWidth: number;
  imageHeight: number;
}

export interface CubeFace {
  name: FaceName;
  /** Ordner relativ zum Paket, z. B. panos/S001/px. */
  dir: string;
  image: PinholeImage;
}

export interface CubePanorama {
  kind: 'cube';
  faceSize: number;
  /** Aufsteigend, z. B. [512, 1024, 2048, 4096]. */
  levels: number[];
  levelFile: string;
  /** In FACES-Reihenfolge. */
  faces: CubeFace[];
}

export interface Station {
  id: string;
  name: string;
  pose: Pose;
  groundZ: number | null;
  panorama: CubePanorama | null;
  /**
   * PointSourceId der Punkte dieses Scans (Scanindex + 1); null = unbekannt (fehlt, 0,
   * ungültig — z. B. Carry-over-Stationen oder Altpakete).
   */
  pointSourceId: number | null;
  /** Warum panorama null ist: none = hat eins; absent = Quelle ohne Würfel; legacy = Schema 1; invalid = kaputt;
   *  gesperrt = für diese Rolle nicht freigegeben (Server-Tor F20, `ohnePanoramen`). */
  panoramaIssue: 'none' | 'absent' | 'legacy' | 'invalid' | 'gesperrt';
}

export interface StationSet {
  schemaVersion: 1 | 2;
  stations: Station[];
}

export class StationsError extends Error {}

/** IDs landen im URL-Hash (st=…) und in Dateipfaden — nur harmlose Zeichen. */
export const STATION_ID_RE = /^[A-Za-z0-9_-]{1,32}$/;

const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const isVec3 = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3 && v.every(finite);
const isQuat = (v: unknown): v is Quat =>
  Array.isArray(v) && v.length === 4 && v.every(finite) && Math.hypot(...(v as number[])) > 1e-9;
export const isPose = (p: unknown): p is Pose =>
  !!p && typeof p === 'object' && isVec3((p as Pose).translation) && isQuat((p as Pose).rotationQuat);
const isFace = (f: unknown): f is FaceName => typeof f === 'string' && (FACES as readonly string[]).includes(f);
const positive = (x: unknown): x is number => finite(x) && x > 0;

function parsePinhole(raw: unknown): PinholeImage | null {
  const im = raw as Record<string, unknown> | null;
  if (!im || typeof im !== 'object' || im.kind !== 'pinhole' || !isFace(im.face) || !isPose(im.pose)) return null;
  const pp = im.principalPoint;
  if (!Array.isArray(pp) || pp.length !== 2 || !pp.every(finite)) return null;
  if (!positive(im.focalLength) || !positive(im.pixelWidth) || !positive(im.pixelHeight)) return null;
  if (!Number.isInteger(im.imageWidth) || !positive(im.imageWidth)) return null;
  if (!Number.isInteger(im.imageHeight) || !positive(im.imageHeight)) return null;
  return {
    face: im.face,
    pose: im.pose,
    focalLength: im.focalLength,
    pixelWidth: im.pixelWidth,
    pixelHeight: im.pixelHeight,
    principalPoint: [pp[0] as number, pp[1] as number],
    imageWidth: im.imageWidth,
    imageHeight: im.imageHeight,
  };
}

function parsePanorama(raw: unknown, images: unknown): CubePanorama | null {
  const p = raw as Record<string, unknown>;
  if (p.kind !== 'cube' || !positive(p.faceSize)) return null;
  const levels = p.levels;
  if (!Array.isArray(levels) || levels.length === 0 || !levels.every((l) => Number.isInteger(l) && l > 0)) return null;
  if (!levels.every((l, i) => i === 0 || l > levels[i - 1])) return null;
  if (typeof p.levelFile !== 'string' || !p.levelFile.includes('{level}')) return null;
  const dirs = p.faces as Record<string, unknown> | undefined;
  const idx = p.faceImages as Record<string, unknown> | undefined;
  if (!dirs || !idx || !Array.isArray(images)) return null;
  const faces: CubeFace[] = [];
  for (const name of FACES) {
    const dir = dirs[name];
    const i = idx[name];
    if (typeof dir !== 'string' || dir === '' || !Number.isInteger(i) || (i as number) < 0 || (i as number) >= images.length)
      return null;
    const image = parsePinhole(images[i as number]);
    if (!image || image.face !== name) return null;
    faces.push({ name, dir, image });
  }
  return { kind: 'cube', faceSize: p.faceSize, levels: levels as number[], levelFile: p.levelFile, faces };
}

export function parseStations(json: unknown): StationSet {
  const d = json as Record<string, unknown> | null;
  if (!d || typeof d !== 'object') throw new StationsError('stations.json ist kein Objekt.');
  // Fehlt schemaVersion, gilt Schema 1.
  const version: 1 | 2 | null =
    d.schemaVersion === undefined || d.schemaVersion === 1 ? 1 : d.schemaVersion === 2 ? 2 : null;
  if (version === null)
    throw new StationsError(
      `stations.json: Schema-Version ${String(d.schemaVersion)} wird nicht unterstützt (erwartet 1 oder 2).`,
    );
  if (!Array.isArray(d.stations)) throw new StationsError('stations.json: Liste "stations" fehlt.');
  const seen = new Set<string>();
  const stations = d.stations.map((raw: unknown): Station => {
    const s = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
    const id = s.id;
    if (typeof id !== 'string' || !STATION_ID_RE.test(id))
      throw new StationsError(`stations.json: ungültige Standpunkt-ID ${JSON.stringify(id)}.`);
    if (seen.has(id)) throw new StationsError(`stations.json: Standpunkt-ID ${id} doppelt.`);
    seen.add(id);
    const pose = s.pose;
    if (!isPose(pose)) throw new StationsError(`stations.json: Standpunkt ${id} ohne gültige Pose.`);
    let panorama: CubePanorama | null = null;
    let panoramaIssue: Station['panoramaIssue'] = 'absent';
    if (s.panorama != null) {
      if (version === 1) panoramaIssue = 'legacy';
      else {
        panorama = parsePanorama(s.panorama, s.images);
        panoramaIssue = panorama ? 'none' : 'invalid';
      }
    }
    const groundZ = s.groundZ;
    return {
      id,
      name: typeof s.name === 'string' ? s.name : '',
      pose,
      groundZ: finite(groundZ) ? groundZ : null,
      pointSourceId: validPointSourceId(s.pointSourceId) ? s.pointSourceId : null,
      panorama,
      panoramaIssue,
    };
  });
  return { schemaVersion: version, stations };
}

export function levelUrl(pkgBase: string, p: CubePanorama, face: CubeFace, level: number): string {
  return resolveUrl(pkgBase, `${face.dir}/${p.levelFile.replace('{level}', String(level))}`);
}

/** Lädt stations.json. Fehler → StationsError (main.ts zeigt sie an, die Punktwolke bleibt nutzbar). */
export async function loadStations(
  pkgBase: string,
  m: Pick<Project, 'stations'>,
  fetchFn: typeof fetch = fetch,
): Promise<StationSet> {
  if (!m.stations || m.stations.count === 0) return { schemaVersion: 2, stations: [] };
  const url = resolveUrl(pkgBase, m.stations.url);
  let json: unknown;
  try {
    const res = await fetchFn(url);
    if (!res.ok) {
      void res.body?.cancel().catch(() => undefined);
      throw new Error(`Status ${res.status}`);
    }
    json = await res.json();
  } catch (e) {
    throw new StationsError(`Standpunkte nicht lesbar (${url}: ${e instanceof Error ? e.message : String(e)}).`);
  }
  return parseStations(json);
}

/** Panoramen für diese Rolle nicht freigegeben (Server-Tor): Standpunkte bleiben, Bilder nicht. */
export function ohnePanoramen(set: StationSet): StationSet {
  return {
    ...set,
    stations: set.stations.map((s) => (s.panorama ? { ...s, panorama: null, panoramaIssue: 'gesperrt' as const } : s)),
  };
}
