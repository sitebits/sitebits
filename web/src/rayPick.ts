// Zweistufige Strahlsuche, aus measure.ts herausgelöst
// („pickAlongRay“), damit Messen im Panorama und POIs dieselbe Suche nutzen.
// Im Panorama steht die Kamera im Scannerzentrum; Giro3Ds Pick-Rendering der unter dem Panorama weiter
// gerenderten Punktwolke aus derselben Kamera ist das „unsichtbare Tiefen-/ID-Rendering“.
// API-Fakten und Begründungen (Pick-Radius, Limit, gescherte Pickzone, Budget): Kommentarblock in
// measure.ts — er bleibt dort, weil er auch das Zeichnen der Messungen erklärt.
import { PerspectiveCamera, Raycaster, Vector2, Vector3 } from 'three';
import type { PointCloudAttribute, PointCloudNode } from '@giro3d/giro3d/sources/PointCloudSource.js';
import { pointAllowed } from './clipBoxMath';
import type { ClipBoxState } from './clipBoxMath';
import {
  boundedCoarse,
  collectNodes,
  depthAlong,
  findSurfaceAlongRay,
  mitteTiefen,
  ohneDurchscheinen,
  pixelFootprintM,
  pointsNearRay,
  rayBoxExit,
  refineBox,
  surfaceGapM,
  tiefenFensterGueltig,
} from './pickMath';
import { classifyHit, planCoarse, verdictsWithin } from './scanFilter';
import { SNAP_NEIGHBOUR_MIN_M, SNAP_NEIGHBOUR_PX, SNAP_RADIUS_PX, sphereTouchesBox } from './edgeSnap';
import { snapWithinDeadline } from './snapDeadline';
import { knotenCache } from './knotenCache';
import { memoNodeIndex } from './nodeIndex';
import type { SnapResult } from './edgeSnap';
import type { HitVerdict, ScanFilter, ScanFilterMode } from './scanFilter';
import type { AlongRayResult, Box, FineFailure, PointChunk, Ray, Vec3 as PVec3 } from './pickMath';
import type { Viewer } from './viewer';
import { filterKennung } from './klassen';

/** „Unter dem Cursor“ [px]. */
export const PICK_RADIUS = 4;
/** Umgebung für die vorderste Fläche [px]. */
export const PICK_RADIUS_WIDE = 16;
/** Obergrenze = Pixel der Pickzone (2·16+1)² (keine Kürzung). */
const PICK_LIMIT = (2 * PICK_RADIUS_WIDE + 1) ** 2;
/** Vorschau: Pick-Radius um den Zielpunkt [px]; Obergrenze = alle Pixel des Quadrats (2·R+1)². */
export const VORSCHAU_RADIUS_PX = 8;
const VORSCHAU_LIMIT = (2 * VORSCHAU_RADIUS_PX + 1) ** 2;
/** Vorschau: „Mitte“ = Treffer bis zu diesem Pixelabstand vom Zielpunkt. */
export const VORSCHAU_MITTE_PX = 2;
/** Zeitbudget für das Nachladen der feinsten Stufe ab Klick. */
export const FINE_BUDGET_MS = 500;
/** Panorama: Zeitbudget der feinsten Stufe — Orbit behält FINE_BUDGET_MS. */
export const PANO_FINE_BUDGET_MS = 1500;
/** Obergrenze der über Klicks geteilten dekodierten Knoten (knotenCache.ts); ≈ 50 Knoten zu 100 000 Punkten. */
export const PICK_CACHE_BYTES = 64 * 1024 * 1024;
/**
 * Panorama: jeder grobe Ersatz (nach Zeitbudget, Fehler, leerem Strahl) nur bis zu dieser Fehlerschranke
 * PICK_RADIUS · k · Tiefe [m]. Aus 3–12 m Stationsabstand läge er sonst 13–50 mm daneben
 * (gedrosselter Referenzlauf: Strecke #5 −23,7 mm).
 */
export const PANO_COARSE_MAX_M = 0.01;
/** Suchkegel der feinsten Stufe: PICK_RADIUS in Pixeln in der jeweiligen Tiefe, mindestens 5 mm. */
const FINE_MIN_RADIUS_M = 0.005;
/** Streupunkt-Regel der feinen Stufe (pickMath.SurfaceOptions.minClusterSize). */
const FINE_MIN_CLUSTER = 2;
/**
 * Kantenfang: noch nicht dekodierte Knoten im Fangvolumen werden nachgeladen, aber nur bis
 * so viele ms vor Ablauf des Zeitbudgets — der Rest bleibt der Rechnung (edgeSnap, ≤ 100 ms bei 30 000
 * Punkten Vorrat). Was bis dahin nicht da ist, gilt als „unbekannt“ (Kandidaten daneben zählen nicht).
 */
