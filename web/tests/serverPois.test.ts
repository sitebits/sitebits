import { describe, expect, it } from 'vitest';
import { ABGLEICH_TEXT } from '../src/abgleich';
import { PoiError, PoiPersistence, PoiStore } from '../src/poiStore';
import type { Poi } from '../src/poiStore';
import type { PoiDaten, ServerEintrag } from '../src/plattformApi';
import { ServerPoiPersistenz } from '../src/serverPois';
import { WartendeAnlagen } from '../src/rueckmeldung';
import type { AnlageFolge } from '../src/rueckmeldung';
import { fakePlattform, fakeUhr } from './fakePlattform';
import type { FakePlattform } from './fakePlattform';

const TUER = { title: 'Tür', category: '', note: '', position: [1, 2, 3], station: null };
const NEU = { title: 'Neu', category: '', note: '', position: [0, 0, 0] as [number, number, number], station: null };

function aufbau(
  schreibbar: boolean,
  o: { paket?: Poi[]; verliereErstenPost?: boolean; verliereErstenPatch?: boolean; eigen?: (e: ServerEintrag<PoiDaten>) => boolean; vorher?: (f: FakePlattform) => void } = {},
) {
  const f = fakePlattform();
  f.eintrag('pois', 'P-1', TUER);
  o.vorher?.(f);
  const echt = f.fetchFn;
  const keepalive: boolean[] = [];
  const verlieren = new Set<string>([...(o.verliereErstenPost ? ['POST'] : []), ...(o.verliereErstenPatch ? ['PATCH'] : [])]);
  // Zeichnet keepalive auf; verliert auf Wunsch die Antwort des ersten POST/PATCH (Server hat ihn verarbeitet).
  const fetchFn = (async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method && init.method !== 'GET') keepalive.push(init.keepalive === true);
    const r = await echt(input, init);
    if (init?.method && verlieren.delete(init.method)) throw new TypeError('Antwort verloren');
    return r;
  }) as typeof fetch;
  const u = fakeUhr();
  const meldungen: string[] = [];
  const speicher = new Map<string, string>();
  const lokal = new PoiPersistence({ getItem: (k) => speicher.get(k) ?? null, setItem: (k, v) => void speicher.set(k, v) }, 'scanview:pois:p1');
  const store = new PoiStore('p1', () => 'P-neu');
  store.setPackagePois(o.paket ?? []);
  const sp = new ServerPoiPersistenz({ projectId: 'p1', schreibbar, eigen: o.eigen, lokal, melde: (t) => meldungen.push(t), fetchFn, uhr: u.uhr });
  return { f, u, meldungen, speicher, store, sp, keepalive };
}

