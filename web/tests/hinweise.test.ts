import { describe, expect, it } from 'vitest';
import { echteTastatur, gezaehlt, HINWEIS_GRENZE, padAktiv, TAETIGKEIT, hinweisFuerAktion, hinweisSchluessel, hinweisTaste, ladeHinweise, padKurz, padTypVon, schneidet, seiteFuer, sichtbareHinweise, speichereHinweise, ueberdeckt, zurueckgesetzt } from '../src/hinweise';
import type { HinweisId, HinweisKontext } from '../src/hinweise';
import { PAD_VORGABE } from '../src/gamepad';
import type { AktionId } from '../src/tastenbelegung';
import { kuerzelFuer, vorgabeBelegung } from '../src/tastenbelegung';

const k = (over: Partial<HinweisKontext> = {}): HinweisKontext =>
  ({ begehen: false, panoramaZiel: false, werkzeug: 'navigate', auswahl: false, empfang: false, gamepad: false, ...over });
const taste = (id: HinweisId): string => ({ begehen: 'W', tastenhilfe: '?', setzen: 'Leertaste', zielen: 'Z', befund: 'F', beenden: 'Esc', teilen: 'T', drehen: '← →', maus: '', finger: '' })[id];
const ids = (r: { id: HinweisId }[]): HinweisId[] => r.map((x) => x.id);

describe('sichtbareHinweise', () => {
  it('Werkzeugansicht: Begehen und Tastenhilfe, mit Auswahl auch Teilen', () => {
    expect(ids(sichtbareHinweise(k(), zurueckgesetzt(), { immer: false, taste }))).toEqual(['begehen', 'tastenhilfe']);
    expect(ids(sichtbareHinweise(k({ auswahl: true }), zurueckgesetzt(), { immer: false, taste }))).toEqual(['begehen', 'teilen', 'tastenhilfe']);
  });
  it('Begehen mit Messwerkzeug: höchstens drei, in fester Reihenfolge, Text mit aktueller Taste', () => {
    const r = sichtbareHinweise(k({ begehen: true, werkzeug: 'distance' }), zurueckgesetzt(), { immer: false, taste });
    expect(r).toEqual([
      { id: 'setzen', text: 'Leertaste: Punkt setzen' },
      { id: 'befund', text: 'F: Befund setzen' },
      { id: 'zielen', text: 'Z: zielen' },
    ]);
  });
  it('Begehen mit Navigieren: kein Setzen, dafür Beenden', () => {
    expect(ids(sichtbareHinweise(k({ begehen: true }), zurueckgesetzt(), { immer: false, taste }))).toEqual(['befund', 'zielen', 'beenden']);
  });
  it('Panorama-Fadenkreuz: Setzen, Befund, Drehen; kein Zielen und kein Beenden', () => {
    expect(ids(sichtbareHinweise(k({ panoramaZiel: true, werkzeug: 'distance' }), zurueckgesetzt(), { immer: false, taste }))).toEqual(['setzen', 'befund', 'drehen']);
  });
  it('ausgeschöpfte Hinweise machen Platz für die nächsten', () => {
    let z = zurueckgesetzt();
    for (let i = 0; i < HINWEIS_GRENZE; i++) z = gezaehlt(z, 'setzen');
    expect(ids(sichtbareHinweise(k({ begehen: true, werkzeug: 'distance' }), z, { immer: false, taste }))).toEqual(['befund', 'zielen', 'beenden']);
  });
  it('verschwindet nach der dritten Ausführung, „immer zeigen“ holt ihn zurück', () => {
    let z = zurueckgesetzt();
    for (let i = 0; i < HINWEIS_GRENZE - 1; i++) z = gezaehlt(z, 'begehen');
    expect(ids(sichtbareHinweise(k(), z, { immer: false, taste }))).toEqual(['begehen', 'tastenhilfe']);
    z = gezaehlt(z, 'begehen');
    expect(ids(sichtbareHinweise(k(), z, { immer: false, taste }))).toEqual(['tastenhilfe']);
    expect(ids(sichtbareHinweise(k(), z, { immer: true, taste }))).toEqual(['begehen', 'tastenhilfe']);
    expect(ids(sichtbareHinweise(k(), zurueckgesetzt(), { immer: false, taste }))).toEqual(['begehen', 'tastenhilfe']);
  });
  it('nicht belegte Taste: kein Hinweis', () => {
    expect(ids(sichtbareHinweise(k(), zurueckgesetzt(), { immer: false, taste: (id) => (id === 'begehen' ? '' : taste(id)) }))).toEqual(['tastenhilfe']);
  });
  it('Empfang: nur Tastenhilfe und Drehen', () => {
    expect(ids(sichtbareHinweise(k({ empfang: true, auswahl: true }), zurueckgesetzt(), { immer: false, taste }))).toEqual(['tastenhilfe', 'drehen']);
  });
  it('kein Spielvokabular: keine Tätigkeit, in keinem Kontext', () => {
    const verboten = /ping|quest|spieler|level|punkte gesammelt|erfolg|belohn|abzeichen|streak/i;
    for (const t of Object.values(TAETIGKEIT)) expect(t).not.toMatch(verboten);
    const kontexte = [k(), k({ auswahl: true }), k({ begehen: true }), k({ begehen: true, werkzeug: 'poi' }), k({ begehen: true, werkzeug: 'distance' }),
      k({ panoramaZiel: true, werkzeug: 'distance' }), k({ empfang: true, auswahl: true }), k({ zeiger: 'maus' }), k({ zeiger: 'finger' })];
    const gesehen = new Set<HinweisId>();
    for (const kx of kontexte)
      for (const r of sichtbareHinweise(kx, zurueckgesetzt(), { immer: true, taste })) {
        gesehen.add(r.id);
        expect(r.text).not.toMatch(verboten);
      }
    expect([...gesehen].sort()).toEqual(Object.keys(TAETIGKEIT).sort());
  });
});

