import { describe, expect, it } from 'vitest';
import { MeasureStore } from '../src/measureStore';
import { fingerabdruck, lokaleUebernahme, markeWennFertig, OHNE_NUTZER_ID, poiSchluessel, uebernimm, uebernommenSchluessel, verbindePlattform, wirksameRechte } from '../src/plattformViewer';
import { NUR_LESEN, rechte } from '../src/rechte';
import { LOKALE_MESSUNGEN } from '../src/serverMessungen';
import { PoiPersistence, PoiStore, parsePoiFile } from '../src/poiStore';
import { ServerPoiPersistenz } from '../src/serverPois';
import { fakePlattform, fakeUhr } from './fakePlattform';

const speicher = (werte: Record<string, string>) => ({
  getItem: (k: string) => werte[k] ?? null,
  setItem: (k: string, v: string) => void (werte[k] = v),
});
const datei = (n: number) => JSON.stringify({ schemaVersion: 1, projectId: 'p1', revision: 1, frame: 'world', unit: 'm', pois: Array.from({ length: n }, (_, i) => ({ id: `P-${i}`, title: 'T', category: '', note: '', position: [0, 0, 0], station: null })) });

describe('lokaleUebernahme', () => {
  it('bietet Browser-POIs an, bis genau dieser Stand übernommen ist; ein neuer Stand wird wieder angeboten', () => {
    const w: Record<string, string> = { [poiSchluessel('p1')]: datei(2) };
    expect(lokaleUebernahme(speicher(w), 'p1')).toBe(datei(2));
    w[uebernommenSchluessel('p1')] = fingerabdruck(datei(2));
    expect(lokaleUebernahme(speicher(w), 'p1')).toBeNull();
    w[poiSchluessel('p1')] = datei(3);
    expect(lokaleUebernahme(speicher(w), 'p1')).toBe(datei(3));
  });
  it('nichts anzubieten: kein Speicher, leer, kaputt', () => {
    expect(lokaleUebernahme(null, 'p1')).toBeNull();
    expect(lokaleUebernahme(speicher({}), 'p1')).toBeNull();
    expect(lokaleUebernahme(speicher({ [poiSchluessel('p1')]: datei(0) }), 'p1')).toBeNull();
    expect(lokaleUebernahme(speicher({ [poiSchluessel('p1')]: '{kaputt' }), 'p1')).toBeNull();
  });
  it('Schlüssel wie in pois.ts', () => {
    expect(poiSchluessel('p1')).toBe('scanview:pois:p1');
  });
  it('fingerabdruck unterscheidet Inhalte', () => {
    expect(fingerabdruck(datei(2))).toBe(fingerabdruck(datei(2)));
    expect(fingerabdruck(datei(2))).not.toBe(fingerabdruck(datei(3)));
  });
});

async function aufbau() {
  const f = fakePlattform();
  const u = fakeUhr();
  const w: Record<string, string> = { [poiSchluessel('p1')]: datei(2) };
  const store = new PoiStore('p1');
  const pois = new ServerPoiPersistenz({
    projectId: 'p1', schreibbar: true, lokal: new PoiPersistence(null, 'x'), melde: () => undefined, fetchFn: f.fetchFn, uhr: u.uhr,
  });
  await pois.load(store);
  // wie pois.ts importText: in den Store übernehmen, speichern (stößt den Abgleich an)
  const importText = (t: string): number => {
    const r = store.importBrowserPois(parsePoiFile(JSON.parse(t), 'p1', 'browser'));
    pois.save(store, 1);
    return r.added;
  };
  const o = () => ({
    roh: datei(2),
    importText,
    pois,
    vorhanden: (id: string) => store.get(id)?.source === 'browser',
    speicher: speicher(w),
    pid: 'p1',
  });
  return { f, u, w, store, pois, o };
}

