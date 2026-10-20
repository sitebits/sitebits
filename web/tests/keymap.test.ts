import { describe, expect, it } from 'vitest';
import { resolveKey, resolveKeyUp } from '../src/keymap';
import type { KeyContext, KeyInput } from '../src/keymap';
import { AKTIONEN, akkordVon, belege, vorgabeBelegung } from '../src/tastenbelegung';

const b0 = vorgabeBelegung();
const key = (k: string, over: Partial<KeyInput> = {}): KeyInput => ({
  key: k, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, altGraph: false, inTextField: false, inMenu: false, inDialog: false, ...over,
});
const ctx = (over: Partial<KeyContext> = {}): KeyContext => ({
  drawing: false, dragging: false, panorama: false, selection: false, begehen: false, leinwandFokus: false, panoramaZiel: false, zeigerGesperrt: false, schnittbox: false, empfang: false, markieren: false, ...over,
});

describe('resolveKey', () => {
  it('Panorama-Ziel: Pfeile drehen, Leertaste setzt, F Befund, übrige Tasten wie bisher', () => {
    const c = ctx({ panorama: true, panoramaZiel: true, leinwandFokus: true });
    expect(resolveKey(key('ArrowLeft'), c, b0)).toEqual({ type: 'halten', aktion: 'begehen.drehen-links' });
    expect(resolveKey(key('ArrowUp'), c, b0)).toEqual({ type: 'halten', aktion: 'begehen.neigen-hoch' });
    expect(resolveKey(key(' '), c, b0)).toEqual({ type: 'halten', aktion: 'begehen.setzen' });
    expect(resolveKey(key('f'), c, b0)).toEqual({ type: 'befund' });
    expect(resolveKey(key('x'), c, b0)).toEqual({ type: 'werkzeug-wechsel', richtung: -1 });
    expect(resolveKey(key('c'), c, b0)).toEqual({ type: 'werkzeug-wechsel', richtung: 1 });
    expect(resolveKey(key('a'), c, b0)).toEqual({ type: 'tool', tool: 'area' }); // fällt in die globale Ebene
    expect(resolveKey(key('d'), c, b0)).toEqual({ type: 'tool', tool: 'distance' });
    expect(resolveKey(key('w'), c, b0)).toEqual({ type: 'begehen-start' }); // main.ts startet im Panorama kein Begehen
    expect(resolveKey(key('z'), c, b0)).not.toEqual({ type: 'halten', aktion: 'begehen.zielen' });
    expect(resolveKey(key('Escape'), c, b0)).toEqual({ type: 'leave-panorama' });
    expect(resolveKey(key('Tab'), c, b0)).toBeNull(); // Tab bleibt dem Browser: der Fokus wandert weiter
  });
  it('Panorama-Ziel: Loslassen der Leertaste kommt an, Loslassen von Bewegungstasten nicht', () => {
    const c = ctx({ panorama: true, panoramaZiel: true, leinwandFokus: true });
    expect(resolveKeyUp(key(' '), c, b0)).toContain('begehen.setzen');
    expect(resolveKeyUp(key('ArrowLeft'), c, b0)).toEqual(['begehen.drehen-links']);
    expect(resolveKeyUp(key('d'), c, b0)).toEqual([]);
    expect(resolveKeyUp(key(' '), ctx({ panorama: true }), b0)).toEqual([]);
  });
  it('Panorama ohne Werkzeug oder ohne Leinwandfokus: wie bisher', () => {
    expect(resolveKey(key('a'), ctx({ panorama: true, panoramaZiel: false, leinwandFokus: true }), b0)).toEqual({ type: 'tool', tool: 'area' });
    expect(resolveKey(key('f'), ctx({ panorama: true, panoramaZiel: false, leinwandFokus: true }), b0)).toEqual({ type: 'alles-zeigen' });
    expect(resolveKey(key('ArrowLeft'), ctx({ panorama: true, panoramaZiel: false, leinwandFokus: true }), b0)).toBeNull();
    expect(resolveKey(key('f'), ctx({ panorama: true, panoramaZiel: true, leinwandFokus: false }), b0)).toEqual({ type: 'alles-zeigen' });
  });
  it('F im Begehen setzt einen Befund, außerhalb zeigt f weiter alles', () => {
    expect(resolveKey(key('f'), ctx({ begehen: true, leinwandFokus: true }), b0)).toEqual({ type: 'befund' });
    expect(resolveKey(key('F', { shiftKey: true }), ctx({ begehen: true, leinwandFokus: true }), b0)).toEqual({ type: 'befund' });
    expect(resolveKey(key('f'), ctx(), b0)).toEqual({ type: 'alles-zeigen' });
    expect(resolveKey(key('f'), ctx({ begehen: true, leinwandFokus: false }), b0)).toBeNull();
  });
  it('t teilt, auch im Begehen; abgeschaltet mit den Einzeltasten', () => {
    expect(resolveKey(key('t'), ctx(), b0)).toEqual({ type: 'teilen' });
    // Koordinatenanzeige (F23): C schaltet, U kopiert; K bleibt das Panel Karte.
    expect(resolveKey(key('c'), ctx(), b0)).toEqual({ type: 'koordinaten' });
    expect(resolveKey(key('u'), ctx(), b0)).toEqual({ type: 'koordinate-kopieren' });
    expect(resolveKey(key('k'), ctx(), b0)).toEqual({ type: 'toggle-panel', panel: 'karte' });
    expect(resolveKey(key('t'), ctx({ begehen: true, leinwandFokus: true }), b0)).toEqual({ type: 'teilen' });
    expect(resolveKey(key('t'), ctx(), { ...b0, einzeltastenAus: true })).toBeNull();
  });
  it('Werkzeugtasten, groß wie klein', () => {
    expect(resolveKey(key('d'), ctx(), b0)).toEqual({ type: 'tool', tool: 'distance' });
    expect(resolveKey(key('A', { shiftKey: true }), ctx(), b0)).toEqual({ type: 'tool', tool: 'area' });
  });
  it('f, s, g lösen ohne Schnittbox nur alles-zeigen bzw. nichts aus', () => {
    expect(resolveKey(key('f'), ctx(), b0)).toEqual({ type: 'alles-zeigen' });
    expect(resolveKey(key('s'), ctx(), b0)).toBeNull();
    expect(resolveKey(key('g'), ctx(), b0)).toBeNull();
  });
  it('E schaltet den Kantenfang, mit Strg nicht', () => {
    expect(resolveKey(key('e'), ctx(), b0)).toEqual({ type: 'toggle-snap' });
    expect(resolveKey(key('E', { shiftKey: true }), ctx({ drawing: true, panorama: true }), b0)).toEqual({ type: 'toggle-snap' });
    expect(resolveKey(key('e', { ctrlKey: true }), ctx(), b0)).toBeNull();
  });
  it('Browser-Kürzel mit Strg/Alt/Cmd bleiben unberührt, außer Strg/Cmd+Z', () => {
    expect(resolveKey(key('d', { ctrlKey: true }), ctx(), b0)).toBeNull();
    expect(resolveKey(key('p', { metaKey: true }), ctx(), b0)).toBeNull();
    expect(resolveKey(key('l', { altKey: true }), ctx(), b0)).toBeNull();
    expect(resolveKey(key('z', { ctrlKey: true }), ctx(), b0)).toEqual({ type: 'undo' });
    expect(resolveKey(key('Z', { metaKey: true }), ctx(), b0)).toEqual({ type: 'undo' });
    expect(resolveKey(key('z', { ctrlKey: true, shiftKey: true }), ctx(), b0)).toBeNull();
  });
  it('Esc-Vorrang: Ziehen → Zeichnung → Panorama; ohne alles nichts', () => {
    expect(resolveKey(key('Escape'), ctx({ dragging: true, drawing: true, panorama: true }), b0)).toEqual({ type: 'cancel-drag' });
    expect(resolveKey(key('Escape'), ctx({ drawing: true, panorama: true }), b0)).toEqual({ type: 'cancel-drawing' });
    expect(resolveKey(key('Escape'), ctx({ panorama: true }), b0)).toEqual({ type: 'leave-panorama' });
    expect(resolveKey(key('Escape'), ctx(), b0)).toBeNull();
  });
  it('Enter nur beim Zeichnen, Entf/Rücktaste nur mit Auswahl und ohne Zeichnung', () => {
    expect(resolveKey(key('Enter'), ctx({ drawing: true }), b0)).toEqual({ type: 'finish' });
    expect(resolveKey(key('Enter'), ctx(), b0)).toBeNull();
    expect(resolveKey(key('Delete'), ctx({ selection: true }), b0)).toEqual({ type: 'delete-selection' });
    expect(resolveKey(key('Backspace'), ctx({ selection: true, drawing: true }), b0)).toBeNull();
    expect(resolveKey(key('m'), ctx(), b0)).toEqual({ type: 'toggle-list' });
  });
  it('in Textfeldern nichts — auch kein Esc (POI-Formular, Suche)', () => {
    expect(resolveKey(key('Escape', { inTextField: true }), ctx({ panorama: true }), b0)).toBeNull();
    expect(resolveKey(key('d', { inTextField: true }), ctx(), b0)).toBeNull();
  });
  it('im Positions-/Fenster-Menü: Esc/Enter durchlassen, andere Tasten weiter wirksam (M1)', () => {
    expect(resolveKey(key('Escape', { inMenu: true }), ctx({ drawing: true, panorama: true }), b0)).toBeNull();
    expect(resolveKey(key('Enter', { inMenu: true }), ctx({ drawing: true }), b0)).toBeNull();
    expect(resolveKey(key('d', { inMenu: true }), ctx(), b0)).toEqual({ type: 'tool', tool: 'distance' });
  });
});

