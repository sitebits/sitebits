import { describe, expect, it } from 'vitest';
import { befundAusLink, befundLink, revHinweis, seitentitel } from '../src/befundLink';
import { befundImLink } from '../src/befundSteuerung';
import { PoiPersistence, PoiStore } from '../src/poiStore';
import { ServerPoiPersistenz } from '../src/serverPois';
import { fakePlattform } from './fakePlattform';
import type { BefundMeta, PoiPersistenz } from '../src/poiPersistenz';
import type { Poi } from '../src/poiStore';

type Teil = Pick<PoiPersistenz, 'meta' | 'lokalId' | 'bestaetigt'>;
const meta = (serverId: string, paketRevision: number | null = 4): BefundMeta =>
  ({ serverId, paketRevision, erstelltVonName: null, geaendertAm: 1, eigen: true });
const plattform = (je: Record<string, BefundMeta>, bestaetigt: string[] = []): Teil => ({
  meta: (l) => je[l] ?? null,
  lokalId: (s) => Object.keys(je).find((l) => je[l]!.serverId === s) ?? null,
  bestaetigt: (l) => bestaetigt.includes(l),
});
const store = (je: Record<string, Poi['source']>) => (id: string): Pick<Poi, 'source'> | undefined =>
  je[id] ? { source: je[id] } : undefined;

describe('befundLink', () => {
  it('setzt Herkunft, Pfad, Suche und Hash zusammen, ?p= bleibt unverändert', () => {
    expect(
      befundLink({ origin: 'https://scan.example.org', pathname: '/ansicht', search: '?p=/data/kirche-v2/', hash: '#cam=1.000,2.000,3.000&b=P-1&rev=2' }),
    ).toBe('https://scan.example.org/ansicht?p=/data/kirche-v2/#cam=1.000,2.000,3.000&b=P-1&rev=2');
    expect(befundLink({ origin: 'http://localhost:18290', pathname: '/', search: '', hash: '#b=P-1' })).toBe('http://localhost:18290/#b=P-1');
  });
});

describe('revHinweis', () => {
  it('ohne Revision im Link oder gleicher Stand: kein Hinweis', () => {
    expect(revHinweis(undefined, 5)).toBeNull();
    expect(revHinweis(5, 5)).toBeNull();
  });
  it('Link mit kleinerem Stand: älterer Stand, mit beiden Ständen', () => {
    expect(revHinweis(3, 5)).toBe('Befund wurde auf einem älteren Stand des Datensatzes gesetzt (Stand 3, aktuell 5).');
    expect(revHinweis(0, 1)).toBe('Befund wurde auf einem älteren Stand des Datensatzes gesetzt (Stand 0, aktuell 1).');
  });
  it('Link mit größerem Stand: neuerer Stand, mit beiden Ständen', () => {
    expect(revHinweis(6, 5)).toBe('Befund wurde auf einem neueren Stand des Datensatzes gesetzt (Stand 6, aktuell 5).');
    expect(revHinweis(2, 1)).not.toContain('älter');
  });
});

describe('seitentitel', () => {
  it('mit und ohne ausgewählten Befund', () => {
    expect(seitentitel('Riss Nordwand', 'Kirche')).toBe('Riss Nordwand – Kirche – siteBits');
    expect(seitentitel(null, 'Kirche')).toBe('Kirche – siteBits');
  });
  it('langer Titel wird nicht gekürzt', () => {
    const lang = 'Riss '.repeat(40).trim();
    expect(seitentitel(lang, 'Kirche')).toBe(`${lang} – Kirche – siteBits`);
  });
});

