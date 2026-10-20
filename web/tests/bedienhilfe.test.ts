import { describe, expect, it } from 'vitest';
import { ansichtVon, ansichtWechselText, mausUndFinger, punktwolkeSatz, vollbildVerfuegbar } from '../src/bedienhilfe';
import type { HilfeKontext } from '../src/bedienhilfe';

const kontext = (teil: Partial<HilfeKontext> = {}): HilfeKontext => ({
  ansicht: 'orbit', empfang: false, orbitLinks: 'drehen', vollbild: true, eigenesFenster: false, ...teil,
});
const text = (k: HilfeKontext): string => mausUndFinger(k).map((a) => [a.titel, ...a.zeilen.map((z) => `${z.geste} = ${z.wirkung}`)].join('\n')).join('\n\n');

describe('Ansicht', () => {
  it('Begehen vor Panorama vor Orbit', () => {
    expect(ansichtVon({ begehen: true, panorama: false })).toBe('begehen');
    expect(ansichtVon({ begehen: false, panorama: true })).toBe('panorama');
    expect(ansichtVon({ begehen: false, panorama: false })).toBe('orbit');
  });
});

describe('Maus und Finger je Ansicht', () => {
  it('die aktive Ansicht steht oben und ist als aktiv markiert', () => {
    const a = mausUndFinger(kontext({ ansicht: 'panorama' }));
    expect(a[0]!.ansicht).toBe('panorama');
    expect(a[0]!.aktiv).toBe(true);
    expect(a.slice(1).every((x) => !x.aktiv)).toBe(true);
    expect(a.map((x) => x.ansicht).sort()).toEqual(['begehen', 'orbit', 'panorama']);
  });

  it('Orbit mit Vorgabe: links dreht, rechts oder Umschalt+links verschiebt, Rad zoomt zum Drehpunkt, Doppelklick setzt ihn', () => {
    const t = text(kontext());
    expect(t).toContain('Linke Maustaste ziehen = dreht um den Drehpunkt');
    expect(t).toContain('Rechte Maustaste oder Umschalt + linke Maustaste ziehen = verschiebt die Ansicht');
    expect(t).toContain('Mausrad = zoomt zum Drehpunkt');
    expect(t).toContain('Doppelklick auf die Punktwolke = setzt dort den Drehpunkt');
  });

  it('Orbit mit „links verschiebt“: die Belegung folgt der Einstellung', () => {
    const t = text(kontext({ orbitLinks: 'verschieben' }));
    expect(t).toContain('Linke Maustaste ziehen = verschiebt die Ansicht');
    expect(t).toContain('Rechte Maustaste oder Umschalt + linke Maustaste ziehen = dreht um den Drehpunkt');
  });

  it('Orbit mit Fingern: wie die Steuerung es tut (ein Finger dreht, zwei zoomen und verschieben)', () => {
    const t = text(kontext());
    expect(t).toContain('Ein Finger ziehen = dreht um den Drehpunkt');
    expect(t).toContain('Zwei Finger auseinander oder zusammen = zoomt');
    expect(t).toContain('Zwei Finger gemeinsam ziehen = verschiebt die Ansicht');
    expect(t).not.toMatch(/Ein Finger ziehen = verschiebt/);
  });

  it('Orbit mit Fingern und „links verschiebt“: die Finger folgen der Einstellung', () => {
    const t = text(kontext({ orbitLinks: 'verschieben' }));
    expect(t).toContain('Ein Finger ziehen = verschiebt die Ansicht');
    expect(t).toContain('Zwei Finger gemeinsam ziehen = dreht um den Drehpunkt');
  });

  it('Panorama: Ziehen schaut um, Rad ändert den Bildwinkel, Marke und Doppelklick wechseln den Standpunkt, Esc zurück', () => {
    const t = text(kontext({ ansicht: 'panorama' }));
    expect(t).toContain('Ziehen (Maus oder ein Finger) = schaut sich um');
    expect(t).toContain('Mausrad = ändert den Bildwinkel');
    expect(t).toContain('Klick oder Tippen auf eine Standpunkt-Marke = wechselt zu diesem Standpunkt');
    expect(t).toContain('Doppelklick in eine Richtung = wechselt zum nächsten Standpunkt in dieser Richtung');
    expect(t).toMatch(/Esc = zurück zur Punktwolke/);
    // Kein Zwei-Finger-Zoom im Panorama (die Steuerung kennt nur das Rad).
    expect(t.split('\n\n')[0]).not.toContain('Zwei Finger');
  });

  it('Begehen: Klick fängt die Maus, Maus schaut, Rad ändert das Tempo, keine Fingersteuerung', () => {
    const t = text(kontext({ ansicht: 'begehen' }));
    expect(t).toContain('Klick in die 3D-Ansicht = fängt den Mauszeiger');
    expect(t).toContain('Maus bewegen = schaut sich um');
    expect(t).toContain('Mausrad = ändert das Lauftempo');
    expect(t).toContain('Rechte Maustaste halten = zielen');
    expect(t).toMatch(/Finger = keine Fingersteuerung/);
  });

  it('Empfängeransicht: Knöpfe der Befundkarte, kein Begehen', () => {
    const a = mausUndFinger(kontext({ empfang: true }));
    expect(a.map((x) => x.ansicht)).not.toContain('begehen');
    const t = text(kontext({ empfang: true }));
    expect(t).toContain('Knöpfe der Befundkarte = drehen, neigen und zoomen in Schritten');
    expect(t).toContain('Frei umsehen = verlässt das Panorama');
  });

  it('Empfängeransicht: Trenner zwischen Ansicht und Befundkarte (nur dort, einmal)', () => {
    const t = text(kontext({ empfang: true }));
    expect(t).toContain('Trenner zwischen Ansicht und Befundkarte (Handy) = ziehen verschiebt die Aufteilung');
    expect(t.match(/Trenner zwischen/g)).toHaveLength(1);
    // Fotos haben nun einen eigenen Trenner; der Befundkarten-Trenner bleibt auf Empfang begrenzt.
    expect(text(kontext())).not.toContain('Trenner zwischen Ansicht und Befundkarte');
    expect(text(kontext())).toContain('Trenner der Fotoansicht');
  });

  it('Vollbild: Knopf, wo es geht; sonst Hinweis auf den Home-Bildschirm; im eigenen Fenster nichts', () => {
    expect(text(kontext())).toContain('Knopf Vollbild = ganzer Bildschirm, Esc beendet');
    const ohne = text(kontext({ vollbild: false }));
    expect(ohne).toContain('Zum Home-Bildschirm');
    expect(ohne).not.toContain('Knopf Vollbild');
    const fenster = text(kontext({ vollbild: false, eigenesFenster: true }));
    expect(fenster).not.toContain('Home-Bildschirm');
  });
});

