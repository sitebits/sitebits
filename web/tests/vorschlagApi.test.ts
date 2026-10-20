import { afterEach, describe, expect, it, vi } from 'vitest';
import { annehmen, verwerfen, fadenAnnehmen, fadenVerwerfen, zuPruefen } from '../src/vorschlagApi';
import { PlattformFehler } from '../src/plattformApi';
afterEach(() => vi.unstubAllGlobals());
describe('Prüf-API', () => {
  it('kodiert Pfade und sendet Version und CSRF-Kopf', async () => {
    const f = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ id: 'P1', version: 4 }));
    await annehmen('pois', 'p /', 'P /', 3, f);
    expect(f).toHaveBeenCalledWith('/api/v1/projekte/p%20%2F/pois/P%20%2F/annehmen', expect.objectContaining({ method: 'POST', body: '{"version":3}', headers: { 'X-Scanview': '1', 'Content-Type': 'application/json' }, credentials: 'same-origin' }));
    f.mockResolvedValue(new Response(null, { status: 204 }));
    expect(await verwerfen('messungen', 'p', 'M1', 3, f)).toBeNull();
    expect(f.mock.calls[1]?.[0]).toBe('/api/v1/projekte/p/messungen/M1/verwerfen');
  });
  it('409 ohne Stand bewahrt Status und Servertext in PlattformFehler', async () => {
    const f = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ fehler: 'Vorschlag bereits erledigt.' }, { status: 409 }));
    await expect(annehmen('pois', 'p', 'P1', 1, f)).rejects.toMatchObject({ name: 'PlattformFehler', status: 409, message: 'Vorschlag bereits erledigt.', aktuell: undefined });
    await expect(verwerfen('pois', 'p', 'P1', 1, f)).rejects.toBeInstanceOf(PlattformFehler);
  });
  it('Faden-Pfade, optionale Begründung und Sammelliste', async () => {
    const f = vi.fn<typeof fetch>().mockImplementation(async () => Response.json({}));
    vi.stubGlobal('fetch', f);
    await fadenAnnehmen('p', 'P1', '0123456789abcdef', 7);
    await fadenVerwerfen('p', 'P1', '0123456789abcdef', 'Schon erledigt');
    await fadenVerwerfen('p', 'P1', '0123456789abcdef');
    await zuPruefen('p');
    expect(f.mock.calls.map(([url, init]) => [url, init?.body])).toEqual([
      ['/api/v1/projekte/p/pois/P1/faden/0123456789abcdef/annehmen', '{"version":7}'],
      ['/api/v1/projekte/p/pois/P1/faden/0123456789abcdef/verwerfen', '{"text":"Schon erledigt"}'],
      ['/api/v1/projekte/p/pois/P1/faden/0123456789abcdef/verwerfen', '{}'],
      ['/api/v1/projekte/p/zu-pruefen', undefined],
    ]);
  });
});
it.each([403, 409, 422])('zeigt Servertext bei Prüf-Fehler %s', async (status) => {
  const f = vi.fn<typeof fetch>().mockImplementation(async () => Response.json({ fehler: 'Prüfung nicht möglich.' }, { status }));
  await expect(annehmen('pois', 'p', 'P1', 1, f)).rejects.toMatchObject({ status, message: 'Prüfung nicht möglich.' });
});
