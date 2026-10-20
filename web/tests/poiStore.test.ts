import { describe, expect, it } from 'vitest';
import { istRueckmeldung, kategorieVorschlaege, markerKlassen, POI_CATEGORIES, POI_ID_RE, POI_LIMITS, PoiError, PoiPersistence, PoiStore, parsePoiFile, RUECKMELDUNG } from '../src/poiStore';
import type { PoiDraft } from '../src/poiStore';

const draft = (title: string, over: Partial<PoiDraft> = {}): PoiDraft => ({
  title, category: 'Zugang', note: '', position: [1470.5, 7100.25, 1.5], station: 'S005', ...over,
});
const ids = (): (() => string) => {
  let n = 0;
  return () => `P-${++n}`;
};
const file = (pois: unknown[], projectId = 'musterhalle-nord'): unknown => ({
  schemaVersion: 1, projectId, revision: 1, frame: 'world', unit: 'm', pois,
});

describe('PoiStore', () => {
  it('legt an, listet nach Titel, sucht umlaut- und großschreibungsunabhängig', () => {
    const s = new PoiStore('musterhalle-nord', ids());
    s.add(draft('Südportal Türbreite'));
    s.add(draft('Sakristei', { category: 'Raum', note: 'Schlüssel beim Küster' }));
    expect(s.list().map((p) => p.title)).toEqual(['Sakristei', 'Südportal Türbreite']);
    expect(s.search('turbreite').map((p) => p.title)).toEqual(['Südportal Türbreite']);
    expect(s.search('KÜSTER raum').map((p) => p.title)).toEqual(['Sakristei']);
    expect(s.search('  ').length).toBe(2);
  });
  it('Titel ist Pflicht, Längen sind begrenzt, Position muss endlich sein', () => {
    const s = new PoiStore('p', ids());
    expect(() => s.add(draft('  '))).toThrow(PoiError);
    expect(() => s.add(draft('x'.repeat(POI_LIMITS.title + 1)))).toThrow(/Titel/);
    expect(() => s.add(draft('a', { position: [1, Number.NaN, 2] }))).toThrow(/position/);
  });
  it('Paket-POIs sind schreibgeschützt', () => {
    const s = new PoiStore('musterhalle-nord', ids());
    s.setPackagePois(parsePoiFile(file([{ id: 'P-pkg', title: 'Portal', position: [1, 2, 3] }]), 'musterhalle-nord', 'package'));
    expect(() => s.update('P-pkg', { title: 'x' })).toThrow(/schreibgeschützt/);
    expect(() => s.remove('P-pkg')).toThrow(/schreibgeschützt/);
    expect(s.get('P-pkg')?.source).toBe('package');
  });
  it('Export/Import: Rundreise, gleiche ID ersetzt, Paket-IDs werden nicht dupliziert', () => {
    const a = new PoiStore('musterhalle-nord', ids());
    a.setPackagePois(parsePoiFile(file([{ id: 'P-pkg', title: 'Portal', position: [1, 2, 3] }]), 'musterhalle-nord', 'package'));
    const p = a.add(draft('Tür'));
    const f = a.toFile(3);
    expect(f.pois.map((x) => x.id).sort()).toEqual(['P-1', 'P-pkg']);
    expect(f.pois.find((x) => x.id === 'P-1')).toEqual({ id: 'P-1', title: 'Tür', category: 'Zugang', note: '', position: [1470.5, 7100.25, 1.5], station: 'S005' });
    expect(a.toFile(3, 'browser').pois.map((x) => x.id)).toEqual(['P-1']);
    const b = new PoiStore('musterhalle-nord', ids());
    b.setPackagePois(parsePoiFile(file([{ id: 'P-pkg', title: 'Portal', position: [1, 2, 3] }]), 'musterhalle-nord', 'package'));
    expect(b.importBrowserPois(parsePoiFile(JSON.parse(JSON.stringify(f)), 'musterhalle-nord', 'browser'))).toEqual({ added: 1, renamed: 0 });
    expect(b.list().filter((x) => x.id === 'P-pkg')).toHaveLength(1);
    expect(b.get(p.id)?.position).toEqual([1470.5, 7100.25, 1.5]);
    b.importBrowserPois(parsePoiFile(file([{ ...f.pois.find((x) => x.id === 'P-1'), title: 'Tür neu' }]), 'musterhalle-nord', 'browser'));
    expect(b.get('P-1')?.title).toBe('Tür neu');
  });
});