describe('uebernimm', () => {
  it('setzt die Marke erst, wenn der Server alles bestätigt hat', async () => {
    const t = await aufbau();
    expect(await uebernimm(t.o())).toBe('fertig');
    expect(t.f.eintraege('pois')).toHaveLength(2);
    expect(t.w[uebernommenSchluessel('p1')]).toBe(fingerabdruck(datei(2)));
  });
  it('offline: keine Marke, der Knopf bleibt', async () => {
    const t = await aufbau();
    t.f.offline = true;
    expect(await uebernimm(t.o())).toBe('offen');
    expect(t.w[uebernommenSchluessel('p1')]).toBeUndefined();
  });
  it('erneuter Klick nach „offen“ überschreibt keine neueren Stände', async () => {
    const t = await aufbau();
    t.f.offline = true;
    expect(await uebernimm(t.o())).toBe('offen');
    t.store.update('P-0', { title: 'Neuer' });
    t.pois.save(t.store, 1);
    t.f.offline = false;
    expect(await uebernimm(t.o())).toBe('fertig');
    expect(t.f.eintraege('pois').map((e) => [e.id, e.daten.title]).sort()).toEqual([['P-0', 'Neuer'], ['P-1', 'T']]);
    expect(t.store.get('P-0')?.title).toBe('Neuer');
    expect(t.w[uebernommenSchluessel('p1')]).toBe(fingerabdruck(datei(2)));
  });
  it('Marke folgt, sobald der Abgleich später durchgeht', async () => {
    const t = await aufbau();
    t.f.offline = true;
    expect(await uebernimm(t.o())).toBe('offen');
    expect(markeWennFertig(t.o())).toBe(false);
    t.f.offline = false;
    t.u.alle();
    await t.pois.abgleich.lauf();
    expect(markeWennFertig(t.o())).toBe(true);
    expect(t.w[uebernommenSchluessel('p1')]).toBe(fingerabdruck(datei(2)));
  });
});

