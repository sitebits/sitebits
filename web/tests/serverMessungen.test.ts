import { describe, expect, it } from 'vitest';
import { MeasureStore } from '../src/measureStore';
import { ABGLEICH_TEXT } from '../src/abgleich';
import { LOKALE_MESSUNGEN, gleicheGeometrie, MessungSync } from '../src/serverMessungen';
import { fakePlattform, fakeUhr } from './fakePlattform';

function aufbau(schreibbar = true, o: { verliereErstenPost?: boolean } = {}) {
  const f = fakePlattform();
  const u = fakeUhr();
  const meldungen: string[] = [];
  const store = new MeasureStore();
  const koerper: unknown[] = [];
  let verlieren = o.verliereErstenPost === true;
  // Zeichnet POST-Körper auf; verliert auf Wunsch die Antwort des ersten POST (Server hat ihn verarbeitet).
  const fetchFn = (async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'POST') koerper.push(JSON.parse(String(init.body)));
    const r = await f.fetchFn(input, init);
    if (verlieren && init?.method === 'POST') {
      verlieren = false;
      throw new TypeError('Antwort verloren');
    }
    return r;
  }) as typeof fetch;
  const sync = new MessungSync({ store, projectId: 'p1', schreibbar, melde: (t) => meldungen.push(t), fetchFn, uhr: u.uhr });
  return { f, u, meldungen, store, sync, koerper };
}
const schreibend = (a: string[]) => a.filter((x) => !x.startsWith('GET'));

describe('MessungSync', () => {
  it('ordnet Link-Messungen Servermessungen zu, übrige bleiben lokal; Öffnen schreibt nie', async () => {
    const t = aufbau();
    t.f.eintrag('messungen', 'M-s1', { kind: 'distance', points: [[0, 0, 0], [3, 4, 0]] });
    t.f.eintrag('messungen', 'M-s2', { kind: 'height', points: [[0, 0, 0], [0, 0, 2]] });
    t.store.add([0, 0, 0.0004], [3, 4, 0]); // aus dem Link, auf mm gerundet
    t.store.add([10, 10, 10], [11, 10, 10]); // nur im Link
    const hinweis = await t.sync.laden();
    expect(t.store.list().map((m) => m.kind)).toEqual(['distance', 'distance', 'height']);
    expect(hinweis).toMatch(/1 Messung\(en\) aus dem Link nur lokal/);
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(schreibend(t.f.aufrufe)).toEqual([]);
  });

  it('neue Messung → POST mit Client-ID, Verschieben → PATCH, Löschen → DELETE', async () => {
    const t = aufbau();
    await t.sync.laden();
    const m = t.store.addGeometry('area', [[0, 0, 0], [4, 0, 0], [4, 3, 0]]);
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    const [e] = t.f.eintraege('messungen');
    expect(e!.id).toMatch(/^M-[0-9a-f]{12}$/);
    expect((t.koerper[0] as { id: string }).id).toBe(e!.id);
    expect(e!.daten.kind).toBe('area');
    expect(e!.daten.areaM2).toBeCloseTo(6);
    t.store.moveVertex(m.id, 2, [4, 4, 0]);
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(schreibend(t.f.aufrufe)).toEqual(['POST /api/v1/projekte/p1/messungen', `PATCH /api/v1/projekte/p1/messungen/${e!.id}`]);
    t.store.remove(m.id);
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(t.f.eintraege('messungen')).toEqual([]);
    expect(t.sync.offen()).toBe(0);
  });

  it('wiederholter POST nach verlorener Antwort: gleiche ID, keine Doppelung, kein Konflikt', async () => {
    const t = aufbau(true, { verliereErstenPost: true });
    await t.sync.laden();
    t.store.add([0, 0, 0], [1, 0, 0]);
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline]);
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline, ABGLEICH_TEXT.wieder]);
    expect(t.koerper).toHaveLength(2);
    expect((t.koerper[1] as { id: string }).id).toBe((t.koerper[0] as { id: string }).id);
    expect(t.f.eintraege('messungen')).toHaveLength(1);
    expect(t.sync.offen()).toBe(0);
  });

  it('nach einer Konflikt-Übernahme löscht Rückgängig nicht die Fassung des Kollegen', async () => {
    const t = aufbau();
    let gezeichnet = 0;
    t.sync.setNeuZeichnen(() => gezeichnet++);
    await t.sync.laden();
    const m = t.store.add([0, 0, 0], [1, 0, 0]);
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    const [e] = t.f.eintraege('messungen');
    t.f.aendereDirekt('messungen', e!.id, { kind: 'distance', points: [[0, 0, 0], [2, 0, 0]] });
    t.store.moveVertex(m.id, 1, [3, 0, 0]);
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.konflikt]);
    expect(gezeichnet).toBe(1);
    expect(t.store.list().map((x) => x.points[1])).toEqual([[2, 0, 0]]);
    t.store.undo();
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(schreibend(t.f.aufrufe).filter((a) => a.startsWith('DELETE'))).toEqual([]);
    expect(t.f.eintraege('messungen')).toHaveLength(1);
    expect(t.store.list()).toHaveLength(1);
  });

  it('flush(): sofort, ohne Sammelfenster', async () => {
    const t = aufbau();
    await t.sync.laden();
    t.store.add([0, 0, 0], [1, 0, 0]);
    t.sync.geaendert();
    await t.sync.flush();
    expect(t.u.timer).toEqual([]);
    expect(t.f.eintraege('messungen')).toHaveLength(1);
  });

  it('Ohne Recht auf gemeinsame Messungen: kein Schreiben, genau ein Hinweis', async () => {
    const t = aufbau(false);
    await t.sync.laden();
    t.store.add([0, 0, 0], [1, 0, 0]);
    t.sync.geaendert();
    t.sync.geaendert();
    expect(t.meldungen).toEqual([LOKALE_MESSUNGEN]);
    expect(LOKALE_MESSUNGEN).not.toMatch(/Betrachter/);
    expect(t.u.timer).toEqual([]);
    expect(schreibend(t.f.aufrufe)).toEqual([]);
  });

  it('Servermessung mit ungültiger Geometrie wird nicht angezeigt und gemeldet', async () => {
    const t = aufbau();
    t.f.eintrag('messungen', 'M-x', { kind: 'area', points: [[0, 0, 0], [1, 0, 0], [2, 0, 0]] });
    expect(await t.sync.laden()).toMatch(/1 Messung\(en\) des Projekts mit ungültiger Geometrie/);
    expect(t.store.list()).toEqual([]);
  });

  it('gleicheGeometrie: Toleranz 2 mm, Art und Punktzahl müssen passen', () => {
    const s = new MeasureStore();
    const m = s.add([0, 0, 0], [1, 0, 0]);
    expect(gleicheGeometrie(m, { kind: 'distance', points: [[0, 0, 0.0019], [1, 0, 0]] })).toBe(true);
    expect(gleicheGeometrie(m, { kind: 'distance', points: [[0, 0, 0.0021], [1, 0, 0]] })).toBe(false);
    expect(gleicheGeometrie(m, { kind: 'height', points: [[0, 0, 0], [1, 0, 0]] })).toBe(false);
  });
});