export const SNAP_RESERVE_MS = 150;

/** PickInfo.snap.result: 'edge'/'vertex' = eingerastet; sonst der Grund, warum nicht (normaler Treffer). */
export type SnapOutcome = 'none' | 'edge' | 'vertex' | Exclude<SnapResult, { kind: 'edge' }>['why'] | 'error';

/** Ergebnis/Diagnose des letzten Picks — für Hinweis und E2E-Beleg (window.__scanview). */
export interface PickInfo {
  source?: 'pointcloud' | 'mesh';
  layerId?: string;
  triangle?: { tile: string; index: number };
  /** 'fine' = nächster Originalpunkt aus voller Auflösung; 'coarse' = Treffer aus der Anzeige. */
  level: 'fine' | 'coarse' | 'none';
  /**
   * Grund für 'coarse'/'none': Zeitbudget, Fehler, keine Originalpunkte am Strahl; 'imprecise' =
   * grober Ersatz über der Fehlerschranke verworfen (PickOptions.coarseMaxM), Ursache in fineReason;
   * 'hidden' = die Punktwolke ist ausgeblendet (Ebenen-Register) — kein Pick-Rendering, keine
   * Strahlsuche.
   */
  reason?: 'timeout' | 'error' | 'empty' | 'imprecise' | 'hidden';
  ms: number;
  /** Kandidaten im 16-px- bzw. 4-px-Kreis (nach Schnittbox-Filter). */
  wideCandidates: number;
  nearCandidates: number;
  /** Tiefe der vordersten Fläche der Umgebung / des vordersten Cursor-Kandidaten [m]. */
  frontDepthM?: number;
  nearDepthM?: number;
  segments?: number;
  fineNodes?: number;
  finePoints?: number;
  /** Bei reason 'imprecise': warum die feinste Stufe keinen Punkt geliefert hat. */
  fineReason?: FineFailure;
  /**
   * Nur im Panorama: 'on' = grobe und feine Kandidaten nur aus dem Scan
   * der Station; 'off-legacy'/'off-unknown' = ungefiltert (Paket ohne Scan-Zuordnung bzw. Station ohne
   * Scan-ID). Im Orbit fehlt das Feld.
   */
  scanFilter?: ScanFilterMode;
  /**
   * Bei scanFilter 'on': grobe Kandidaten (16-px-Kreis, nach Schnittbox) aus anderen Scans bzw. ohne
   * bestätigte Scan-ID — sie sind verworfen; wideCandidates/nearCandidates zählen nur eigene.
   */
  foreignCandidates?: number;
  unverifiedCandidates?: number;
  /**
   * Einrasten: 'edge' = Kantenfang, 'vertex' = eigener Eckpunkt (measure.ts); sonst warum der
   * Kantenfang nicht eingerastet hat. ``original`` = der Punkt ist per Referenz ein Element des Vorrats
   * (Kante) bzw. ein vorhandener Eckpunkt. ``shiftMm`` = Abstand zum Feintreffer. Fehlt ohne Fang-Option.
   */
  snap?: { result: SnapOutcome; sigma?: number; shiftMm?: number; original?: boolean };
  /** Tiefe des gelieferten Punkts entlang des Strahls [m]; gesetzt, wenn point ≠ null. */
  tiefeM?: number;
}

export interface RayPickResult {
  point: PVec3 | null;
  info: PickInfo;
}

/** Abweichungen vom Orbit-Verhalten je Aufruf (Panorama); ohne Angabe gilt Inventar Punkt 4. */
export interface PickOptions {
  /** Zeitbudget der feinsten Stufe ab Klick [ms]; Standard FINE_BUDGET_MS. */
  budgetMs?: number;
  /** Grober Ersatz (nach Zeitbudget, Fehler oder leerem Strahl) nur bis zu dieser Fehlerschranke [m]; Standard: ohne Schranke. */
  coarseMaxM?: number;
  /** Kantenfang nach dem Feintreffer; nur für feine Treffer, nie für den groben Ersatz. */
  snapEdges?: boolean;
  /**
   * Tiefenfenster der feinen Suche [min, max) entlang des Strahls [m] (Wahl „vorn/hinten“). Ersetzt das
   * aus der Umgebung abgeleitete Fenster. Liefert die feine Suche darin nichts, gibt es keinen Punkt
   * (reason 'empty', bei Zeitbudget/Fehler 'timeout'/'error') — kein grober Ersatz, der stammt aus der
   * vordersten Fläche und läge womöglich außerhalb des Fensters.
   */
  tiefenFenster?: { min: number; max: number };
}