describe('Maus und Finger', () => {
  const opt = { immer: false, taste };
  it('Orbit mit Maus: eine Zeile zu Maus vorab, die Tastenhinweise bleiben (höchstens drei)', () => {
    const r = sichtbareHinweise(k({ zeiger: 'maus', auswahl: true }), zurueckgesetzt(), opt);
    expect(r[0]).toEqual({ id: 'maus', text: 'Doppelklick: Drehpunkt · Ziehen: drehen · Rad: zoomen' });
    expect(ids(r)).toEqual(['maus', 'begehen', 'teilen', 'tastenhilfe']);
  });
  it('die Maus-Zeile folgt der Einstellung „Linke Maustaste im Orbit“', () => {
    const r = sichtbareHinweise(k({ zeiger: 'maus', orbitLinks: 'verschieben' }), zurueckgesetzt(), opt);
    expect(r[0]!.text).toBe('Doppelklick: Drehpunkt · Rechts ziehen: drehen · Rad: zoomen');
  });
  it('die Finger-Zeile folgt derselben Einstellung', () => {
    const r = sichtbareHinweise(k({ zeiger: 'finger', tastatur: false, orbitLinks: 'verschieben' }), zurueckgesetzt(), opt);
    expect(r[0]!.text).toBe('Ein Finger: verschieben · Zwei Finger: zoomen und drehen');
  });
  it('reines Touch-Gerät: Finger-Zeile wie die Steuerung es tut; Tastenhinweise erst nach einer Taste', () => {
    expect(sichtbareHinweise(k({ zeiger: 'finger', tastatur: false }), zurueckgesetzt(), opt)).toEqual([
      { id: 'finger', text: 'Ein Finger: drehen · Zwei Finger: zoomen und verschieben' },
    ]);
    expect(ids(sichtbareHinweise(k({ zeiger: 'finger', tastatur: true }), zurueckgesetzt(), opt))).toEqual(['finger', 'begehen', 'tastenhilfe']);
  });
  it('nur im Orbit der Werkzeugansicht: nicht im Panorama, im Begehen, in der Empfängeransicht', () => {
    expect(ids(sichtbareHinweise(k({ zeiger: 'maus', panorama: true }), zurueckgesetzt(), opt))).not.toContain('maus');
    expect(ids(sichtbareHinweise(k({ zeiger: 'maus', begehen: true }), zurueckgesetzt(), opt))).not.toContain('maus');
    expect(ids(sichtbareHinweise(k({ zeiger: 'maus', empfang: true }), zurueckgesetzt(), opt))).toEqual(['tastenhilfe', 'drehen']);
  });
  it('verschwindet nach dreimaliger Ausführung; der Zähler übersteht das Laden', () => {
    let z = zurueckgesetzt();
    for (let i = 0; i < HINWEIS_GRENZE; i++) z = gezaehlt(z, 'maus');
    z = ladeHinweise(speichereHinweise(z));
    expect(z.n.maus).toBe(HINWEIS_GRENZE);
    expect(ids(sichtbareHinweise(k({ zeiger: 'maus' }), z, opt))).toEqual(['begehen', 'tastenhilfe']);
    expect(ids(sichtbareHinweise(k({ zeiger: 'maus' }), z, { ...opt, immer: true }))).toContain('maus');
    const f = ladeHinweise(speichereHinweise(gezaehlt(zurueckgesetzt(), 'finger')));
    expect(f.n.finger).toBe(1);
  });
});

