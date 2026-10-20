import { OHNE_NAMEN, statusText } from '../fadenModell';
import type { Namen, Stand, Zustaendig } from '../fadenModell';
import type { PoiDaten, ServerEintrag } from '../plattformApi';

export const SPALTEN = ['Nr.', 'Titel', 'Kategorie', 'Notiz', 'Standpunkt', 'X [m]', 'Y [m]', 'Z [m]',
  'Erstellt am (UTC)', 'Erstellt von', 'Geändert am (UTC)', 'Geändert von'] as const;
/** Zusatzspalten bei eingeschalteten Befund-Fäden — dieselben wie in der XLSX des Servers. */
export const FADEN_SPALTEN = ['Status', 'Zuständig', 'Frist'] as const;

type Befund = ServerEintrag<PoiDaten> & { stand?: Stand };

export function zeitUTC(zeit: number): string {
  return new Date(zeit * 1000).toISOString().slice(0, 16).replace('T', ' ');
}

const zahl = (n: number): string => (Math.abs(n) < 0.0005 ? 0 : n).toFixed(3).replace('.', ',');

/** Spalten der Druckansicht: mit Stand (der Server liefert ihn nur bei eingeschalteten Befund-Fäden) drei mehr. */
export const spaltenFuer = (pois: readonly Befund[]): readonly string[] =>
  pois.some((p) => p.stand !== undefined) ? [...SPALTEN, ...FADEN_SPALTEN] : SPALTEN;

/** „<Gewerk-Kurzzeichen> / <Person>“ wie der Server; Unbekanntes bleibt als Kennung erkennbar. */
function zustaendigKurz(z: Zustaendig | null, n: Namen): string {
  if (z === null) return '';
  const teile: string[] = [];
  if (z.gewerk_id !== null) teile.push(n.gewerke.get(z.gewerk_id)?.kurz ?? `Gewerk ${z.gewerk_id}`);
  if (z.nutzer_id !== null) teile.push(n.personen.get(z.nutzer_id) ?? `Konto ${z.nutzer_id}`);
  return teile.join(' / ');
}

const fadenWerte = (st: Stand | undefined, n: Namen): string[] =>
  st ? [statusText(st.status), zustaendigKurz(st.zustaendig, n), st.frist ?? ''] : ['', '', ''];

export function zeilen(pois: readonly Befund[], namen: Namen = OHNE_NAMEN): string[][] {
  const mitFaden = pois.some((p) => p.stand !== undefined);
  return [...pois].sort((a, b) => a.erstelltAm - b.erstelltAm || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .map((p, i) => [String(i + 1), p.daten.title, p.daten.category, p.daten.note, p.daten.station ?? '',
      ...p.daten.position.map(zahl), zeitUTC(p.erstelltAm), p.erstelltVonName ?? '',
      zeitUTC(p.geaendertAm), p.geaendertVonName ?? '', ...(mitFaden ? fadenWerte(p.stand, namen) : [])]);
}
