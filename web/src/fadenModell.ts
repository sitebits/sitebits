// Befund-Faden, reine Teile für Viewer und Portal (Vitest ohne DOM): Typen der Faden-API, Beschriftungen von
// Status, Frist und Zuständigkeit, Kennzeichen in Textform (nicht nur Farbe), Nummerierung der sichtbaren
// Einträge nach Position, Filter der Liste „Fällig/überfällig“ als Abfrage und der Befundlink mit Eintrags-Anker.
// Regeln (wer darf was, wann ist etwas überfällig) entscheidet allein der Server: die Oberfläche zeigt, was
// `rechte` erlaubt, und „überfällig“ nur, wo der Server es meldet.
import type { PoiDaten, ServerEintrag } from './plattformApi';

export const STATUS_WERTE = ['offen', 'in_arbeit', 'erledigt', 'abgeschlossen', 'abgelehnt'] as const;
const STATUS_TEXT: Record<string, string> = {
  offen: 'offen', in_arbeit: 'in Arbeit', erledigt: 'erledigt (gemeldet)', abgeschlossen: 'abgeschlossen', abgelehnt: 'abgelehnt',
};
export const ZUSTAENDIG_WERTE = ['alle', 'gewerk', 'ich'] as const;
export const FRIST_WERTE = ['ueberfaellig', 'woche', 'ohne'] as const;
export const ZUSTAENDIG_TEXT: Record<(typeof ZUSTAENDIG_WERTE)[number], string> = { alle: 'alle', gewerk: 'mein Gewerk', ich: 'ich' };
export const FRIST_TEXT: Record<(typeof FRIST_WERTE)[number], string> = { ueberfaellig: 'überfällig', woche: 'diese Woche', ohne: 'ohne Frist' };
/** Externe Kennung eines Eintrags (zufällig, 16 Hex-Zeichen) — nie als Nummer angezeigt. */
export const SCHLUESSEL_RE = /^[0-9a-f]{16}$/;

export interface Zustaendig { gewerk_id: number | null; nutzer_id: number | null }
export interface Stand {
  status: string;
  zustaendig: Zustaendig | null;
  frist: string | null;
  version: number;
  /** Nur in Antworten des Fadens (GET und POST …/faden): überfällig nach der Regel des Servers. */
  ueberfaellig?: boolean;
}
export interface FadenEintrag {
  herkunft?: 'mensch' | 'ki';
  bezug?: string | null;
  offen?: boolean;
  schluessel: string;
  art: string;
  text: string | null;
  wert: unknown;
  intern: boolean;
  erstellt_von: { id: number | null; name: string | null };
  erstellt_am: number;
  kanal: string;
  zurueckgezogen_am: number | null;
}
export interface FadenRechte { kommentieren: boolean; status: string[]; zustaendig: boolean; frist: boolean; intern: boolean }
export interface Faden { stand: Stand; eintraege: FadenEintrag[]; rechte: FadenRechte }
export interface BefundZeile { poi: ServerEintrag<PoiDaten>; stand: Stand; ueberfaellig: boolean; letzter_eintrag_am: number | null }
export interface Befunde { befunde: BefundZeile[]; zaehler: { ueberfaellig_meine: number } }

export const statusText = (s: string): string => (Object.hasOwn(STATUS_TEXT, s) ? STATUS_TEXT[s]! : s);

/** Frist JJJJ-MM-TT als TT.MM.JJJJ (Kalendertag, keine Zeitzone im Spiel); unlesbar bleibt roh, ohne Frist leer. */
export function fristText(frist: string | null): string {
  if (frist === null) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(frist);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : frist;
}

/** Kennzeichen eines Befunds als Text: Status, Frist und — nur nach Angabe des Servers — „überfällig“. */
export function kennzeichen(st: Pick<Stand, 'status' | 'frist'>, ueberfaellig: boolean): string {
  const teile = [`Status: ${statusText(st.status)}`];
  if (st.frist !== null) teile.push(`Frist: ${fristText(st.frist)}`);
  if (ueberfaellig) teile.push('überfällig');
  return teile.join(' · ');
}

/** Kurzform am Marker (Schild über der Kugel): Status und gegebenenfalls „überfällig“. */
export const kennzeichenKurz = (st: Pick<Stand, 'status'>, ueberfaellig: boolean): string =>
  ueberfaellig ? `${statusText(st.status)} · überfällig` : statusText(st.status);

