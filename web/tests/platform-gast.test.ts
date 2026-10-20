// Nur-Lese-Link (F28): erkenneModus() erkennt den Gast aus /api/sitzung, gastFetch() lenkt nur das
// Manifest auf das bereinigte /api/teilen/manifest um.
import { describe, expect, it } from 'vitest';
import { erkenneModus, gastFetch, paketId } from '../src/platform';

const json = (body: unknown) =>
  (async () => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })) as unknown as typeof fetch;
const loc = (search: string) => ({ pathname: '/ansicht', search, hash: '' });
const GAST = { projekt: 'ost', name: 'Gast Ost', ablauf: 1_800_000_000 };

describe('Gast-Ansicht', () => {
  it('nur Gast, passendes Paket → Einzelmodus mit gast', async () => {
    const m = await erkenneModus(json({ dienst: 'scanview', nutzer: null, gast: GAST }), loc('?p=/data/ost/&gast=1'));
    expect(m).toEqual({ art: 'einzeln', sicher: false, gast: GAST });
    // auch ohne gast=1 (Neuladen, Lesezeichen)
    expect(await erkenneModus(json({ dienst: 'scanview', nutzer: null, gast: GAST }), loc('?p=/data/ost/'))).toEqual(m);
  });
  it('nur Gast, anderes Paket → Anmeldung', async () => {
    const m = await erkenneModus(json({ dienst: 'scanview', nutzer: null, gast: GAST }), loc('?p=/data/kirche/'));
    expect(m).toEqual({ art: 'weiterleiten', ziel: '/anmelden?weiter=%2Fansicht%3Fp%3D%2Fdata%2Fkirche%2F' });
  });
  it('mit Konto: Gast nur bei gast=1', async () => {
    const body = { dienst: 'scanview', nutzer: { id: 3, name: 'K' }, gast: GAST };
    expect(await erkenneModus(json(body), loc('?p=/data/ost/'))).toEqual({ art: 'plattform', name: 'K', nutzerId: 3 });
    expect(await erkenneModus(json(body), loc('?p=/data/ost/&gast=1'))).toEqual({ art: 'einzeln', sicher: false, gast: GAST });
  });
  it('kaputter gast-Block zählt nicht', async () => {
    for (const gast of [null, 'x', { projekt: 'ost' }, { ...GAST, ablauf: 'morgen' }]) {
      const m = await erkenneModus(json({ dienst: 'scanview', nutzer: null, gast }), loc('?p=/data/ost/'));
      expect(m.art, JSON.stringify(gast)).toBe('weiterleiten');
    }
  });
  it('paketId', () => {
    expect(paketId('/data/ost/')).toBe('ost');
    expect(paketId('/data/ost')).toBe('ost');
    for (const p of ['/data/Ost/', '/data/ost/x/', 'data/ost/', '/data/../ost/', '/data/ost/?x']) expect(paketId(p), p).toBeNull();
  });
  it('gastFetch lenkt nur project.json um', async () => {
    const gerufen: string[] = [];
    const f = (async (u: RequestInfo | URL) => { gerufen.push(String(u)); return new Response('{}'); }) as unknown as typeof fetch;
    const g = gastFetch(f);
    await g('http://localhost:3000/data/ost/project.json');
    await g('http://localhost:3000/data/ost/cloud.copc.laz');
    expect(gerufen).toEqual(['/api/teilen/manifest', 'http://localhost:3000/data/ost/cloud.copc.laz']);
  });
});