describe('parsePoiFile (fremde Daten)', () => {
  it('fremde Projekt-ID wird abgelehnt', () => {
    expect(() => parsePoiFile(file([], 'anderes'), 'musterhalle-nord', 'browser')).toThrow(/gehört zu Projekt/);
  });
  it('falsche Version, fehlende Liste, doppelte/unsichere IDs, falsche Typen', () => {
    expect(() => parsePoiFile({ ...(file([]) as object), schemaVersion: 2 }, 'musterhalle-nord', 'browser')).toThrow(/Schema-Version/);
    expect(() => parsePoiFile({ schemaVersion: 1, projectId: 'musterhalle-nord' }, 'musterhalle-nord', 'browser')).toThrow(/pois/);
    const ok = { id: 'P-a', title: 'A', position: [1, 2, 3] };
    expect(() => parsePoiFile(file([ok, ok]), 'musterhalle-nord', 'browser')).toThrow(/doppelt/);
    expect(() => parsePoiFile(file([{ ...ok, id: '<b>' }]), 'musterhalle-nord', 'browser')).toThrow(/ID/);
    expect(() => parsePoiFile(file([{ ...ok, title: 5 }]), 'musterhalle-nord', 'browser')).toThrow(/Titel/);
    expect(() => parsePoiFile(file([{ ...ok, station: 'S 1<' }]), 'musterhalle-nord', 'browser')).toThrow(/Standpunkt/);
  });
  it('HTML im Titel bleibt unverändert Text (Anzeige nur per textContent)', () => {
    const [p] = parsePoiFile(file([{ id: 'P-x', title: '<img src=x onerror=alert(1)>', position: [1, 2, 3] }]), 'musterhalle-nord', 'browser');
    expect(p!.title).toBe('<img src=x onerror=alert(1)>');
  });
});

// Abschluss-Review I1: kein stiller POI-Verlust (Mengenlimit, ID-Kollision, Ladefehler, Speicherfehler).
const own = (id: string, title = 'A', over: Record<string, unknown> = {}): Record<string, unknown> => ({
  id, title, category: '', note: '', position: [1, 2, 3], station: null, ...over,
});
const pf = (pois: unknown[]): unknown => file(pois, 'p');
const many = (n: number, prefix = 'X'): Record<string, unknown>[] => Array.from({ length: n }, (_, i) => own(`${prefix}-${i}`));

class MemStorage {
  readonly data = new Map<string, string>();
  failWrites = false;
  getItem(k: string): string | null {
    return this.data.get(k) ?? null;
  }
  setItem(k: string, v: string): void {
    if (this.failWrites) throw new Error('QuotaExceededError');
    this.data.set(k, v);
  }
}