/** „3 überfällig“ für die Projektkarte; nichts, wenn keiner überfällig ist. */
export const ueberfaelligText = (n: number | undefined): string => (n !== undefined && n > 0 ? `${n} überfällig` : '');

export interface Namen {
  gewerke: ReadonlyMap<number, { name: string; kurz: string }>;
  personen: ReadonlyMap<number, string>;
}

/** Namen aus /gewerke und /beteiligte (beide für jede Rolle im Projekt lesbar). */
export function namenAus(
  gewerke: readonly { id: number; name: string; kurz: string }[],
  beteiligte: readonly { nutzerId: number; nutzer: string }[],
): Namen {
  return {
    gewerke: new Map(gewerke.map((g) => [g.id, { name: g.name, kurz: g.kurz }])),
    personen: new Map(beteiligte.map((b) => [b.nutzerId, b.nutzer])),
  };
}

export const OHNE_NAMEN: Namen = { gewerke: new Map(), personen: new Map() };

const gewerkText = (id: number, n: Namen): string => {
  const g = n.gewerke.get(id);
  return g ? `${g.name} (${g.kurz})` : `Gewerk ${id}`;
};

/** „Gewerk (Kurz) / Person“; eine Person, die nicht mehr beteiligt ist, bleibt als „ehemalig“ erkennbar. */
export function zustaendigText(z: Zustaendig | null, n: Namen): string {
  if (z === null || (z.gewerk_id === null && z.nutzer_id === null)) return 'niemand';
  const teile: string[] = [];
  if (z.gewerk_id !== null) teile.push(gewerkText(z.gewerk_id, n));
  if (z.nutzer_id !== null) teile.push(n.personen.get(z.nutzer_id) ?? `Konto ${z.nutzer_id} (ehemalig)`);
  return teile.join(' / ');
}

/** Stand-Zeile des Verlaufs: Kennzeichen (überfällig laut Server) und Zuständigkeit. */
export function standZeile(st: Stand, n: Namen): string {
  const zust = st.zustaendig ? ` · Zuständig: ${zustaendigText(st.zustaendig, n)}` : '';
  return `${kennzeichen(st, st.ueberfaellig === true)}${zust}`;
}

/** Sichtbare Einträge mit ihrer Nummer nach Position (1, 2, 3 …) — interne Lücken gibt es so nicht. */
export const nummeriert = (l: readonly FadenEintrag[]): { nr: number; eintrag: FadenEintrag }[] =>
  l.map((eintrag, i) => ({ nr: i + 1, eintrag }));

const ART_TEXT: Record<string, string> = {
  vorschlag: 'Vorschlag', kommentar: 'Kommentar', link: 'Link', status: 'Status', zustaendig: 'Zuständigkeit', frist: 'Frist', markierung: 'Markierung',
};
export const artText = (art: string): string => (Object.hasOwn(ART_TEXT, art) ? ART_TEXT[art]! : art);

const FELD: Record<string, string> = { title: 'Titel', category: 'Kategorie', note: 'Notiz', position: 'Position', station: 'Standpunkt' };
const ZAHL = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 });
const alsObjekt = (x: unknown): Record<string, unknown> | null =>
  x !== null && typeof x === 'object' && !Array.isArray(x) ? (x as Record<string, unknown>) : null;

function feldWert(v: unknown): string {
  if (v === null || v === undefined || v === '') return '–';
  if (Array.isArray(v) && v.every((x) => typeof x === 'number')) return v.map((x) => ZAHL.format(x as number)).join(' / ');
  if (typeof v === 'string') return `„${v}“`;
  return JSON.stringify(v);
}

function markierungText(wert: unknown): string {
  const w = alsObjekt(wert);
  const vorher = alsObjekt(w?.vorher) ?? {};
  const nachher = alsObjekt(w?.nachher) ?? {};
  const felder = [...new Set([...Object.keys(vorher), ...Object.keys(nachher)])];
  const teile = felder.map((f) => `${FELD[f] ?? f} ${feldWert(vorher[f])} → ${feldWert(nachher[f])}`);
  return teile.length > 0 ? `Markierung geändert: ${teile.join('; ')}` : 'Markierung geändert';
}

const mitBegruendung = (kopf: string, text: string | null): string => (text ? `${kopf}. Begründung: ${text}` : kopf);

