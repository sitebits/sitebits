import { describe, expect, it } from 'vitest';
import { darfVerwalten, einladungErgebnisText, hinzufuegen, umschalten, verschiebe, vergebbareRollen, zeileAenderbar } from '../src/portal/projektEinstellungenModell';
import type { Beteiligter, Kategorie } from '../src/portal/projektEinstellungenModell';

const b = (rolle: string, admin = false): Beteiligter => ({ nutzerId: 1, nutzer: 'X', rolle, gewerk: null, admin });
const liste: Kategorie[] = [{ name: 'Raum', aktiv: true }, { name: 'Mangel', aktiv: true }, { name: 'Sonstiges', aktiv: false }];

describe('Projekt-Einstellungen', () => {
  it('verwalten dürfen nur Projektleitung und Admin', () => {
    expect(['projektleitung', 'admin'].map(darfVerwalten)).toEqual([true, true]);
    expect(['bearbeiter', 'betrachter', 'rueckmelder', '', 'chef'].map(darfVerwalten)).toEqual([false, false, false, false, false]);
  });
  it('Rollenauswahl: Leitung nur 1–3, Admin zusätzlich Projektleitung, sonst nichts', () => {
    expect(vergebbareRollen('projektleitung').map((r) => r.wert)).toEqual(['betrachter', 'rueckmelder', 'bearbeiter']);
    expect(vergebbareRollen('admin').map((r) => r.wert)).toEqual(['betrachter', 'rueckmelder', 'bearbeiter', 'projektleitung']);
    expect(vergebbareRollen('bearbeiter')).toEqual([]);
    expect(vergebbareRollen('admin')[3]!.text).toBe('Projektleitung');
  });
  it('Zeilen: Leitung ändert keine Admins und keine Projektleitung, Admin alles', () => {
    expect(zeileAenderbar(b('bearbeiter'), 'projektleitung')).toBe(true);
    expect(zeileAenderbar(b('projektleitung'), 'projektleitung')).toBe(false);
    expect(zeileAenderbar(b('bearbeiter', true), 'projektleitung')).toBe(false);
    expect(zeileAenderbar(b('projektleitung'), 'admin')).toBe(true);
    expect(zeileAenderbar(b('betrachter'), 'bearbeiter')).toBe(false);
  });
  it('Kategorien verschieben, umschalten, hinzufügen', () => {
    expect(verschiebe(liste, 1, -1).map((k) => k.name)).toEqual(['Mangel', 'Raum', 'Sonstiges']);
    expect(verschiebe(liste, 0, -1)).toEqual(liste);
    expect(verschiebe(liste, 2, 1)).toEqual(liste);
    expect(umschalten(liste, 2)[2]).toEqual({ name: 'Sonstiges', aktiv: true });
    expect(liste[2]!.aktiv).toBe(false); // unverändert
    expect(hinzufuegen(liste, '  Frage ')).toEqual([...liste, { name: 'Frage', aktiv: true }]);
    expect(hinzufuegen(liste, 'mangel')).toEqual({ fehler: 'Diese Kategorie gibt es schon.' });
    expect(hinzufuegen(liste, 'Rückmeldung')).toEqual({ fehler: '„Rückmeldung“ ist für Rückmeldungen reserviert.' });
    expect(hinzufuegen(liste, '   ')).toEqual({ fehler: 'Bitte einen Namen eingeben.' });
    expect(hinzufuegen(liste, 'x'.repeat(61))).toEqual({ fehler: 'Höchstens 60 Zeichen.' });
    const voll = Array.from({ length: 30 }, (_, i) => ({ name: `K${i}`, aktiv: true }));
    expect(hinzufuegen(voll, 'Neu')).toEqual({ fehler: 'Höchstens 30 Kategorien.' });
  });
  it('Ergebnistexte der Einladung', () => {
    expect(einladungErgebnisText({ ergebnis: 'eingeladen', link: 'l', einladung: { id: 'a', name: 'n', email: 'e', rolle: 'betrachter', gewerk: null, erstelltAm: 0, ablauf: 0 } }))
      .toBe('Einladung angelegt. Den Link jetzt kopieren und selbst weitergeben.');
    expect(einladungErgebnisText({ ergebnis: 'zugriff-erteilt', gesperrt: false, text: 'Konto vorhanden — Zugriff erteilt.' })).toBe('Konto vorhanden — Zugriff erteilt.');
  });
});