describe('PoiStore — Mengenlimit und ID-Kollision (I1)', () => {
  it('add() lehnt mehr als POI_LIMITS.count eigene POIs ab', () => {
    const s = new PoiStore('p', ids());
    s.importBrowserPois(parsePoiFile(pf(many(POI_LIMITS.count)), 'p', 'browser'));
    expect(() => s.add(draft('zu viel'))).toThrow(/5000/);
    expect(s.toFile(1, 'browser').pois).toHaveLength(POI_LIMITS.count);
  });
  it('Import über das Limit wirft und ändert nichts (zweimal 3000)', () => {
    const s = new PoiStore('p', ids());
    s.importBrowserPois(parsePoiFile(pf(many(3000, 'A')), 'p', 'browser'));
    expect(() => s.importBrowserPois(parsePoiFile(pf(many(3000, 'B')), 'p', 'browser'))).toThrow(/5000/);
    expect(s.toFile(1, 'browser').pois).toHaveLength(3000);
    // Ersetzen vorhandener IDs zählt nicht als neu.
    expect(s.importBrowserPois(parsePoiFile(pf(many(3000, 'A')), 'p', 'browser')).added).toBe(3000);
  });
  it('Browser-POI mit Paket-ID, aber anderem Inhalt, bleibt unter neuer ID erhalten', () => {
    const s = new PoiStore('p', ids());
    s.setPackagePois(parsePoiFile(pf([own('P-pkg', 'Portal')]), 'p', 'package'));
    const r = s.importBrowserPois(parsePoiFile(pf([own('P-pkg', 'Mein Portal'), own('P-pkg2', 'x')]), 'p', 'browser'));
    expect(r).toEqual({ added: 2, renamed: 1 });
    expect(s.get('P-pkg')?.title).toBe('Portal');
    const mine = s.list().filter((p) => p.title === 'Mein Portal');
    expect(mine).toHaveLength(1);
    expect(mine[0]!.id).not.toBe('P-pkg');
    expect(mine[0]!.source).toBe('browser');
  });
  it('setPackagePois behält vorhandene Browser-POIs bei ID-Kollision (neue ID), gleiche Inhalte entfallen', () => {
    const s = new PoiStore('p', ids());
    s.importBrowserPois(parsePoiFile(pf([own('P-a', 'Eigen'), own('P-b', 'Gleich')]), 'p', 'browser'));
    s.setPackagePois(parsePoiFile(pf([own('P-a', 'Paket'), own('P-b', 'Gleich')]), 'p', 'package'));
    expect(s.list().map((p) => `${p.title}/${p.source}`).sort()).toEqual(['Eigen/browser', 'Gleich/package', 'Paket/package']);
  });
  it('add() mit mitgegebener ID: gültig und frei wird übernommen, vergeben oder ungültig wirft', () => {
    const s = new PoiStore('p', ids());
    s.setPackagePois(parsePoiFile(pf([own('P-pkg', 'Portal')]), 'p', 'package'));
    expect(s.add(draft('Fortgesetzt'), 'P-entwurf-1').id).toBe('P-entwurf-1');
    expect(s.get('P-entwurf-1')?.source).toBe('browser');
    expect(() => s.add(draft('doppelt eigen'), 'P-entwurf-1')).toThrow(PoiError);
    expect(() => s.add(draft('doppelt Paket'), 'P-pkg')).toThrow(PoiError);
    expect(() => s.add(draft('ungültig'), 'a b')).toThrow(PoiError);
    expect(() => s.add(draft('zu lang'), 'x'.repeat(65))).toThrow(PoiError);
    expect(s.list().filter((p) => p.source === 'browser').map((p) => p.title)).toEqual(['Fortgesetzt']);
  });
  it('reserviereId: freie, gültige ID ohne Anlegen; je Aufruf eine andere; add() übernimmt sie', () => {
    const s = new PoiStore('p', ids());
    s.setPackagePois(parsePoiFile(pf([own('P-pkg', 'Portal')]), 'p', 'package'));
    s.add(draft('Eigen'));
    const a = s.reserviereId();
    const b = s.reserviereId();
    expect(a).toMatch(POI_ID_RE);
    expect(a).not.toBe(b);
    expect(s.get(a)).toBeUndefined();
    expect(s.list().map((p) => p.id)).not.toContain(a);
    expect(s.add(draft('Mit reservierter ID'), a).id).toBe(a);
  });
  it('reserviereId weicht vergebenen IDs aus (Paket und eigen)', () => {
    let n = 0;
    const s = new PoiStore('p', () => `P-${++n > 4 ? n : (n % 2) + 1}`);
    s.setPackagePois(parsePoiFile(pf([own('P-1', 'Portal')]), 'p', 'package'));
    s.importBrowserPois(parsePoiFile(pf([own('P-2', 'Eigen')]), 'p', 'browser'));
    expect(['P-1', 'P-2']).not.toContain(s.reserviereId());
  });
  it('neue IDs kollidieren nicht mit vorhandenen', () => {
    let n = 0;
    const s = new PoiStore('p', () => `P-${++n > 3 ? n : 1}`);
    s.importBrowserPois(parsePoiFile(pf([own('P-1')]), 'p', 'browser'));
    expect(s.add(draft('neu')).id).not.toBe('P-1');
  });
});

describe('parsePoiFile — Grenzfälle (I1)', () => {
  it('mehr als 5000 Einträge, Überlänge Kategorie/Notiz, Nicht-Objekt, null-Kategorie', () => {
    expect(() => parsePoiFile(pf(many(POI_LIMITS.count + 1)), 'p', 'browser')).toThrow(/mehr als 5000/);
    expect(parsePoiFile(pf(many(POI_LIMITS.count)), 'p', 'browser')).toHaveLength(POI_LIMITS.count);
    expect(() => parsePoiFile(pf([own('P-a', 'A', { category: 'x'.repeat(POI_LIMITS.category + 1) })]), 'p', 'browser')).toThrow(/Kategorie länger/);
    expect(() => parsePoiFile(pf([own('P-a', 'A', { note: 'x'.repeat(POI_LIMITS.note + 1) })]), 'p', 'browser')).toThrow(/Notiz länger/);
    expect(() => parsePoiFile(pf(['P-a']), 'p', 'browser')).toThrow(/Eintrag 1: ungültige ID/);
    expect(() => parsePoiFile(pf([null]), 'p', 'browser')).toThrow(/Eintrag 1: ungültige ID/);
    // null ist kein Text: strikt abgelehnt (der Viewer schreibt fehlende Kategorie als '').
    expect(() => parsePoiFile(pf([own('P-a', 'A', { category: null })]), 'p', 'browser')).toThrow(/Kategorie muss Text sein/);
  });
});

