// Einheitstests mit gefälschtem fetch: Adresse, Kopf, Körper, Abfrage, Antwortformate, Fehler.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Sitebits, SitebitsFehler } from '../src/index.ts';

interface Aufruf { url: string; init: RequestInit }

function client(antwort: (a: Aufruf) => Response | Promise<Response>): { c: Sitebits; aufrufe: Aufruf[] } {
  const aufrufe: Aufruf[] = [];
  const fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    const a = { url: String(url), init: init ?? {} };
    aufrufe.push(a);
    return antwort(a);
  }) as typeof globalThis.fetch;
  return { c: new Sitebits({ basis: 'https://bits.example.org/', token: 'sv_test', fetch }), aufrufe };
}

const json = (daten: unknown, status = 200): Response =>
  new Response(JSON.stringify(daten), { status, headers: { 'Content-Type': 'application/json' } });

test('GET mit Token im Kopf, ohne Körper, Basis ohne Schrägstrich am Ende', async () => {
  const { c, aufrufe } = client(() => json({ version: '1.2.3' }));
  assert.deepEqual(await c.version(), { version: '1.2.3' });
  const [a] = aufrufe;
  assert.equal(a?.url, 'https://bits.example.org/api/v1/version');
  assert.equal(a?.init.method, 'GET');
  assert.deepEqual(a?.init.headers, { Authorization: 'Bearer sv_test' });
  assert.equal(a?.init.body, undefined);
  assert.equal(a?.init.redirect, 'error');
});

test('Pfadparameter werden kodiert, Abfrage nur mit gesetzten Werten', async () => {
  const { c, aufrufe } = client(() => json({ pois: [] }));
  await c.pois('ost/../x', { vorschlaege: true });
  assert.equal(aufrufe[0]?.url, 'https://bits.example.org/api/v1/projekte/ost%2F..%2Fx/pois?vorschlaege=1');
  await c.protokoll('ost', { limit: 5 });
  assert.equal(aufrufe[1]?.url, 'https://bits.example.org/api/v1/projekte/ost/protokoll?limit=5');
  await assert.rejects(c.projekt(''), TypeError);
});

test('POST schickt JSON-Körper mit Inhaltstyp', async () => {
  const { c, aufrufe } = client(() => json({ id: 'P-1' }, 201));
  const daten = { title: 'Riss', position: [1, 2, 3] };
  await c.poiAnlegen('ost', { daten });
  const a = aufrufe[0];
  assert.equal(a?.init.method, 'POST');
  assert.deepEqual(a?.init.headers, { Authorization: 'Bearer sv_test', 'Content-Type': 'application/json' });
  assert.deepEqual(JSON.parse(String(a?.init.body)), { daten });
});

test('DELETE mit version in der Abfrage; 204 → null, 202 → Löschvorschlag', async () => {
  let status = 204;
  const { c, aufrufe } = client(() => status === 204 ? new Response(null, { status }) : json({ id: 'P-1' }, status));
  assert.equal(await c.poiLoeschen('ost', 'P-1', 3), null);
  assert.equal(aufrufe[0]?.url, 'https://bits.example.org/api/v1/projekte/ost/pois/P-1?version=3');
  assert.equal(aufrufe[0]?.init.method, 'DELETE');
  status = 202;
  assert.deepEqual(await c.poiLoeschen('ost', 'P-1', 3), { id: 'P-1' });
});

test('Text und Bytes für Dateien', async () => {
  const { c } = client(a => new Response(a.url.endsWith('.xlsx') ? new Uint8Array([80, 75]) : 'a;b\n1;2\n'));
  assert.equal(await c.exportCsv('ost'), 'a;b\n1;2\n');
  assert.deepEqual(await c.maengellisteXlsx('ost'), new Uint8Array([80, 75]));
});