describe('abgeschaltete Taste über den echten Kürzel-Weg', () => {
  it('Einzeltasten aus: kein Hinweis auf Begehen und Tastenhilfe, Begehen-Hinweise bleiben', () => {
    const b = { ...vorgabeBelegung(), einzeltastenAus: true };
    const echt = (id: HinweisId): string => hinweisTaste(id, { kuerzel: (a) => kuerzelFuer(b, a), pad: null });
    expect(ids(sichtbareHinweise(k(), zurueckgesetzt(), { immer: true, taste: echt }))).toEqual([]);
    expect(ids(sichtbareHinweise(k({ begehen: true }), zurueckgesetzt(), { immer: true, taste: echt }))).toEqual(['befund', 'zielen', 'beenden']);
  });
});

describe('padAktiv', () => {
  it('Controller-Knöpfe nur mit benutztem, noch verbundenem Controller im Begehen', () => {
    expect(padAktiv('xbox', true, 'Xbox Controller')).toBe(true);
    expect(padAktiv('xbox', true, '')).toBe(false); // abgesteckt: zurück zu Tasten
    expect(padAktiv(null, true, 'Xbox Controller')).toBe(false); // zuletzt Tastatur
    expect(padAktiv('xbox', false, 'Xbox Controller')).toBe(false); // wirkt nur im Begehen
  });
});

describe('Speichern/Laden', () => {
  it('Rundreise; Fremdes, Negatives und Unbekanntes wird verworfen', () => {
    const z = gezaehlt(gezaehlt(zurueckgesetzt(), 'befund'), 'befund');
    expect(ladeHinweise(speichereHinweise(z))).toEqual(z);
    expect(ladeHinweise(null)).toEqual(zurueckgesetzt());
    expect(ladeHinweise('{')).toEqual(zurueckgesetzt());
    expect(ladeHinweise(JSON.stringify({ schema: 1, n: { befund: -2, begehen: 'x', fremd: 5, teilen: 2 } }))).toEqual({ schema: 1, n: { teilen: 2 } });
    expect(ladeHinweise(JSON.stringify({ schema: 1, n: { befund: 1.5, setzen: 4, zielen: 3 } }))).toEqual({ schema: 1, n: { zielen: 3 } });
    expect(ladeHinweise(JSON.stringify({ schema: 2, n: { teilen: 2 } }))).toEqual(zurueckgesetzt());
    expect(ladeHinweise(JSON.stringify({ schema: 1, n: [1, 2] }))).toEqual(zurueckgesetzt());
  });
  it('Schlüssel je Konto', () => {
    expect(hinweisSchluessel(null)).toBe('sv-hinweise');
    expect(hinweisSchluessel(7)).toBe('sv-hinweise:7');
  });
  it('Zähler wächst nicht unbegrenzt', () => {
    let z = zurueckgesetzt();
    for (let i = 0; i < 50; i++) z = gezaehlt(z, 'setzen');
    expect(z.n.setzen).toBe(HINWEIS_GRENZE);
  });
});