describe('PoiPersistence — Browser-Speicher (I1)', () => {
  const KEY = 'scanview:pois:p';
  it('Rundreise: speichern, in neuem Store laden', () => {
    const st = new MemStorage();
    const a = new PoiStore('p', ids());
    a.add(draft('Tür'));
    expect(new PoiPersistence(st, KEY).save(a, 1)).toBeNull();
    const b = new PoiStore('p', ids());
    const r = new PoiPersistence(st, KEY).load(b);
    expect(r).toEqual({ loaded: 1, renamed: 0, warning: null });
    expect(b.search('tür')).toHaveLength(1);
  });
  it('Ladefehler: Rohwert wird gesichert, Hinweis, späteres Speichern verliert ihn nicht', () => {
    const st = new MemStorage();
    const raw = JSON.stringify(pf(many(POI_LIMITS.count + 1)));
    st.data.set(KEY, raw);
    const s = new PoiStore('p', ids());
    const per = new PoiPersistence(st, KEY);
    const r = per.load(s);
    expect(r.loaded).toBe(0);
    expect(r.warning).toMatch(/nicht lesbar/);
    expect(r.warning).toContain(`${KEY}:defekt`);
    s.add(draft('neu'));
    expect(per.save(s, 1)).toBeNull();
    expect(st.data.get(`${KEY}:defekt`)).toBe(raw);
  });
  it('Ladefehler: vorhandene Sicherung wird nicht überschrieben', () => {
    const st = new MemStorage();
    st.data.set(`${KEY}:defekt`, 'ältere Sicherung');
    st.data.set(KEY, '{kaputt');
    const per = new PoiPersistence(st, KEY);
    expect(per.load(new PoiStore('p', ids())).warning).toContain(`${KEY}:defekt-2`);
    expect(st.data.get(`${KEY}:defekt`)).toBe('ältere Sicherung');
    expect(st.data.get(`${KEY}:defekt-2`)).toBe('{kaputt');
  });
  it('Ladefehler und Sicherung scheitert: Speichern gesperrt, Rohwert bleibt', () => {
    const st = new MemStorage();
    st.data.set(KEY, '{kaputt');
    st.failWrites = true;
    const s = new PoiStore('p', ids());
    const per = new PoiPersistence(st, KEY);
    expect(per.load(s).warning).toMatch(/nicht gespeichert/);
    st.failWrites = false;
    s.add(draft('neu'));
    expect(per.save(s, 1)).toMatch(/gesperrt/);
    expect(st.data.get(KEY)).toBe('{kaputt');
  });
  it('Speicherfehler (Quota) wird gemeldet, nicht verschluckt', () => {
    const st = new MemStorage();
    st.failWrites = true;
    const s = new PoiStore('p', ids());
    s.add(draft('neu'));
    expect(new PoiPersistence(st, KEY).save(s, 1)).toMatch(/nicht im Browser gespeichert/);
    expect(new PoiPersistence(null, KEY).save(s, 1)).toMatch(/nicht im Browser gespeichert/);
  });
  it('umbenannte Browser-POIs beim Laden werden gezählt', () => {
    const st = new MemStorage();
    st.data.set(KEY, JSON.stringify(pf([own('P-pkg', 'Mein Portal')])));
    const s = new PoiStore('p', ids());
    s.setPackagePois(parsePoiFile(pf([own('P-pkg', 'Portal')]), 'p', 'package'));
    expect(new PoiPersistence(st, KEY).load(s)).toEqual({ loaded: 1, renamed: 1, warning: null });
  });
});

