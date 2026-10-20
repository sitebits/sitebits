import { describe, expect, it } from 'vitest';
import type { Vec3 } from '../src/manifest';
import { decodeHash, encodeHash, HASH_MAX_VERTICES } from '../src/hash';
import { hashGeometrie } from '../src/hashGeometrie';
import { MeasureStore } from '../src/measureStore';
import { MessungSync } from '../src/serverMessungen';
import { fakePlattform, fakeUhr } from './fakePlattform';

const origin: Vec3 = [1000, 2000, 300];

describe('hashGeometrie', () => {
  it('Einzelmodus (ohne Filter): alle Messungen in Anlegereihenfolge, wie bisher', () => {
    const s = new MeasureStore();
    s.add([1000, 2000, 300], [1001, 2000, 300]);
    s.addGeometry('area', [[1000, 2000, 300], [1004, 2000, 300], [1004, 2003, 300]]);
    s.add([1002, 2000, 300], [1002, 2001, 300]);
    const g = hashGeometrie(s.list());
    expect(g.order).toEqual(['distance', 'area', 'distance']);
    expect(g.m).toHaveLength(2);
    expect(g.g).toEqual([{ kind: 'area', points: [[1000, 2000, 300], [1004, 2000, 300], [1004, 2003, 300]] }]);
    const zurueck = decodeHash(encodeHash({ cam: [0, 0, 0], tgt: [1, 1, 1], ...g }, origin), origin);
    expect(zurueck.order).toEqual(g.order);
    expect(zurueck.m).toEqual(g.m);
  });

  it('Filter: m, g und o nur aus den ungebundenen Messungen, Link bleibt gültig', () => {
    const s = new MeasureStore();
    const a = s.add([1000, 2000, 300], [1001, 2000, 300]);
    const b = s.addGeometry('polyline', [[1000, 2000, 300], [1001, 2001, 300]]);
    const c = s.addGeometry('height', [[1000, 2000, 300], [1000, 2000, 302]]);
    const d = s.add([1003, 2000, 300], [1003, 2001, 300]);
    const gebunden = new Set([a.id, c.id]);
    const g = hashGeometrie(s.list(), (m) => !gebunden.has(m.id));
    expect(g).toEqual({ m: [[d.a, d.b]], g: [{ kind: 'polyline', points: b.points }], order: ['polyline', 'distance'] });
    const zurueck = decodeHash(encodeHash({ cam: [0, 0, 0], tgt: [1, 1, 1], ...g }, origin), origin);
    expect(zurueck.order).toEqual(['polyline', 'distance']);
    expect(zurueck.g).toHaveLength(1);
    expect(zurueck.m).toHaveLength(1);
  });
});

