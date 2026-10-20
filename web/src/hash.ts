// URL-Hash für Deep-Links: Kameraposition/-ziel und Messungen werden relativ zu originWorld
// (Manifest, auf ganze Meter gerundeter Bounding-Box-Mittelpunkt) mit 3 Nachkommastellen
// kodiert — damit bleiben die Zahlen im Hash kurz (keine sechsstelligen UTM-Werte) und
// millimetergenau. Reines Modul ohne Giro3D/DOM-Abhängigkeit (bis auf URLSearchParams, das auch
// in Vitest/Node verfügbar ist) — siehe tests/hash.test.ts.
// `st` (Standpunkt-ID), `yaw`/`pitch` in Grad (yaw 0 = +X, gegen
// den Uhrzeigersinn; pitch über dem Horizont), `fov` = vertikaler Blickwinkel in Grad. Mit `st`
// entfallen `cam`/`tgt`; alte Links mit `cam`/`tgt` bleiben gültig.
// Messarten Polylinie/Fläche/Höhe im Parameter `g` = `<art>:<p1>;<p2>;…` je Messung, getrennt
// durch `|`; `<art>` ∈ `p` (Polylinie), `a` (Fläche), `h` (Höhe/Lot); Punkte wie `m` relativ zu
// `originWorld` mit 3 Nachkommastellen. Strecken bleiben in `m` (alte Links und ältere Viewer).
// Messungs-IDs stehen nicht im Link. Die Anlegereihenfolge über beide Parameter steht in
// `o` (je Messung ein Kürzel `d`/`p`/`a`/`h`; `d` nimmt das nächste Paar aus `m`, die übrigen den nächsten
// Eintrag aus `g`) — nur geschrieben, wenn `m` und `g` beide belegt sind. Fehlt `o` oder passt es nicht zu
// `m`/`g`, wird es ignoriert und die Strecken kommen zuerst (die Messungen selbst bleiben gültig).
// Die Zeichen `: ; , | . -` sind in URLSearchParams unkodiert gültig.
// `ly` trägt die sichtbaren Ebenen-IDs kommagetrennt (`ly=<id>,<id>`); ob das
// überhaupt geschrieben wird (nur bei Abweichung von der Voreinstellung), entscheidet layers.ts
// (lyParam) — hash.ts kodiert/dekodiert nur. Fehlt `ly` → `undefined`; `ly=` (leer) → `[]`; einzelne
// IDs, die nicht LAYER_ID_RE erfüllen, werden verworfen (kein harter Fehler wie bei cam/m/g).
// `b` (Befundlink) ist die ID eines Befunds (POI_ID_RE), `rev` die Revision des Datensatzes, auf der er
// gesetzt wurde (ganze Zahl, nur zusammen mit `b`); beide stehen am Ende. Ungültige `b`/`rev` werden wie
// `ly` einzeln verworfen (ohne `b` gilt auch `rev` nicht) und lassen Kamera, Standpunkt und Messungen stehen;
// umgekehrt liest decodeBefund `b`/`rev` auch dann, wenn eine kaputte Pose den übrigen Zustand verwirft.
// `e` (nur mit `b`) ist der Anker auf einen Eintrag im Befund-Faden (Schlüssel aus 16 Hex-Zeichen); er wird nur
// gelesen, nie geschrieben — die Adresszeile trägt ihn nach dem Öffnen nicht weiter.
// `ko=1`: Koordinatenanzeige am Zeiger an (koordinatenzeile.ts); aus = Parameter fehlt.
// `kx` = ausgeblendete Punktklassen (kommagetrennt, 0–255), `ec=1` = nur erste, `ec=2` = nur letzte Echos (F30, klassen.ts); ungültige
// Werte fallen einzeln heraus, ohne den übrigen Zustand zu verwerfen.
import type { Vec3 } from './manifest';
import { LAYER_ID_RE } from './manifest';
import { filterAusLink } from './klassen';
import { clampFov, clampPitch, normalizeYaw } from './panoMath';
import { POI_ID_RE } from './poiStore';
import { STATION_ID_RE } from './stations';

export type GeometryKind = 'polyline' | 'area' | 'height';
export interface HashGeometry {
  kind: GeometryKind;
  points: Vec3[];
}
/** Schutz vor übergroßen Links (Parsen, Zeichnen): höchstens so viele Eckpunkte in g= insgesamt. */
export const HASH_MAX_VERTICES = 1000;
const KIND_CODE: Record<GeometryKind, string> = { polyline: 'p', area: 'a', height: 'h' };
const CODE_KIND: Record<string, GeometryKind> = { p: 'polyline', a: 'area', h: 'height' };
/** Erlaubte Eckpunktzahl je Art (wie measureStore.POINT_RANGE; hash.ts bleibt ohne Speicher-Import). */
const HASH_POINTS: Record<GeometryKind, readonly [number, number]> = { polyline: [2, Infinity], area: [3, Infinity], height: [2, 2] };
/** Art je Messung in Anlegereihenfolge. */
export type OrderKind = 'distance' | GeometryKind;