describe('Panel-Kürzel', () => {
  it('i/o/k/n schalten Panels', () => {
    expect(resolveKey(key('i'), ctx(), b0)).toEqual({ type: 'toggle-panel', panel: 'ansicht' });
    expect(resolveKey(key('o'), ctx(), b0)).toEqual({ type: 'toggle-panel', panel: 'pois' });
    expect(resolveKey(key('K'), ctx(), b0)).toEqual({ type: 'toggle-panel', panel: 'karte' });
    expect(resolveKey(key('n'), ctx(), b0)).toEqual({ type: 'toggle-panel', panel: 'ebenen' });
  });
  it('Vorgabe: keine Taste doppelt belegt je Ebene, keine Überschneidung mit Ziffern 1–9', () => {
    for (const ebene of ['global', 'begehen'] as const) {
      const tasten = AKTIONEN.filter((x) => x.ebene === ebene).map((x) => akkordVon(b0, x.id)!.taste);
      expect(new Set(tasten).size).toBe(tasten.length);
      expect(tasten.some((t) => /^[1-9]$/.test(t))).toBe(false);
    }
  });
  it('in Textfeldern nichts', () => expect(resolveKey(key('o', { inTextField: true }), ctx(), b0)).toBeNull());
});

describe('Ziffern schalten Ebenen', () => {
  it('1–9 schalten Ebenen', () => {
    expect(resolveKey(key('1'), ctx(), b0)).toEqual({ type: 'toggle-layer', index: 1 });
    expect(resolveKey(key('9'), ctx(), b0)).toEqual({ type: 'toggle-layer', index: 9 });
    expect(resolveKey(key('0'), ctx(), b0)).toBeNull();
  });
});