describe('ServerPoiPersistenz', () => {
  it('Bearbeiter: Server-POIs werden eigene, neue POIs landen auf dem Server', async () => {
    const t = aufbau(true);
    expect(await t.sp.load(t.store)).toEqual({ loaded: 1, renamed: 0, warning: null });
    expect(t.store.get('P-1')?.source).toBe('browser');
    t.store.add(NEU);
    expect(t.sp.save(t.store, 1)).toBeNull();
    expect(t.sp.offen()).toBe(1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.f.aufrufe).toContain('POST /api/v1/projekte/p1/pois');
    expect(t.f.eintraege('pois').map((e) => e.id).sort()).toEqual(['P-1', 'P-neu']);
    expect(t.sp.offen()).toBe(0);
    expect(t.speicher.size).toBe(0); // localStorage bleibt unberührt
  });

  it('Betrachter: Server-POIs schreibgeschützt, eigene POIs nur lokal', async () => {
    const t = aufbau(false);
    await t.sp.load(t.store);
    expect(t.store.get('P-1')?.source).toBe('package');
    expect(() => t.store.remove('P-1')).toThrow(PoiError);
    t.store.add(NEU);
    expect(t.sp.save(t.store, 1)).toBeNull();
    expect(t.speicher.get('scanview:pois:p1')).toContain('Neu');
    expect(t.f.aufrufe.filter((a) => !a.startsWith('GET'))).toEqual([]);
  });

  it('Laden scheitert → Bearbeiter bekommt Hinweis, Speichern ist gesperrt', async () => {
    const t = aufbau(true);
    t.f.offline = true;
    const r = await t.sp.load(t.store);
    expect(r.warning).toMatch(/nicht geladen/);
    expect(t.sp.save(t.store, 1)).toMatch(/neu laden/);
  });

  it('Server-POI mit Paket-ID: lokal umbenannt, Öffnen schreibt nicht, Bearbeiten ändert den Server-POI', async () => {
    const t = aufbau(true, { paket: [{ id: 'P-1', title: 'Paket', category: '', note: '', position: [9, 9, 9], station: null, source: 'package' }] });
    const r = await t.sp.load(t.store);
    expect(r).toEqual({ loaded: 1, renamed: 0, warning: null });
    const eigene = t.store.list().filter((p) => p.source === 'browser');
    expect(eigene.map((p) => [p.id, p.title])).toEqual([['P-neu', 'Tür']]);
    expect(t.sp.save(t.store, 1)).toBeNull();
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.f.aufrufe.filter((a) => !a.startsWith('GET'))).toEqual([]);
    expect(t.sp.offen()).toBe(0);
    t.store.update('P-neu', { title: 'Tür neu' });
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.f.aufrufe.filter((a) => !a.startsWith('GET'))).toEqual(['PATCH /api/v1/projekte/p1/pois/P-1']);
    expect(t.f.eintraege('pois').map((e) => [e.id, e.daten.title])).toEqual([['P-1', 'Tür neu']]);
  });

  it('wiederholter POST nach verlorener Antwort legt nichts doppelt an und meldet keinen Konflikt', async () => {
    const t = aufbau(true, { verliereErstenPost: true });
    await t.sp.load(t.store);
    t.store.add(NEU);
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline]);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.offline, ABGLEICH_TEXT.wieder]);
    expect(t.f.aufrufe.filter((a) => a.startsWith('POST'))).toHaveLength(2);
    expect(t.f.eintraege('pois').map((e) => e.id).sort()).toEqual(['P-1', 'P-neu']);
    expect(t.sp.offen()).toBe(0);
  });

  it('verlorene Antwort auf POST: nach der Bestätigung per 409 sind die Metadaten des neuen POIs da', async () => {
    const t = aufbau(true, { verliereErstenPost: true });
    await t.sp.load(t.store);
    t.store.add(NEU);
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.sp.meta('P-neu')).toBeNull();
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.sp.bestaetigt('P-neu')).toBe(true);
    expect(t.sp.meta('P-neu')).toMatchObject({ serverId: 'P-neu', geaendertAm: 1, eigen: true });
    expect(t.sp.lokalId('P-neu')).toBe('P-neu');
  });

  it('verlorene Antwort auf PATCH: nach der Bestätigung per 409 ist die Änderungszeit nachgeführt', async () => {
    const t = aufbau(true, { verliereErstenPatch: true });
    await t.sp.load(t.store);
    expect(t.sp.meta('P-1')?.geaendertAm).toBe(1);
    t.store.update('P-1', { title: 'Geändert' });
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.f.aufrufe.filter((a) => a.startsWith('PATCH'))).toHaveLength(2);
    expect(t.meldungen).not.toContain(ABGLEICH_TEXT.konflikt);
    expect(t.sp.meta('P-1')).toMatchObject({ serverId: 'P-1', geaendertAm: 2, eigen: true });
  });

  it('flush(): sofort und mit keepalive (Seite wird verlassen)', async () => {
    const t = aufbau(true);
    await t.sp.load(t.store);
    t.store.add(NEU);
    t.sp.save(t.store, 1);
    await t.sp.flush();
    expect(t.u.timer).toEqual([]);
    expect(t.keepalive).toEqual([true]);
    expect(t.f.eintraege('pois').map((e) => e.id).sort()).toEqual(['P-1', 'P-neu']);
  });

  it('409: Stand des anderen Bearbeiters wird übernommen und neu gezeichnet', async () => {
    const t = aufbau(true);
    let gezeichnet = 0;
    t.sp.setNeuZeichnen(() => gezeichnet++);
    await t.sp.load(t.store);
    t.f.aendereDirekt('pois', 'P-1', { ...TUER, title: 'Fremd' });
    t.store.update('P-1', { title: 'Meins' });
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.store.get('P-1')?.title).toBe('Fremd');
    expect(gezeichnet).toBe(1);
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.konflikt]);
  });
});

