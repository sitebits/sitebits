import { describe, expect, it, vi } from 'vitest';
import { Abgleich } from '../src/abgleich';
import type { ServerEintrag, PoiDaten, MessungDaten } from '../src/plattformApi';
import { PoiStore, PoiPersistence } from '../src/poiStore';
import { MeasureStore } from '../src/measureStore';
import { measurementsCsv } from '../src/measureCsv';
import { encodeHash, decodeHash } from '../src/hash';
import { vorschlagAnzeige } from '../src/vorschlagApi';
import { ServerPoiPersistenz } from '../src/serverPois';
import { MessungSync } from '../src/serverMessungen';
const meta = { herkunft: 'ki' as const, loeschen: false };
const daten: PoiDaten = { title: 'Prüfpunkt', category: '', note: '', position: [1, 2, 3], station: null };
const eintrag = <D>(d: D, version = 1): ServerEintrag<D> => ({ id: 'P1', version, daten: d, erstelltVon: 1, erstelltAm: 1, geaendertVon: 1, geaendertVonName: 'KI', geaendertAm: 1, paketRevision: 1, pruefstand: 'vorschlag', herkunft: 'ki' });
describe('Vorschläge im Viewer', () => {
  it('Hash merkt Ausschalten und ignoriert unbekannte Werte', () => {
    expect(encodeHash({ kv: false }, [0, 0, 0])).toBe('#kv=0');
    expect(decodeHash('#kv=0', [0, 0, 0])).toEqual({ kv: false });
    expect(decodeHash('#kv=1', [0, 0, 0])).toEqual({});
  });
  it('Kennzeichnung bleibt rückwärtskompatibel und erkennt Löschvorschläge mit Zeit 0', () => {
    expect(vorschlagAnzeige({})).toBeUndefined();
    expect(vorschlagAnzeige({ pruefstand: 'gueltig', loeschenVorgeschlagen: 0 })).toEqual({ ...meta, loeschen: true });
  });
  it('POI-Export und Browserpersistenz enthalten keine Vorschläge', () => {
    const s = new PoiStore('p');
    const a = s.add(daten);
    a.vorschlag = meta;
    const b = s.add({ ...daten, title: 'Gültig' });
    expect(s.toFile(1).pois.map(p => p.id)).toEqual([b.id]);
    const set = vi.fn();
    new PoiPersistence({ getItem: () => null, setItem: set }, 'k').save(s, 1);
    expect(String(set.mock.calls[0]?.[1])).not.toContain('KI');
    expect((JSON.parse(String(set.mock.calls[0]?.[1])) as {
      pois: unknown[];
    }).pois).toHaveLength(1);
  });
  it('Mess-CSV und JSON enthalten nur gültige Messungen, auch nach Geometrieänderung', () => {
    const s = new MeasureStore();
    const a = s.add([0, 0, 0], [1, 0, 0]);
    a.vorschlag = meta;
    const b = s.add([0, 0, 0], [2, 0, 0]);
    s.moveVertex(a.id, 1, [3, 0, 0]);
    expect(measurementsCsv(s.list())).not.toContain(a.id + ';');
    expect(measurementsCsv(s.list())).toContain(b.id + ';');
    expect(s.toJSON({ projectId: 'p', revision: 1 })).toMatchObject({ measurements: [{ id: b.id }] });
  });
  it.each([null, eintrag({ wert: 'gültig' }, 2)])('Prüfentscheidungen werden nicht zurückgespielt: %j', async (aktuell) => {
    let liste = [{ id: 'L', wert: 'vorschlag' }];
    const senden = vi.fn();
    const a = new Abgleich<{
      id: string;
      wert: string;
    }, {
      wert: string;
    }>({ liste: () => liste, schluessel: x => x.id, daten: x => ({ wert: x.wert }), clientId: x => x.id, uebernehmen: (id, e) => { liste = e ? [{ id, wert: e.daten.wert }] : []; return e ? id : null; } }, { anlegen: senden, aendern: senden, loeschen: senden }, () => { });
    a.kenne('L', 'P1', 1, liste[0]!);
    a.geprueft('L', aktuell);
    await a.sofort();
    expect(a.offen()).toBe(0);
    expect(senden).not.toHaveBeenCalled();
    expect(a.version('L')).toBe(aktuell?.version);
  });
  it('POI-Adapter fragt Vorschläge an, übernimmt Annahme und entfernt Verwerfen ohne DELETE', async () => {
    let e = eintrag(daten);
    const f = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
      if (init?.method === 'GET')
        return Response.json({ pois: [e] });
      if (String(url).endsWith('/annehmen')) {
        e = { ...e, version: 2, pruefstand: 'gueltig' };
        return Response.json(e);
      }
      if (String(url).endsWith('/verwerfen'))
        return new Response(null, { status: 204 });
      throw new Error('Unerlaubtes Zurückspielen');
    });
    const store = new PoiStore('p');
    const sp = new ServerPoiPersistenz({ projectId: 'p', schreibbar: true, vorschlaege: true, lokal: new PoiPersistence(null, 'k'), melde: () => { }, fetchFn: f });
    await sp.load(store);
    expect(f.mock.calls[0]?.[0]).toContain('?vorschlaege=1');
    expect(store.get('P1')?.vorschlag).toEqual(meta);
    await sp.pruefen('P1', 'annehmen');
    expect(store.get('P1')?.vorschlag).toBeUndefined();
    expect(sp.abgleich.version('P1')).toBe(2);
    await sp.pruefen('P1', 'verwerfen');
    await sp.abgleich.sofort();
    expect(store.list()).toEqual([]);
    expect(sp.offen()).toBe(0);
    expect(f).toHaveBeenCalledTimes(3);
  });
  it('Messadapter übernimmt Prüfung und leert Undo, ohne erneut zu senden', async () => {
    const d: MessungDaten = { kind: 'distance', points: [[0, 0, 0], [1, 0, 0]] };
    const f = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => init?.method === 'GET' ? Response.json({ messungen: [eintrag(d)] }) : new Response(null, { status: 204 }));
    const store = new MeasureStore();
    const sync = new MessungSync({ store, projectId: 'p', schreibbar: true, vorschlaege: true, melde: () => { }, fetchFn: f });
    await sync.laden();
    expect(store.list()[0]?.vorschlag).toEqual(meta);
    await sync.pruefen(store.list()[0]!.id, 'verwerfen');
    await sync.abgleich.sofort();
    expect(store.list()).toEqual([]);
    expect(store.canUndo()).toBe(false);
    expect(f).toHaveBeenCalledTimes(2);
  });
});
it('Löschvorschläge lassen den gültigen Befund im Export, aber ohne Prüfmetadaten', () => {
  const pois = new PoiStore('p');
  const p = pois.add(daten);
  p.vorschlag = { ...meta, loeschen: true };
  expect(pois.toFile(1).pois).toEqual([expect.objectContaining({ id: p.id, title: 'Prüfpunkt' })]);
  expect(JSON.stringify(pois.toFile(1))).not.toContain('vorschlag');
  const messungen = new MeasureStore();
  const m = messungen.add([0, 0, 0], [1, 0, 0]);
  m.vorschlag = { ...meta, loeschen: true };
  expect(measurementsCsv(messungen.list())).toContain(m.id + ';');
  expect(JSON.stringify(messungen.toJSON({ projectId: 'p', revision: 1 }))).not.toContain('vorschlag');
});