test('Fehlerantwort wird SitebitsFehler mit Status, Satz und aktuellem Stand', async () => {
  const { c } = client(() => json({ dienst: 'scanview', fehler: 'Inzwischen geändert', aktuell: { version: 4 } }, 409));
  const e = await c.projektAendern('ost', { titel: 'x', version: 1 }).then(() => null, (f: unknown) => f);
  assert.ok(e instanceof SitebitsFehler);
  assert.equal(e.status, 409);
  assert.equal(e.methode, 'PATCH');
  assert.equal(e.pfad, '/api/v1/projekte/ost');
  assert.equal(e.fehler, 'Inzwischen geändert');
  assert.deepEqual(e.aktuell, { version: 4 });
  assert.match(e.message, /PATCH \/api\/v1\/projekte\/ost: HTTP 409 – Inzwischen geändert/);
});

test('Fehler ohne JSON-Körper und nicht erreichbarer Dienst', async () => {
  const { c } = client(() => new Response('<html>', { status: 502 }));
  await assert.rejects(c.sitzung(), (e: unknown) => e instanceof SitebitsFehler && e.status === 502 && e.fehler === null);
  const { c: weg } = client(() => { throw new TypeError('fetch failed'); });
  await assert.rejects(weg.sitzung(), (e: unknown) =>
    e instanceof SitebitsFehler && e.status === 0 && e.cause instanceof TypeError);
});

test('Token taucht in keiner Fehlermeldung auf', async () => {
  const { c } = client(() => json({ dienst: 'scanview', fehler: 'nicht angemeldet' }, 401));
  await assert.rejects(c.projekte(), (e: unknown) => e instanceof Error && !e.message.includes('sv_test'));
});

test('Optionen werden geprüft', () => {
  assert.throws(() => new Sitebits({ basis: 'bits.example.org', token: 'x' }), TypeError);
  assert.throws(() => new Sitebits({ basis: 'https://bits.example.org', token: '' }), TypeError);
});

test('aufAuswertungWarten fragt bis zum Ende und hält eine Zeitgrenze', async () => {
  const folge = ['wartend', 'laufend', 'fertig'];
  const { c, aufrufe } = client(() => json({ id: 'a1', status: folge.shift() ?? 'fertig' }));
  const pausen: number[] = [];
  const a = await c.aufAuswertungWarten('ost', 'a1', { intervallMs: 5, warten: async ms => { pausen.push(ms); } });
  assert.equal(a.status, 'fertig');
  assert.equal(aufrufe.length, 3);
  assert.deepEqual(pausen, [5, 5]);
  const { c: haengt } = client(() => json({ id: 'a1', status: 'laufend' }));
  await assert.rejects(haengt.aufAuswertungWarten('ost', 'a1', { zeitMs: 0, warten: async () => {} }),
    (e: unknown) => e instanceof SitebitsFehler && e.status === 0 && /noch „laufend“/.test(e.message));
});

test('Upload-Stück: roher Körper, Content-Range und Content-Digest (SHA-256 des Stücks)', async () => {
  const { c, aufrufe } = client(() => json({ nr: 0, stand: 3, groesse: 10 }));
  const stueck = new Uint8Array([97, 98, 99]);
  assert.deepEqual(await c.uploadStueck('u1', 0, 0, stueck, 10), { nr: 0, stand: 3, groesse: 10 });
  const a = aufrufe[0];
  assert.equal(a?.url, 'https://bits.example.org/api/v1/uploads/u1/dateien/0');
  assert.equal(a?.init.method, 'PUT');
  assert.equal(a?.init.body, stueck);
  const kopf = a?.init.headers as Record<string, string>;
  assert.equal(kopf['Content-Range'], 'bytes 0-2/10');
  // SHA-256("abc"), Base64
  assert.equal(kopf['Content-Digest'], 'sha-256=:ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=:');
  assert.equal(kopf['Content-Type'], 'application/octet-stream');
});

test('Upload-Stand liest Upload-Offset aus HEAD, ohne Kopf ist es ein Fehler', async () => {
  const { c, aufrufe } = client(() => new Response(null, { status: 204, headers: { 'Upload-Offset': '67108864' } }));
  assert.equal(await c.uploadStand('u1', 2), 67108864);
  assert.equal(aufrufe[0]?.init.method, 'HEAD');
  assert.equal(aufrufe[0]?.url, 'https://bits.example.org/api/v1/uploads/u1/dateien/2');
  const ohne = client(() => new Response(null, { status: 204 }));
  await assert.rejects(ohne.c.uploadStand('u1', 2), SitebitsFehler);
});
