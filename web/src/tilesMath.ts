// Reiner Rechenkern der gekachelten Mesh-Ebene (3D Tiles 1.1, REPLACE): tileset.json prüfen und in einen
// Kachelbaum mit Welt-Hüllquadern überführen, Blätter entlang eines Strahls finden. Ohne Szene, ohne Netz.
//
// Vertrag (Ausgabe des Kachlers, docs/mesh-kacheln.md): Kacheln in Projektkoordinaten (Z oben), Kachel-
// Transformationen nur Verschiebungen (Wurzel ohne oder mit Verschiebung, Kinder relativ zum Elternrahmen),
// `refine: REPLACE`, geometricError streng fallend bis 0 im Blatt, Hüllkörper `box`. Wer davon abweicht, wird
// abgelehnt statt geraten: ein falsch gelegtes Modell wäre schlimmer als ein fehlendes.
import { Matrix4, Vector3 } from 'three';
import type { Ray } from 'three';
import type { Vec3 } from './manifest';

export class TilesetFehler extends Error {}

/** Obergrenze der Kachelzahl: 9,6 Mio. Dreiecke in Blättern zu 6–40 k ergeben einige Hundert Kacheln. */
export const TILES_MAX_KACHELN = 100_000;
/** Tiefe des Baums: je Ebene halbiert der Kachler; 64 Ebenen wären 2^64 Blätter. */
export const TILES_MAX_TIEFE = 64;
/** Toleranz der Lageprüfung Wurzel-Hüllquader gegen `boundsWorld` [m]: Rundung beim Schreiben, Hüllquader-Aufschlag. */
export const LAGE_TOLERANZ_M = 1;

export interface TilesKnoten {
  /** Laufende Nummer in Tiefensuchreihenfolge (stabil, für Tests und Diagnose). */
  id: number;
  tiefe: number;
  /** Relativer Pfad des GLB, gegen die tileset.json aufzulösen; null = Kachel ohne Inhalt. */
  uri: string | null;
  geometricError: number;
  /** Kachel → Welt (Verkettung der Kachel-Transformationen, ohne Y→Z-Drehung des GLB). */
  welt: Matrix4;
  /** Einheitswürfel [-1,1]³ → Welt; das Inverse dient dem Strahltest. */
  boxWelt: Matrix4;
  boxWeltInvers: Matrix4;
  kinder: TilesKnoten[];
}

export interface Tileset {
  wurzel: TilesKnoten;
  kachelzahl: number;
  blaetter: TilesKnoten[];
  /** Achsenparalleler Hüllquader der Wurzel in Welt. */
  weltBox: [Vec3, Vec3];
}

interface TileJson {
  boundingVolume?: { box?: unknown };
  geometricError?: unknown;
  refine?: unknown;
  transform?: unknown;
  content?: { uri?: unknown };
  children?: unknown;
}

/** Y oben (glTF) → Z oben (3D Tiles): Drehung um +90° um X, wie sie der Renderer auf den Kachelinhalt anwendet. */
export const GLTF_ZU_Z_OBEN: Matrix4 = new Matrix4().makeRotationX(Math.PI / 2);

/** Relativer Pfad ohne Schema, führenden Schrägstrich, Rückwärtsschrägstrich und `.`/`..`-Segmente. */
export function relativerPfad(pfad: unknown): pfad is string {
  return typeof pfad === 'string' && pfad !== '' && !/^[a-z][a-z0-9+.-]*:/i.test(pfad) && !pfad.startsWith('/') &&
    !pfad.includes('\\') && !pfad.includes('%') && !pfad.includes('?') && !pfad.includes('#') &&
    pfad.split('/').every((s) => s !== '' && s !== '.' && s !== '..');
}

const endlich = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);

/** Nur Verschiebung: Spalten 0–2 Einheitsvektoren, Zeile 4 = (0,0,0,1). */
function verschiebung(raw: unknown, wo: string): Vector3 {
  if (raw === undefined) return new Vector3();
  if (!Array.isArray(raw) || raw.length !== 16 || !raw.every(endlich)) throw new TilesetFehler(`${wo}: transform ungültig.`);
  const m = raw as number[];
  const einheit = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0];
  const nahe = (a: number, b: number): boolean => Math.abs(a - b) < 1e-9;
  if (!einheit.every((v, i) => nahe(m[i]!, v)) || !nahe(m[15]!, 1))
    throw new TilesetFehler(`${wo}: transform ist keine reine Verschiebung (Drehung/Maßstab nicht unterstützt).`);
  return new Vector3(m[12], m[13], m[14]);
}

function box(raw: unknown, wo: string): { mitte: Vector3; achsen: [Vector3, Vector3, Vector3] } {
  if (!Array.isArray(raw) || raw.length !== 12 || !raw.every(endlich))
    throw new TilesetFehler(`${wo}: boundingVolume.box fehlt oder ist ungültig.`);
  const b = raw as number[];
  return { mitte: new Vector3(b[0], b[1], b[2]),
    achsen: [new Vector3(b[3], b[4], b[5]), new Vector3(b[6], b[7], b[8]), new Vector3(b[9], b[10], b[11])] };
}

