// Ersatz-Server und Ersatz-Uhr für die Vitest-Tests der Plattform-Persistenz: bildet
// /api/v1/projekte/{pid}[/pois|/messungen[/{id}]] mit version/409/404 im Speicher nach. Kein *.test.ts —
// wird nur importiert.
import type { ServerEintrag } from '../src/plattformApi';
import type { Uhr } from '../src/abgleich';

type Tabelle = 'pois' | 'messungen';

export interface FakePlattform {
  fetchFn: typeof fetch;
  aufrufe: string[];
  offline: boolean;
  rolle: string;
  /** Kategorien des Projekts in der Projektantwort; undefined = Feld fehlt (älterer Server). */
  kategorien?: unknown;
  /** Schreibende Anfrage ablehnen (422 mit diesem Grund), z. B. eine Regel des Dienstes; null = annehmen. */
  ablehnen: ((methode: string, id: string | null, daten: unknown) => string | null) | null;
  /** Konto, das die Anfragen stellt (erstelltVon/geaendertVon neuer Einträge). */
  nutzer: { id: number; name: string };
  eintrag(t: Tabelle, id: string, daten: unknown, urheber?: { id: number; name: string }): ServerEintrag<unknown>;
  aendereDirekt(t: Tabelle, id: string, daten: unknown): void;
  eintraege(t: Tabelle): ServerEintrag<Record<string, unknown>>[];
}

const antwort = (status: number, body?: unknown): Response =>
  body === undefined
    ? new Response(null, { status })
    : new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

export function fakePlattform(pid = 'p1'): FakePlattform {
  const daten: Record<Tabelle, Map<string, ServerEintrag<unknown>>> = { pois: new Map(), messungen: new Map() };
  let n = 0;
  const neu = (id: string, version: number, d: unknown, urheber = f.nutzer, alt?: ServerEintrag<unknown>): ServerEintrag<unknown> => ({
    id,
    version,
    daten: d,
    paketRevision: 1,
    erstelltVon: alt ? alt.erstelltVon : urheber.id,
    erstelltVonName: alt ? (alt.erstelltVonName ?? null) : urheber.name,
    erstelltAm: 0,
    geaendertVon: f.nutzer.id,
    geaendertVonName: f.nutzer.name,
    geaendertAm: version,
  });
  const f: FakePlattform = {
    aufrufe: [],
    offline: false,
    rolle: 'bearbeiter',
    ablehnen: null,
    nutzer: { id: 1, name: 'Test' },
    eintrag: (t, id, d, urheber) => {
      const e = neu(id, 1, d, urheber);
      daten[t].set(id, e);
      return e;
    },
    aendereDirekt: (t, id, d) => {
      const alt = daten[t].get(id);
      if (alt) daten[t].set(id, neu(id, alt.version + 1, d, undefined, alt));
    },
    eintraege: (t) => [...daten[t].values()] as ServerEintrag<Record<string, unknown>>[],
    fetchFn: (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), 'https://sv.test');
      const methode = init?.method ?? 'GET';
      f.aufrufe.push(`${methode} ${url.pathname}`);
      if (f.offline) throw new TypeError('Failed to fetch');
      const m = /^\/api\/v1\/projekte\/([^/]+)(?:\/(pois|messungen)(?:\/([^/]+))?)?$/.exec(url.pathname);
      if (!m || m[1] !== pid) return antwort(404, { dienst: 'scanview', fehler: 'nicht gefunden' });
      const t = m[2] as Tabelle | undefined;
      if (!t) return antwort(200, { id: pid, titel: pid, beschreibung: '', rolle: f.rolle, version: 0, ...(f.kategorien === undefined ? {} : { kategorien: f.kategorien }) });
      const tab = daten[t];
      const id = m[3] ? decodeURIComponent(m[3]) : undefined;
      const body = init?.body ? (JSON.parse(String(init.body)) as { id?: string | null; daten?: unknown; version?: number }) : null;
      if (methode === 'GET') return antwort(200, { [t]: [...tab.values()] });
      const grund = f.ablehnen?.(methode, id ?? body?.id ?? null, body?.daten) ?? null;
      if (grund !== null) return antwort(422, { dienst: 'scanview', fehler: grund });
      if (methode === 'POST') {
        const neueId = body?.id ?? `M-${++n}`;
        const da = tab.get(neueId);
        if (da) return antwort(409, { dienst: 'scanview', fehler: 'Diese ID ist schon vergeben.', aktuell: da });
        const e = neu(neueId, 1, body?.daten);
        tab.set(neueId, e);
        return antwort(201, e);
      }
      const alt = id === undefined ? undefined : tab.get(id);
      if (!alt || id === undefined) return antwort(404, { dienst: 'scanview', fehler: 'nicht gefunden' });
      const version = methode === 'DELETE' ? Number(url.searchParams.get('version')) : body?.version;
      if (version !== alt.version) return antwort(409, { dienst: 'scanview', fehler: 'Konflikt', aktuell: alt });
      if (methode === 'DELETE') {
        tab.delete(id);
        return antwort(204);
      }
      const e = neu(id, alt.version + 1, { ...(alt.daten as object), ...(body?.daten as object) }, undefined, alt);
      tab.set(id, e);
      return antwort(200, e);
    }) as typeof fetch,
  };
  return f;
}

export function fakeUhr(): { uhr: Uhr; timer: { id: number; ms: number; fn: () => void }[]; alle(): number[] } {
  const timer: { id: number; ms: number; fn: () => void }[] = [];
  let n = 0;
  return {
    timer,
    uhr: {
      setTimeout: (fn, ms) => {
        const id = ++n;
        timer.push({ id, ms, fn });
        return id;
      },
      clearTimeout: (id) => {
        const i = timer.findIndex((x) => x.id === id);
        if (i >= 0) timer.splice(i, 1);
      },
    },
    alle: () => {
      const jetzt = timer.splice(0);
      for (const x of jetzt) x.fn();
      return jetzt.map((x) => x.ms);
    },
  };
}
