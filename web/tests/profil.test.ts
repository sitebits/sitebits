// Ansichtsprofile (P08-S6): wirksames Profil aus Link und Adresse, Positivliste der Kürzel je Profil.
import { describe, expect, it } from 'vitest';
import type { KeyAction } from '../src/keymap';
import { aktionErlaubt, hilfeZeigt, profilAus, schlank, werkzeugReihe, wirksamesProfil } from '../src/profil';
import { AKTIONEN } from '../src/tastenbelegung';
import type { Profil } from '../src/profil';

describe('wirksamesProfil', () => {
  it('ohne Gast: arbeiten, mit ?zeigen=1 ansehen (Alias der Messe-Links)', () => {
    expect(wirksamesProfil('?p=/data/x/')).toBe('arbeiten');
    expect(wirksamesProfil('?p=/data/x/&zeigen=1')).toBe('ansehen');
    // Kein eigener URL-Schalter: profil= in der Adresse wirkt nicht.
    expect(wirksamesProfil('?p=/data/x/&profil=messen')).toBe('arbeiten');
  });
  it('als Gast zählt das Profil des Links; die Adresse kann es nur verkleinern', () => {
    const faelle: [string, Profil, Profil][] = [
      ['?p=/data/x/&gast=1', 'ansehen', 'ansehen'],
      ['?p=/data/x/&gast=1', 'messen', 'messen'],
      ['?p=/data/x/&gast=1', 'arbeiten', 'arbeiten'],
      ['?p=/data/x/&gast=1&zeigen=1', 'arbeiten', 'ansehen'],
      ['?p=/data/x/&gast=1&zeigen=1', 'messen', 'ansehen'],
      // Kein Weg nach oben: weder ohne zeigen noch mit profil=/zeigen=0.
      ['?p=/data/x/&gast=1&profil=arbeiten', 'ansehen', 'ansehen'],
      ['?p=/data/x/&gast=1&zeigen=0', 'ansehen', 'ansehen'],
      ['?p=/data/x/&gast=1&profil=arbeiten', 'messen', 'messen'],
    ];
    for (const [suche, link, erwartet] of faelle) expect(wirksamesProfil(suche, link), `${suche} ${link}`).toBe(erwartet);
  });
  it('profilAus: Unbekanntes wird ansehen', () => {
    for (const roh of [undefined, null, '', 'Arbeiten', 'editor', 3, {}]) expect(profilAus(roh), String(roh)).toBe('ansehen');
    expect(profilAus('messen')).toBe('messen');
    expect(profilAus('arbeiten')).toBe('arbeiten');
  });
  it('schlank nur ohne arbeiten', () => {
    expect([schlank('ansehen'), schlank('messen'), schlank('arbeiten')]).toEqual([true, true, false]);
  });
});

describe('aktionErlaubt', () => {
  const werkzeug = (tool: Extract<KeyAction, { type: 'tool' }>['tool']): KeyAction => ({ type: 'tool', tool });
  const gesperrt: KeyAction[] = [
    { type: 'toggle-panel', panel: 'ebenen' }, { type: 'toggle-panel', panel: 'pois' }, { type: 'toggle-panel', panel: 'karte' },
    { type: 'toggle-panel', panel: 'ansicht' }, { type: 'toggle-layer', index: 1 }, { type: 'toggle-list' },
    { type: 'befund' }, { type: 'teilen' }, { type: 'schnittbox', modus: 'translate' }, { type: 'begehen-start' },
    { type: 'koordinaten' }, { type: 'koordinate-kopieren' }, { type: 'standpunkt', richtung: 1 }, werkzeug('poi'),
  ];
  it('ansehen: nur Navigieren, eine Strecke und Hilfe', () => {
    for (const a of gesperrt) expect(aktionErlaubt('ansehen', a), JSON.stringify(a)).toBe(false);
    for (const t of ['polyline', 'area', 'height'] as const) expect(aktionErlaubt('ansehen', werkzeug(t)), t).toBe(false);
    expect(aktionErlaubt('ansehen', { type: 'delete-selection' })).toBe(false);
    for (const a of [werkzeug('navigate'), werkzeug('distance'), { type: 'tastenhilfe' }, { type: 'alles-zeigen' }, { type: 'finish' },
      { type: 'cancel-drawing' }] as KeyAction[]) expect(aktionErlaubt('ansehen', a), JSON.stringify(a)).toBe(true);
  });
  it('messen: dazu alle Messwerkzeuge, Löschen und Kantenfang, sonst nichts', () => {
    for (const a of gesperrt) expect(aktionErlaubt('messen', a), JSON.stringify(a)).toBe(false);
    for (const t of ['distance', 'polyline', 'area', 'height'] as const) expect(aktionErlaubt('messen', werkzeug(t)), t).toBe(true);
    expect(aktionErlaubt('messen', { type: 'delete-selection' })).toBe(true);
    expect(aktionErlaubt('messen', { type: 'toggle-snap' })).toBe(true);
  });
  it('arbeiten: alles wie bisher', () => {
    for (const a of gesperrt) expect(aktionErlaubt('arbeiten', a), JSON.stringify(a)).toBe(true);
  });
});

describe('Controller-Werkzeugwechsel und Tastenhilfe je Profil', () => {
  const ALLE = ['navigate', 'distance', 'polyline', 'area', 'height', 'poi'] as const;
  it('werkzeugReihe: schlanke Profile nur Navigieren und ihre Messwerkzeuge, nie POI', () => {
    expect(werkzeugReihe('ansehen', ALLE)).toEqual(['navigate', 'distance']);
    expect(werkzeugReihe('messen', ALLE)).toEqual(['navigate', 'distance', 'polyline', 'area', 'height']);
    expect(werkzeugReihe('arbeiten', ALLE)).toEqual([...ALLE]);
    expect(aktionErlaubt('ansehen', { type: 'werkzeug-wechsel', richtung: 1 })).toBe(true);
  });
  it('hilfeZeigt: in „ansehen“ nur Navigieren, Strecke, Alles zeigen und Hilfe', () => {
    const ids = (p: Profil) => AKTIONEN.filter((a) => hilfeZeigt(p, a.id)).map((a) => a.id);
    expect(ids('ansehen')).toEqual(['werkzeug.navigate', 'werkzeug.distance', 'alles-zeigen', 'tastenhilfe']);
    expect(ids('messen')).toEqual(['werkzeug.navigate', 'werkzeug.distance', 'werkzeug.polyline', 'werkzeug.area', 'werkzeug.height',
      'kantenfang', 'alles-zeigen', 'tastenhilfe']);
    expect(ids('arbeiten')).toEqual(AKTIONEN.map((a) => a.id));
  });
});
