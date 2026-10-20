import { describe, expect, it } from 'vitest';
import { BESCHREIBUNG_MAX, geaenderteFelder, pruefeMeta, TITEL_MAX, uebernehmeKonflikt } from '../src/portal/metaRegeln';

describe('pruefeMeta', () => {
  it('lässt normale Eingaben und Zeilenumbrüche in der Beschreibung zu', () => {
    expect(pruefeMeta('Musterhalle Nord', 'Außen und innen\n68 Standpunkte')).toBeNull();
    expect(pruefeMeta('', '')).toBeNull(); // leer = Paketwerte gelten wieder
  });
  it('Längen wie auf dem Server', () => {
    expect(pruefeMeta('x'.repeat(TITEL_MAX), '')).toBeNull();
    expect(pruefeMeta('x'.repeat(TITEL_MAX + 1), '')).toMatch(/höchstens 120/);
    expect(pruefeMeta('', 'x'.repeat(BESCHREIBUNG_MAX + 1))).toMatch(/höchstens 2000/);
  });
  it('Steuerzeichen', () => {
    expect(pruefeMeta('a\nb', '')).toMatch(/Titel/);
    expect(pruefeMeta('a', 'b\u0000c')).toMatch(/Steuerzeichen/);
    expect(pruefeMeta('a', 'b\tc\r\nd')).toBeNull();
  });
});

// Nur tatsächlich geänderte Felder werden ans PATCH geschickt — sonst
// schreibt ein Speichern, das nur die Beschreibung ändern will, den (unangetasteten, aufgelösten)
// Titel als stillen Override fest.
describe('geaenderteFelder', () => {
  it('meldet nichts, wenn sich nichts geändert hat', () => {
    expect(geaenderteFelder({ titel: 'A', beschreibung: 'B' }, { titel: 'A', beschreibung: 'B' })).toEqual({});
  });
  it('meldet nur die Beschreibung, wenn nur sie sich ändert', () => {
    expect(geaenderteFelder({ titel: 'A', beschreibung: 'B' }, { titel: 'A', beschreibung: 'C' })).toEqual({ beschreibung: 'C' });
  });
  it('meldet nur den Titel, wenn nur er sich ändert', () => {
    expect(geaenderteFelder({ titel: 'A', beschreibung: 'B' }, { titel: 'Z', beschreibung: 'B' })).toEqual({ titel: 'Z' });
  });
  it('meldet beide, wenn beide sich ändern', () => {
    expect(geaenderteFelder({ titel: 'A', beschreibung: 'B' }, { titel: 'Z', beschreibung: 'C' })).toEqual({ titel: 'Z', beschreibung: 'C' });
  });
});

// Der bei 409 gelieferte aktuelle Stand muss dauerhaft in das Projektobjekt
// der Karte übernommen werden — sonst bleibt `version` nach „Abbrechen“ veraltet und ein erneutes
// „Bearbeiten“ läuft garantiert wieder in einen Konflikt.
describe('uebernehmeKonflikt', () => {
  it('überschreibt titel/beschreibung/version im übergebenen Objekt', () => {
    const p = { titel: 'Alt', beschreibung: 'Alt', version: 3 };
    uebernehmeKonflikt(p, { titel: 'Von B', beschreibung: 'Neu', version: 4 });
    expect(p).toEqual({ titel: 'Von B', beschreibung: 'Neu', version: 4 });
  });
});
