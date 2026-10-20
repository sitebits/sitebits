// Prüfentscheidungen verwenden dieselbe Fehlerklasse wie die übrige Plattform-API.
import { anfrage, projektPfad } from './plattformApi';
import type { ServerEintrag, PoiDaten, MessungDaten } from './plattformApi';
import type { Stand, FadenEintrag } from './fadenModell';
export type Tabelle = 'pois' | 'messungen';
export interface Pruefliste {
  pois: ServerEintrag<PoiDaten>[];
  messungen: ServerEintrag<MessungDaten>[];
  faden: {
    poi: string;
    eintrag: FadenEintrag;
  }[];
}
const pfad = (t: Tabelle, p: string, e: string): string => `${projektPfad(p)}/${t}/${encodeURIComponent(e)}`;
export async function entscheiden<D>(t: Tabelle, p: string, e: string, version: number, aktion: 'annehmen' | 'verwerfen', fetchFn: typeof fetch = fetch): Promise<ServerEintrag<D> | null> {
  return await anfrage<ServerEintrag<D> | undefined>('POST', `${pfad(t, p, e)}/${aktion}`, { version }, fetchFn) ?? null;
}
export const annehmen = <D>(t: Tabelle, p: string, e: string, v: number, f?: typeof fetch): Promise<ServerEintrag<D> | null> => entscheiden(t, p, e, v, 'annehmen', f);
export const verwerfen = <D>(t: Tabelle, p: string, e: string, v: number, f?: typeof fetch): Promise<ServerEintrag<D> | null> => entscheiden(t, p, e, v, 'verwerfen', f);
const fadenPfad = (p: string, poi: string, s: string): string => `${pfad('pois', p, poi)}/faden/${encodeURIComponent(s)}`;
export const fadenAnnehmen = (p: string, poi: string, s: string, version: number): Promise<{
  stand: Stand;
  eintrag: FadenEintrag;
}> => anfrage('POST', `${fadenPfad(p, poi, s)}/annehmen`, { version });
export const fadenVerwerfen = (p: string, poi: string, s: string, text?: string): Promise<{
  eintrag: FadenEintrag;
}> => anfrage('POST', `${fadenPfad(p, poi, s)}/verwerfen`, text ? { text } : {});
export const zuPruefen = (p: string): Promise<Pruefliste> => anfrage('GET', `${projektPfad(p)}/zu-pruefen`);
export interface VorschlagAnzeige {
  herkunft: string;
  loeschen: boolean;
}
export const vorschlagAnzeige = (e: Pick<ServerEintrag<unknown>, 'herkunft' | 'pruefstand' | 'loeschenVorgeschlagen'>): VorschlagAnzeige | undefined => e.pruefstand === 'vorschlag' || e.loeschenVorgeschlagen != null ? { herkunft: e.herkunft ?? 'ki', loeschen: e.loeschenVorgeschlagen != null } : undefined;
export const vorschlagText = (v: VorschlagAnzeige): string => v.loeschen ? 'Löschen vorgeschlagen' : 'KI-Vorschlag';
/** Ein Löschvorschlag ändert die Gültigkeit des bisherigen Befunds nicht. */
export const exportGueltig = (x: {
  vorschlag?: VorschlagAnzeige;
}): boolean => !x.vorschlag || x.vorschlag.loeschen;
export const ausgelasseneVorschlaege = (l: readonly {
  vorschlag?: VorschlagAnzeige;
}[]): number => l.filter(x => !exportGueltig(x)).length;
