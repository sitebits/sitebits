import { describe, expect, it } from 'vitest';
import { artText, downloadPfad, kanalText, kontoText, LEER_TEXT, NICHT_GELADEN_TEXT, projektText, seitenPfad } from '../src/portal/lesezugriffe';
import type { Lesezugriff } from '../src/portal/lesezugriffe';
import { zeitText } from '../src/portal/verlaufText';

const e = (teil: Partial<Lesezugriff>): Lesezugriff => ({
  id: 1, zeit: 1_790_000_000, nutzerId: 7, nutzer: 'Anna', kanal: 'web', token: null, projekt: 'ost', art: 'oeffnen', objekt: '', ...teil,
});

describe('Lesezugriffe: Darstellung', () => {
  it('Art lesbar, je Objekt', () => {
    expect(artText(e({ art: 'oeffnen' }))).toBe('Projekt geöffnet');
    expect(artText(e({ art: 'paket' }))).toBe('Projektdaten geladen');
    expect(artText(e({ art: 'export', objekt: 'csv' }))).toBe('Export als CSV');
    expect(artText(e({ art: 'export', objekt: 'json' }))).toBe('Export als JSON');
    expect(artText(e({ art: 'export', objekt: 'verlauf' }))).toBe('Verlauf exportiert');
    expect(artText(e({ art: 'api', objekt: 'pois' }))).toBe('POIs gelesen');
    expect(artText(e({ art: 'api', objekt: 'messungen' }))).toBe('Messungen gelesen');
    expect(artText(e({ art: 'api', objekt: 'lesezugriffe' }))).toBe('Lesezugriffe gelesen');
    expect(artText(e({ art: 'export', objekt: 'lesezugriffe' }))).toBe('Lesezugriffe exportiert');
  });
  it('unbekannte Kombination bleibt sichtbar statt zu verschwinden', () => {
    expect(artText(e({ art: 'api', objekt: 'neu' }))).toBe('api neu');
    expect(artText(e({ art: 'paket', objekt: 'x' }))).toBe('Projektdaten geladen');
  });
  it('Konto-Name, anonymisiert und ohne Namen', () => {
    expect(kontoText(e({}))).toBe('Anna');
    expect(kontoText(e({ nutzer: 'Gelöschtes Konto 7' }))).toBe('Gelöschtes Konto 7');
    expect(kontoText(e({ nutzer: null }))).toBe('Konto 7');
  });
  it('Kanal mit Token-Namen, nie mit Wert', () => {
    expect(kanalText(e({}))).toBe('Web');
    expect(kanalText(e({ kanal: 'api', token: 'Skript' }))).toBe('API, Token „Skript“');
    expect(kanalText(e({ kanal: 'api' }))).toBe('API');
  });
  it('Zeit in deutscher Schreibweise (Europe/Berlin)', () => {
    expect(zeitText(e({}).zeit)).toMatch(/^\d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}$/);
  });
  it('Projekttitel statt ID, sonst die ID', () => {
    expect(projektText('ost', [{ id: 'ost', titel: 'Ostflügel' }])).toBe('Ostflügel');
    expect(projektText('weg', [{ id: 'ost', titel: 'Ostflügel' }])).toBe('weg');
  });
  it('Hinweis für die leere Liste', () => {
    expect(LEER_TEXT).toBe('Keine Lesezugriffe im Aufbewahrungszeitraum.');
  });
  it('Ausgangszustand nennt, dass erst auf „Anzeigen“ geladen wird', () => {
    expect(NICHT_GELADEN_TEXT).toContain('„Anzeigen“');
  });
});

describe('Lesezugriffe: Abfragepfade', () => {
  it('erste Seite ohne Filter', () => {
    expect(seitenPfad({}, null)).toBe('/api/v1/lesezugriffe?limit=50');
  });
  it('Nachladen mit Marke und Filter; Konto als „nutzer“', () => {
    expect(seitenPfad({ projekt: 'ost', konto: 7 }, 42)).toBe('/api/v1/lesezugriffe?limit=50&vor=42&projekt=ost&nutzer=7');
  });
  it('Download ohne Seitengröße, Filter bleibt', () => {
    expect(downloadPfad({})).toBe('/api/v1/lesezugriffe.jsonl');
    expect(downloadPfad({ konto: 7 })).toBe('/api/v1/lesezugriffe.jsonl?nutzer=7');
  });
  it('Projekt-ID wird kodiert', () => {
    expect(seitenPfad({ projekt: 'a&b=c' }, null)).toContain('projekt=a%26b%3Dc');
  });
});
