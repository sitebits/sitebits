// Befund-Faden am Server (Viewer und Portal): Faden lesen, Eintrag schreiben, eigenen Kommentar oder Link
// zurückziehen, Liste „Fällig/überfällig“ und die Namen für Zuständigkeiten. Dünne Hülle um `anfrage` —
// Fehler kommen als PlattformFehler mit dem unveränderten deutschen Servertext (409 trägt `aktuell`).
import type { Befunde, Faden, FadenEintrag, Filter, Stand } from './fadenModell';
import { filterQuery } from './fadenModell';
import { anfrage, projektPfad } from './plattformApi';

export interface NeuerEintrag {
  art: 'kommentar' | 'link' | 'status' | 'zustaendig' | 'frist';
  text?: string | null;
  wert?: unknown;
  intern?: boolean;
  version?: number;
}

export interface GewerkInfo { id: number; name: string; kurz: string; bauherrenseite?: boolean }
export interface BeteiligterInfo { nutzerId: number; nutzer: string; rolle: string; gewerk: { id: number; name: string; kurz: string } | null }

const faden = (pid: string, poi: string): string => `${projektPfad(pid)}/pois/${encodeURIComponent(poi)}/faden`;

export const fadenLesen = (pid: string, poi: string): Promise<Faden> => anfrage<Faden>('GET', faden(pid, poi));

export const eintragSchreiben = (pid: string, poi: string, e: NeuerEintrag): Promise<{ eintrag: FadenEintrag; stand: Stand }> =>
  anfrage('POST', faden(pid, poi), e);

export const zurueckziehen = (pid: string, poi: string, schluessel: string): Promise<{ eintrag: FadenEintrag }> =>
  anfrage('POST', `${faden(pid, poi)}/${encodeURIComponent(schluessel)}/zurueckziehen`, {});

export function befundeLesen(pid: string, f: Filter): Promise<Befunde> {
  const q = filterQuery(f);
  return anfrage<Befunde>('GET', `${projektPfad(pid)}/befunde${q ? `?${q}` : ''}`);
}

/** Gewerke und Beteiligte (für jede Rolle im Projekt lesbar); scheitert eins, bleibt die Liste leer. */
export async function namenLaden(pid: string): Promise<{ gewerke: GewerkInfo[]; beteiligte: BeteiligterInfo[] }> {
  const [g, b] = await Promise.all([
    anfrage<{ gewerke: GewerkInfo[] }>('GET', `${projektPfad(pid)}/gewerke`).then((x) => x.gewerke, () => []),
    anfrage<{ beteiligte: BeteiligterInfo[] }>('GET', `${projektPfad(pid)}/beteiligte`).then((x) => x.beteiligte, () => []),
  ]);
  return { gewerke: g, beteiligte: b };
}
