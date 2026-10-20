import { describe, expect, it } from 'vitest';
import { anfrage, KEINE_VERBINDUNG, PlattformFehler, projektPfad, UNERWARTET } from '../src/plattformApi';

const mit = (status: number, body: string | null, typ = 'application/json') => {
  const aufrufe: { pfad: string; init: RequestInit | undefined }[] = [];
  const fetchFn = (async (pfad: RequestInfo | URL, init?: RequestInit) => {
    aufrufe.push({ pfad: String(pfad), init });
    return new Response(body, { status, headers: { 'content-type': typ } });
  }) as typeof fetch;
  return { fetchFn, aufrufe };
};

describe('anfrage', () => {
  it('GET ohne CSRF-Header, mit Cookie', async () => {
    const f = mit(200, '{"a":1}');
    expect(await anfrage('GET', '/api/v1/x', undefined, f.fetchFn)).toEqual({ a: 1 });
    const init = f.aufrufe[0]!.init!;
    expect(init.credentials).toBe('same-origin');
    expect((init.headers as Record<string, string>)['X-Scanview']).toBeUndefined();
  });
  it('Änderungen tragen X-Scanview und JSON-Körper', async () => {
    const f = mit(201, '{"id":"P-1"}');
    await anfrage('POST', '/api/v1/x', { a: 1 }, f.fetchFn);
    const init = f.aufrufe[0]!.init!;
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'X-Scanview': '1', 'Content-Type': 'application/json' });
    expect(init.body).toBe('{"a":1}');
  });
  it('204 → undefined, DELETE ohne Content-Type', async () => {
    const f = mit(204, null);
    expect(await anfrage('DELETE', '/api/v1/x?version=2', undefined, f.fetchFn)).toBeUndefined();
    expect(f.aufrufe[0]!.init!.headers).toEqual({ 'X-Scanview': '1' });
  });
  it('409 trägt den aktuellen Stand', async () => {
    const f = mit(409, '{"dienst":"scanview","fehler":"Inzwischen geändert","aktuell":{"id":"P-1","version":3}}');
    const e = await anfrage('PATCH', '/x', {}, f.fetchFn).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(PlattformFehler);
    expect((e as PlattformFehler).status).toBe(409);
    expect((e as PlattformFehler).message).toBe('Inzwischen geändert');
    expect((e as PlattformFehler).aktuell).toEqual({ id: 'P-1', version: 3 });
  });
  it('Netzfehler → Status 0', async () => {
    const f = (async () => {
      throw new TypeError('offline');
    }) as unknown as typeof fetch;
    const e = (await anfrage('GET', '/x', undefined, f).catch((x: unknown) => x)) as PlattformFehler;
    expect([e.status, e.message]).toEqual([0, KEINE_VERBINDUNG]);
  });
  it('Fehler ohne JSON → „Fehler <Status>“', async () => {
    const e = (await anfrage('GET', '/x', undefined, mit(502, '<html>', 'text/html').fetchFn).catch((x: unknown) => x)) as PlattformFehler;
    expect([e.status, e.message]).toEqual([502, 'Fehler 502']);
  });
  it('Zeitüberschreitung → Status 0 (Wiederholungspfad)', async () => {
    const f = ((_p: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_ok, fehler) => {
        init?.signal?.addEventListener('abort', () => fehler(new DOMException('Zeit', 'TimeoutError')));
      })) as typeof fetch;
    const e = (await anfrage('GET', '/x', undefined, f, false, 20).catch((x: unknown) => x)) as PlattformFehler;
    expect([e.status, e.message]).toEqual([0, KEINE_VERBINDUNG]);
  });
  it('2xx ohne JSON → Status 0 statt null', async () => {
    const e = (await anfrage('GET', '/x', undefined, mit(200, '<html>', 'text/html').fetchFn).catch((x: unknown) => x)) as PlattformFehler;
    expect(e).toBeInstanceOf(PlattformFehler);
    expect([e.status, e.message]).toEqual([0, UNERWARTET]);
  });
  it('keepalive wird durchgereicht', async () => {
    const f = mit(201, '{}');
    await anfrage('POST', '/x', {}, f.fetchFn, true);
    expect(f.aufrufe[0]!.init!.keepalive).toBe(true);
  });
  it('keepalive nur für Körper unter 60 KiB', async () => {
    const f = mit(201, '{}');
    await anfrage('POST', '/x', { t: 'x'.repeat(61 * 1024) }, f.fetchFn, true);
    expect(f.aufrufe[0]!.init!.keepalive).toBe(false);
  });
  it('keepalive-Budget gilt für gleichzeitige Anfragen zusammen', async () => {
    const init: RequestInit[] = [];
    let los: () => void = () => undefined;
    const tor = new Promise<void>((r) => (los = r));
    const f = (async (_p: RequestInfo | URL, i?: RequestInit) => {
      init.push(i!);
      await tor;
      return new Response('{}', { status: 201, headers: { 'content-type': 'application/json' } });
    }) as typeof fetch;
    const koerper = { t: 'x'.repeat(40 * 1024) };
    const a = anfrage('POST', '/x', koerper, f, true);
    const b = anfrage('POST', '/x', koerper, f, true);
    los();
    await Promise.all([a, b]);
    expect(init.map((i) => i.keepalive)).toEqual([true, false]);
    // nach dem Ende ist das Budget wieder frei
    await anfrage('POST', '/x', koerper, mit(201, '{}').fetchFn, true);
    const c = mit(201, '{}');
    await anfrage('POST', '/x', koerper, c.fetchFn, true);
    expect(c.aufrufe[0]!.init!.keepalive).toBe(true);
  });
  it('projektPfad kodiert', () => {
    expect(projektPfad('ost')).toBe('/api/v1/projekte/ost');
  });
});