describe('Plattformmodus: Serverstand gehört nicht in den Link', () => {
  function aufbau(schreibbar = true) {
    const f = fakePlattform();
    const u = fakeUhr();
    const store = new MeasureStore();
    const sync = new MessungSync({ store, projectId: 'p1', schreibbar, melde: () => undefined, fetchFn: f.fetchFn, uhr: u.uhr });
    return { f, u, store, sync };
  }

  it('Servermessungen stehen nicht im Link, lokale (nur im Link) schon', async () => {
    const t = aufbau();
    t.f.eintrag('messungen', 'M-s1', { kind: 'distance', points: [[0, 0, 0], [3, 4, 0]] });
    t.store.add([10, 10, 10], [11, 10, 10]); // nur im Link, kein Gegenstück am Server
    await t.sync.laden();
    const g = hashGeometrie(t.store.list(), (m) => t.sync.imLink(m));
    expect(g.m).toEqual([[[10, 10, 10], [11, 10, 10]]]);
    expect(g.order).toEqual(['distance']);
  });

  it('viele Servermessungen sprengen den Link nicht (> HASH_MAX_VERTICES Eckpunkte)', async () => {
    const t = aufbau();
    const pts: [number, number, number][] = Array.from({ length: HASH_MAX_VERTICES + 10 }, (_, i) => [i, 0, 0]);
    t.f.eintrag('messungen', 'M-gross', { kind: 'polyline', points: pts });
    t.store.addGeometry('area', [[0, 0, 0], [4, 0, 0], [4, 3, 0]]); // lokale Messung aus dem Link
    await t.sync.laden();
    expect(t.store.list()).toHaveLength(2);
    const g = hashGeometrie(t.store.list(), (m) => t.sync.imLink(m));
    const zurueck = decodeHash(encodeHash({ cam: [0, 0, 0], tgt: [1, 1, 1], ...g }, origin), origin);
    expect(zurueck.g).toEqual([{ kind: 'area', points: [[0, 0, 0], [4, 0, 0], [4, 3, 0]] }]);
    // Ohne Filter (alter Stand) verwürfe decodeHash den ganzen Link:
    expect(decodeHash(encodeHash({ cam: [0, 0, 0], tgt: [1, 1, 1], ...hashGeometrie(t.store.list()) }, origin), origin)).toEqual({});
  });

  it('neue Messung: bis zur Bestätigung im Link, danach nicht mehr', async () => {
    const t = aufbau();
    await t.sync.laden();
    const m = t.store.add([0, 0, 0], [1, 0, 0]);
    expect(t.sync.imLink(m)).toBe(true);
    t.f.offline = true; // POST geht raus, Antwort bleibt aus → weiter im Link
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(t.sync.imLink(t.store.list()[0]!)).toBe(true);
    t.f.offline = false;
    await t.sync.abgleich.lauf();
    expect(t.sync.imLink(t.store.list()[0]!)).toBe(false);
  });

  it('bearbeitete gebundene Messung: im Link, bis der Server die Änderung bestätigt', async () => {
    const t = aufbau();
    t.f.eintrag('messungen', 'M-s1', { kind: 'distance', points: [[0, 0, 0], [3, 4, 0]] });
    await t.sync.laden();
    const [m] = t.store.list();
    expect(t.sync.imLink(m!)).toBe(false);
    t.store.moveVertex(m!.id, 1, [6, 8, 0]);
    expect(t.sync.imLink(t.store.list()[0]!)).toBe(true);
    t.f.offline = true;
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(t.sync.imLink(t.store.list()[0]!)).toBe(true);
    t.f.offline = false;
    await t.sync.abgleich.lauf();
    expect(t.sync.imLink(t.store.list()[0]!)).toBe(false);
  });

  it('Betrachter: eigene Messungen im Link, Servermessungen nicht', async () => {
    const t = aufbau(false);
    t.f.eintrag('messungen', 'M-s1', { kind: 'distance', points: [[0, 0, 0], [3, 4, 0]] });
    await t.sync.laden();
    t.store.add([5, 5, 5], [6, 5, 5]);
    const g = hashGeometrie(t.store.list(), (m) => t.sync.imLink(m));
    expect(g.m).toEqual([[[5, 5, 5], [6, 5, 5]]]);
  });

  it('Link-Messung, die einer Servermessung entspricht, übernimmt deren exakte Koordinaten (keine mm-Rundung zurück)', async () => {
    const t = aufbau();
    t.f.eintrag('messungen', 'M-s1', { kind: 'distance', points: [[0.00012, 0, 0], [3, 4.00031, 0]] });
    const ausLink = t.store.add([0, 0, 0], [3, 4, 0]);
    await t.sync.laden();
    const [m] = t.store.list();
    expect(m!.id).toBe(ausLink.id);
    expect(m!.points).toEqual([[0.00012, 0, 0], [3, 4.00031, 0]]);
    t.sync.geaendert();
    t.u.alle();
    await t.sync.abgleich.lauf();
    expect(t.f.aufrufe.filter((a) => !a.startsWith('GET'))).toEqual([]);
  });
});