describe('verbindePlattform', () => {
  const umgebung = () => ({
    fenster: { addEventListener: () => undefined },
    dok: { addEventListener: () => undefined, visibilityState: 'visible' },
    speicher: null,
  });
  async function verbinde(rolle: string, nutzerId: number | null) {
    const f = fakePlattform();
    f.rolle = rolle;
    f.nutzer = { id: 7, name: 'Rita' };
    f.eintrag('pois', 'B-8', { title: 'Riss', category: 'Mangel', note: '', position: [1, 2, 3], station: null }, { id: 8, name: 'Bernd' });
    f.eintrag('pois', 'R-7', { title: 'Frage', category: 'Rückmeldung', note: '', position: [4, 5, 6], station: null }, { id: 7, name: 'Rita' });
    const meldungen: string[] = [];
    const store = new MeasureStore();
    const p = await verbindePlattform({ projectId: 'p1', store, notify: (t) => meldungen.push(t), nutzerId, fetchFn: f.fetchFn, umgebung: umgebung() });
    if (!p) throw new Error('keine Verbindung');
    return { f, p, store, meldungen };
  }
  const schreibend = (f: ReturnType<typeof fakePlattform>, art: string) => f.aufrufe.filter((a) => !a.startsWith('GET') && a.includes(`/${art}`));

  it('Kategorien: aktive aus dem Projekt, ohne das Feld die feste Liste', async () => {
    const f = fakePlattform();
    f.kategorien = [{ name: 'Mangel', aktiv: true }, { name: 'Raum', aktiv: false }];
    const p = await verbindePlattform({ projectId: 'p1', store: new MeasureStore(), notify: () => undefined, nutzerId: 7, fetchFn: f.fetchFn, umgebung: umgebung() });
    expect(p?.kategorien).toEqual(['Mangel']);
    const alt = fakePlattform();
    const q = await verbindePlattform({ projectId: 'p1', store: new MeasureStore(), notify: () => undefined, nutzerId: 7, fetchFn: alt.fetchFn, umgebung: umgebung() });
    expect(q?.kategorien).toEqual(['Raum', 'Anlage', 'Zugang', 'Mangel', 'Sonstiges']);
  });

  it('Rückmelder: Messungen bleiben lokal, nichts geht an den Server', async () => {
    const t = await verbinde('rueckmelder', 7);
    expect(t.p.rolle).toBe('rueckmelder');
    expect(t.p.rechte).toEqual({ poisAnlegen: true, alleAendern: false, nurRueckmeldung: true, messungenTeilen: false, uebernahme: false });
    expect(t.p.messungenGemeinsam()).toBe(false);
    t.store.add([0, 0, 0], [1, 0, 0]);
    t.p.messungenGeaendert();
    await new Promise((r) => setTimeout(r, 500));
    expect(schreibend(t.f, 'messungen')).toEqual([]);
    expect(t.meldungen).toContain(LOKALE_MESSUNGEN);
  });

  it('die zurückgegebene Persistenz reicht meta, lokalId, bestaetigt und onErgebnis durch', async () => {
    const t = await verbinde('rueckmelder', 7);
    const pp = t.p.poiPersistenz;
    expect(typeof pp.meta).toBe('function');
    expect(typeof pp.lokalId).toBe('function');
    expect(typeof pp.bestaetigt).toBe('function');
    expect(typeof pp.onErgebnis).toBe('function');
    const store = new PoiStore('p1', () => 'P-neu');
    await pp.load(store);
    expect(pp.meta!('B-8')).toMatchObject({ serverId: 'B-8', erstelltVonName: 'Bernd', eigen: false });
    expect(pp.meta!('R-7')).toMatchObject({ eigen: true });
    expect(pp.lokalId!('R-7')).toBe('R-7');
    expect(store.get('B-8')?.source).toBe('package');
    expect(store.get('R-7')?.source).toBe('browser');
    store.add({ title: 'Neu', category: 'Rückmeldung', note: '', position: [0, 0, 0], station: null });
    expect(pp.save(store, 1)).toBeNull();
    expect(pp.bestaetigt!('P-neu')).toBe(false);
  });

  it('onErgebnis über die zurückgegebene Persistenz: Bestätigung und Ablehnung kommen beim Aufrufer an', async () => {
    const t = await verbinde('rueckmelder', 7);
    const pp = t.p.poiPersistenz;
    const ergebnisse: [string, string | null][] = [];
    pp.onErgebnis!((id, fehler) => ergebnisse.push([id, fehler]));
    let n = 0;
    const store = new PoiStore('p1', () => `P-${++n}`);
    await pp.load(store);
    store.add({ title: 'Neu', category: 'Rückmeldung', note: '', position: [0, 0, 0], station: null });
    pp.save(store, 1);
    await expect.poll(() => ergebnisse, { timeout: 3000 }).toEqual([['P-1', null]]);
    expect(pp.bestaetigt!('P-1')).toBe(true);
    t.f.ablehnen = () => 'Höchstens 100 eigene Rückmeldungen je Projekt.';
    store.add({ title: 'Noch eine', category: 'Rückmeldung', note: '', position: [0, 0, 0], station: null });
    pp.save(store, 1);
    await expect.poll(() => ergebnisse.length, { timeout: 3000 }).toBe(2);
    expect(ergebnisse[1]).toEqual(['P-2', 'Höchstens 100 eigene Rückmeldungen je Projekt.']);
  });

  it('Bearbeiter: Messungen gemeinsam, alle Server-POIs eigene', async () => {
    const t = await verbinde('bearbeiter', 7);
    expect(t.p.messungenGemeinsam()).toBe(true);
    expect(t.p.rechte.alleAendern).toBe(true);
    const store = new PoiStore('p1');
    await t.p.poiPersistenz.load(store);
    expect(store.get('B-8')?.source).toBe('browser');
  });

  it('Rückmelder ohne eigene Nutzer-ID: nur lesen, Hinweis, kein Schreiben am Server', async () => {
    const t = await verbinde('rueckmelder', null);
    expect(t.p.rechte).toEqual({ poisAnlegen: false, alleAendern: false, nurRueckmeldung: false, messungenTeilen: false, uebernahme: false });
    expect(t.meldungen.some((m) => /Rückmeldungen nicht möglich/.test(m))).toBe(true);
    const store = new PoiStore('p1');
    await t.p.poiPersistenz.load(store);
    expect(store.get('R-7')?.source).toBe('package');
    store.add({ title: 'Neu', category: 'Rückmeldung', note: '', position: [0, 0, 0], station: null });
    t.p.poiPersistenz.save(store, 1);
    await new Promise((r) => setTimeout(r, 500));
    expect(schreibend(t.f, 'pois')).toEqual([]);
  });

  it('unbekannte Rolle: nur lesen', async () => {
    const t = await verbinde('chef', 7);
    expect(t.p.rechte.poisAnlegen).toBe(false);
    expect(t.p.messungenGemeinsam()).toBe(false);
  });
});

describe('wirksameRechte', () => {
  it('Rückmelder ohne Nutzer-ID: nur lesen, mit Hinweis; mit ID: Rollenrechte ohne Hinweis', () => {
    expect(wirksameRechte('rueckmelder', null)).toEqual({ rechte: { ...NUR_LESEN }, hinweis: OHNE_NUTZER_ID });
    expect(wirksameRechte('rueckmelder', 7)).toEqual({ rechte: rechte('rueckmelder'), hinweis: null });
  });
  it('Hinweis genau dann, wenn die wirksamen Rechte von denen der Rolle abweichen (eine Bedingung)', () => {
    for (const rolle of ['admin', 'bearbeiter', 'rueckmelder', 'betrachter', 'unbekannt'])
      for (const nutzerId of [null, 7]) {
        const w = wirksameRechte(rolle, nutzerId);
        const abweichend = JSON.stringify(w.rechte) !== JSON.stringify(rechte(rolle));
        expect(w.hinweis !== null, `${rolle}/${String(nutzerId)}`).toBe(abweichend);
      }
  });
});
