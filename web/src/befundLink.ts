// Befundlink, reine Teile (Vitest ohne DOM): den Link zusammensetzen, den Hinweis bei abweichendem
// Datensatzstand formulieren, den Seitentitel bilden und die ID aus dem Link einem lokalen POI zuordnen.
// Der Link trägt wie jeder Ansichtslink die Ansicht (samt Ebenen und noch nicht gespeicherten Messungen), dazu
// Befund-ID und Datensatzstand — kein Geheimnis und kein Token; wer ihn öffnet,
// braucht eine Anmeldung und ein Recht am Projekt.
import type { PoiPersistenz } from './poiPersistenz';
import type { Poi } from './poiStore';

/** Herkunft + Pfad + Suche (`?p=…` unverändert) + Hash. */
export function befundLink(o: { origin: string; pathname: string; search: string; hash: string }): string {
  return `${o.origin}${o.pathname}${o.search}${o.hash}`;
}

/** Hinweis, wenn der Befund auf einem anderen Stand des Datensatzes gesetzt wurde als dem geladenen — je nach
 *  Richtung „älteren“ (Link kleiner) oder „neueren“ (Link größer, z. B. wenn hier noch ein alter Stand liegt). */
export function revHinweis(linkRev: number | undefined, manifestRev: number): string | null {
  if (linkRev === undefined || linkRev === manifestRev) return null;
  const richtung = linkRev < manifestRev ? 'älteren' : 'neueren';
  return `Befund wurde auf einem ${richtung} Stand des Datensatzes gesetzt (Stand ${linkRev}, aktuell ${manifestRev}).`;
}

/** „<Befund> – <Projekt> – siteBits“ bzw. „<Projekt> – siteBits“; der Titel wird nicht gekürzt. */
export function seitentitel(befund: string | null, projekt: string): string {
  return befund ? `${befund} – ${projekt} – siteBits` : `${projekt} – siteBits`;
}

/**
 * Lokale ID des Befunds aus dem Link, null = nicht gefunden (gelöscht, nicht freigegeben, anderes Projekt —
 * bewusst nicht unterschieden). Gegenstück zu befundImLink: Im Plattformmodus führt nur die Server-ID eines
 * geladenen Server-POIs zum Ziel, aufgelöst über die Zuordnung Server-ID → lokale ID der Persistenz (auch wenn
 * er lokal nach einer Kollision mit einer Paket-ID anders heißt). Ein POI aus pois.json oder ein anderer
 * Server-POI mit zufällig dieser lokalen ID gilt nie als Befund. Einzelmodus: die lokale ID, wenn es den POI gibt.
 */
export function befundAusLink(
  b: string,
  o: { poi: (id: string) => Pick<Poi, 'source'> | undefined; persistenz: Pick<PoiPersistenz, 'meta' | 'lokalId' | 'bestaetigt'> | undefined },
): string | null {
  const { persistenz } = o;
  if (!persistenz?.meta) return o.poi(b) ? b : null;
  // Nie über die lokale ID: nach einer Kollision kann sie einem anderen Server-POI gehören.
  const lokal = persistenz.lokalId?.(b) ?? null;
  return lokal !== null && o.poi(lokal) && persistenz.meta(lokal)?.serverId === b ? lokal : null;
}
