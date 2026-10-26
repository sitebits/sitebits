import type { Stand } from './fadenModell';
import type { PointSource } from './pointSources';
// API v1 der Plattform — dünne fetch-Hülle für Viewer und Portal. Ändernde Anfragen
// tragen den CSRF-Header (X-Scanview: 1; Origin setzt der Browser). Fehler kommen als PlattformFehler mit
// dem deutschen Servertext; 409 trägt den aktuellen Stand (`aktuell`), Status 0 heißt „keine Verbindung“
// (auch Zeitüberschreitung und eine 2xx-Antwort ohne JSON, z. B. eine Fehlerseite davor).
// Reine Logik mit austauschbarem fetch (Vitest, environment: node).

export type Methode = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
/** Rolle laut Server. Rechte daraus nur über die Erlaubnisliste in rechte.ts ableiten — eine unbekannte
 *  Rolle (neuer Server, alter Viewer) darf nie mehr dürfen als lesen. */
export type Rolle = 'betrachter' | 'rueckmelder' | 'bearbeiter' | 'projektleitung' | 'admin';

export class PlattformFehler extends Error {
  readonly status: number;
  readonly aktuell: unknown;

  constructor(status: number, text: string, aktuell: unknown = undefined) {
    super(text);
    this.name = 'PlattformFehler';
    this.status = status;
    this.aktuell = aktuell;
  }
}

export const KEINE_VERBINDUNG = 'Keine Verbindung zum Server.';
export const UNERWARTET = 'Unerwartete Antwort vom Server.';
/** keepalive-Anfragen dürfen im Browser zusammen höchstens 64 KiB Körper tragen; darüber ohne keepalive senden.
 *  Das Budget gilt für alle gleichzeitig laufenden keepalive-Anfragen. */
export const KEEPALIVE_MAX_BYTES = 60 * 1024;
let keepaliveBytes = 0;
/** Zeitgrenze je Anfrage [ms]; die Start-Abfragen des Viewers nehmen START_MS, damit er nicht hängt. */
export const ZEIT_MS = 15_000;
export const START_MS = 10_000;

export async function anfrage<T>(
  methode: Methode,
  pfad: string,
  body?: unknown,
  fetchFn: typeof fetch = fetch,
  keepalive = false,
  timeoutMs = ZEIT_MS,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (methode !== 'GET') headers['X-Scanview'] = '1';
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const koerper = body === undefined ? undefined : JSON.stringify(body);
  const bytes = koerper === undefined ? 0 : new TextEncoder().encode(koerper).length;
  const mitKeepalive = keepalive && keepaliveBytes + bytes < KEEPALIVE_MAX_BYTES;
  if (mitKeepalive) keepaliveBytes += bytes;
  let antwort: Response;
  try {
    antwort = await fetchFn(pfad, {
      method: methode,
      credentials: 'same-origin',
      headers,
      body: koerper,
      keepalive: mitKeepalive,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    throw new PlattformFehler(0, KEINE_VERBINDUNG);
  } finally {
    if (mitKeepalive) keepaliveBytes -= bytes;
  }
  if (antwort.status === 204) return undefined as T;
  let daten: unknown = null;
  let json = true;
  try {
    daten = await antwort.json();
  } catch {
    daten = null;
    json = false;
  }
  if (!antwort.ok) {
    const d = (daten !== null && typeof daten === 'object' ? daten : {}) as { fehler?: unknown; aktuell?: unknown };
    throw new PlattformFehler(antwort.status, typeof d.fehler === 'string' ? d.fehler : `Fehler ${antwort.status}`, d.aktuell);
  }
  if (!json) throw new PlattformFehler(0, UNERWARTET);
  return daten as T;
}

export interface ServerEintrag<D> {
  id: string;
  version: number;
  daten: D;
  paketRevision: number | null;
  erstelltVon: number | null;
  /** Anzeigename des Urhebers; ältere Server liefern ihn nicht. */
  erstelltVonName?: string | null;
  erstelltAm: number;
  geaendertVon: number | null;
  geaendertVonName: string | null;
  geaendertAm: number;
  /** Stand des Befund-Fadens (Status, Zuständig, Frist) — nur POIs bei eingeschalteten Befund-Fäden. */
  stand?: Stand;
  herkunft?: 'mensch' | 'ki';
  pruefstand?: 'gueltig' | 'vorschlag' | 'verworfen';
  loeschenVorgeschlagen?: number | null;
}

export interface PoiDaten {
  positionSource?: PointSource|null;
  title: string;
  category: string;
  note: string;
  position: [number, number, number];
  station: string | null;
}

export interface MessungDaten {
  pointSources?: (PointSource|null)[];
  kind: 'distance' | 'polyline' | 'area' | 'height';
  points: [number, number, number][];
  areaM2?: number;
  planarityM?: number;
}

export interface ProjektInfo {
  id: string;
  titel: string;
  beschreibung: string;
  rolle: Rolle;
  version: number;
  /** Kategorien des Projekts `[{ name, aktiv }]`; fehlt bei älteren Servern. Geprüft wird erst in `kategorieVorschlaege`. */
  kategorien?: unknown;
  /** Projekt-Schalter „Befund-Fäden“; fehlt bei älteren Servern (dann aus). */
  befund_faeden?: boolean;
  /** Panoramen für diese Rolle freigegeben (F20); fehlt bei älteren Servern (dann ja). */
  panoramen_sichtbar?: boolean;
}

export const projektPfad = (pid: string): string => `/api/v1/projekte/${encodeURIComponent(pid)}`;