/**
 * tileset.json prüfen und Baum bauen. `lage` verschiebt das ganze Tileset (Matrix der Kachelgruppe in der Szene;
 * Einheitsmatrix, solange die Kacheln im Paketrahmen liegen). `tileset.extras` muss CRS und Höhensystem des
 * Pakets nennen — sonst ist nicht belegt, dass die Koordinaten zum Paketrahmen gehören.
 */
export function pruefeTileset(json: unknown, erwartet: { crs: string; heightSystem: string }, lage: Matrix4 = new Matrix4()): Tileset {
  const t = json as { asset?: { version?: unknown }; extras?: Record<string, unknown>; root?: TileJson } | null;
  if (!t || typeof t !== 'object' || !t.root || typeof t.root !== 'object') throw new TilesetFehler('tileset.json: root fehlt.');
  if (typeof t.asset?.version !== 'string' || !/^1\.[01]$/.test(t.asset.version))
    throw new TilesetFehler('tileset.json: asset.version muss 1.0 oder 1.1 sein.');
  const ex = t.extras;
  if (!ex || ex.coordinateReferenceSystem !== erwartet.crs)
    throw new TilesetFehler(`tileset.json: extras.coordinateReferenceSystem ${JSON.stringify(ex?.coordinateReferenceSystem)} passt nicht zu ${erwartet.crs}.`);
  if (ex.heightSystem !== erwartet.heightSystem)
    throw new TilesetFehler(`tileset.json: extras.heightSystem ${JSON.stringify(ex.heightSystem)} passt nicht zu ${erwartet.heightSystem}.`);
  if (ex.axis !== 'Z' || ex.unit !== 'm') throw new TilesetFehler('tileset.json: extras.axis/unit müssen Z und m sein.');

  let zaehler = 0; const blaetter: TilesKnoten[] = [];
  const bau = (j: TileJson, eltern: Matrix4, tiefe: number): TilesKnoten => {
    const id = zaehler++;
    const wo = `Kachel ${id}`;
    if (id >= TILES_MAX_KACHELN) throw new TilesetFehler(`tileset.json: mehr als ${TILES_MAX_KACHELN} Kacheln.`);
    if (tiefe > TILES_MAX_TIEFE) throw new TilesetFehler(`tileset.json: Baum tiefer als ${TILES_MAX_TIEFE} Ebenen.`);
    if (!j || typeof j !== 'object') throw new TilesetFehler(`${wo}: kein Objekt.`);
    if (j.refine !== undefined && j.refine !== 'REPLACE') throw new TilesetFehler(`${wo}: nur refine REPLACE wird unterstützt.`);
    if (tiefe === 0 && j.refine !== 'REPLACE') throw new TilesetFehler(`${wo}: Wurzel muss refine REPLACE erklären.`);
    if (!endlich(j.geometricError) || j.geometricError < 0) throw new TilesetFehler(`${wo}: geometricError ungültig.`);
    const welt = eltern.clone().multiply(new Matrix4().makeTranslation(verschiebung(j.transform, wo)));
    const b = box(j.boundingVolume?.box, wo);
    const boxWelt = welt.clone().multiply(new Matrix4().makeBasis(...b.achsen).setPosition(b.mitte));
    if (Math.abs(boxWelt.determinant()) < 1e-18) throw new TilesetFehler(`${wo}: Hüllquader ohne Ausdehnung.`);
    let uri: string | null = null;
    if (j.content !== undefined) {
      const u = j.content?.uri;
      if (!relativerPfad(u)) throw new TilesetFehler(`${wo}: content.uri muss ein relativer Pfad im Paket sein.`);
      if (!/\.glb$/i.test(u)) throw new TilesetFehler(`${wo}: content.uri muss auf .glb zeigen (externe Tilesets nicht unterstützt).`);
      uri = u;
    }
    const kinder = j.children === undefined ? [] : j.children;
    if (!Array.isArray(kinder)) throw new TilesetFehler(`${wo}: children ist keine Liste.`);
    const knoten: TilesKnoten = { id, tiefe, uri, geometricError: j.geometricError, welt, boxWelt, boxWeltInvers: boxWelt.clone().invert(), kinder: [] };
    for (const k of kinder as TileJson[]) {
      const kind = bau(k, welt, tiefe + 1);
      if (!(kind.geometricError < knoten.geometricError))
        throw new TilesetFehler(`${wo}: geometricError muss zu den Kindern hin streng fallen.`);
      knoten.kinder.push(kind);
    }
    if (!knoten.kinder.length) {
      if (j.geometricError !== 0) throw new TilesetFehler(`${wo}: Blatt mit geometricError ${j.geometricError} — feinste Stufe muss 0 sein.`);
      blaetter.push(knoten);
    }
    return knoten;
  };
  const wurzel = bau(t.root, lage, 0);
  if (!blaetter.some((b) => b.uri !== null)) throw new TilesetFehler('tileset.json: kein Blatt mit Inhalt.');
  return { wurzel, kachelzahl: zaehler, blaetter, weltBox: boxAabb(wurzel.boxWelt) };
}

