// Viewer-Naht zur Plattform: erkenneModus() entscheidet allein anhand der Antwort auf
// GET /api/sitzung, ob der Viewer als Einzelmodus (nginx-Demo, kein Plattform-Server dahinter),
// im Plattformmodus (eingeloggt) oder mit einer Weiterleitung (nicht eingeloggt / Startseite ohne
// Paket) startet. Feste Testfälle zuerst, danach zwei ergänzende Fälle (siehe unten).
import { describe, expect, it } from 'vitest';
import { erkenneModus } from '../src/platform';

const antwort = (status: number, body: string, typ: string) =>
  (async () => new Response(body, { status, headers: { 'content-type': typ } })) as unknown as typeof fetch;
const loc = (search: string, hash = '') => ({ pathname: '/ansicht', search, hash });

describe('erkenneModus', () => {
  it('erkennt HTML-200 als Einzelmodus', async () => {
    expect(await erkenneModus(antwort(200, '<!doctype html>', 'text/html'), loc('?p=/data/x/'))).toEqual({ art: 'einzeln', sicher: true });
  });
  it('404 → Einzelmodus', async () => {
    expect(await erkenneModus(antwort(404, 'x', 'text/plain'), loc(''))).toEqual({ art: 'einzeln', sicher: true });
  });
  it('Netzfehler → Einzelmodus', async () => {
    const f = (async () => { throw new TypeError('offline'); }) as unknown as typeof fetch;
    expect(await erkenneModus(f, loc(''))).toEqual({ art: 'einzeln', sicher: false });
  });
  it('JSON ohne Dienstkennung → Einzelmodus', async () => {
    expect(await erkenneModus(antwort(200, '{"x":1}', 'application/json'), loc('?p=/data/x/'))).toEqual({ art: 'einzeln', sicher: true });
  });
  it('200 scanview → Plattform', async () => {
    const m = await erkenneModus(antwort(200, '{"dienst":"scanview","nutzer":{"name":"T"}}', 'application/json'), loc('?p=/data/x/'));
    expect(m).toEqual({ art: 'plattform', name: 'T', nutzerId: null });
  });
  it('200 scanview mit eigener Nutzer-ID → Plattform mit ID', async () => {
    const m = await erkenneModus(antwort(200, '{"dienst":"scanview","nutzer":{"id":7,"name":"R"}}', 'application/json'), loc('?p=/data/x/'));
    expect(m).toEqual({ art: 'plattform', name: 'R', nutzerId: 7 });
  });
  it('kaputte Nutzer-ID → Plattform ohne ID', async () => {
    for (const id of ['"7"', '0', '-3', '1.5', 'null', '9007199254740993', 'true', '[7]']) {
      const m = await erkenneModus(antwort(200, `{"dienst":"scanview","nutzer":{"id":${id},"name":"R"}}`, 'application/json'), loc('?p=/data/x/'));
      expect(m, id).toEqual({ art: 'plattform', name: 'R', nutzerId: null });
    }
  });
  it('Plattform ohne p → Startseite', async () => {
    const m = await erkenneModus(antwort(200, '{"dienst":"scanview","nutzer":{"name":"T"}}', 'application/json'), loc(''));
    expect(m).toEqual({ art: 'weiterleiten', ziel: '/' });
  });
  it('401 → Anmelden mit Rücksprung', async () => {
    const m = await erkenneModus(antwort(401, '{"dienst":"scanview"}', 'application/json; charset=utf-8'), loc('?p=/data/x/'));
    expect(m).toEqual({ art: 'weiterleiten', ziel: '/anmelden?weiter=%2Fansicht%3Fp%3D%2Fdata%2Fx%2F' });
  });
  it('401 → Anmelden mit Rücksprung inkl. Hash (Kamera/Standpunkt/Messungen, hash.ts)', async () => {
    const m = await erkenneModus(
      antwort(401, '{"dienst":"scanview"}', 'application/json; charset=utf-8'),
      loc('?p=/data/x/', '#st=S017&yaw=30'),
    );
    expect(m).toEqual({ art: 'weiterleiten', ziel: '/anmelden?weiter=%2Fansicht%3Fp%3D%2Fdata%2Fx%2F%23st%3DS017%26yaw%3D30' });
  });

  // Ergänzend (von den sieben Fällen oben nicht abgedeckt):
  it('401 ohne Dienstkennung/HTML (nginx-Demo antwortet auf alles mit 401+index.html) → Einzelmodus', async () => {
    // Deckt ab, dass die content-type/dienst-Prüfung auch für 401 gilt — ein Stub, der jeden 401
    // ungeprüft nach /anmelden schickt, würde diesen Test nicht bestehen.
    expect(await erkenneModus(antwort(401, '<!doctype html>', 'text/html'), loc('?p=/data/x/'))).toEqual({ art: 'einzeln', sicher: true });
  });
  it('Zeitüberschreitung → Einzelmodus nur als Rückfall (nicht sicher)', async () => {
    const f = (async () => { throw new DOMException('Zeit abgelaufen', 'TimeoutError'); }) as unknown as typeof fetch;
    expect(await erkenneModus(f, loc('?p=/data/x/'))).toEqual({ art: 'einzeln', sicher: false });
  });
  it('Plattform-Dienst mit unerwartetem Status → Einzelmodus nur als Rückfall (nicht sicher)', async () => {
    for (const status of [403, 404, 500, 503]) {
      const m = await erkenneModus(antwort(status, '{"dienst":"scanview","fehler":"x"}', 'application/json'), loc('?p=/data/x/'));
      expect(m, String(status)).toEqual({ art: 'einzeln', sicher: false });
    }
  });
  it('Serverfehler ohne Dienstkennung (z. B. Vorschaltserver, Dienst dahinter weg) → nicht sicher', async () => {
    expect(await erkenneModus(antwort(502, '<html>Bad Gateway</html>', 'text/html'), loc('?p=/data/x/'))).toEqual({ art: 'einzeln', sicher: false });
    expect(await erkenneModus(antwort(500, '{"x":1}', 'application/json'), loc('?p=/data/x/'))).toEqual({ art: 'einzeln', sicher: false });
  });
  it('anderer 4xx ohne Dienstkennung (z. B. Vorschaltfilter 403/429) → nicht sicher', async () => {
    for (const status of [400, 403, 407, 429]) {
      expect(await erkenneModus(antwort(status, '<html>gesperrt</html>', 'text/html'), loc('?p=/data/x/')), String(status)).toEqual({ art: 'einzeln', sicher: false });
      expect(await erkenneModus(antwort(status, '{"x":1}', 'application/json'), loc('?p=/data/x/')), `${status} json`).toEqual({ art: 'einzeln', sicher: false });
    }
  });
  it('404 als JSON ohne Dienstkennung bleibt sicherer Einzelmodus', async () => {
    expect(await erkenneModus(antwort(404, '{"x":1}', 'application/json'), loc(''))).toEqual({ art: 'einzeln', sicher: true });
  });
  it('JSON-Typ mit kaputtem Körper → nicht sicher', async () => {
    expect(await erkenneModus(antwort(200, '{kaputt', 'application/json'), loc('?p=/data/x/'))).toEqual({ art: 'einzeln', sicher: false });
  });
  it('fragt GET /api/sitzung mit einem AbortSignal ab (Timeout-Regel)', async () => {
    let gotInput: unknown;
    let gotSignal: AbortSignal | undefined;
    const f = (async (input: unknown, init?: RequestInit) => {
      gotInput = input;
      gotSignal = init?.signal ?? undefined;
      return new Response('{"dienst":"scanview","nutzer":{"name":"T"}}', { status: 200, headers: { 'content-type': 'application/json' } });
    }) as unknown as typeof fetch;
    await erkenneModus(f, loc('?p=/data/x/'));
    expect(gotInput).toBe('/api/sitzung');
    expect(gotSignal).toBeInstanceOf(AbortSignal);
  });
});
