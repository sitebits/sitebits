import { describe, expect, it } from 'vitest';
import { befundImLink } from '../src/befundSteuerung';
import type { BefundMeta, PoiPersistenz } from '../src/poiPersistenz';

type Teil = Pick<PoiPersistenz, 'meta' | 'lokalId' | 'bestaetigt'>;
const meta = (serverId: string, paketRevision: number | null = 4): BefundMeta =>
  ({ serverId, paketRevision, erstelltVonName: null, geaendertAm: 1, eigen: false });
/** Persistenz mit Server-Angaben je lokaler ID; lokalId bildet die Server-ID zurück. */
const plattform = (je: Record<string, BefundMeta>, bestaetigt: string[] = []): Teil => ({
  meta: (l) => je[l] ?? null,
  lokalId: (s) => Object.keys(je).find((l) => je[l]!.serverId === s) ?? null,
  bestaetigt: (l) => bestaetigt.includes(l),
});

describe('befundImLink', () => {
  it('ohne Auswahl oder unbekannter POI: leer', () => {
    expect(befundImLink({ id: null, poi: undefined, persistenz: undefined, revision: 3 })).toEqual({});
    expect(befundImLink({ id: 'P-1', poi: undefined, persistenz: undefined, revision: 3 })).toEqual({});
  });
  it('Einzelmodus: lokale ID und Revision des Datensatzes', () => {
    expect(befundImLink({ id: 'P-1', poi: { source: 'browser' }, persistenz: undefined, revision: 3 })).toEqual({ b: 'P-1', rev: 3 });
    expect(befundImLink({ id: 'pkg-1', poi: { source: 'package' }, persistenz: {}, revision: 3 })).toEqual({ b: 'pkg-1', rev: 3 });
  });
  it('Plattform: Server-ID und Revision, auf der der Befund gesetzt wurde', () => {
    const p = plattform({ 'P-1': meta('P-1', 2) });
    expect(befundImLink({ id: 'P-1', poi: { source: 'browser' }, persistenz: p, revision: 3 })).toEqual({ b: 'P-1', rev: 2 });
  });
  it('Plattform ohne Revision am POI: die des Datensatzes', () => {
    const p = plattform({ 'P-1': meta('P-1', null) });
    expect(befundImLink({ id: 'P-1', poi: { source: 'package' }, persistenz: p, revision: 3 })).toEqual({ b: 'P-1', rev: 3 });
  });
  it('lokal umbenannter Server-POI: die Server-ID, nicht die lokale', () => {
    const p = plattform({ 'B-8-k2': meta('B-8') });
    expect(befundImLink({ id: 'B-8-k2', poi: { source: 'package' }, persistenz: p, revision: 3 })).toEqual({ b: 'B-8', rev: 4 });
  });
  it('POI aus pois.json gilt nie als Server-Befund, auch bei gleicher ID', () => {
    // Server-Angaben unter der ID, die aber auf einen anderen lokalen POI zurückzeigt.
    const p: Teil = { meta: () => meta('B-8'), lokalId: () => 'B-8-k2', bestaetigt: () => false };
    expect(befundImLink({ id: 'B-8', poi: { source: 'package' }, persistenz: p, revision: 3 })).toEqual({});
    expect(befundImLink({ id: 'pkg-1', poi: { source: 'package' }, persistenz: plattform({}), revision: 3 })).toEqual({});
  });
  it('eigener POI, nur über einen Konflikt mit gleichem Inhalt bestätigt: lokale ID (= ID beim Anlegen)', () => {
    expect(befundImLink({ id: 'P-neu', poi: { source: 'browser' }, persistenz: plattform({}, ['P-neu']), revision: 3 })).toEqual({ b: 'P-neu', rev: 3 });
  });
  it('unbestätigt oder nur lokal (Betrachter): kein Befundlink', () => {
    expect(befundImLink({ id: 'P-neu', poi: { source: 'browser' }, persistenz: plattform({}), revision: 3 })).toEqual({});
  });
});