describe('hinweisFuerAktion', () => {
  const imBegehen = { begehen: true, panoramaZiel: false };
  const imPanorama = { begehen: false, panoramaZiel: true };
  it('Zielen nur im Begehen, Drehen nur im Panorama-Fadenkreuz', () => {
    expect(hinweisFuerAktion({ type: 'halten', aktion: 'begehen.zielen' }, imBegehen)).toBe('zielen');
    expect(hinweisFuerAktion({ type: 'halten', aktion: 'begehen.drehen-links' }, imPanorama)).toBe('drehen');
    expect(hinweisFuerAktion({ type: 'halten', aktion: 'begehen.drehen-rechts' }, imPanorama)).toBe('drehen');
    expect(hinweisFuerAktion({ type: 'halten', aktion: 'begehen.drehen-links' }, imBegehen)).toBeNull();
    expect(hinweisFuerAktion({ type: 'halten', aktion: 'begehen.zielen' }, imPanorama)).toBeNull();
  });
  it('Drehen in Schritten (Empfängeransicht) zählt als Drehen, in jede Richtung', () => {
    for (const richtung of ['links', 'rechts', 'hoch', 'runter'] as const)
      expect(hinweisFuerAktion({ type: 'drehen-schritt', richtung }, { begehen: false, panoramaZiel: false })).toBe('drehen');
  });
  it('Setzen, Befund, Teilen und Tastenhilfe zählen nicht über die Taste (sonst doppelt mit Knopf und Controller)', () => {
    expect(hinweisFuerAktion({ type: 'halten', aktion: 'begehen.setzen' }, imBegehen)).toBeNull();
    expect(hinweisFuerAktion({ type: 'befund' }, imBegehen)).toBeNull();
    expect(hinweisFuerAktion({ type: 'teilen' }, imBegehen)).toBeNull();
    expect(hinweisFuerAktion({ type: 'tastenhilfe' }, imBegehen)).toBeNull();
    expect(hinweisFuerAktion({ type: 'begehen-start' }, imBegehen)).toBeNull();
  });
});

describe('Beschriftung', () => {
  const kuerzel = (a: AktionId): string => ({ begehen: 'W', tastenhilfe: '?', 'begehen.setzen': 'Leertaste', 'begehen.zielen': 'Z', 'begehen.befund': 'F', teilen: 'T', 'begehen.drehen-links': '←', 'begehen.drehen-rechts': '→' } as Partial<Record<AktionId, string>>)[a] ?? '';
  it('Tastatur: aktuelle Kürzel, Drehen aus beiden Tasten, Beenden immer Esc', () => {
    expect(hinweisTaste('begehen', { kuerzel, pad: null })).toBe('W');
    expect(hinweisTaste('drehen', { kuerzel, pad: null })).toBe('← →');
    expect(hinweisTaste('drehen', { kuerzel: (a) => (a === 'begehen.drehen-links' ? '' : kuerzel(a)), pad: null })).toBe('→');
    expect(hinweisTaste('beenden', { kuerzel: () => '', pad: null })).toBe('Esc');
    expect(hinweisTaste('teilen', { kuerzel: () => '', pad: null })).toBe('');
  });
  it('Controller: Knopfnamen je Typ, umbelegt und nicht belegt', () => {
    const pad = { typ: 'xbox' as const, belegung: { ...PAD_VORGABE } };
    expect(hinweisTaste('befund', { kuerzel, pad })).toBe('Y');
    expect(hinweisTaste('setzen', { kuerzel, pad })).toBe('RT');
    expect(hinweisTaste('zielen', { kuerzel, pad })).toBe('LT');
    expect(hinweisTaste('befund', { kuerzel, pad: { typ: 'playstation', belegung: { ...PAD_VORGABE } } })).toBe('△');
    expect(hinweisTaste('setzen', { kuerzel, pad: { typ: 'playstation', belegung: { ...PAD_VORGABE } } })).toBe('R2');
    expect(hinweisTaste('befund', { kuerzel, pad: { typ: 'generisch', belegung: { ...PAD_VORGABE, befund: 2 } } })).toBe('X');
    expect(hinweisTaste('befund', { kuerzel, pad: { typ: 'xbox', belegung: { ...PAD_VORGABE, befund: -1 } } })).toBe('');
    // Ohne Controller-Gegenstück bleibt es bei der Taste.
    expect(hinweisTaste('beenden', { kuerzel, pad })).toBe('Esc');
    expect(hinweisTaste('tastenhilfe', { kuerzel, pad })).toBe('?');
  });
  it('Knopf außerhalb des Standard-Mappings ist leer', () => {
    expect(padKurz('xbox', -1)).toBe('');
    expect(padKurz('xbox', 17)).toBe('');
    expect(padKurz('playstation', 1.5)).toBe('');
    expect(padTypVon('Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)')).toBe('playstation');
    expect(padTypVon('Test Pad (STANDARD GAMEPAD)')).toBe('generisch');
  });
});