describe('befundAusLink', () => {
  it('Einzelmodus: die lokale ID, wenn es den POI gibt', () => {
    expect(befundAusLink('P-1', { poi: store({ 'P-1': 'browser' }), persistenz: undefined })).toBe('P-1');
    expect(befundAusLink('pkg-1', { poi: store({ 'pkg-1': 'package' }), persistenz: {} })).toBe('pkg-1');
    expect(befundAusLink('P-weg', { poi: store({}), persistenz: undefined })).toBeNull();
  });
  it('Plattform: Server-ID → lokale ID, auch nach Kollision mit einer Paket-ID', () => {
    const p = plattform({ 'P-1': meta('P-1'), 'X-1-k2': meta('X-1') });
    const s = store({ 'P-1': 'browser', 'X-1': 'package', 'X-1-k2': 'package' });
    expect(befundAusLink('P-1', { poi: s, persistenz: p })).toBe('P-1');
    expect(befundAusLink('X-1', { poi: s, persistenz: p })).toBe('X-1-k2');
  });
  it('Plattform: ein POI aus pois.json mit derselben ID gilt nie als Befund', () => {
    const p = plattform({});
    expect(befundAusLink('pkg-1', { poi: store({ 'pkg-1': 'package' }), persistenz: p })).toBeNull();
  });
  it('Plattform: die lokale ID eines Server-POIs ist kein Befundlink', () => {
    const p = plattform({ 'X-1-k2': meta('X-1') });
    expect(befundAusLink('X-1-k2', { poi: store({ 'X-1': 'package', 'X-1-k2': 'package' }), persistenz: p })).toBeNull();
  });
  it('Plattform: nie über die lokale ID, auch nicht für einen bestätigten eigenen POI ohne Server-Zuordnung', () => {
    const p = plattform({}, ['P-neu']);
    expect(befundAusLink('P-neu', { poi: store({ 'P-neu': 'browser' }), persistenz: p })).toBeNull();
  });
  it('Plattform: fremde oder unbekannte ID → nicht gefunden', () => {
    const p = plattform({ 'P-1': meta('P-1') });
    expect(befundAusLink('P-gibtesnicht', { poi: store({ 'P-1': 'browser' }), persistenz: p })).toBeNull();
    // lokalId zeigt auf einen POI, der nicht (mehr) im Store ist
    expect(befundAusLink('P-1', { poi: store({}), persistenz: p })).toBeNull();
  });
  it('Rundreise mit befundImLink: geteilte ID öffnet genau diesen POI', () => {
    const p = plattform({ 'P-1': meta('P-1'), 'X-1-k2': meta('X-1') });
    const quellen: Record<string, Poi['source']> = { 'P-1': 'browser', 'X-1': 'package', 'X-1-k2': 'package' };
    for (const id of ['P-1', 'X-1-k2']) {
      const { b } = befundImLink({ id, poi: { source: quellen[id]! }, persistenz: p, revision: 3 });
      expect(b).toBeDefined();
      expect(befundAusLink(b!, { poi: store(quellen), persistenz: p })).toBe(id);
    }
    // Paket-POI ohne Server-Angaben: weder teilbar noch zu öffnen
    expect(befundImLink({ id: 'X-1', poi: { source: 'package' }, persistenz: p, revision: 3 })).toEqual({});
  });
});

describe('befundAusLink mit der Persistenz des Plattformmodus', () => {
  const daten = (title: string) => ({ title, category: 'Riss', note: '', position: [1, 2, 3], station: null });
  it('Server liefert B-8 und B-8-k2, das Paket trägt B-8: jeder Link öffnet genau seinen Server-POI', async () => {
    const f = fakePlattform();
    f.eintrag('pois', 'B-8', daten('Server B-8'));
    f.eintrag('pois', 'B-8-k2', daten('Server B-8-k2'));
    const lokal = new PoiPersistence({ getItem: () => null, setItem: () => undefined }, 'scanview:pois:p1');
    const s = new PoiStore('p1', () => 'P-neu');
    s.setPackagePois([{ id: 'B-8', title: 'Paket B-8', category: '', note: '', position: [0, 0, 1], station: null, source: 'package' }]);
    const sp = new ServerPoiPersistenz({ projectId: 'p1', schreibbar: false, lokal, melde: () => undefined, fetchFn: f.fetchFn });
    await sp.load(s);
    const oeffne = (b: string): string | null => befundAusLink(b, { poi: (id) => s.get(id), persistenz: sp });
    const titel = (b: string): string | undefined => {
      const l = oeffne(b);
      return l === null ? undefined : s.get(l)?.title;
    };
    expect(titel('B-8')).toBe('Server B-8');
    expect(titel('B-8-k2')).toBe('Server B-8-k2');
    // Die lokale ID des einen ist nicht der Link des anderen; der Paket-POI hat keinen.
    expect(oeffne('B-8')).not.toBe('B-8');
    expect(oeffne('B-8-k2')).not.toBe(oeffne('B-8'));
    // Rundreise: geteilt wird die Server-ID, geöffnet derselbe lokale POI.
    for (const l of [oeffne('B-8')!, oeffne('B-8-k2')!]) {
      const { b } = befundImLink({ id: l, poi: s.get(l), persistenz: sp, revision: 1 });
      expect(oeffne(b!)).toBe(l);
    }
    expect(befundImLink({ id: 'B-8', poi: s.get('B-8'), persistenz: sp, revision: 1 })).toEqual({});
  });
});