/** Achsenparalleler Hüllquader der acht Ecken eines Einheitswürfel-Bildes. */
export function boxAabb(boxWelt: Matrix4): [Vec3, Vec3] {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1]) {
    const p = new Vector3(sx, sy, sz).applyMatrix4(boxWelt);
    [p.x, p.y, p.z].forEach((v, i) => { lo[i] = Math.min(lo[i]!, v); hi[i] = Math.max(hi[i]!, v); });
  }
  return [lo as Vec3, hi as Vec3];
}

/** Wurzel-Hüllquader gegen die im Manifest erklärten Grenzen: weicht eine Fläche um mehr als `tol` ab, liegt das Modell woanders. */
export function pruefeLage(weltBox: readonly [Vec3, Vec3], bounds: readonly [Vec3, Vec3], tol = LAGE_TOLERANZ_M): void {
  for (let i = 0; i < 3; i++) {
    for (const s of [0, 1] as const) {
      if (Math.abs(weltBox[s][i]! - bounds[s][i]!) > tol)
        throw new TilesetFehler(`Lage: Hüllquader der Kacheln weicht von boundsWorld um ${Math.abs(weltBox[s][i]! - bounds[s][i]!).toFixed(2)} m ab (Achse ${'xyz'[i]}).`);
    }
  }
}

/** EPSG-Code des Paketrahmens aus dem WKT (letzte AUTHORITY, Kurzform ohne Hochkomma-Varianten); null = nicht erkennbar. */
export function epsgAusWkt(wkt: string | null): number | null {
  if (!wkt) return null;
  const treffer = [...wkt.matchAll(/(?:AUTHORITY\["EPSG","(\d+)"\]|ID\["EPSG",(\d+)\])/g)];
  const letzter = treffer.at(-1);
  const n = letzter ? Number(letzter[1] ?? letzter[2]) : NaN;
  return Number.isFinite(n) ? n : null;
}

/** Das CRS der Kacheln darf dem erkennbaren CRS des Paketrahmens nicht widersprechen. */
export function pruefeCrs(layerCrs: string, rahmenWkt: string | null): void {
  const code = epsgAusWkt(rahmenWkt);
  const m = /^EPSG:(\d+)$/.exec(layerCrs);
  if (code !== null && (!m || Number(m[1]) !== code))
    throw new TilesetFehler(`CRS der Kacheln (${layerCrs}) passt nicht zum Paketrahmen (EPSG:${code}).`);
}

/** Schnitt Strahl–orientierter Hüllquader: Eintritts- und Austrittsabstand entlang des (normierten) Strahls; null = kein Treffer. */
export function strahlBoxIntervall(ray: Ray, k: Pick<TilesKnoten, 'boxWeltInvers'>): [number, number] | null {
  const o = ray.origin.clone().applyMatrix4(k.boxWeltInvers);
  // Richtung ohne Verschiebung: Differenz zweier Bilder, der Parameter t bleibt der des Weltstrahls.
  const d = ray.origin.clone().addScaledVector(ray.direction, 1).applyMatrix4(k.boxWeltInvers).sub(o);
  let tMin = -Infinity, tMax = Infinity;
  for (const [oi, di] of [[o.x, d.x], [o.y, d.y], [o.z, d.z]] as const) {
    if (Math.abs(di) < 1e-15) { if (oi < -1 || oi > 1) return null; continue; }
    const a = (-1 - oi) / di, b = (1 - oi) / di;
    tMin = Math.max(tMin, Math.min(a, b)); tMax = Math.min(tMax, Math.max(a, b));
    if (tMin > tMax) return null;
  }
  return tMax < 0 ? null : [Math.max(0, tMin), tMax];
}

export interface BlattKandidat { knoten: TilesKnoten; ein: number; aus: number }

/**
 * Blätter mit Inhalt, deren Hüllquader der Strahl trifft, nach Eintrittsabstand aufsteigend. Der Abstieg
 * schneidet jeden Teilbaum ab, dessen Hüllquader der Strahl verfehlt — unabhängig davon, was der Renderer
 * gerade geladen oder sichtbar hat. `maxT` kappt Treffer jenseits einer Grenze (Tiefenfenster).
 */
export function blattKandidaten(ts: Pick<Tileset, 'wurzel'>, ray: Ray, maxT = Infinity): BlattKandidat[] {
  const aus: BlattKandidat[] = [];
  const abstieg = (k: TilesKnoten): void => {
    const iv = strahlBoxIntervall(ray, k);
    if (!iv || iv[0] > maxT) return;
    if (k.kinder.length) { for (const kind of k.kinder) abstieg(kind); return; }
    if (k.uri !== null) aus.push({ knoten: k, ein: iv[0], aus: iv[1] });
  };
  abstieg(ts.wurzel);
  return aus.sort((a, b) => a.ein - b.ein || a.knoten.id - b.knoten.id);
}