export interface ViewState {
  kv?: boolean;
  cam?: Vec3;
  tgt?: Vec3;
  m?: [Vec3, Vec3][];
  g?: HashGeometry[];
  /** Anlegereihenfolge über m und g (o=); fehlt → erst alle Strecken, dann g. */
  order?: OrderKind[];
  st?: string;
  yaw?: number;
  pitch?: number;
  fov?: number;
  /** Sichtbare Ebenen-IDs; nur geschrieben, wenn layers.ts (lyParam) das entscheidet. */
  ly?: string[];
  /** Koordinatenanzeige am Zeiger an (ko=1); aus wird nicht geschrieben. */
  ko?: boolean;
  /** Ausgeblendete Punktklassen (kx=), aufsteigend; leer wird nicht geschrieben. */
  kx?: number[];
  /** Nur erste (ec=1) bzw. nur letzte (ec=2) Echos. */
  ec?: 'erste' | 'letzte';
  /** Befundlink: ID des verlinkten Befunds. */
  b?: string;
  /** Revision des Datensatzes, auf der der Befund gesetzt wurde; nur zusammen mit b. */
  rev?: number;
  /** Eintrag im Befund-Faden (Schlüssel), nur zusammen mit b; wird nicht geschrieben. */
  e?: string;
}

/** Größte erlaubte Revision im Link (ganze Zahl, 32 Bit vorzeichenbehaftet). */
const REV_MAX = 2_147_483_647;
const gueltigeRev = (r: number): boolean => Number.isInteger(r) && r >= 0 && r <= REV_MAX;

const enc = (p: Vec3, o: Vec3): string => p.map((v, i) => (v - o[i]!).toFixed(3)).join(',');

const dec = (s: string, o: Vec3): Vec3 | null => {
  const n = s.split(',').map(Number);
  return n.length === 3 && n.every(Number.isFinite) ? [n[0]! + o[0], n[1]! + o[1], n[2]! + o[2]] : null;
};

export function encodeHash(s: ViewState, origin: Vec3): string {
  const parts: string[] = [];
  if (s.st) {
    parts.push(`st=${s.st}`);
    if (s.yaw !== undefined) parts.push(`yaw=${s.yaw.toFixed(2)}`);
    if (s.pitch !== undefined) parts.push(`pitch=${s.pitch.toFixed(2)}`);
    if (s.fov !== undefined) parts.push(`fov=${s.fov.toFixed(1)}`);
  } else {
    if (s.cam) parts.push(`cam=${enc(s.cam, origin)}`);
    if (s.tgt) parts.push(`tgt=${enc(s.tgt, origin)}`);
  }
  if (s.m?.length) parts.push(`m=${s.m.map(([a, b]) => `${enc(a, origin)};${enc(b, origin)}`).join('|')}`);
  if (s.g?.length) parts.push(`g=${s.g.map((x) => `${KIND_CODE[x.kind]}:${x.points.map((p) => enc(p, origin)).join(';')}`).join('|')}`);
  if (s.order && s.m?.length && s.g?.length) parts.push(`o=${s.order.map((k) => (k === 'distance' ? 'd' : KIND_CODE[k])).join('')}`);
  if (s.ly !== undefined) parts.push(`ly=${s.ly.join(',')}`);
  if (s.kv === false) parts.push('kv=0');
  if (s.ko) parts.push('ko=1');
  if (s.kx?.length) parts.push(`kx=${[...s.kx].sort((a, b) => a - b).join(',')}`);
  if (s.ec) parts.push(`ec=${s.ec === 'erste' ? 1 : 2}`);
  if (s.b !== undefined && POI_ID_RE.test(s.b)) {
    parts.push(`b=${s.b}`);
    if (s.rev !== undefined && gueltigeRev(s.rev)) parts.push(`rev=${s.rev}`);
  }
  return `#${parts.join('&')}`;
}