describe('Ansage beim Ansichtswechsel', () => {
  it('ins Panorama und zurück', () => {
    expect(ansichtWechselText('orbit', 'panorama', 'drehen')).toBe('Panorama: Sie stehen am Standpunkt und schauen sich um. Zurück mit Esc.');
    expect(ansichtWechselText('panorama', 'orbit', 'drehen')).toBe('Punktwolke: Sie sehen das Modell von außen. Ziehen dreht um den Drehpunkt, Doppelklick setzt ihn.');
    expect(punktwolkeSatz('verschieben')).toBe('Punktwolke: Sie sehen das Modell von außen. Rechts ziehen dreht um den Drehpunkt, Doppelklick setzt ihn.');
  });

  it('kein Wechsel, und Begehen sagt Start und Ende selbst an', () => {
    expect(ansichtWechselText('panorama', 'panorama', 'drehen')).toBeNull();
    expect(ansichtWechselText('orbit', 'begehen', 'drehen')).toBeNull();
    expect(ansichtWechselText('begehen', 'orbit', 'drehen')).toBeNull();
  });
});

describe('Vollbild verfügbar', () => {
  it('nur mit Erlaubnis und Funktion', () => {
    expect(vollbildVerfuegbar({ fullscreenEnabled: true }, { requestFullscreen: () => undefined })).toBe(true);
    expect(vollbildVerfuegbar({ fullscreenEnabled: false }, { requestFullscreen: () => undefined })).toBe(false);
    // iPhone: weder die Erlaubnis noch die Funktion am Dokument.
    expect(vollbildVerfuegbar({}, {})).toBe(false);
    // Älteres iPadOS: nur mit Präfix.
    expect(vollbildVerfuegbar({ webkitFullscreenEnabled: true }, { webkitRequestFullscreen: () => undefined })).toBe(true);
  });
});