describe('ueberdeckt', () => {
  const leiste = { left: 800, top: 500, right: 1000, bottom: 580 };
  it('Schnitt mit einem Knopf: verdeckt', () => {
    expect(ueberdeckt(leiste, { left: 950, top: 560, right: 990, bottom: 590 })).toBe(true);
  });
  it('daneben oder nur berührend: nicht verdeckt', () => {
    expect(ueberdeckt(leiste, { left: 10, top: 560, right: 50, bottom: 590 })).toBe(false);
    expect(ueberdeckt(leiste, { left: 760, top: 500, right: 800, bottom: 540 })).toBe(false);
  });
  it('die vollflächige 3D-Ansicht umschließt die Leiste: nicht verdeckt', () => {
    expect(ueberdeckt(leiste, { left: 0, top: 0, right: 1280, bottom: 720 })).toBe(false);
  });
  it('unsichtbares Element (leeres Rechteck): nicht verdeckt', () => {
    expect(ueberdeckt(leiste, { left: 900, top: 540, right: 900, bottom: 540 })).toBe(false);
  });
});

describe('seiteFuer', () => {
  // Fenster 1280 breit, Leiste rechts unten mit 8 px Rand.
  const rechts = { left: 1072, top: 640, right: 1272, bottom: 712 };
  const minikarte = { left: 960, top: 500, right: 1272, bottom: 712 };
  const linksUnten = { left: 8, top: 600, right: 300, bottom: 712 };
  it('frei: rechts', () => {
    expect(seiteFuer(rechts, 1280, null, [])).toBe('rechts');
  });
  it('Panel unten rechts, links frei: links', () => {
    expect(seiteFuer(rechts, 1280, null, [minikarte])).toBe('links');
  });
  it('Panels auf beiden Seiten: rechts bleibt', () => {
    expect(seiteFuer(rechts, 1280, null, [minikarte, linksUnten])).toBe('rechts');
  });
  it('fokussiertes Element rechts wiegt schwerer als ein Panel links', () => {
    expect(seiteFuer(rechts, 1280, { left: 1200, top: 690, right: 1260, bottom: 710 }, [linksUnten])).toBe('links');
  });
  it('fokussiertes Element links: nicht dorthin ausweichen, auch wenn rechts ein Panel liegt', () => {
    expect(seiteFuer(rechts, 1280, { left: 20, top: 690, right: 120, bottom: 710 }, [minikarte])).toBe('rechts');
  });
  it('die 3D-Ansicht als Fokus zählt nicht', () => {
    expect(seiteFuer(rechts, 1280, { left: 0, top: 0, right: 1280, bottom: 720 }, [])).toBe('rechts');
  });
  it('schneidet: leere Rechtecke nie', () => {
    expect(schneidet(rechts, { left: 1100, top: 650, right: 1100, bottom: 700 })).toBe(false);
    expect(schneidet(rechts, minikarte)).toBe(true);
  });
});

describe('echteTastatur (Hinweise auf Touch-Geräten erst nach einer echten Taste)', () => {
  it('echte Taste außerhalb eines Textfelds zählt', () => {
    expect(echteTastatur({ key: 'f', keyCode: 70 }, false)).toBe(true);
    expect(echteTastatur({ key: 'Tab', keyCode: 9, isComposing: false }, false)).toBe(true);
  });
  it('Bildschirmtastatur (Unidentified, Process, Code 229, Komposition) zählt nicht', () => {
    expect(echteTastatur({ key: 'Unidentified', keyCode: 229 }, false)).toBe(false);
    expect(echteTastatur({ key: 'Unidentified' }, false)).toBe(false);
    expect(echteTastatur({ key: 'Process', keyCode: 229 }, false)).toBe(false);
    expect(echteTastatur({ key: 'a', keyCode: 229 }, false)).toBe(false);
    expect(echteTastatur({ key: 'a', keyCode: 65, isComposing: true }, false)).toBe(false);
  });
  it('in einem Textfeld zählt nichts, auch nicht Enter oder Rücktaste', () => {
    expect(echteTastatur({ key: 'Enter', keyCode: 13 }, true)).toBe(false);
    expect(echteTastatur({ key: 'Backspace', keyCode: 8 }, true)).toBe(false);
  });
});