export function decodeHash(h: string, origin: Vec3): ViewState {
  const out: ViewState = {};
  const q = new URLSearchParams(h.replace(/^#/, ''));
  const cam = q.get('cam');
  const tgt = q.get('tgt');
  const m = q.get('m');
  if (cam) {
    const p = dec(cam, origin);
    if (!p) return {};
    out.cam = p;
  }
  if (tgt) {
    const p = dec(tgt, origin);
    if (!p) return {};
    out.tgt = p;
  }
  if (m) {
    const pairs = m.split('|').map((pair) => pair.split(';').map((x) => dec(x, origin)));
    if (pairs.some((p) => p.length !== 2 || p.some((x) => !x))) return {};
    out.m = pairs as [Vec3, Vec3][];
  }
  const g = q.get('g');
  if (g !== null) {
    const out2: HashGeometry[] = [];
    let total = 0;
    for (const part of g.split('|')) {
      const hit = /^([pah]):(.+)$/.exec(part);
      if (!hit) return {};
      const kind = CODE_KIND[hit[1]!]!;
      const points = hit[2]!.split(';').map((x) => dec(x, origin));
      const [lo, hi] = HASH_POINTS[kind];
      if (points.length < lo || points.length > hi || points.some((p) => !p)) return {};
      total += points.length;
      if (total > HASH_MAX_VERTICES) return {};
      out2.push({ kind, points: points as Vec3[] });
    }
    out.g = out2;
  }
  const order = decodeOrder(q.get('o'), out);
  if (order) out.order = order;
  const st = q.get('st');
  if (st !== null) {
    if (!STATION_ID_RE.test(st)) return {};
    out.st = st;
    const num = (key: string): number | null | undefined => {
      const raw = q.get(key);
      if (raw === null) return undefined;
      const n = Number(raw);
      return raw.trim() !== '' && Number.isFinite(n) ? n : null;
    };
    const yaw = num('yaw');
    const pitch = num('pitch');
    const fov = num('fov');
    if (yaw === null || pitch === null || fov === null) return {};
    if (yaw !== undefined) out.yaw = normalizeYaw(yaw);
    if (pitch !== undefined) out.pitch = clampPitch(pitch);
    if (fov !== undefined) out.fov = clampFov(fov);
  }
  const ly = q.get('ly');
  if (ly !== null) out.ly = ly === '' ? [] : ly.split(',').filter((id) => LAYER_ID_RE.test(id));
  if (q.get('kv') === '0') out.kv = false;
  if (q.get('ko') === '1') out.ko = true;
  const pf = filterAusLink(q.get('kx'), q.get('ec'));
  if (pf.aus.size) out.kx = [...pf.aus].sort((a, b) => a - b);
  if (pf.echo !== 'alle') out.ec = pf.echo;
  // Zuletzt und ohne vorzeitiges return: ein kaputtes b/rev verwirft nur sich selbst.
  return { ...out, ...befundAus(q) };
}

/** Nur die Pose (Standpunkt mit Blickwinkeln bzw. Kamera und Ziel), ohne Messungen, Ebenen und Befundangaben. */
export function poseAus(s: ViewState): ViewState {
  const r: ViewState = {};
  if (s.cam) r.cam = s.cam;
  if (s.tgt) r.tgt = s.tgt;
  if (s.st !== undefined) r.st = s.st;
  if (s.yaw !== undefined) r.yaw = s.yaw;
  if (s.pitch !== undefined) r.pitch = s.pitch;
  if (s.fov !== undefined) r.fov = s.fov;
  return r;
}

/** Nur b/rev aus dem Hash — unabhängig vom übrigen Zustand: ein Befundlink mit kaputter Pose (cam/tgt/m/g/st,
 *  die decodeHash ganz verwirft) öffnet den Befund trotzdem. */
export function decodeBefund(h: string): Pick<ViewState, 'b' | 'rev' | 'e'> {
  return befundAus(new URLSearchParams(h.replace(/^#/, '')));
}

const SCHLUESSEL_RE = /^[0-9a-f]{16}$/;

function befundAus(q: URLSearchParams): Pick<ViewState, 'b' | 'rev' | 'e'> {
  const b = q.get('b');
  if (b === null || !POI_ID_RE.test(b)) return {};
  const rev = q.get('rev');
  const e = q.get('e');
  return {
    b,
    ...(rev !== null && /^(0|[1-9]\d{0,9})$/.test(rev) && gueltigeRev(Number(rev)) ? { rev: Number(rev) } : {}),
    ...(e !== null && SCHLUESSEL_RE.test(e) ? { e } : {}),
  };
}

/** o= passt nur, wenn es genau die Strecken aus m und die Arten aus g in deren Reihenfolge enthält. */
function decodeOrder(raw: string | null, s: ViewState): OrderKind[] | null {
  if (!raw || !s.m?.length || !s.g?.length) return null;
  const order: OrderKind[] = [];
  let gi = 0;
  for (const ch of raw) {
    if (ch === 'd') order.push('distance');
    else {
      const kind = CODE_KIND[ch];
      if (!kind || s.g[gi]?.kind !== kind) return null;
      gi++;
      order.push(kind);
    }
  }
  const dCount = order.length - gi;
  return dCount === s.m.length && gi === s.g.length ? order : null;
}
