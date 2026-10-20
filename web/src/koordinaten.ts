// Beschriftung von Koordinaten mit Koordinatensystem und Höhensystem (F23).
// Eine Quelle für Statuszeile, Kopiertext und CSV-Kopf; rein (Vitest). Nichts wird geraten: ein lokaler
// Rahmen heißt „lokal“ und trägt kein Höhensystem, ein unbekanntes Höhensystem erscheint als Rohtext.
// Zahlen: Anzeige mit Tausendertrennung (schmales geschütztes Leerzeichen) und Dezimalkomma, Kopiertext
// mit Dezimalpunkt für GIS. Der CSV-Inhalt bleibt bei num() (measureCsv.ts).
import { classifyCrs } from './crs';
import type { Frame, Undulation, Vec3 } from './manifest';

/** Werte aus import/src/scanview_import/hoehen.py (DHHN2016, ELLIPSOIDISCH, DHHN92). */
const HOEHE: Record<string, string> = {
  dhhn2016: 'NHN',
  dhhn92: 'NHN (DHHN92)',
  'ellipsoidal-etrs89': 'ellips.',
};

const EPSG_KURZ: Record<string, string> = {
  'EPSG:25832': 'ETRS89/UTM32',
  'EPSG:25833': 'ETRS89/UTM33',
  'EPSG:3068': 'Soldner Berlin',
};

const MAX_NAME = 32;

export interface Beschriftung {
  lokal: boolean;
  /** Kurzname des Koordinatensystems: „ETRS89/UTM33“, „lokal“, Name aus der WKT oder „EPSG:…“. */
  crs: string;
  /** „NHN“, „ellips.“, Rohtext eines unbekannten Systems; null bei lokalem Rahmen oder ohne Angabe. */
  hoehe: string | null;
  /** Zweithöhe (F23 §2 Nr. 5): andere Höhe = eigene + `versatz`, nur wenn das Paket ζ trägt und es über die
   *  Ausdehnung höchstens ZWEIT_MAX_M schwankt. */
  zweit?: { name: string; versatz: number; schwankung: number };
}

/** Größte Schwankung von ζ über das Paket, bis zu der eine Zahl am Ursprung als Zweithöhe genügt (1 cm). */
export const ZWEIT_MAX_M = 0.01;

function zweitHoehe(vd: string, u: Undulation | undefined): Beschriftung['zweit'] {
  if (!u || u.maxDeviationM > ZWEIT_MAX_M) return undefined;
  if (vd === 'dhhn2016') return { name: 'ellips.', versatz: u.atOriginM, schwankung: u.maxDeviationM };
  if (vd === 'ellipsoidal-etrs89') return { name: 'NHN', versatz: -u.atOriginM, schwankung: u.maxDeviationM };
  return undefined;
}

function wktName(wkt: string): string {
  const m = /^\s*[A-Z][A-Z0-9_]*\s*\[\s*"([^"]*)"/.exec(wkt);
  const name = m?.[1]?.trim() || 'WKT';
  return name.length > MAX_NAME ? `${name.slice(0, MAX_NAME)}…` : name;
}

export function beschriftung(crs: Frame['crs'], undulation?: Undulation): Beschriftung {
  const k = classifyCrs(crs.wkt);
  if (k.kind === 'local') return { lokal: true, crs: 'lokal', hoehe: null };
  const name = k.kind === 'epsg' ? (EPSG_KURZ[k.id] ?? k.id) : wktName(k.id);
  const vd = crs.verticalDatum?.trim();
  const hoehe = vd ? (HOEHE[vd.toLowerCase()] ?? vd) : null;
  const zweit = vd ? zweitHoehe(vd.toLowerCase(), undulation) : undefined;
  return { lokal: false, crs: name, hoehe, ...(zweit ? { zweit } : {}) };
}

/** 3 Nachkommastellen, Dezimalkomma, Tausender mit schmalem geschütztem Leerzeichen, kein „-0,000“. */
export function zahlAnzeige(x: number): string {
  const t = x.toFixed(3);
  const s = t === '-0.000' ? '0.000' : t;
  const [ganz, nach] = s.split('.') as [string, string];
  const vz = ganz.startsWith('-') ? '-' : '';
  const ziffern = vz ? ganz.slice(1) : ganz;
  return `${vz}${ziffern.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')},${nach}`;
}

/** Statuszeile: „E 367 334,404  N 5 807 317,817  H 59,837 m NHN“ bzw. „x … y … z … m (lokal)“. */
export function koordinateAnzeige(p: Vec3, b: Beschriftung): string {
  const [x, y, z] = p.map(zahlAnzeige) as [string, string, string];
  if (b.lokal) return `x ${x}  y ${y}  z ${z} m (lokal)`;
  const zweit = b.zweit
    ? ` (${b.zweit.name} ${zahlAnzeige(p[2] + b.zweit.versatz)} m ±${(b.zweit.schwankung * 100).toFixed(1).replace('.', ',')} cm)`
    : '';
  return `E ${x}  N ${y}  H ${z} m ${b.hoehe ?? 'Höhe ?'}${zweit} · ${b.crs}`;
}

/** Kopiertext für GIS: „E;N;H;CRS;Höhensystem“ mit Dezimalpunkt; lokal „x;y;z;lokal;“. */
export function koordinateKopie(p: Vec3, b: Beschriftung): string {
  const zahl = (v: number): string => {
    const t = v.toFixed(3);
    return t === '-0.000' ? '0.000' : t;
  };
  return [...p.map(zahl), b.crs, b.hoehe ?? ''].join(';');
}

/** Einheitenangabe für Spaltenköpfe: „[m, ETRS89/UTM33, NHN]“, „[m, lokal]“, „[m, ETRS89/UTM33, Höhe ?]“. */
export function einheitKopf(b: Beschriftung): string {
  return b.lokal ? '[m, lokal]' : `[m, ${b.crs}, ${b.hoehe ?? 'Höhe ?'}]`;
}
