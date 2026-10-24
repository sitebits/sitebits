// Eingabeschicht Stufe 3: Belegung je Ansicht, Symbole je Controllertyp, Kontextleiste.
import { describe, expect, it } from 'vitest';
import { PAD_VORGABE } from '../src/gamepad';
import { belegungsTabelle, kontextLeiste, symbol } from '../src/padModus';
import type { PadKontext } from '../src/padModus';

const k = (over: Partial<PadKontext> = {}): PadKontext => ({ modus: 'uebersicht', werkzeug: false, zeichnung: false, rueckfrage: false, ...over });
const wirkung = (t: { eingabe: string; wirkung: string }[], eingabe: string): string | undefined => t.find((z) => z.eingabe === eingabe)?.wirkung;
const leiste = (kk: PadKontext, typ: 'playstation' | 'xbox' | 'neutral' = 'playstation'): string =>
  kontextLeiste(kk, typ, PAD_VORGABE).map((e) => `${e.knopf} ${e.text}`).join(' · ');

describe('Symbole', () => {
  it('PlayStation, Xbox und neutral für dieselben Knöpfe', () => {
    expect([0, 1, 2, 3].map((i) => symbol('playstation', i))).toEqual(['✕', '○', '□', '△']);
    expect([0, 1, 2, 3].map((i) => symbol('xbox', i))).toEqual(['A', 'B', 'X', 'Y']);
    expect([0, 1, 2, 3].map((i) => symbol('neutral', i))).toEqual(['Unten', 'Rechts', 'Links', 'Oben']);
    expect([4, 5, 6, 7, 8, 9].map((i) => symbol('playstation', i))).toEqual(['L1', 'R1', 'L2', 'R2', 'Create', 'Options']);
    expect([4, 5, 6, 7, 8, 9].map((i) => symbol('xbox', i))).toEqual(['LB', 'RB', 'LT', 'RT', 'Ansicht', 'Menü']);
    expect(symbol('playstation', 17)).toBe('Touchpad');
  });
  it('nicht belegt oder unbekannt ist leer', () => {
    expect(symbol('xbox', -1)).toBe('');
    expect(symbol('xbox', 99)).toBe('');
    expect(symbol('playstation', 1.5)).toBe('');
  });
});

describe('Ein Schema für alle Ansichten', () => {
  const ps = (m: PadKontext['modus']) => belegungsTabelle(m, 'playstation', PAD_VORGABE);
  it('✕ setzt bzw. bestätigt überall, ○ geht überall zurück', () => {
    expect(wirkung(ps('uebersicht'), '✕')).toMatch(/setzen/);
    expect(wirkung(ps('begehen'), '✕')).toMatch(/setzen/);
    expect(wirkung(ps('panorama'), '✕')).toMatch(/setzen/);
    expect(wirkung(ps('menue'), '✕')).toBe('Auswählen');
    expect(wirkung(ps('uebersicht'), '○')).toMatch(/abbrechen/);
    expect(wirkung(ps('begehen'), '○')).toMatch(/Zurück/);
    expect(wirkung(ps('panorama'), '○')).toMatch(/verlassen/);
    expect(wirkung(ps('menue'), '○')).toBe('Schließen');
  });
  it('Auslöser: Höhe im Begehen, Zoom in Übersicht und Panorama; L1/R1 überall Werkzeug', () => {
    expect(wirkung(ps('begehen'), 'L2 / R2')).toBe('Abwärts / aufwärts');
    expect(wirkung(ps('uebersicht'), 'L2 / R2')).toMatch(/zoomen/);
    expect(wirkung(ps('panorama'), 'L2 / R2')).toMatch(/zoomen/);
    for (const m of ['uebersicht', 'begehen', 'panorama'] as const) expect(wirkung(ps(m), 'L1 / R1')).toBe('Werkzeug wechseln');
  });
  it('△ startet in der Übersicht das Begehen und setzt dort einen Befund; Options ist überall das Menü', () => {
    expect(wirkung(ps('uebersicht'), '△')).toBe('Begehen starten');
    expect(wirkung(ps('begehen'), '△')).toBe('Befund setzen');
    for (const m of ['uebersicht', 'begehen', 'panorama'] as const) expect(wirkung(ps(m), 'Options')).toBe('Menü');
    for (const m of ['uebersicht', 'begehen', 'panorama'] as const) expect(wirkung(ps(m), 'Create')).toBe('Belegung ein/aus');
  });
  it('Xbox-Tabelle hat dieselben Zeilen mit Xbox-Namen', () => {
    const x = belegungsTabelle('begehen', 'xbox', PAD_VORGABE);
    expect(x.map((z) => z.wirkung)).toEqual(ps('begehen').map((z) => z.wirkung));
    expect(wirkung(x, 'A')).toMatch(/setzen/);
    expect(wirkung(x, 'LT / RT')).toBe('Abwärts / aufwärts');
  });
  it('folgt der Umbelegung und lässt nicht belegte Aktionen weg', () => {
    const t = belegungsTabelle('begehen', 'xbox', { ...PAD_VORGABE, kontext: 2, zielen: -1 });
    expect(wirkung(t, 'X')).toBe('Befund setzen');
    expect(t.some((z) => z.wirkung.startsWith('Zielen'))).toBe(false);
  });
});

describe('Kontextleiste', () => {
  it('Übersicht ohne Werkzeug: Begehen, Alles zeigen, Belegung', () => {
    expect(leiste(k())).toBe('△ Begehen · R3 Alles zeigen · Create Belegung');
  });
  it('Übersicht mit Werkzeug und offener Zeichnung', () => {
    expect(leiste(k({ werkzeug: true, zeichnung: true }), 'xbox')).toBe('A Punkt setzen · B Abbrechen · Y Begehen · RS Alles zeigen · Ansicht Belegung');
  });
  it('Begehen: Setzen nur mit Werkzeug, ○ beendet bzw. bricht die Zeichnung ab', () => {
    expect(leiste(k({ modus: 'begehen' }))).toBe('R1 Werkzeug · △ Befund · □ Zielen · ○ Beenden · Create Belegung');
    expect(leiste(k({ modus: 'begehen', werkzeug: true, zeichnung: true }))).toBe('✕ Punkt setzen · △ Befund · □ Zielen · ○ Abbrechen · Create Belegung');
  });
  it('Rückfrage und Menü haben Vorrang', () => {
    expect(leiste(k({ modus: 'begehen', rueckfrage: true }))).toBe('✕ Bestätigen · Kreuz ↑↓ Wechseln · ○ Abbrechen');
    expect(leiste(k({ modus: 'menue' }), 'neutral')).toBe('Unten Auswählen · Rechts Schließen · Kreuz ↑↓ Auswahl');
  });
  it('Panorama mit Werkzeug', () => {
    expect(leiste(k({ modus: 'panorama', werkzeug: true }))).toBe('✕ Punkt setzen · △ Befund · ○ Panorama verlassen · Create Belegung');
  });
  it('höchstens fünf Einträge', () => {
    for (const m of ['uebersicht', 'begehen', 'panorama', 'menue'] as const) {
      expect(kontextLeiste(k({ modus: m, werkzeug: true, zeichnung: true }), 'xbox', PAD_VORGABE).length).toBeLessThanOrEqual(5);
    }
  });
});