describe('Kategorie Rückmeldung und Unicode-Normalform', () => {
  const zerlegt = 'Ru\u0308ckmeldung';
  it('die Konstante steht in der zusammengesetzten Form und ist kein Vorschlag für Bearbeiter', () => {
    expect(RUECKMELDUNG).toBe('R\u00fcckmeldung');
    expect(RUECKMELDUNG).toBe(RUECKMELDUNG.normalize('NFC'));
    expect(POI_CATEGORIES as readonly string[]).not.toContain(RUECKMELDUNG);
  });
  it('istRueckmeldung vergleicht nach Normalform und ohne Rand', () => {
    expect(istRueckmeldung(RUECKMELDUNG)).toBe(true);
    expect(istRueckmeldung(zerlegt)).toBe(true);
    expect(istRueckmeldung(` ${zerlegt} `)).toBe(true);
    expect(istRueckmeldung('Rueckmeldung')).toBe(false);
    expect(istRueckmeldung('rückmeldung')).toBe(false);
    expect(istRueckmeldung('')).toBe(false);
  });
  it('Kategorie wird beim Anlegen, Ändern und Einlesen in der zusammengesetzten Form gespeichert', () => {
    const s = new PoiStore('p1');
    const p = s.add({ title: 'T', category: zerlegt, note: '', position: [0, 0, 0], station: null });
    expect(p.category).toBe(RUECKMELDUNG);
    s.update(p.id, { category: 'Ma\u0308ngel' });
    expect(s.get(p.id)?.category).toBe('M\u00e4ngel');
    const datei = { schemaVersion: 1, projectId: 'p1', pois: [{ id: 'X', title: 'T', category: zerlegt, position: [0, 0, 0] }] };
    expect(parsePoiFile(datei, 'p1', 'browser')[0]?.category).toBe(RUECKMELDUNG);
  });
  it('idZumAnlegen: belegte neue ID wird ersetzt, fortgesetzte bleibt', () => {
    const st = new PoiStore('musterhalle-nord', ids());
    const id = st.reserviereId();
    expect(st.idZumAnlegen(id, false)).toBe(id);
    st.add({ title: 'A', category: '', note: '', position: [0, 0, 0], station: null }, id);
    const neu = st.idZumAnlegen(id, false);
    expect(neu).not.toBe(id);
    expect(st.get(neu)).toBeUndefined();
    expect(st.idZumAnlegen(id, true)).toBe(id);
  });
});

describe('markerKlassen', () => {
  it('Rückmeldung bekommt die Sprechblase, alles andere die Raute', () => {
    expect(markerKlassen(RUECKMELDUNG, false)).toBe('scanview-poi-label sv-marker-rueckmeldung');
    expect(markerKlassen('Mangel', false)).toBe('scanview-poi-label sv-marker-befund');
    expect(markerKlassen('', false)).toBe('scanview-poi-label sv-marker-befund');
  });
  it('zerlegte Schreibweise gilt als Rückmeldung', () => {
    expect(markerKlassen('Ru\u0308ckmeldung', false)).toBe('scanview-poi-label sv-marker-rueckmeldung');
  });
  it('Auswahl ergänzt sv-gewaehlt', () => {
    expect(markerKlassen('Raum', true)).toBe('scanview-poi-label sv-marker-befund sv-gewaehlt');
    expect(markerKlassen(RUECKMELDUNG, true)).toBe('scanview-poi-label sv-marker-rueckmeldung sv-gewaehlt');
  });
});

describe('kategorieVorschlaege', () => {
  it('ohne Projektliste die feste Liste', () => {
    expect(kategorieVorschlaege(undefined)).toEqual(['Raum', 'Anlage', 'Zugang', 'Mangel', 'Sonstiges']);
    expect(kategorieVorschlaege('kaputt')).toEqual(['Raum', 'Anlage', 'Zugang', 'Mangel', 'Sonstiges']);
  });
  it('nur aktive, in Reihenfolge, ohne Rückmeldung, ohne Doppelte und Ungültige', () => {
    expect(kategorieVorschlaege([
      { name: 'Mangel', aktiv: true }, { name: 'Raum', aktiv: false }, { name: 'Rückmeldung', aktiv: true },
      { name: 'Frage', aktiv: true }, { name: 'Frage', aktiv: true }, { name: 42, aktiv: true }, null,
      { name: 'x'.repeat(61), aktiv: true }, { name: 'Hinweis', aktiv: 'ja' },
    ])).toEqual(['Mangel', 'Frage']);
    expect(kategorieVorschlaege([])).toEqual([]);
  });
});