it('Alle löschen behält Vorschläge und bleibt mit einem einzigen Undo rückgängig', () => {
  const s = new MeasureStore();
  const a = s.add([0, 0, 0], [1, 0, 0]); a.vorschlag = meta;
  const b = s.add([0, 0, 0], [2, 0, 0]); const c = s.add([0, 0, 0], [3, 0, 0]);
  s.resetHistory(); s.clear(true);
  expect(s.list().map(m => m.id)).toEqual([a.id]);
  expect(s.undo()).toBe(true); expect(s.list().map(m => m.id)).toEqual([a.id, b.id, c.id]);
  expect(s.canUndo()).toBe(false);
});

import { fakeUhr } from './fakePlattform';
it.each([0, 422])('Prüfung überschreibt bei Störung/Ablehnung %s keine lokale Änderung', async status => {
  const u = fakeUhr();
  const f = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
    if (init?.method === 'GET') return Response.json({ pois: [eintrag(daten)] });
    if (status === 0) throw new TypeError('offline');
    return Response.json({ fehler: 'Änderung abgelehnt.' }, { status });
  });
  const store = new PoiStore('p');
  const sp = new ServerPoiPersistenz({ projectId: 'p', schreibbar: true, vorschlaege: true, lokal: new PoiPersistence(null, 'k'), melde: () => {}, fetchFn: f, uhr: u.uhr });
  await sp.load(store); store.update('P1', { title: 'Lokal geändert' });
  await expect(sp.pruefen('P1', 'annehmen')).rejects.toThrow('Noch nicht gespeicherte Änderungen');
  expect(store.get('P1')?.title).toBe('Lokal geändert');
  expect(f.mock.calls.some(([url]) => String(url).endsWith('/annehmen'))).toBe(false);
});
