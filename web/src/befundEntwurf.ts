import { parsePointSource } from './pointSources';
import type { PointSource } from './pointSources';
// Lokaler Befundentwurf als Daten: Schlüssel je Konto und Projekt, strenge Prüfung beim Lesen (der Browser-
// Speicher ist nicht vertrauenswürdig), Größe des Vorschaubilds, Textalternative. Rein (Vitest); IndexedDB
// liegt in entwurfSpeicher.ts, Ablauf und Anzeige in entwurfSteuerung.ts.
import { POI_ID_RE, POI_LIMITS } from './poiStore';
import { STATION_ID_RE } from './stations';

export { ENTWURF_DB, ENTWURF_STORE } from './entwurfKonstanten';
export const VORSCHAU_MAX_PX = 320;
const ANSICHT_MAX = 20_000;

export interface FormularWerte {
  title: string;
  category: string;
  note: string;
}
export interface Entwurf {
  positionSource?: PointSource|null;
  schema: 1;
  schluessel: string;
  /** Zeitpunkt der letzten Änderung (ms seit 1970). */
  zeit: number;
  /** Zielpunkt aus der feinen Suche (Welt, m). */
  punkt: [number, number, number];
  station: string | null;
  /** Pose beim Öffnen des Formulars (Adresszeilen-Form ohne „#“; ohne Messungen, Ebenen und Befundangaben). */
  ansicht: string;
  /** ID des künftigen POIs, fest ab dem Öffnen des Formulars — beim Fortsetzen wieder verwendet (kein Doppel).
   *  null nur bei Entwürfen aus älteren Fassungen. */
  poiId: string | null;
  formular: FormularWerte;
}

export const entwurfSchluessel = (konto: number | null, projectId: string): string => `${konto ?? 'lokal'}|${projectId}`;

/** Wie der Viewer läuft: Einzelmodus `sicher` nur, wenn ihn eine Antwort positiv belegt (kein Plattform-Server),
 *  nicht als Rückfall bei Netzfehler, Zeitüberschreitung oder unerwarteter Antwort. */
export type EntwurfModus = { art: 'einzeln'; sicher: boolean } | { art: 'plattform'; nutzerId: number | null };

/** Schlüssel für den Viewer: sicherer Einzelmodus „lokal“; Plattform je Konto. Kein Entwurf ohne eigene Konto-ID
 *  (älterer Server) und im Einzelmodus als Rückfall — sonst teilten sich alle Konten dieses Browsers einen
 *  Schlüssel. */
export function entwurfSchluesselFuer(modus: EntwurfModus, projectId: string): string | null {
  if (modus.art === 'einzeln') return modus.sicher ? entwurfSchluessel(null, projectId) : null;
  return modus.nutzerId === null ? null : entwurfSchluessel(modus.nutzerId, projectId);
}

/** Höchstalter eines Entwurfs; ältere werden beim nächsten Öffnen des Projekts gelöscht statt angeboten. */
export const ENTWURF_MAX_ALTER_MS = 30 * 24 * 60 * 60 * 1000;
/** Spielraum für eine vorgehende Uhr; was weiter in der Zukunft liegt, liefe nie ab. */
const ZUKUNFT_MAX_MS = 24 * 60 * 60 * 1000;

export function entwurfAbgelaufen(e: Pick<Entwurf, 'zeit'>, jetzt: number): boolean {
  return jetzt - e.zeit > ENTWURF_MAX_ALTER_MS || e.zeit - jetzt > ZUKUNFT_MAX_MS;
}

const text = (x: unknown, max: number): x is string => typeof x === 'string' && x.length <= max;

/** Gelesener Entwurf als eigene, geprüfte Kopie; null bei fremdem Schlüssel oder ungültigen Daten. */
export function pruefeEntwurf(roh: unknown, schluessel: string): Entwurf | null {
  if (typeof roh !== 'object' || roh === null) return null;
  const r = roh as Record<string, unknown>;
  if (r.schema !== 1 || r.schluessel !== schluessel) return null;
  if (typeof r.zeit !== 'number' || !Number.isFinite(r.zeit) || r.zeit < 0) return null;
  const p = r.punkt;
  if (!Array.isArray(p) || p.length !== 3 || !p.every((v) => typeof v === 'number' && Number.isFinite(v))) return null;
  if (r.station !== null && !(typeof r.station === 'string' && STATION_ID_RE.test(r.station))) return null;
  if (!text(r.ansicht, ANSICHT_MAX)) return null;
  if (r.poiId !== null && !(typeof r.poiId === 'string' && POI_ID_RE.test(r.poiId))) return null;
  const f = r.formular as Record<string, unknown> | null;
  if (typeof f !== 'object' || f === null) return null;
  if (!text(f.title, POI_LIMITS.title) || !text(f.category, POI_LIMITS.category) || !text(f.note, POI_LIMITS.note)) return null;
  let positionSource: PointSource|null|undefined;
  try { if (r.positionSource !== undefined) positionSource = parsePointSource(r.positionSource); } catch { return null; }
  return {
    ...(positionSource !== undefined ? {positionSource} : {}),
    schema: 1,
    schluessel,
    zeit: r.zeit,
    punkt: [p[0] as number, p[1] as number, p[2] as number],
    station: r.station as string | null,
    ansicht: r.ansicht,
    poiId: r.poiId as string | null,
    formular: { title: f.title, category: f.category, note: f.note },
  };
}

/** Größe des Vorschaubilds: höchstens `max` Pixel breit, Seitenverhältnis bleibt, nie vergrößert. */
export function vorschauGroesse(breite: number, hoehe: number, max = VORSCHAU_MAX_PX): { breite: number; hoehe: number } {
  if (breite <= 0 || hoehe <= 0) return { breite: 0, hoehe: 0 };
  const f = Math.min(1, max / breite);
  return { breite: Math.round(breite * f), hoehe: Math.round(hoehe * f) };
}

const ZAHL = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const zahl = (v: number): string => ZAHL.format(v).replace('-', '−');

/** Textalternative des Vorschaubilds: Titel, Standpunkt (falls bekannt) und Koordinate. */
export function entwurfText(e: Entwurf, o: { titelLeer: string }): string {
  const titel = e.formular.title.trim() || o.titelLeer;
  return [
    `Vorschau des Entwurfs „${titel}“`,
    e.station ? `Standpunkt ${e.station}` : '',
    `Koordinate ${e.punkt.map(zahl).join(' / ')} m`,
  ]
    .filter(Boolean)
    .join(', ');
}