/**
 * Vorschau am Zielpunkt: nur Anzeige, nie ein Messwert (Messwerte kommen ausschließlich aus der feinen
 * Suche). Tiefen entlang des Kamerastrahls durch den Zielpunkt, Reihenfolge der Listen unbestimmt.
 * Der Scanfilter des Panoramas wird hier nicht angewandt: das Fadenkreuz gibt es nur im Begehen, nie
 * im Panorama.
 */
export interface Vorschau {
  /** Vorderster sichtbarer Punkt der Mitte (Rückfall: des Fensters); Welt-/Szenenkoordinaten (wie camera.position, RayPickResult.point und der Measure-Store). */
  punktWelt: readonly [number, number, number] | null;
  /** Tiefe dieses Punkts entlang des Strahls [m]. */
  tiefeM: number | null;
  /** Tiefen der sichtbaren Punkte bis VORSCHAU_MITTE_PX um den Zielpunkt [m]; liegt keiner so nah, die
   *  nächstgelegene Gruppe (pickMath.mitteTiefen). */
  mitteTiefen: number[];
  /** Tiefen der sichtbaren Punkte bis VORSCHAU_RADIUS_PX um den Zielpunkt [m]; ohne Punkte, die nur durch
   *  Lücken der Darstellung einer näheren Fläche durchscheinen (pickMath.ohneDurchscheinen). */
  fensterTiefen: number[];
  /** Dauer des Aufrufs [ms]. */
  ms: number;
}

export interface FineSearch {
  ray: Ray;
  /** Tiefenfenster [minDepth, maxDepth) entlang des Strahls [m]. */
  minDepth: number;
  maxDepth: number;
  /** Pixel-Fußabdruck je Meter Tiefe (pixelFootprintM(1, fov, Höhe)). */
  k: number;
  accept?: (p: PVec3) => boolean;
  /** Beginn des Zeitbudgets (performance.now()); Standard: jetzt. */
  t0?: number;
  budgetMs?: number;
}

/**
 * Schmale Naht zwischen Messgeometrie (Messen, Eckpunkte, POIs) und Datenquelle. Heute setzt nur die
 * Strahlsuche auf der Punktwolke sie um (createRayPicker); ein Mesh-Raycast (glTF/3D Tiles, spätere
 * Scheibe) wird eine zweite Umsetzung derselben Schnittstelle. Weitere Kandidatenfilter (wie die
 * Schnittbox) gehören in die Umsetzung (Getter beim Erzeugen), nicht in die Aufrufer.
 */
export interface PickSource {
  /** Punkt unter dem Ereignis = pickAtScreen(ev.clientX, ev.clientY, opts). */
  pickAtEvent(ev: MouseEvent, opts?: PickOptions): Promise<RayPickResult>;
  /** Punkt an Fensterkoordinaten; Pick-Rendering und Strahl werden synchron beim Aufruf erfasst. */
  pickAtScreen(clientX: number, clientY: number, opts?: PickOptions): Promise<RayPickResult>;
  /** Vorschau an Fensterkoordinaten: synchron, nur GPU-Pick der angezeigten Punkte, kein Dekodieren. */
  vorschauAtScreen(clientX: number, clientY: number): Vorschau;
  /** Vorwärmen, damit der erste Klick nicht Aufbaukosten ins Zeitbudget zahlt. */
  warm(): void;
}

/** Panorama: für jeden Punkt dieselben Optionen; budgetMs nur für den E2E-Haken. */
export const panoPickOptions = (budgetMs: number = PANO_FINE_BUDGET_MS): PickOptions => ({ budgetMs, coarseMaxM: PANO_COARSE_MAX_M });

export interface RayPicker extends PickSource {
  /** Nur die feinste Stufe entlang eines Strahls (für Aufrufer mit eigenem Tiefenfenster). */
  pickAlongRay(s: FineSearch): Promise<{ result: AlongRayResult | 'timeout' | 'error'; nodes: number }>;
}

/**
 * ``getScanFilter``: null im Orbit, sonst der Scanfilter der aktuellen Station
 * (scanFilter.scanFilterFor). Er wirkt auf grobe **und** feine Kandidaten — auch für jeden Aufrufer von
 * pickAlongRay (z. B. Kantenfang), damit alle Messgeometrie dieselben gefilterten Punkte sieht.
 * ``getCloudVisible`` (Ebenen-Register): liefert sie `false`, bricht pickAtEvent sofort mit
 * `info.reason: 'hidden'` ab — kein Pick-Rendering, keine Strahlsuche (die ausgeblendete Wolke steht
 * nicht mehr für Messungen/POIs zur Verfügung). Ohne Angabe gilt „immer sichtbar" (Standard `true`).
 */
