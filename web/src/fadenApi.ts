// Befund-Faden am Server (Viewer und Portal): Faden lesen, Eintrag schreiben, eigenen Kommentar oder Link
// zurückziehen, Liste „Fällig/überfällig“ und die Namen für Zuständigkeiten. Dünne Hülle um `anfrage` —
// Fehler kommen als PlattformFehler mit dem unveränderten deutschen Servertext (409 trägt `aktuell`).
import type { Befunde, Faden, FadenEintrag, Filter, Stand } from './fadenModell';
import { filterQuery } from './fadenModell';
import { anfrage, PlattformFehler, projektPfad } from './plattformApi';

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

export const ANHANG_TYPEN = ['image/jpeg', 'image/png', 'application/pdf'] as const;
export const ANHANG_MAX = 20 * 1024 * 1024;

/** Adresse der Datei eines Anhangs (Bild inline, PDF als Download). */
export const anhangUrl = (pid: string, poi: string, id: string): string =>
  `${faden(pid, poi)}/anhaenge/${encodeURIComponent(id)}`;

/** Foto oder PDF als rohen Körper hochladen (kein multipart); Fehler wie `anfrage` (deutscher Servertext). */
export async function anhangHochladen(pid: string, poi: string, datei: Blob, name: string, fetchFn: typeof fetch = fetch): Promise<{ eintrag: FadenEintrag }> {
  const r = await fetchFn(`${faden(pid, poi)}/anhaenge?name=${encodeURIComponent(name)}`, {
    method: 'POST', credentials: 'same-origin', body: datei,
    headers: { 'X-Scanview': '1', 'Content-Type': datei.type || 'application/octet-stream' },
  });
  let daten: unknown = null;
  try { daten = await r.json(); } catch { /* kein JSON, z. B. 413 vom Proxy */ }
  if (!r.ok) {
    const text = daten !== null && typeof daten === 'object' && typeof (daten as { fehler?: unknown }).fehler === 'string'
      ? (daten as { fehler: string }).fehler
      : r.status === 413 ? 'Höchstens 20 MB je Anhang.' : `Hochladen fehlgeschlagen (HTTP ${r.status}).`;
    throw new PlattformFehler(r.status, text);
  }
  return daten as { eintrag: FadenEintrag };
}

export interface FadenBenachrichtigung { aus: boolean; konto: { eingeschaltet: boolean; bestaetigt: boolean } }

/** E-Mails zu diesem Befund (F8); 404, wenn die Instanz keine Mails verschickt. */
export const benachrichtigungLesen = (pid: string, poi: string): Promise<FadenBenachrichtigung> =>
  anfrage('GET', `${faden(pid, poi)}/benachrichtigung`);

export const benachrichtigungSetzen = (pid: string, poi: string, aus: boolean): Promise<FadenBenachrichtigung> =>
  anfrage('PUT', `${faden(pid, poi)}/benachrichtigung`, { aus });

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
