import { describe, expect, it } from 'vitest';
import {
  AKTIONEN, HALTE_AKTIONEN, aktionFuer, akkordVon, belege, belegePad, beschriftung, istFest, konflikte, kuerzelFuer, ladeBelegung, normalisiere,
  padBelegung, padKonflikte, speichereBelegung, vorgabeBelegung,
} from '../src/tastenbelegung';
import { PAD_NICHT_BELEGT, PAD_VORGABE } from '../src/gamepad';
import type { Akkord } from '../src/tastenbelegung';

const k = (taste: string, over: Partial<Akkord> = {}): Akkord => ({ taste, strg: false, alt: false, ...over });

describe('Vorgabebelegung', () => {
  it('entspricht den heutigen Kürzeln und der Bedienvorgabe', () => {
    const b = vorgabeBelegung();
    const v = (id: Parameters<typeof akkordVon>[1]) => akkordVon(b, id)?.taste;
    expect([v('werkzeug.navigate'), v('werkzeug.distance'), v('werkzeug.polyline'), v('werkzeug.area'), v('werkzeug.height'), v('werkzeug.poi')])
      .toEqual(['v', 'd', 'l', 'a', 'h', 'p']);
    expect([v('panel.ansicht'), v('panel.pois'), v('panel.karte'), v('panel.ebenen')]).toEqual(['i', 'o', 'k', 'n']);
    expect([v('messliste'), v('kantenfang'), v('alles-zeigen'), v('schnittbox.verschieben'), v('schnittbox.skalieren')])
      .toEqual(['m', 'e', 'f', 'g', 's']);
    expect([v('begehen'), v('tastenhilfe'), v('standpunkt.voriger'), v('standpunkt.naechster')]).toEqual(['w', '?', '[', ']']);
    expect([v('begehen.vor'), v('begehen.zurueck'), v('begehen.links'), v('begehen.rechts'), v('begehen.runter'), v('begehen.hoch')])
      .toEqual(['w', 's', 'a', 'd', 'q', 'e']);
    expect([v('begehen.schnell'), v('begehen.zielen'), v('begehen.setzen'), v('begehen.ansage')]).toEqual(['Shift', 'z', ' ', 'r']);
    expect([v('begehen.drehen-links'), v('begehen.drehen-rechts'), v('begehen.neigen-hoch'), v('begehen.neigen-runter')])
      .toEqual(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']);
    expect([v('begehen.werkzeug-voriges'), v('begehen.werkzeug-naechstes')]).toEqual(['x', 'c']);
  });
  it('hat keine Konflikte und keine festen Tasten', () => {
    expect(konflikte(vorgabeBelegung())).toEqual([]);
    for (const a of AKTIONEN) if (a.vorgabe) expect(istFest(a.vorgabe)).toBe(false);
  });
  it('jede Aktion hat einen deutschen Titel ohne Spielvokabular', () => {
    for (const a of AKTIONEN) {
      expect(a.titel.length).toBeGreaterThan(2);
      expect(a.titel).not.toMatch(/spieler|level|ping|quest/i);
    }
  });
});

describe('normalisiere', () => {
  it('Buchstaben klein, Umschalt egal, Meta zählt als Strg', () => {
    expect(normalisiere({ key: 'W', ctrlKey: false, metaKey: false, altKey: false })).toEqual(k('w'));
    expect(normalisiere({ key: 'd', ctrlKey: false, metaKey: true, altKey: false })).toEqual(k('d', { strg: true }));
  });
  it('AltGr (Windows: Strg+Alt) gilt als ohne Modifikator', () => {
    expect(normalisiere({ key: '[', ctrlKey: true, metaKey: false, altKey: true, altGraph: true })).toEqual(k('['));
  });
  it('benannte Tasten bleiben', () => {
    expect(normalisiere({ key: 'ArrowLeft', ctrlKey: false, metaKey: false, altKey: false }).taste).toBe('ArrowLeft');
    expect(normalisiere({ key: ' ', ctrlKey: false, metaKey: false, altKey: false }).taste).toBe(' ');
  });
});

describe('aktionFuer', () => {
  it('Ebenen getrennt: w global = begehen, w im Begehen = vor', () => {
    const b = vorgabeBelegung();
    expect(aktionFuer(k('w'), 'global', b)).toBe('begehen');
    expect(aktionFuer(k('w'), 'begehen', b)).toBe('begehen.vor');
    expect(aktionFuer(k('a'), 'global', b)).toBe('werkzeug.area');
  });
  it('Einzeltasten aus: global ohne Modifikator wirkungslos, Begehen bleibt', () => {
    const b = { ...vorgabeBelegung(), einzeltastenAus: true };
    expect(aktionFuer(k('d'), 'global', b)).toBeNull();
    expect(aktionFuer(k('w'), 'begehen', b)).toBe('begehen.vor');
    const mitStrg = belege(b, 'werkzeug.distance', k('d', { strg: true, alt: true }));
    expect(mitStrg.ok).toBe(true);
    if (mitStrg.ok) expect(aktionFuer(k('d', { strg: true, alt: true }), 'global', mitStrg.belegung)).toBe('werkzeug.distance');
  });
  it('entfernte Belegung löst nichts aus', () => {
    const r = belege(vorgabeBelegung(), 'messliste', null);
    expect(r.ok).toBe(true);
    if (r.ok) expect(aktionFuer(k('m'), 'global', r.belegung)).toBeNull();
  });
});

describe('istFest', () => {
  it('Grenzen von istFest: feste Tasten und reine Modifikatoren', () => {
    // Fest: Tab (auch mit Modifikatoren), Esc, Enter, Delete, Backspace, Ziffern 1-9, Strg+Z
    expect(istFest(k('Tab'))).toBe(true);
    expect(istFest(k('Tab', { strg: true }))).toBe(true); // Umschalt+Tab ist auch fest
    expect(istFest(k('Escape'))).toBe(true);
    expect(istFest(k('Enter'))).toBe(true);
    expect(istFest(k('Delete'))).toBe(true);
    expect(istFest(k('Backspace'))).toBe(true);
    expect(istFest(k('3'))).toBe(true);
    expect(istFest(k('z', { strg: true }))).toBe(true);
    // Fest: reine Modifikatoren
    expect(istFest(k('Control'))).toBe(true);
    expect(istFest(k('Alt'))).toBe(true);
    expect(istFest(k('Meta'))).toBe(true);
    expect(istFest(k('AltGraph'))).toBe(true);
    // Belegbar: Modifikatoren mit anderen Tasten
    expect(istFest(k('z', { strg: true, alt: true }))).toBe(false);
    expect(istFest(k('3', { strg: true }))).toBe(false);
    expect(istFest(k('3', { alt: true }))).toBe(false);
  });
});

describe('belege', () => {
  it('feste Tasten sind nicht belegbar', () => {
    const b = vorgabeBelegung();
    for (const f of [k('Tab'), k('Escape'), k('Enter'), k('Delete'), k('Backspace'), k('3'), k('z', { strg: true })]) {
      expect(belege(b, 'messliste', f)).toEqual({ ok: false, grund: 'fest' });
    }
  });
  it('Pfeiltasten, Leertaste, Pos1 und Ende sind in der Ebene global fest, auch mit Strg/Alt', () => {
    const b = vorgabeBelegung();
    for (const t of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', ' ', 'Home', 'End']) {
      expect(belege(b, 'messliste', k(t))).toEqual({ ok: false, grund: 'fest' });
      expect(belege(b, 'tastenhilfe', k(t, { strg: true }))).toEqual({ ok: false, grund: 'fest' });
      expect(belege(b, 'kantenfang', k(t, { alt: true }))).toEqual({ ok: false, grund: 'fest' });
    }
  });
  it('in der Ebene begehen bleiben Pfeiltasten, Leertaste, Pos1 und Ende belegbar', () => {
    const b = vorgabeBelegung();
    expect(belege(b, 'begehen.ansage', k('Home')).ok).toBe(true);
    expect(belege(b, 'begehen.ansage', k('End')).ok).toBe(true);
    // Pfeile und Leertaste sind dort Vorgaben: Tausch innerhalb der Ebene ist ein Konflikt, kein „fest“.
    expect(belege(b, 'begehen.ansage', k('ArrowLeft'))).toEqual({ ok: false, grund: 'konflikt', mit: 'begehen.drehen-links' });
    expect(belege(b, 'begehen.ansage', k(' '))).toEqual({ ok: false, grund: 'konflikt', mit: 'begehen.setzen' });
  });
  it('Konflikt in derselben Ebene wird gemeldet, mit der anderen Aktion', () => {
    expect(belege(vorgabeBelegung(), 'messliste', k('d'))).toEqual({ ok: false, grund: 'konflikt', mit: 'werkzeug.distance' });
    expect(belege(vorgabeBelegung(), 'begehen.ansage', k('q'))).toEqual({ ok: false, grund: 'konflikt', mit: 'begehen.runter' });
  });
  it('ebenenübergreifend kein Konflikt (Begehen überdeckt global)', () => {
    expect(belege(vorgabeBelegung(), 'begehen.ansage', k('m')).ok).toBe(true);
  });
  it('belegt unveränderlich (Eingabe bleibt gleich)', () => {
    const b = vorgabeBelegung();
    const r = belege(b, 'messliste', k('j'));
    expect(r.ok && akkordVon(r.belegung, 'messliste')?.taste).toBe('j');
    expect(akkordVon(b, 'messliste')?.taste).toBe('m');
  });
});

describe('beschriftung', () => {
  it('lesbar für Hinweise und Knöpfe', () => {
    expect(beschriftung(k('d'))).toBe('D');
    expect(beschriftung(k('d', { strg: true, alt: true }))).toBe('Strg+Alt+D');
    expect(beschriftung(k(' '))).toBe('Leertaste');
    expect(beschriftung(k('ArrowLeft'))).toBe('←');
    expect(beschriftung(k('Shift'))).toBe('Umschalt');
    expect(beschriftung(null)).toBe('');
  });
});

describe('Speichern/Laden', () => {
  it('Rundreise', () => {
    const r = belege({ ...vorgabeBelegung(), einzeltastenAus: true }, 'messliste', k('j', { alt: true }));
    if (!r.ok) throw new Error('belege');
    expect(ladeBelegung(speichereBelegung(r.belegung))).toEqual(r.belegung);
  });
  it('Rundreise mit explizitem null', () => {
    let b = vorgabeBelegung();
    const r1 = belege(b, 'messliste', null);
    if (!r1.ok) throw new Error('belege messliste null');
    b = r1.belegung;
    const r2 = belege(b, 'kantenfang', k('j'));
    if (!r2.ok) throw new Error('belege kantenfang');
    b = r2.belegung;
    expect(ladeBelegung(speichereBelegung(b))).toEqual(b);
    expect(akkordVon(b, 'messliste')).toBeNull();
    expect(akkordVon(b, 'kantenfang')?.taste).toBe('j');
  });
  it('Tausch zweier Tasten überlebt die Rundreise (beide Schlüsselreihenfolgen)', () => {
    // distance und messliste tauschen: distance='m', messliste='d'
    const data = { schema: 1, einzeltastenAus: false, tasten: {
      'werkzeug.distance': { taste: 'm', strg: false, alt: false },
      'messliste': { taste: 'd', strg: false, alt: false },
    } };
    const b1 = ladeBelegung(JSON.stringify(data));
    expect(akkordVon(b1, 'werkzeug.distance')?.taste).toBe('m');
    expect(akkordVon(b1, 'messliste')?.taste).toBe('d');
    expect(konflikte(b1)).toEqual([]);
    // Umgekehrte Schlüsselreihenfolge testen
    const reversed = { schema: 1, einzeltastenAus: false, tasten: {
      'messliste': { taste: 'd', strg: false, alt: false },
      'werkzeug.distance': { taste: 'm', strg: false, alt: false },
    } };
    const b2 = ladeBelegung(JSON.stringify(reversed));
    expect(akkordVon(b2, 'werkzeug.distance')?.taste).toBe('m');
    expect(akkordVon(b2, 'messliste')?.taste).toBe('d');
    expect(konflikte(b2)).toEqual([]);
  });
  it('Kette von Tasten überlebt die Rundreise', () => {
    // werkzeug.navigate→d, werkzeug.distance→l, werkzeug.polyline→v
    const data = { schema: 1, einzeltastenAus: false, tasten: {
      'werkzeug.navigate': { taste: 'd', strg: false, alt: false },
      'werkzeug.distance': { taste: 'l', strg: false, alt: false },
      'werkzeug.polyline': { taste: 'v', strg: false, alt: false },
    } };
    const b = ladeBelegung(JSON.stringify(data));
    expect(akkordVon(b, 'werkzeug.navigate')?.taste).toBe('d');
    expect(akkordVon(b, 'werkzeug.distance')?.taste).toBe('l');
    expect(akkordVon(b, 'werkzeug.polyline')?.taste).toBe('v');
    expect(konflikte(b)).toEqual([]);
  });
  it('null gibt eine Taste für eine andere Aktion frei', () => {
    // messliste=null, dann kantenfang='m'
    const data = { schema: 1, einzeltastenAus: false, tasten: {
      messliste: null,
      kantenfang: { taste: 'm', strg: false, alt: false },
    } };
    const b = ladeBelegung(JSON.stringify(data));
    expect(akkordVon(b, 'messliste')).toBeNull();
    expect(akkordVon(b, 'kantenfang')?.taste).toBe('m');
    expect(konflikte(b)).toEqual([]);
  });
  it('Kanonisierung: Großbuchstaben werden kleingeschrieben', () => {
    const data = { schema: 1, einzeltastenAus: false, tasten: {
      messliste: { taste: 'J', strg: false, alt: false },
    } };
    const b = ladeBelegung(JSON.stringify(data));
    expect(akkordVon(b, 'messliste')?.taste).toBe('j');
  });
  it('Reine Modifikatortasten werden verworfen', () => {
    const data = { schema: 1, einzeltastenAus: false, tasten: {
      messliste: { taste: 'Control', strg: false, alt: false },
      kantenfang: { taste: 'Alt', strg: false, alt: false },
    } };
    const b = ladeBelegung(JSON.stringify(data));
    // Sollten auf Vorgaben zurückgehen
    expect(akkordVon(b, 'messliste')?.taste).toBe('m');
    expect(akkordVon(b, 'kantenfang')?.taste).toBe('e');
  });
  it('kaputte, fremde oder gefährliche Daten ergeben die Vorgabe bzw. werden verworfen', () => {
    expect(ladeBelegung(null)).toEqual(vorgabeBelegung());
    expect(ladeBelegung('{')).toEqual(vorgabeBelegung());
    expect(ladeBelegung('{"schema":2}')).toEqual(vorgabeBelegung());
    const fremd = ladeBelegung(JSON.stringify({ schema: 1, einzeltastenAus: 'ja', tasten: {
      unbekannt: { taste: 'x', strg: false, alt: false },
      messliste: { taste: 'Tab', strg: false, alt: false },
      'werkzeug.poi': { taste: 'd', strg: false, alt: false },
      kantenfang: { taste: 'x'.repeat(40), strg: false, alt: false },
    } }));
    expect(fremd.einzeltastenAus).toBe(false);
    expect(akkordVon(fremd, 'messliste')?.taste).toBe('m');        // feste Taste verworfen
    expect(akkordVon(fremd, 'werkzeug.poi')?.taste).toBe('p');     // Konflikt mit d verworfen
    expect(akkordVon(fremd, 'kantenfang')?.taste).toBe('e');       // überlange Taste verworfen
    expect(konflikte(fremd)).toEqual([]);
  });
  it('gespeicherte Pfeil-/Leer-/Pos1-/Ende-Tasten in der Ebene global werden verworfen, im Begehen nicht', () => {
    const b = ladeBelegung(JSON.stringify({ schema: 1, tasten: {
      messliste: { taste: 'ArrowLeft', strg: false, alt: false },
      tastenhilfe: { taste: ' ', strg: false, alt: false },
      'alles-zeigen': { taste: 'Home', strg: true, alt: false },
      'begehen.ansage': { taste: 'End', strg: false, alt: false },
    } }));
    expect(akkordVon(b, 'messliste')?.taste).toBe('m');
    expect(akkordVon(b, 'tastenhilfe')?.taste).toBe('?');
    expect(akkordVon(b, 'alles-zeigen')?.taste).toBe('f');
    expect(akkordVon(b, 'begehen.ansage')?.taste).toBe('End');
  });
});

describe('HALTE_AKTIONEN', () => {
  it('stimmt mit AKTIONEN überein für art=halten', () => {
    const halteVonAktionen = AKTIONEN.filter(a => a.art === 'halten').map(a => a.id);
    expect(HALTE_AKTIONEN).toEqual(halteVonAktionen);
  });
});

describe('Härtung der Tastennamen', () => {
  const lade = (taste: string) => ladeBelegung(JSON.stringify({ schema: 1, tasten: { kantenfang: { taste, strg: false, alt: false } } }));
  it('lehnt Namen mit Sonderzeichen oder Leerraum beim Laden ab', () => {
    for (const schlecht of ['<b>', 'a b', 'Arrow Left', '1F5', '<img>', 'ab<']) {
      expect(akkordVon(lade(schlecht), 'kantenfang')?.taste, schlecht).toBe('e');
    }
  });
  it('lässt einzelne Zeichen und benannte Tasten zu', () => {
    // Pfeiltasten und Leertaste sind in der Ebene global fest, deshalb hier andere benannte Tasten.
    for (const gut of ['j', '<', 'PageDown', 'F5', 'Shift', '#']) {
      expect(akkordVon(lade(gut), 'kantenfang')?.taste, gut).toBe(gut);
    }
  });
  it('lehnt tote und unbestimmte Tasten ab (Laden und belege)', () => {
    for (const tot of ['Dead', 'Unidentified', 'Process']) {
      expect(akkordVon(lade(tot), 'kantenfang')?.taste, tot).toBe('e');
      expect(belege(vorgabeBelegung(), 'kantenfang', { taste: tot, strg: false, alt: false }), tot).toEqual({ ok: false, grund: 'ungueltig' });
    }
  });
  it('belege weist ungültige Namen ab', () => {
    const r = belege(vorgabeBelegung(), 'kantenfang', { taste: '<b>', strg: false, alt: false });
    expect(r).toEqual({ ok: false, grund: 'ungueltig' });
    expect(belege(vorgabeBelegung(), 'kantenfang', { taste: 'F5', strg: false, alt: false }).ok).toBe(true);
  });
});

describe('Controller-Knöpfe', () => {
  const lade = (pad: unknown, padSchema: unknown = 2) => ladeBelegung(JSON.stringify({ schema: 1, tasten: {}, pad, padSchema }));
  it('ohne Eintrag gilt das Standard-Mapping', () => {
    expect(padBelegung(vorgabeBelegung())).toEqual(PAD_VORGABE);
  });
  it('speichert und lädt eine Umbelegung (Schema 2)', () => {
    const r = belegePad(vorgabeBelegung(), 'kontext', 16);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.belegung.padSchema).toBe(2);
    const zurueck = ladeBelegung(speichereBelegung(r.belegung));
    expect(padBelegung(zurueck).kontext).toBe(16);
    expect(padBelegung(zurueck).zielen).toBe(PAD_VORGABE.zielen);
  });
  it('Umbelegungen des alten Schemas (nur Begehen) werden verworfen', () => {
    expect(padBelegung(lade({ zielen: 6, setzen: 7, befund: 2 }, null))).toEqual(PAD_VORGABE);
    expect(padBelegung(lade({ kontext: 2 }, 1))).toEqual(PAD_VORGABE);
  });
  it('belegePad weist einen schon vergebenen Knopf als Konflikt ab', () => {
    expect(belegePad(vorgabeBelegung(), 'kontext', 7)).toEqual({ ok: false, grund: 'konflikt', mit: 'hoch' });
  });
  it('belegePad erlaubt den Touchpad-Klick (17), weist Knöpfe außerhalb 0–17 und Nicht-Ganzzahlen ab', () => {
    expect(belegePad(vorgabeBelegung(), 'menue', 17).ok).toBe(true);
    for (const n of [18, -2, 1.5, NaN]) expect(belegePad(vorgabeBelegung(), 'kontext', n), String(n)).toEqual({ ok: false, grund: 'ungueltig' });
  });
  it('belegePad kann eine Aktion abschalten', () => {
    const r = belegePad(vorgabeBelegung(), 'kontext', PAD_NICHT_BELEGT);
    expect(r.ok && padBelegung(r.belegung).kontext).toBe(PAD_NICHT_BELEGT);
  });
  it('Laden verwirft ungültige Werte und fällt auf die Vorgabe zurück', () => {
    const b = padBelegung(lade({ kontext: 99, zielen: 'x', bestaetigen: 2.5, menue: null, unbekannt: 3 }));
    expect(b).toEqual(PAD_VORGABE);
  });
  it('Laden verträgt ein Nicht-Objekt', () => {
    expect(padBelegung(lade('kaputt'))).toEqual(PAD_VORGABE);
    expect(padBelegung(lade([1, 2]))).toEqual(PAD_VORGABE);
  });
  it('Laden löst Konflikte auf: die spätere Aktion fällt auf ihre Vorgabe zurück', () => {
    // kontext (3) steht in PAD_AKTIONEN vor menue; beide auf 16 → menue zurück auf 9
    const b = padBelegung(lade({ kontext: 16, menue: 16 }));
    expect(b.kontext).toBe(16);
    expect(b.menue).toBe(9);
  });
  it('Laden schaltet ab, wenn auch die Vorgabe kollidiert', () => {
    const b = padBelegung(lade({ kontext: 0, bestaetigen: 3, zielen: 3 }));
    expect(padKonflikte({ ...vorgabeBelegung(), pad: b })).toEqual([]);
  });
  it('padKonflikte findet doppelte Knöpfe', () => {
    expect(padKonflikte({ ...vorgabeBelegung(), pad: { kontext: 7 } })).toEqual([{ aktion: 'kontext', mit: 'hoch' }]);
  });
  it('nicht belegte Aktionen kollidieren nicht miteinander', () => {
    expect(padKonflikte({ ...vorgabeBelegung(), pad: { kontext: -1, bestaetigen: -1 } })).toEqual([]);
  });
});