export function createRayPicker(
  v: Pick<Viewer, 'instance' | 'cloud' | 'punktfilter'>,
  getClipState?: () => ClipBoxState,
  getScanFilter?: () => ScanFilter | null,
  getCloudVisible: () => boolean = () => true,
): RayPicker {
  const { instance, cloud } = v;
  if (!cloud) return {
    pickAlongRay: async () => ({ result: 'error', nodes: 0 }),
    warm: () => undefined,
    pickAtEvent: async () => ({ point: null, info: { reason: 'empty', level: 'none', ms: 0, wideCandidates: 0, nearCandidates: 0 } }),
    pickAtScreen: async () => ({ point: null, info: { reason: 'empty', level: 'none', ms: 0, wideCandidates: 0, nearCandidates: 0 } }),
    vorschauAtScreen: () => ({ punktWelt: null, tiefeM: null, mitteTiefen: [], fensterTiefen: [], ms: 0 }),
  };
  // Hierarchie einmal (lazy) aus der Quelle holen — liegt nach der Initialisierung komplett im Speicher.
  // Gemessen am Referenzpaket Kirche: 23 521 Knoten, Tiefe 0–8, ≈ 60 ms. Nebenwirkung (D49): COPCSource legt dabei
  // _nodeMap neu an — nur tragbar, solange Giro3D exakt 2.0.4 bleibt.
  let hierarchy: Promise<PointCloudNode> | null = null;
  const getHierarchy = (): Promise<PointCloudNode> => (hierarchy ??= cloud.source.getHierarchy());
  const toVec = (p: { x: number; y: number; z: number }): PVec3 => [p.x, p.y, p.z];
  const boxOf = (n: PointCloudNode): Box => ({ min: toVec(n.volume.min), max: toVec(n.volume.max) });
  // Knoten nach ID (Giro3D benennt jedes angezeigte Punktnetz nach seinem Knoten) — für die Prüfung der
  // groben Kandidaten gegen die Dekodierung. Karte einmal gebaut, als ein Promise gemerkt (nodeIndex.ts).
  const nodeIndex = memoNodeIndex<PointCloudNode>(getHierarchy);
  const nodeById = nodeIndex.get;
  // Beschreibung des Attributs PointSourceId (aus den Metadaten der Quelle, einmal).
  let idAttr: Promise<PointCloudAttribute | null> | null = null;
  const getIdAttr = (): Promise<PointCloudAttribute | null> =>
    (idAttr ??= cloud.source.getMetadata().then((md) => md.attributes.find((a) => a.name === 'PointSourceId') ?? null));

  /** Dekodierte Knoten über Klicks hinweg geteilt (knotenCache.ts): gleichzeitige und aufeinander folgende
   *  Suchen laden und dekodieren denselben Knoten nur einmal, statt jede für sich das Budget zu verbrauchen. */
  const bytesOf = (a: ArrayLike<number> | undefined): number =>
    a ? ((a as { byteLength?: number }).byteLength ?? a.length * 8) : 0;
  const geteilt = knotenCache<PointChunk | null>(PICK_CACHE_BYTES, (c) => (c ? bytesOf(c.positions) + bytesOf(c.ids) : 0));
  /** Je Klick: welche Knoten diese Suche benutzt (info.fineNodes, Kantenfang); Daten aus dem geteilten Merker. */
  type Decoder = { chunkOf: (n: PointCloudNode) => Promise<PointChunk | null>; size: () => number; has: (n: PointCloudNode) => boolean };
  const createDecoder = (withIds: boolean): Decoder => {
    const cache = new Map<string, Promise<PointChunk | null>>();
    const chunkOf = (node: PointCloudNode): Promise<PointChunk | null> => {
      let c = cache.get(node.id);
      if (!c) {
        // Punktfilter (F30) wirkt beim Dekodieren: seine Kennung gehört in den Schlüssel. Ändert er sich während
        // des Ladens, ist das Ergebnis keinem Zustand sicher zuzuordnen → Fehler (wird nicht gemerkt).
        const kennung = filterKennung(v.punktfilter());
        c = geteilt.get(`${withIds ? 'ids' : 'pos'}/${kennung}/${node.id}`, async (): Promise<PointChunk | null> => {
          const attr = withIds ? await getIdAttr() : null;
          const d = await cloud.source.getNodeData({ node, position: true, attributes: attr ? [attr] : [] });
          if (filterKennung(v.punktfilter()) !== kennung) throw new Error('Punktfilter während des Ladens geändert');
          if (!d.position) return null;
          const chunk: PointChunk = { origin: toVec(d.origin), positions: d.position.array };
          // Ohne IDs (Attribut fehlt) bleibt der Knoten bei aktivem Filter leer (pickMath.pointsNearRay).
          const ids = attr ? d.attributes?.[0]?.array : undefined;
          return ids ? { ...chunk, ids } : chunk;
        });
        cache.set(node.id, c);
      }
      return c;
    };
    return { chunkOf, size: () => cache.size, has: (n) => cache.has(n.id) };
  };

  const fineSearch = async (
    { ray, minDepth, maxDepth, k, accept, t0 = performance.now(), budgetMs = FINE_BUDGET_MS }: FineSearch,
    decoder: Decoder,
    sourceId: number | undefined,
  ): Promise<{ result: AlongRayResult | 'timeout' | 'error'; nodes: number }> => {
    const root = getHierarchy();
    const load = async (a: number, b: number): Promise<PVec3[]> => {
      const radiusM = Math.max(FINE_MIN_RADIUS_M, PICK_RADIUS * k * b);
      const box = refineBox(ray, a, 0, b - a, radiusM);
      const nodes = collectNodes(await root, box, boxOf, (n) => n.children).filter((n) => n.hasData);
      const chunks = (await Promise.all(nodes.map(decoder.chunkOf))).filter((c): c is PointChunk => c !== null);
      return pointsNearRay(chunks, ray, radiusM, a, b, accept, sourceId);
    };

    let cancelled = false;
    let timer: number | undefined;
    const timeout = new Promise<'timeout'>((resolve) => {
      timer = window.setTimeout(() => resolve('timeout'), Math.max(0, budgetMs - (performance.now() - t0)));
    });
    let result: AlongRayResult | 'timeout' | 'error';
    try {
      const search = findSurfaceAlongRay(ray, minDepth, maxDepth, load, {
        minClusterSize: FINE_MIN_CLUSTER,
        shouldStop: () => cancelled || performance.now() - t0 > budgetMs,
      });
      // Verliert die Suche das Rennen und scheitert danach, wäre das sonst eine unbehandelte Ablehnung.
      search.catch(() => undefined);
      result = await Promise.race([search, timeout]);
    } catch (e) {
      console.warn('Feinste Stufe nicht lesbar, grober Treffer:', e);
      result = 'error';
    } finally {
      window.clearTimeout(timer);
      // Läuft die Suche über das Budget, hält sie vor dem nächsten Abschnitt an (angestoßene Knoten
      // laden zu Ende und wärmen den Cache für den nächsten Klick).
      cancelled = true;
    }
    return { result, nodes: decoder.size() };
  };

  /**
   * Kantenfang nach dem Feintreffer. Vorrat = Originalpunkte im Fangvolumen aus den Knoten
   * dieses Klicks (derselbe Dekoder: mit aktivem Scanfilter nur Punkte des eigenen Scans, nie ein fremder);
   * fehlende Knoten werden bis SNAP_RESERVE_MS vor Fristende nachgeladen, übrige sind „unbekannt“ und
   * sperren Kandidaten, deren Nachbarschaftskugel sie berührt. Frist wie die feinste Stufe ab Klick:
   * abgelaufen → 'budget' (normaler Treffer, nie eine Zeitüberschreitung durch den Fang).
   */
  const snapNear = async (
    ray: Ray,
    hit: PVec3,
    k: number,
    accept: ((p: PVec3) => boolean) | undefined,
    decoder: Decoder,
    sourceId: number | undefined,
    t0: number,
    budgetMs: number,
  ): Promise<SnapResult & { original?: boolean }> => {
    const expired = (): boolean => performance.now() - t0 > budgetMs;
    if (expired()) return { point: null, why: 'budget' };
    const d = depthAlong(ray, hit);
    const nbR = Math.max(SNAP_NEIGHBOUR_MIN_M, SNAP_NEIGHBOUR_PX * k * d);
    const reach = SNAP_RADIUS_PX * k * d + nbR;
    const gap = surfaceGapM(d);
    const box = refineBox(ray, d, gap + nbR, gap + nbR, reach);
    const nodes = collectNodes(await getHierarchy(), box, boxOf, (n) => n.children).filter((n) => n.hasData);
    const loaded = new Map<string, PointChunk | null>();
    const loads = nodes.map((n) =>
      decoder.chunkOf(n).then(
        (c) => void loaded.set(n.id, c),
        () => undefined,
      ),
    );
    let timer: number | undefined;
    const deadline = new Promise<void>((resolve) => {
      timer = window.setTimeout(resolve, Math.max(0, budgetMs - SNAP_RESERVE_MS - (performance.now() - t0)));
    });
    try {
      await Promise.race([Promise.all(loads), deadline]);
    } finally {
      window.clearTimeout(timer);
    }
    if (expired()) return { point: null, why: 'budget' };
    const unknown = nodes.filter((n) => !loaded.has(n.id)).map(boxOf);
    const chunks = [...loaded.values()].filter((c): c is PointChunk => c !== null);
    const pool = pointsNearRay(chunks, ray, reach, d - gap - nbR, d + gap + nbR, accept, sourceId);
    if (expired()) return { point: null, why: 'budget' };
    // Auch ein erst nach Fristende fertiger Fang wird 'budget' (snapDeadline, Review 12b).
    const r = snapWithinDeadline(
      {
        ray,
        hit,
        k,
        pool,
        gapM: gap,
        neighbourhoodOk: (p, rad) => !unknown.some((b) => sphereTouchesBox(p, rad, b)),
      },
      () => performance.now(),
      t0 + budgetMs,
    );
    // Herkunftsbeleg für E2E: der Fang ist ein Element des Vorrats (Referenzvergleich).
    return r.point ? { ...r, original: pool.includes(r.point) } : r;
  };

  /** Scan-ID, auf die gefiltert wird (nur Panorama mit Scan-Zuordnung), sonst undefined. */
  const activeSourceId = (f: ScanFilter | null): number | undefined => (f?.mode === 'on' ? f.pointSourceId : undefined);

  const pickAlongRay = (s: FineSearch): Promise<{ result: AlongRayResult | 'timeout' | 'error'; nodes: number }> => {
    const sourceId = activeSourceId(getScanFilter?.() ?? null);
    return fineSearch(s, createDecoder(sourceId !== undefined), sourceId);
  };

  /**
   * Fensterkoordinaten → Leinwandkoordinaten, gerechnet von Giro3D selbst (eventToCanvasCoords; ohne
   * Ziel = Leinwand: clientX/Y minus Rechteck der Leinwand). pickObjectsAt rechnet nur echte
   * Event-Objekte um, alles andere nimmt es als fertige Leinwandkoordinaten — deshalb bekommen Pick
   * und Strahl denselben Vektor.
   */
  const canvasAt = (clientX: number, clientY: number): Vector2 =>
    instance.eventToCanvasCoords({ clientX, clientY, target: null } as unknown as MouseEvent, new Vector2());
  const rayThrough = (cc: Vector2, raycaster = new Raycaster(), ndcZiel = new Vector2()): Ray => {
    const ndc = instance.canvasToNormalizedCoords(cc, ndcZiel);
    raycaster.setFromCamera(ndc, instance.view.camera);
    return { origin: toVec(raycaster.ray.origin), dir: toVec(raycaster.ray.direction) };
  };

  const pickAtScreen = async (clientX: number, clientY: number, opts: PickOptions = {}): Promise<RayPickResult> => {
    const t0 = performance.now();
    if (!getCloudVisible()) {
      return { point: null, info: { level: 'none', ms: performance.now() - t0, wideCandidates: 0, nearCandidates: 0, reason: 'hidden' } };
    }
    // Unbrauchbares Tiefenfenster: gar nicht erst suchen.
    if (opts.tiefenFenster && !tiefenFensterGueltig(opts.tiefenFenster)) {
      return { point: null, info: { level: 'none', ms: performance.now() - t0, wideCandidates: 0, nearCandidates: 0, reason: 'empty' } };
    }
    const cc = canvasAt(clientX, clientY);
    const hits = instance.pickObjectsAt(cc, { where: [cloud], radius: PICK_RADIUS_WIDE, limit: PICK_LIMIT });
    // Schnittbox aktiv? Treffer außerhalb vor der Auswahl verwerfen.
    const clip = getClipState?.();
    const accept = clip ? (p: PVec3): boolean => pointAllowed(clip, [p[0], p[1], p[2]]) : undefined;
    const clipped = hits.map((h) => ({ h, p: toVec(h.point) })).filter(({ p }) => !accept || accept(p));
    // Scanfilter beim Klick festhalten — ein Stationswechsel während der Suche ändert ihn nicht.
    const scanFilter = getScanFilter?.() ?? null;
    const sourceId = activeSourceId(scanFilter);
    const decoder = createDecoder(sourceId !== undefined);
    const budgetMs = opts.budgetMs ?? FINE_BUDGET_MS;
    let verdicts: HitVerdict[] | null = null;
    if (sourceId !== undefined) {
      // Giro3Ds GPU-Pick liefert keine PointSourceId (nur Netz + Punktindex; angezeigt wird nur die Farbe).
      // Jeden groben Kandidaten deshalb gegen die Dekodierung seines Knotens prüfen — die Ansichtspuffer
      // der angezeigten Knoten liegen meist im Cache. Die Prüfung läuft unter derselben Frist ab Klick wie
      // die feinste Stufe (sonst gedrosselte Fetches außerhalb des Budgets); offene Urteile gelten als
      // ungeprüft. Fremde und ungeprüfte fallen weg, BEVOR Cursor-Kandidat, grober Ersatz und Obergrenze
      // des Fensters bestimmt werden (scanFilter.planCoarse).
      verdicts = await verdictsWithin(
        clipped,
        async ({ h, p }) => {
          const node = await nodeById(h.object.name);
          const chunk = node ? await decoder.chunkOf(node) : null;
          return classifyHit(chunk, h.index ?? -1, p, sourceId);
        },
        budgetMs - (performance.now() - t0),
      );
    }

    const camera = instance.view.camera;
    const ray = rayThrough(cc);
    // Für eine orthografische Kamera stimmten Pixel-Fußabdruck und Strahlursprung nicht — dann
    // ausdrücklich nicht messen statt falsch messen.
    if (!(camera instanceof PerspectiveCamera)) {
      const own = verdicts ? verdicts.filter((x) => x === 'own').length : clipped.length;
      return { point: null, info: { level: 'none', ms: performance.now() - t0, wideCandidates: own, nearCandidates: 0 } };
    }
    const k = pixelFootprintM(1, camera.fov, instance.domElement.clientHeight);
    // Mit Filter: Austritt aus der Wolke als Fernschranke, falls kein eigener Cursor-Kandidat da ist.
    const far = sourceId !== undefined ? rayBoxExit(ray, boxOf(await getHierarchy())) : undefined;
    const plan = planCoarse(
      clipped.map(({ p }) => p),
      verdicts,
      ray,
      k,
      PICK_RADIUS,
      far,
    );
    const info: PickInfo = { level: 'none', ms: 0, wideCandidates: plan.wide.length, nearCandidates: plan.near.length };
    if (scanFilter) info.scanFilter = scanFilter.mode;
    if (sourceId !== undefined) {
      info.foreignCandidates = plan.foreign;
      info.unverifiedCandidates = plan.unverified;
    }
    if (!plan.front) {
      info.ms = performance.now() - t0;
      // Mit Tiefenfenster ist „nichts im Fenster“ der Grund, wie bei einer leeren feinen Suche.
      if (opts.tiefenFenster) info.reason = 'empty';
      return { point: null, info };
    }
    const { frontDepth, nearDepth, coarse } = plan.front;
    const fenster = opts.tiefenFenster;
    const minDepth = fenster ? fenster.min : plan.front.minDepth;
    const maxDepth = fenster ? fenster.max : plan.front.maxDepth;
    const mitTiefe = (point: PVec3): RayPickResult => {
      info.tiefeM = depthAlong(ray, point);
      return { point, info };
    };
    info.frontDepthM = frontDepth;
    if (nearDepth !== undefined) info.nearDepthM = nearDepth;
    const { result: fine, nodes } = await fineSearch(
      { ray, minDepth, maxDepth, k, accept, t0, budgetMs: opts.budgetMs },
      decoder,
      sourceId,
    );
    info.ms = performance.now() - t0;
    info.fineNodes = nodes;
    if (typeof fine === 'object') {
      info.segments = fine.segments;
      info.finePoints = fine.points;
      if (fine.point) {
        info.level = 'fine';
        if (!opts.snapEdges) return mitTiefe(fine.point);
        // Fang darf den Feintreffer nie kosten: Fehler oder Budget → normaler Treffer (kein Fang statt Zeitüberschreitung).
        let snapped: SnapResult & { original?: boolean };
        try {
          snapped = await snapNear(ray, fine.point, k, accept, decoder, sourceId, t0, budgetMs);
        } catch (e) {
          console.warn('Kantenfang fehlgeschlagen, normaler Treffer:', e);
          info.snap = { result: 'error' };
          info.ms = performance.now() - t0;
          return mitTiefe(fine.point);
        }
        info.ms = performance.now() - t0;
        if (!snapped.point) {
          info.snap = { result: snapped.why };
          return mitTiefe(fine.point);
        }
        const [a, b] = [snapped.point, fine.point];
        info.snap = {
          result: 'edge',
          sigma: snapped.sigma,
          shiftMm: Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) * 1000,
          original: snapped.original === true,
        };
        return mitTiefe(snapped.point);
      }
    }
    const why: FineFailure = fine === 'timeout' || fine === 'error' ? fine : fine.stopped ? 'timeout' : 'empty';
    info.reason = why;
    // Mit Tiefenfenster kein grober Ersatz: er stammt aus der vordersten Fläche, nicht aus dem Fenster.
    if (fenster) return { point: null, info };
    // Panorama: lieber kein Messwert als einer, der aus der Stationstiefe bis zu
    // Zentimeter danebenliegen darf — gleich, warum die feinste Stufe versagt hat.
    const { point, rejected } = boundedCoarse(coarse, why, ray, k, PICK_RADIUS, opts.coarseMaxM);
    if (rejected) {
      info.reason = 'imprecise';
      info.fineReason = rejected;
    }
    info.level = point ? 'coarse' : 'none';
    return point ? mitTiefe(point) : { point, info };
  };

  const pickAtEvent = (ev: MouseEvent, opts?: PickOptions): Promise<RayPickResult> => pickAtScreen(ev.clientX, ev.clientY, opts);

  // Je Bild aufgerufen: Hilfsobjekte einmal anlegen. Nur hier — pickAtScreen liest seine Vektoren noch
  // nach einem await, eine Vorschau dazwischen überschriebe sie.
  const proj = new Vector3();
  const vorschauCc = new Vector2();
  const vorschauNdc = new Vector2();
  const vorschauStrahl = new Raycaster();
  const vorschauAtScreen = (clientX: number, clientY: number): Vorschau => {
    const t0 = performance.now();
    const vorschau: Vorschau = { punktWelt: null, tiefeM: null, mitteTiefen: [], fensterTiefen: [], ms: 0 };
    if (!getCloudVisible()) {
      vorschau.ms = performance.now() - t0;
      return vorschau;
    }
    const cc = instance.eventToCanvasCoords({ clientX, clientY, target: null } as unknown as MouseEvent, vorschauCc);
    const hits = instance.pickObjectsAt(cc, { where: [cloud], radius: VORSCHAU_RADIUS_PX, limit: VORSCHAU_LIMIT, sortByDistance: false });
    // Strahl und Projektion erst nach dem Pick-Rendering: es aktualisiert die Kameramatrizen, alle drei
    // sehen so dieselbe Kamera.
    const camera = instance.view.camera;
    const ray = rayThrough(cc, vorschauStrahl, vorschauNdc);
    const clip = getClipState?.();
    const { width, height } = instance.view;
    const eintraege: { p: PVec3; tiefe: number; px: number; x: number; y: number }[] = [];
    for (const h of hits) {
      const p = toVec(h.point);
      if (clip && !pointAllowed(clip, [p[0], p[1], p[2]])) continue;
      const d = depthAlong(ray, p);
      if (!(d > 0)) continue;
      // Pixelabstand aus der Projektion des Punkts (Giro3Ds coord/index der Treffer sind dafür nicht
      // verlässlich: um den Radius verschoben, Zeilenlänge 2·R statt 2·R+1).
      proj.set(p[0], p[1], p[2]).project(camera);
      const dx = ((proj.x + 1) / 2) * width - cc.x;
      const dy = ((1 - proj.y) / 2) * height - cc.y;
      eintraege.push({ p, tiefe: d, px: Math.hypot(dx, dy), x: dx, y: dy });
    }
    // Was nur durch Lücken der angezeigten Punkte einer näheren Fläche durchscheint, ist nicht sichtbar.
    const sichtbar = ohneDurchscheinen(eintraege);
    vorschau.fensterTiefen = sichtbar.map((e) => e.tiefe);
    vorschau.mitteTiefen = mitteTiefen(sichtbar, VORSCHAU_MITTE_PX);
    // Vorderster Punkt der Mitte, ohne Mitte der des Fensters.
    const mitte = new Set(vorschau.mitteTiefen);
    let vorn: { p: PVec3; tiefe: number } | null = null;
    for (const e of sichtbar) if (mitte.has(e.tiefe) && (!vorn || e.tiefe < vorn.tiefe)) vorn = e;
    if (!vorn) for (const e of sichtbar) if (!vorn || e.tiefe < vorn.tiefe) vorn = e;
    if (vorn) {
      vorschau.punktWelt = vorn.p;
      vorschau.tiefeM = vorn.tiefe;
    }
    vorschau.ms = performance.now() - t0;
    return vorschau;
  };

  return {
    pickAtEvent,
    pickAtScreen,
    vorschauAtScreen,
    pickAlongRay,
    warm: () => {
      getHierarchy().catch((e: unknown) => {
        hierarchy = null;
        console.warn('Punkt-Hierarchie nicht verfügbar:', e);
      });
      // Knotenindex der Scan-Prüfung gleich mit aufbauen — nicht im ersten Klick.
      nodeIndex.warm();
    },
  };
}