describe('Verteiler: Begehen, Dialog, Umbelegung (B-BF-V1)', () => {
  it('Begehen-Kontext: a/d/s/e/f lösen weder Werkzeug, Gizmo, Kantenfang noch Alles zeigen aus', () => {
    const c = ctx({ begehen: true, leinwandFokus: true, schnittbox: true });
    expect(resolveKey(key('a'), c, b0)).toEqual({ type: 'halten', aktion: 'begehen.links' });
    expect(resolveKey(key('d'), c, b0)).toEqual({ type: 'halten', aktion: 'begehen.rechts' });
    expect(resolveKey(key('s'), c, b0)).toEqual({ type: 'halten', aktion: 'begehen.zurueck' });
    expect(resolveKey(key('e'), c, b0)).toEqual({ type: 'halten', aktion: 'begehen.hoch' });
    // f ist im Begehen „Befund setzen“; abgeschaltet bleibt es wirkungslos und zeigt nicht alles.
    expect(resolveKey(key('f'), c, b0)).toEqual({ type: 'befund' });
    expect(resolveKey(key('f'), c, { ...b0, tasten: { 'begehen.befund': null } })).toBeNull();
    expect(resolveKey(key('W', { shiftKey: true }), c, b0)).toEqual({ type: 'halten', aktion: 'begehen.vor' });
    expect(resolveKey(key('l'), c, b0)).toEqual({ type: 'tool', tool: 'polyline' }); // l/h/p/v wirken weiter
  });
  it('Begehen ohne Leinwandfokus: Kamera-Tasten wirken nicht, Werkzeugtasten wie immer', () => {
    const c = ctx({ begehen: true, leinwandFokus: false });
    expect(resolveKey(key('w'), c, b0)).toBeNull(); // begehen-start im Begehen nicht erneut
    expect(resolveKey(key('ArrowLeft'), c, b0)).toBeNull();
    expect(resolveKey(key('d'), c, b0)).toEqual({ type: 'tool', tool: 'distance' });
  });
  it('Textfeld im Begehen: im Begehen bewegt WASD nicht', () => {
    expect(resolveKey(key('w', { inTextField: true }), ctx({ begehen: true, leinwandFokus: true }), b0)).toBeNull();
  });
  it('Dialog offen: nichts, auch Esc nicht', () => {
    expect(resolveKey(key('Escape', { inDialog: true }), ctx({ begehen: true, drawing: true }), b0)).toBeNull();
    expect(resolveKey(key('d', { inDialog: true }), ctx(), b0)).toBeNull();
  });
  it('Esc-Vorrang mit Begehen: Ziehen → Zeichnung → Begehen → Panorama', () => {
    expect(resolveKey(key('Escape'), ctx({ drawing: true, begehen: true }), b0)).toEqual({ type: 'cancel-drawing' });
    expect(resolveKey(key('Escape'), ctx({ begehen: true, panorama: true }), b0)).toEqual({ type: 'begehen-ende' });
  });
  it('Umbelegte Taste wirkt, die alte nicht mehr; w startet Begehen nur außerhalb', () => {
    const r = belege(b0, 'begehen', { taste: 'b', strg: false, alt: false });
    if (!r.ok) throw new Error('belegen fehlgeschlagen');
    expect(resolveKey(key('b'), ctx(), r.belegung)).toEqual({ type: 'begehen-start' });
    expect(resolveKey(key('w'), ctx(), r.belegung)).toBeNull();
    expect(resolveKey(key('w'), ctx(), b0)).toEqual({ type: 'begehen-start' });
  });
  it('Einzeltasten aus: Buchstaben und Ziffern global wirkungslos, Esc/Enter/Strg+Z bleiben', () => {
    const b = { ...b0, einzeltastenAus: true };
    expect(resolveKey(key('d'), ctx(), b)).toBeNull();
    expect(resolveKey(key('3'), ctx(), b)).toBeNull();
    expect(resolveKey(key('Escape'), ctx({ drawing: true }), b)).toEqual({ type: 'cancel-drawing' });
    expect(resolveKey(key('z', { ctrlKey: true }), ctx(), b)).toEqual({ type: 'undo' });
  });
  it('Schnittbox-Tasten nur bei aktiver Schnittbox', () => {
    expect(resolveKey(key('g'), ctx({ schnittbox: true }), b0)).toEqual({ type: 'schnittbox', modus: 'translate' });
    expect(resolveKey(key('s'), ctx({ schnittbox: true }), b0)).toEqual({ type: 'schnittbox', modus: 'scale' });
    expect(resolveKey(key('g'), ctx(), b0)).toBeNull();
  });
  it('AltGr-[ wählt den vorigen Standpunkt', () => {
    expect(resolveKey(key('[', { ctrlKey: true, altKey: true, altGraph: true }), ctx(), b0)).toEqual({ type: 'standpunkt', richtung: -1 });
  });
  it('resolveKeyUp: Loslassen kommt auch ohne Leinwandfokus und mit Strg an', () => {
    expect(resolveKeyUp(key('w', { ctrlKey: true }), ctx({ begehen: true }), b0)).toEqual(['begehen.vor']);
    expect(resolveKeyUp(key('m'), ctx({ begehen: true }), b0)).toEqual([]);
    expect(resolveKeyUp(key('w'), ctx(), b0)).toEqual([]);
  });
  it('resolveKeyUp: zwei Halte-Aktionen auf derselben Taste (w und Alt+W) werden beide losgelassen', () => {
    const r = belege(b0, 'begehen.zurueck', { taste: 'w', strg: false, alt: true });
    if (!r.ok) throw new Error('belegen fehlgeschlagen');
    expect(resolveKeyUp(key('w'), ctx({ begehen: true }), r.belegung)).toEqual(['begehen.vor', 'begehen.zurueck']);
    expect(resolveKeyUp(key('w', { altKey: true }), ctx({ begehen: true }), r.belegung)).toEqual(['begehen.vor', 'begehen.zurueck']);
  });
  it('Begehen-Start wird während eines Ziehens abgewiesen', () => {
    expect(resolveKey(key('w'), ctx({ dragging: true }), b0)).toBeNull();
    expect(resolveKey(key('w'), ctx({ dragging: false }), b0)).toEqual({ type: 'begehen-start' });
  });
  it('Esc unter Pointer-Lock (oder kurz nach seinem Verlust) gehört dem Begehen, nicht der offenen Zeichnung', () => {
    const c = ctx({ begehen: true, drawing: true, zeigerGesperrt: true });
    expect(resolveKey(key('Escape'), c, b0)).toEqual({ type: 'begehen-ende' });
    expect(resolveKey(key('Escape'), ctx({ begehen: true, drawing: true }), b0)).toEqual({ type: 'cancel-drawing' });
    // Ein Ziehen bleibt vorrangig.
    expect(resolveKey(key('Escape'), { ...c, dragging: true }, b0)).toEqual({ type: 'cancel-drag' });
    // Ohne Begehen ist der Lock-Kontext bedeutungslos.
    expect(resolveKey(key('Escape'), ctx({ drawing: true, zeigerGesperrt: true }), b0)).toEqual({ type: 'cancel-drawing' });
  });
  it('Empfang: nur Tastenhilfe, Standpunkte, Karte und Teilen; alles andere bleibt wirkungslos', () => {
    const c = ctx({ empfang: true });
    for (const k of ['a', 'w', 'd', '1', '9', 'f', 'm', 'l', 'p', 'e', 'x', 'c', 'h', 's', 'g', 'Enter', 'Delete', 'Backspace']) {
      expect(resolveKey(key(k), c, b0), k).toBeNull();
      expect(resolveKey(key(k), { ...c, leinwandFokus: true, selection: true, drawing: true }, b0), k).toBeNull();
    }
    expect(resolveKey(key('z', { ctrlKey: true }), c, b0)).toBeNull();
    expect(resolveKey(key('?', { shiftKey: true }), c, b0)).toEqual({ type: 'tastenhilfe' });
    expect(resolveKey(key('t'), c, b0)).toEqual({ type: 'teilen' });
    expect(resolveKey(key('['), c, b0)).toEqual({ type: 'standpunkt', richtung: -1 });
    expect(resolveKey(key(']'), c, b0)).toEqual({ type: 'standpunkt', richtung: 1 });
    expect(resolveKey(key('k'), c, b0)).toEqual({ type: 'toggle-panel', panel: 'karte' });
    expect(resolveKey(key('Tab'), c, b0)).toBeNull();
  });
  it('Empfang: Pfeiltasten drehen in Schritten, nur mit Leinwandfokus', () => {
    const c = ctx({ empfang: true, leinwandFokus: true });
    expect(resolveKey(key('ArrowLeft'), c, b0)).toEqual({ type: 'drehen-schritt', richtung: 'links' });
    expect(resolveKey(key('ArrowRight'), c, b0)).toEqual({ type: 'drehen-schritt', richtung: 'rechts' });
    expect(resolveKey(key('ArrowUp'), c, b0)).toEqual({ type: 'drehen-schritt', richtung: 'hoch' });
    expect(resolveKey(key('ArrowDown'), c, b0)).toEqual({ type: 'drehen-schritt', richtung: 'runter' });
    expect(resolveKey(key('ArrowLeft'), ctx({ empfang: true }), b0)).toBeNull();
    // Im Panorama ebenso (dort gibt es im Empfang kein Fadenkreuz).
    expect(resolveKey(key('ArrowLeft'), { ...c, panorama: true }, b0)).toEqual({ type: 'drehen-schritt', richtung: 'links' });
    // Umbelegt: die Drehtasten folgen der Belegung.
    const r = belege(b0, 'begehen.drehen-links', { taste: 'j', strg: false, alt: false });
    if (!r.ok) throw new Error(r.grund);
    expect(resolveKey(key('j'), c, r.belegung)).toEqual({ type: 'drehen-schritt', richtung: 'links' });
    expect(resolveKey(key('ArrowLeft'), c, r.belegung)).toBeNull();
  });
  it('Empfang: Esc verlässt nur das Panorama, Texteingaben und Dialoge behalten ihre Tasten', () => {
    expect(resolveKey(key('Escape'), ctx({ empfang: true, panorama: true }), b0)).toEqual({ type: 'leave-panorama' });
    expect(resolveKey(key('Escape'), ctx({ empfang: true }), b0)).toBeNull();
    expect(resolveKey(key('Escape'), ctx({ empfang: true, drawing: true }), b0)).toBeNull();
    expect(resolveKey(key('?', { inDialog: true }), ctx({ empfang: true }), b0)).toBeNull();
    expect(resolveKey(key('t', { inTextField: true }), ctx({ empfang: true }), b0)).toBeNull();
  });
  it('Empfang mit „Hier markieren“: Esc bricht das Markieren ab (vor dem Verlassen des Panoramas)', () => {
    const c = ctx({ empfang: true, markieren: true });
    expect(resolveKey(key('Escape'), c, b0)).toEqual({ type: 'tool', tool: 'navigate' });
    expect(resolveKey(key('Escape'), { ...c, panorama: true, leinwandFokus: true, panoramaZiel: true }, b0)).toEqual({ type: 'tool', tool: 'navigate' });
    // Im Formular (Textfeld) gehört Esc dem Formular.
    expect(resolveKey(key('Escape', { inTextField: true }), c, b0)).toBeNull();
    expect(resolveKey(key('Escape', { inDialog: true }), c, b0)).toBeNull();
    // Werkzeugtasten bleiben im Empfang wirkungslos, auch beim Markieren.
    expect(resolveKey(key('d'), c, b0)).toBeNull();
  });
  it('Empfang mit „Hier markieren“ im Panorama-Fadenkreuz: Leertaste setzt (Halten), F setzt einen Befund', () => {
    const c = ctx({ empfang: true, markieren: true, panorama: true, leinwandFokus: true, panoramaZiel: true });
    expect(resolveKey(key(' '), c, b0)).toEqual({ type: 'halten', aktion: 'begehen.setzen' });
    expect(resolveKey(key('f'), c, b0)).toEqual({ type: 'befund' });
    expect(resolveKey(key('ArrowLeft'), c, b0)).toEqual({ type: 'drehen-schritt', richtung: 'links' });
    // Ohne Markieren, ohne Fadenkreuz oder ohne Leinwandfokus: nichts.
    for (const over of [{ markieren: false }, { panoramaZiel: false }, { leinwandFokus: false }]) {
      expect(resolveKey(key(' '), { ...c, ...over }, b0)).toBeNull();
      expect(resolveKey(key('f'), { ...c, ...over }, b0)).toBeNull();
    }
    // Bewegungs- und Zieltasten des Begehens bleiben aus.
    expect(resolveKey(key('w'), c, b0)).toBeNull();
  });
  it('Empfang: Pfeiltasten lösen kein Halten aus, ihr Loslassen damit auch nichts', () => {
    expect(resolveKeyUp(key('ArrowLeft'), ctx({ empfang: true, leinwandFokus: true }), b0)).toEqual([]);
  });
});