describe('Eigene Änderung am Befund-Faden (Status, Zuständig, Frist erhöhen die Version am Server)', () => {
  it('die neue Version wird übernommen: das nächste Ändern scheitert nicht an 409', async () => {
    const t = aufbau(true);
    await t.sp.load(t.store);
    t.f.aendereDirekt('pois', 'P-1', TUER); // Statuswechsel: gleicher Inhalt, Version 2
    t.sp.standGeaendert('P-1', 2);
    t.store.update('P-1', { title: 'Meins' });
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.meldungen).toEqual([]);
    expect(t.f.eintraege('pois').find((e) => e.id === 'P-1')?.daten).toMatchObject({ title: 'Meins' });
  });
  it('mit offener lokaler Änderung bleibt die alte Version: der Konflikt klärt sich wie bisher', async () => {
    const t = aufbau(true);
    await t.sp.load(t.store);
    t.store.update('P-1', { title: 'Meins' });
    t.f.aendereDirekt('pois', 'P-1', TUER);
    t.sp.standGeaendert('P-1', 2);
    expect(t.sp.abgleich.version('P-1')).toBe(1);
  });
});

describe('ServerPoiPersistenz mit „nur eigene“ (Rückmelder)', () => {
  const RUECK = { title: 'Rückfrage', category: 'Rückmeldung', note: '', position: [4, 5, 6], station: null };
  const BEFUND = { title: 'Riss', category: 'Mangel', note: '', position: [7, 8, 9], station: null };
  const rueckmelder = (paket: Poi[] = []) =>
    aufbau(true, {
      paket,
      eigen: (e) => e.erstelltVon === 7,
      vorher: (f) => {
        f.nutzer = { id: 7, name: 'Rita' };
        f.eintrag('pois', 'R-7', RUECK, { id: 7, name: 'Rita' });
        f.eintrag('pois', 'B-8', BEFUND, { id: 8, name: 'Bernd' });
      },
    });
  const schreibend = (f: FakePlattform) => f.aufrufe.filter((a) => !a.startsWith('GET'));

  it('eigener Server-POI ist änderbar, fremder schreibgeschützt; Metadaten zu beiden', async () => {
    const t = rueckmelder();
    await t.sp.load(t.store);
    expect(t.store.get('R-7')?.source).toBe('browser');
    expect(t.store.get('B-8')?.source).toBe('package');
    expect(t.store.get('P-1')?.source).toBe('package'); // Urheber 1, nicht 7
    expect(t.sp.meta('B-8')).toEqual({ serverId: 'B-8', paketRevision: 1, erstelltVonName: 'Bernd', geaendertAm: 1, eigen: false });
    expect(t.sp.meta('R-7')).toEqual({ serverId: 'R-7', paketRevision: 1, erstelltVonName: 'Rita', geaendertAm: 1, eigen: true });
    expect(t.sp.meta('gibt-es-nicht')).toBeNull();
    expect(() => t.store.update('B-8', { title: 'übernommen' })).toThrow(PoiError);
    expect(() => t.store.remove('B-8')).toThrow(PoiError);
  });

  it('der Abgleich fasst fremde POIs nie an, eigene schon', async () => {
    const t = rueckmelder();
    await t.sp.load(t.store);
    expect(t.store.get('B-8')?.source).toBe('package');
    expect(t.sp.save(t.store, 1)).toBeNull();
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(schreibend(t.f)).toEqual([]);
    t.store.update('R-7', { note: 'ergänzt' });
    t.store.add({ ...NEU, category: 'Rückmeldung' });
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(schreibend(t.f).sort()).toEqual(['PATCH /api/v1/projekte/p1/pois/R-7', 'POST /api/v1/projekte/p1/pois']);
    expect(t.sp.offen()).toBe(0);
  });

  it('lokalId bildet eine wegen Paket-ID-Kollision umbenannte lokale ID zurück', async () => {
    const paket: Poi = { id: 'R-7', title: 'Paket', category: '', note: '', position: [0, 0, 1], station: null, source: 'package' };
    const t = rueckmelder([paket]);
    await t.sp.load(t.store);
    const lokal = t.sp.lokalId('R-7');
    expect(lokal).toBe('P-neu');
    expect(t.store.get('P-neu')?.title).toBe('Rückfrage');
    expect(t.sp.meta('P-neu')?.serverId).toBe('R-7');
    expect(t.sp.lokalId('B-8')).toBe('B-8');
    expect(t.sp.lokalId('fehlt')).toBeNull();
  });

  it('fremder Server-POI mit der ID eines Paket-POIs: lokal umbenannt, der Paket-POI hat keine Server-Angaben', async () => {
    const paket: Poi = { id: 'B-8', title: 'Paket', category: '', note: '', position: [0, 0, 1], station: null, source: 'package' };
    const t = rueckmelder([paket]);
    await t.sp.load(t.store);
    expect(t.store.get('B-8')?.title).toBe('Paket');
    expect(t.sp.meta('B-8')).toBeNull();
    const lokal = t.sp.lokalId('B-8')!;
    expect(lokal).not.toBe('B-8');
    expect(t.store.get(lokal)).toMatchObject({ title: 'Riss', source: 'package' });
    expect(t.sp.meta(lokal)).toMatchObject({ serverId: 'B-8', eigen: false });
    expect(t.store.list().filter((p) => p.id === 'B-8')).toHaveLength(1);
  });

  it('bestaetigt: erst nach der Antwort des Servers', async () => {
    const t = rueckmelder();
    await t.sp.load(t.store);
    expect(t.sp.bestaetigt('R-7')).toBe(true);
    t.store.add({ ...NEU, category: 'Rückmeldung' });
    t.sp.save(t.store, 1);
    expect(t.sp.bestaetigt('P-neu')).toBe(false);
    t.f.offline = true;
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.sp.bestaetigt('P-neu')).toBe(false);
    t.f.offline = false;
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.sp.bestaetigt('P-neu')).toBe(true);
    expect(t.sp.meta('P-neu')).toMatchObject({ serverId: 'P-neu', erstelltVonName: 'Rita', eigen: true });
  });

  it('onErgebnis: meldet die Bestätigung des Servers je lokaler ID, offline nichts', async () => {
    const t = rueckmelder();
    const ergebnisse: [string, string | null][] = [];
    t.sp.onErgebnis((id, fehler) => ergebnisse.push([id, fehler]));
    await t.sp.load(t.store);
    t.store.add({ ...NEU, category: 'Rückmeldung' });
    t.sp.save(t.store, 1);
    t.f.offline = true;
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(ergebnisse).toEqual([]);
    t.f.offline = false;
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(ergebnisse).toEqual([['P-neu', null]]);
  });

  it('Betrachter: Metadaten auch für die schreibgeschützten Server-POIs', async () => {
    const t = aufbau(false, { vorher: (f) => void f.eintrag('pois', 'B-8', BEFUND, { id: 8, name: 'Bernd' }) });
    await t.sp.load(t.store);
    expect(t.sp.meta('B-8')).toEqual({ serverId: 'B-8', paketRevision: 1, erstelltVonName: 'Bernd', geaendertAm: 1, eigen: false });
    expect(t.sp.lokalId('B-8')).toBe('B-8');
    expect(t.sp.bestaetigt('B-8')).toBe(false);
  });

  it('Betrachter: Server-POI mit der ID eines Paket-POIs bekommt lokal eine eigene ID', async () => {
    const paket: Poi = { id: 'B-8', title: 'Paket', category: '', note: '', position: [0, 0, 1], station: null, source: 'package' };
    const t = aufbau(false, { paket: [paket], vorher: (f) => void f.eintrag('pois', 'B-8', BEFUND, { id: 8, name: 'Bernd' }) });
    await t.sp.load(t.store);
    expect(t.sp.meta('B-8')).toBeNull();
    const lokal = t.sp.lokalId('B-8')!;
    expect(lokal).not.toBe('B-8');
    expect(t.store.get(lokal)?.title).toBe('Riss');
    expect(t.sp.meta(lokal)?.serverId).toBe('B-8');
  });

  it('Konflikt-Übernahme mit einem fremden Stand: schreibgeschützt eingereiht, danach nie geschrieben', async () => {
    let fremdAb = false;
    const t = aufbau(true, {
      eigen: (e) => !fremdAb && e.erstelltVon === 7,
      vorher: (f) => {
        f.nutzer = { id: 7, name: 'Rita' };
        f.eintrag('pois', 'R-7', RUECK, { id: 7, name: 'Rita' });
      },
    });
    let gezeichnet = 0;
    t.sp.setNeuZeichnen(() => gezeichnet++);
    await t.sp.load(t.store);
    expect(t.store.get('R-7')?.source).toBe('browser');
    t.f.aendereDirekt('pois', 'R-7', { ...RUECK, title: 'anderswo geändert' });
    t.store.update('R-7', { title: 'Meins' });
    t.sp.save(t.store, 1);
    fremdAb = true;
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.konflikt]);
    expect(gezeichnet).toBe(1);
    expect(t.store.get('R-7')).toMatchObject({ title: 'anderswo geändert', source: 'package' });
    expect(t.sp.meta('R-7')).toMatchObject({ serverId: 'R-7', geaendertAm: 2, eigen: false });
    expect(t.sp.lokalId('R-7')).toBe('R-7');
    expect(t.sp.bestaetigt('R-7')).toBe(false);
    expect(() => t.store.update('R-7', { title: 'nochmal' })).toThrow(PoiError);
    expect(() => t.store.remove('R-7')).toThrow(PoiError);
    const vorher = schreibend(t.f).length;
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(schreibend(t.f).slice(vorher)).toEqual([]);
    expect(t.sp.offen()).toBe(0);
  });

  it('Konflikt-Übernahme mit einem fremden Stand, dessen ID ein Paket-POI trägt: eigene lokale ID, Paket-POI unberührt', async () => {
    let fremdAb = false;
    const paket: Poi = { id: 'R-7', title: 'Paket', category: '', note: '', position: [0, 0, 1], station: null, source: 'package' };
    const t = aufbau(true, {
      paket: [paket],
      eigen: (e) => !fremdAb && e.erstelltVon === 7,
      vorher: (f) => {
        f.nutzer = { id: 7, name: 'Rita' };
        f.eintrag('pois', 'R-7', RUECK, { id: 7, name: 'Rita' });
      },
    });
    await t.sp.load(t.store);
    // Der eigene Server-POI heißt wegen des Paket-POIs lokal anders.
    expect(t.sp.lokalId('R-7')).toBe('P-neu');
    t.f.aendereDirekt('pois', 'R-7', { ...RUECK, title: 'anderswo geändert' });
    t.store.update('P-neu', { title: 'Meins' });
    t.sp.save(t.store, 1);
    fremdAb = true;
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.meldungen).toEqual([ABGLEICH_TEXT.konflikt]);
    expect(t.store.get('P-neu')).toBeUndefined();
    expect(t.store.get('R-7')).toMatchObject({ title: 'Paket', source: 'package' });
    expect(t.sp.meta('R-7')).toBeNull();
    const lokal = t.sp.lokalId('R-7')!;
    expect(lokal).not.toBeNull();
    expect(['R-7', 'P-neu']).not.toContain(lokal);
    expect(t.store.get(lokal)).toMatchObject({ title: 'anderswo geändert', source: 'package' });
    expect(t.sp.meta(lokal)).toMatchObject({ serverId: 'R-7', geaendertAm: 2, eigen: false });
    const ids = t.store.list().map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);
    const vorher = schreibend(t.f).length;
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(schreibend(t.f).slice(vorher)).toEqual([]);
  });

  for (const reihenfolge of [['B-8', 'B-8-k2'], ['B-8-k2', 'B-8']]) {
    it(`Server liefert ${reihenfolge.join(' vor ')}, das Paket trägt B-8: eindeutige lokale IDs, beide Server-IDs richtig zugeordnet`, async () => {
      const paket: Poi = { id: 'B-8', title: 'Paket', category: '', note: '', position: [0, 0, 1], station: null, source: 'package' };
      for (const schreibbar of [true, false]) {
        const t = aufbau(schreibbar, {
          paket: [paket],
          eigen: () => false,
          vorher: (f) => {
            for (const id of reihenfolge) f.eintrag('pois', id, { ...BEFUND, title: `Server ${id}` }, { id: 8, name: 'Bernd' });
          },
        });
        await t.sp.load(t.store);
        expect(t.store.get('B-8')?.title).toBe('Paket');
        expect(t.sp.meta('B-8')).toBeNull();
        const l8 = t.sp.lokalId('B-8');
        const lk2 = t.sp.lokalId('B-8-k2');
        expect(l8).not.toBeNull();
        expect(lk2).not.toBeNull();
        expect(l8).not.toBe(lk2);
        expect([l8, lk2]).not.toContain('B-8');
        for (const [server, lokal] of [['B-8', l8!], ['B-8-k2', lk2!]] as const) {
          expect(t.sp.meta(lokal)?.serverId).toBe(server);
          expect(t.store.get(lokal)?.title).toBe(`Server ${server}`);
        }
        const ids = t.store.list().map((p) => p.id);
        expect(new Set(ids).size).toBe(ids.length);
      }
    });
  }

  it('Konflikt-Übernahme hält die Metadaten aktuell', async () => {
    const t = rueckmelder();
    await t.sp.load(t.store);
    t.f.aendereDirekt('pois', 'R-7', { ...RUECK, title: 'anderswo geändert' });
    t.store.update('R-7', { title: 'Meins' });
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
    expect(t.store.get('R-7')?.title).toBe('anderswo geändert');
    expect(t.sp.meta('R-7')).toMatchObject({ serverId: 'R-7', geaendertAm: 2, eigen: true });
  });
});

