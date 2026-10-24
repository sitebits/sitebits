// Stückprotokoll im Browser: Upload ankündigen, Stand abfragen (HEAD), Stücke mit Content-Range und
// Content-Digest (SHA-256 je Stück per Web Crypto) senden, abschließen, Import starten. Austauschbares
// fetch (Vitest). Fehler kommen als PlattformFehler mit dem Text des Servers (ohne Pfade).
import { anfrage, KEINE_VERBINDUNG, PlattformFehler } from '../plattformApi';
import { contentRange, offsetAus, stueckDigest, stueckPlan, type Import, type Upload } from './uploadModell';

export type Datei = Pick<Blob, 'size' | 'slice'> & { name: string };
export type Fortschritt = (gesendet: number, gesamt: number) => void;

export const ankuendigen = (datei: Datei, f: typeof fetch = fetch): Promise<Upload> =>
  anfrage<Upload>('POST', '/api/v1/uploads', { dateien: [{ pfad: datei.name, groesse: datei.size }] }, f);

export const uploadLesen = (id: string, f: typeof fetch = fetch): Promise<Upload> =>
  anfrage<Upload>('GET', `/api/v1/uploads/${encodeURIComponent(id)}`, undefined, f);

export const uploadLoeschen = (id: string, f: typeof fetch = fetch): Promise<void> =>
  anfrage<void>('DELETE', `/api/v1/uploads/${encodeURIComponent(id)}`, undefined, f);

/** Der Server rechnet dabei die SHA-256 der ganzen Datei nach — bei großen Dateien dauert das. */
export const ABSCHLIESSEN_MS = 30 * 60_000;

export const abschliessen = (id: string, f: typeof fetch = fetch): Promise<Upload> =>
  anfrage<Upload>('POST', `/api/v1/uploads/${encodeURIComponent(id)}/abschliessen`, {}, f, false, ABSCHLIESSEN_MS);

export const importStarten = (upload: string, typ: string, optionen: Record<string, string>, f: typeof fetch = fetch): Promise<Import> =>
  anfrage<Import>('POST', '/api/v1/importe', { upload, typ, optionen }, f);

export const importAbbrechen = (id: string, f: typeof fetch = fetch): Promise<Import> =>
  anfrage<Import>('DELETE', `/api/v1/importe/${encodeURIComponent(id)}`, undefined, f);

async function fehlerAus(r: Response): Promise<PlattformFehler> {
  let text = `Fehler ${r.status}`;
  try {
    const d = (await r.json()) as { fehler?: unknown };
    if (typeof d.fehler === 'string') text = d.fehler;
  } catch { /* kein JSON (z. B. Fehlerseite des Proxys) */ }
  return new PlattformFehler(r.status, text);
}

/** Stand der Datei laut Server (HEAD …/dateien/{nr}, Header Upload-Offset). */
export async function stand(id: string, nr: number, f: typeof fetch = fetch): Promise<number> {
  let r: Response;
  try {
    r = await f(`/api/v1/uploads/${encodeURIComponent(id)}/dateien/${nr}`, { method: 'HEAD', credentials: 'same-origin' });
  } catch {
    throw new PlattformFehler(0, KEINE_VERBINDUNG);
  }
  const o = offsetAus(r.headers.get('upload-offset'));
  if (!r.ok || o === null) throw new PlattformFehler(r.status, r.status === 404 ? 'Upload nicht mehr vorhanden.' : `Fehler ${r.status}`);
  return o;
}

/**
 * Sendet die Datei ab `ab` in Stücken. Antwortet der Server mit 409 und einem anderen Stand
 * (Stück schon angekommen, zweiter Tab), geht es dort weiter. Abbruch über `signal`.
 */
export async function senden(
  id: string, nr: number, datei: Datei, stueckBytes: number, ab: number,
  fortschritt: Fortschritt, signal: AbortSignal, f: typeof fetch = fetch, subtle: SubtleCrypto = crypto.subtle,
): Promise<void> {
  let pos = ab;
  let versuche = 0;
  while (pos < datei.size) {
    const [teil] = stueckPlan(datei.size, stueckBytes, pos);
    if (!teil) break;
    const daten = await datei.slice(teil.a, teil.b + 1).arrayBuffer();
    const digest = await stueckDigest(daten, subtle);
    let r: Response;
    try {
      r = await f(`/api/v1/uploads/${encodeURIComponent(id)}/dateien/${nr}`, {
        method: 'PUT', credentials: 'same-origin', signal, body: daten,
        headers: { 'X-Scanview': '1', 'Content-Type': 'application/octet-stream',
          'Content-Range': contentRange(teil.a, teil.b, datei.size), 'Content-Digest': digest },
      });
    } catch (e) {
      if (signal.aborted) throw e;
      throw new PlattformFehler(0, KEINE_VERBINDUNG);
    }
    if (r.status === 409) {
      const o = offsetAus(r.headers.get('upload-offset'));
      if (o !== null && o !== pos && versuche < 3) {
        versuche += 1;
        pos = o;
        fortschritt(pos, datei.size);
        continue;
      }
      throw await fehlerAus(r);
    }
    if (!r.ok) throw await fehlerAus(r);
    versuche = 0;
    pos = teil.b + 1;
    fortschritt(pos, datei.size);
  }
}