function zustaendigAus(wert: unknown): Zustaendig | null {
  const w = alsObjekt(wert);
  if (!w) return null;
  const id = (x: unknown): number | null => (typeof x === 'number' ? x : null);
  return { gewerk_id: id(w.gewerk_id), nutzer_id: id(w.nutzer_id) };
}

/** Hauptzeile eines Eintrags je Art (Text, Zielstatus, Zuständigkeit, Frist, Link, Markierung). */
export function eintragInhalt(e: FadenEintrag, n: Namen): string {
  if (e.zurueckgezogen_am !== null) return 'zurückgezogen';
  switch (e.art) {
    case 'vorschlag': {
      const w = alsObjekt(e.wert);
      const art = typeof w?.art === 'string' ? w.art : '';
      if (!['status', 'frist', 'zustaendig'].includes(art)) return 'schlägt vor: unbekannte Änderung';
      return `schlägt vor: ${eintragInhalt({ ...e, art, wert: w?.wert }, n)}`;
    }
    case 'kommentar':
      return e.text ?? '';
    case 'status':
      return mitBegruendung(`Status → ${typeof e.wert === 'string' ? statusText(e.wert) : '–'}`, e.text);
    case 'zustaendig': {
      const z = zustaendigAus(e.wert);
      return mitBegruendung(z ? `Zuständig → ${zustaendigText(z, n)}` : 'Zuständigkeit entfernt', e.text);
    }
    case 'frist':
      return mitBegruendung(typeof e.wert === 'string' ? `Frist → ${fristText(e.wert)}` : 'Frist entfernt', e.text);
    case 'link': {
      const titel = alsObjekt(e.wert)?.titel;
      return mitBegruendung(`Link: ${typeof titel === 'string' ? titel : '–'}`, e.text);
    }
    case 'markierung':
      return markierungText(e.wert);
    default:
      return e.text ?? '';
  }
}

/** Adresse eines Link-Eintrags, nur wenn sie als Link taugt (https oder Pfad dieser Instanz) — sonst null. */
export function linkAdresse(e: FadenEintrag): string | null {
  if (e.art !== 'link' || e.zurueckgezogen_am !== null) return null;
  const url = alsObjekt(e.wert)?.url;
  if (typeof url !== 'string') return null;
  return /^https:\/\/[^\s/?#]+([/?#]\S*)?$/.test(url) || /^\/(?![/\\])[^\s\\]*$/.test(url) ? url : null;
}

const ZEIT = new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Berlin' });
export const zeitText = (sekunden: number): string => ZEIT.format(new Date(sekunden * 1000));

const RUECKMELDUNG = 'Rückmeldung';

/** Kategorien für den Filter: alle des Projekts (auch ausgeschaltete — sie stehen an Befunden) und „Rückmeldung“. */
export function kategorienAus(roh: unknown): string[] {
  const aus: string[] = [];
  for (const k of Array.isArray(roh) ? roh : []) {
    const name = alsObjekt(k)?.name;
    if (typeof name === 'string' && name.trim() !== '' && !aus.includes(name)) aus.push(name);
  }
  if (!aus.includes(RUECKMELDUNG)) aus.push(RUECKMELDUNG);
  return aus;
}

export interface Filter {
  status: (typeof STATUS_WERTE)[number] | '';
  zustaendig: (typeof ZUSTAENDIG_WERTE)[number];
  frist: (typeof FRIST_WERTE)[number] | '';
  kategorie: string;
}
export const FILTER_LEER: Filter = { status: '', zustaendig: 'alle', frist: '', kategorie: '' };

/** Abfrage für GET …/befunde; nur gewählte Filter („alle“ ist die Vorgabe des Servers). */
export function filterQuery(f: Filter): string {
  const q = new URLSearchParams();
  if (f.status) q.set('status', f.status);
  if (f.zustaendig !== 'alle') q.set('zustaendig', f.zustaendig);
  if (f.frist) q.set('frist', f.frist);
  if (f.kategorie) q.set('kategorie', f.kategorie);
  return q.toString();
}

export const filterAktiv = (f: Filter): boolean => filterQuery(f) !== '';

/** Viewer an der Stelle des Befunds, mit Faden; `e` springt zu einem Eintrag (nur ein gültiger Schlüssel). */
export function befundlinkPfad(pid: string, poiId: string, schluessel?: string): string {
  const e = schluessel !== undefined && SCHLUESSEL_RE.test(schluessel) ? `&e=${schluessel}` : '';
  return `/ansicht?p=/data/${pid}/#b=${encodeURIComponent(poiId)}${e}`;
}