describe('Antwort des Servers auf eine im Empfang angelegte Rückmeldung (WartendeAnlagen am echten Abgleich)', () => {
  const RUECK = { ...NEU, category: 'Rückmeldung' };
  const NUR_LOESCHBAR = 'Als Rückmelder nur noch löschbar.';
  // Wie der Empfang: eine abgelehnte, nie bestätigte Anlage lokal entfernen.
  const empfang = (t: ReturnType<typeof aufbau>) => {
    const wartend = new WartendeAnlagen((id) => t.sp.bestaetigt(id));
    const folgen: (AnlageFolge | null)[] = [];
    t.sp.onErgebnis((id, fehler) => {
      const f = wartend.ergebnis(id, fehler);
      folgen.push(f);
      if (f?.entfernen && t.store.get(id)?.source === 'browser') t.store.remove(id);
    });
    return { wartend, folgen };
  };
  const lauf = async (t: ReturnType<typeof aufbau>) => {
    t.sp.save(t.store, 1);
    t.u.alle();
    await t.sp.abgleich.lauf();
  };
  const loeschungen = (t: ReturnType<typeof aufbau>) => t.f.aufrufe.filter((a) => a.startsWith('DELETE'));

  it('bestätigt, dann geändert und abgelehnt (422): der Eintrag bleibt lokal und am Server, kein DELETE', async () => {
    const t = aufbau(true);
    const e = empfang(t);
    await t.sp.load(t.store);
    t.store.add(RUECK);
    e.wartend.merke('P-neu', 'Seit März');
    await lauf(t);
    expect(t.sp.bestaetigt('P-neu')).toBe(true);
    // Die Rolle ist inzwischen Rückmelder: eine Änderung des eigenen Alt-POIs lehnt der Dienst ab.
    t.f.ablehnen = (m) => (m === 'PATCH' ? NUR_LOESCHBAR : null);
    t.store.update('P-neu', { title: 'geändert', category: 'Mangel' });
    // Ein späterer Abschluss im Empfang meldet dieselbe ID nicht noch einmal als wartend an.
    e.wartend.merke('P-neu');
    await lauf(t);
    expect(t.meldungen).toContain(`Nicht gespeichert: ${NUR_LOESCHBAR}`);
    expect(t.store.get('P-neu')?.title).toBe('geändert');
    await lauf(t);
    expect(loeschungen(t)).toEqual([]);
    expect(t.f.eintraege('pois').some((p) => p.id === 'P-neu')).toBe(true);
  });

  it('nie bestätigt und abgelehnt: die lokale Kopie geht, der Text kommt zurück, kein DELETE', async () => {
    const t = aufbau(true);
    const e = empfang(t);
    await t.sp.load(t.store);
    t.f.ablehnen = (m) => (m === 'POST' ? 'Höchstens 100 eigene Rückmeldungen je Projekt.' : null);
    t.store.add(RUECK);
    e.wartend.merke('P-neu', 'Seit März');
    await lauf(t);
    expect(e.folgen).toEqual([{ art: 'abgelehnt', entfernen: true, text: 'Seit März', grund: 'Höchstens 100 eigene Rückmeldungen je Projekt.' }]);
    expect(t.store.get('P-neu')).toBeUndefined();
    await lauf(t);
    expect(loeschungen(t)).toEqual([]);
  });

  it('bestätigt: danach wartet die ID nicht mehr, ein späteres Ergebnis betrifft den Empfang nicht', async () => {
    const t = aufbau(true);
    const e = empfang(t);
    await t.sp.load(t.store);
    t.store.add(RUECK);
    e.wartend.merke('P-neu', 'Seit März');
    await lauf(t);
    expect(e.folgen).toEqual([{ art: 'gespeichert', entfernen: false, text: 'Seit März', grund: null }]);
    expect(e.wartend.ergebnis('P-neu', 'später abgelehnt')).toBeNull();
  });

  it('Konflikt auf die neue Anlage (ID schon vergeben, eigener Stand übernommen): Ergebnis kommt, nichts wird entfernt, kein DELETE', async () => {
    const t = aufbau(true);
    const e = empfang(t);
    await t.sp.load(t.store);
    // Nach dem Laden am Server unter derselben ID angelegt (der eigene Stand wird übernommen und gebunden).
    t.f.eintrag('pois', 'P-neu', { ...TUER, title: 'schon da' });
    t.store.add(RUECK);
    e.wartend.merke('P-neu', 'Seit März');
    await lauf(t);
    expect(t.meldungen).toContain(ABGLEICH_TEXT.konflikt);
    expect(e.folgen).toEqual([{ art: 'erledigt', entfernen: false, text: 'Seit März', grund: ABGLEICH_TEXT.konflikt }]);
    expect(t.store.get('P-neu')?.title).toBe('schon da');
    await lauf(t);
    expect(loeschungen(t)).toEqual([]);
  });

  it('Konflikt mit einem fremden Stand: Ergebnis „nicht gespeichert“, der fremde POI bleibt schreibgeschützt, kein DELETE', async () => {
    const t = aufbau(true, { eigen: (x) => x.erstelltVon === 7, vorher: (f) => void (f.nutzer = { id: 7, name: 'Rita' }) });
    const e = empfang(t);
    await t.sp.load(t.store);
    // Nach dem Laden legt jemand anderes am Server einen POI unter derselben ID an.
    t.f.eintrag('pois', 'P-neu', { ...TUER, title: 'fremd' }, { id: 8, name: 'Bernd' });
    t.store.add(RUECK);
    e.wartend.merke('P-neu', 'Seit März');
    await lauf(t);
    expect(e.folgen).toHaveLength(1);
    expect(e.folgen[0]).toMatchObject({ art: 'abgelehnt', text: 'Seit März', grund: ABGLEICH_TEXT.konflikt });
    await lauf(t);
    expect(loeschungen(t)).toEqual([]);
    expect(t.f.eintraege('pois').find((p) => p.id === 'P-neu')?.daten.title).toBe('fremd');
  });
});