describe('Befund und Teilen', () => {
  it('f setzt im Begehen einen Befund, t teilt überall; keine Konflikte in der Vorgabe', () => {
    expect(akkordVon(vorgabeBelegung(), 'begehen.befund')?.taste).toBe('f');
    expect(akkordVon(vorgabeBelegung(), 'teilen')?.taste).toBe('t');
    expect(AKTIONEN.find((a) => a.id === 'begehen.befund')).toMatchObject({ ebene: 'begehen', art: 'einmal' });
    expect(AKTIONEN.find((a) => a.id === 'teilen')).toMatchObject({ ebene: 'global', art: 'einmal' });
    expect(konflikte(vorgabeBelegung())).toEqual([]);
  });
  it('eine schon umbelegte Taste bleibt beim Laden, die neue Aktion weicht', () => {
    const b = ladeBelegung(JSON.stringify({ schema: 1, tasten: { 'werkzeug.poi': k('t') } }));
    expect(akkordVon(b, 'werkzeug.poi')?.taste).toBe('t');
    expect(akkordVon(b, 'teilen')).toBeNull();
    expect(konflikte(b)).toEqual([]);
  });
  it('unbekannte Aktion beim Belegen: verständlicher Fehler', () => {
    expect(() => belege(vorgabeBelegung(), 'gibt-es-nicht' as never, k('x'))).toThrow('unbekannte Aktion gibt-es-nicht');
  });
});

describe('kuerzelFuer', () => {
  it('Vorgabe: Beschriftung der Taste; ohne Belegung leer', () => {
    expect(kuerzelFuer(vorgabeBelegung(), 'begehen')).toBe('W');
    expect(kuerzelFuer(vorgabeBelegung(), 'begehen.setzen')).toBe('Leertaste');
    const r = belege(vorgabeBelegung(), 'teilen', null);
    expect(r.ok && kuerzelFuer(r.belegung, 'teilen')).toBe('');
  });
  it('Einzeltasten aus: globale Einzeltaste leer, Strg/Alt-Akkord und Begehen-Taste bleiben', () => {
    const b = { ...vorgabeBelegung(), einzeltastenAus: true };
    expect(kuerzelFuer(b, 'begehen')).toBe('');
    expect(kuerzelFuer(b, 'tastenhilfe')).toBe('');
    expect(kuerzelFuer(b, 'begehen.zielen')).not.toBe('');
    expect(kuerzelFuer(b, 'begehen.befund')).not.toBe('');
    const r = belege(b, 'begehen', k('w', { strg: true, alt: true }));
    expect(r.ok && kuerzelFuer(r.belegung, 'begehen')).toBe('Strg+Alt+W');
  });
});
