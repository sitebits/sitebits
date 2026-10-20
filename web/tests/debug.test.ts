// Debug-Modus: debugAn()/diagnose()/anfrageStatistik()/fpsAusFrames() sind reine
// Funktionen (kein DOM nötig) — Vitest läuft mit environment: 'node' (kein jsdom). Feste Testfälle
// zuerst, danach Ergänzendes (Muster wie platform.test.ts).
import { describe, expect, it } from 'vitest';
import { anfrageStatistik, debugAn, diagnose, dlog, fpsAusFrames, setzeDebug, zaehleFehler } from '../src/debug';
import type { AnfrageEintrag } from '../src/debug';

describe('debugAn', () => {
  it('?debug=1 → an, auch ohne Speicher', () => {
    expect(debugAn({ search: '?debug=1' }, null)).toBe(true);
  });
  it('localStorage sv-debug=1 → an', () => {
    expect(debugAn({ search: '' }, { getItem: () => '1' })).toBe(true);
  });
  it('?debug=0 schaltet für diesen Aufruf aus, auch wenn localStorage an sagt', () => {
    expect(debugAn({ search: '?debug=0' }, { getItem: () => '1' })).toBe(false);
  });
  it('ohne Parameter und ohne Speicher → aus', () => {
    expect(debugAn({ search: '' }, null)).toBe(false);
  });

  // Ergänzend:
  it('localStorage mit anderem Wert als "1" → aus', () => {
    expect(debugAn({ search: '' }, { getItem: () => '0' })).toBe(false);
    expect(debugAn({ search: '' }, { getItem: () => null })).toBe(false);
  });
  it('ein werfender Speicher stürzt nicht ab', () => {
    const werfend: Pick<Storage, 'getItem'> = {
      getItem: () => {
        throw new Error('gesperrt');
      },
    };
    expect(debugAn({ search: '' }, werfend)).toBe(false);
  });
  it('debugAn() ganz ohne Argumente stürzt unter Node (kein `location`) nicht ab', () => {
    expect(() => debugAn()).not.toThrow();
  });
});

describe('setzeDebug', () => {
  it('stürzt ohne echtes localStorage (Node) nicht ab', () => {
    expect(() => setzeDebug(true)).not.toThrow();
    expect(() => setzeDebug(false)).not.toThrow();
  });
});

describe('dlog', () => {
  it('stürzt ohne DOM/location nicht ab (dlog ruft debugAn() intern ohne Argumente auf)', () => {
    expect(() => dlog('test', 1, 2, 3)).not.toThrow();
  });
});

describe('diagnose', () => {
  it('enthält keine Namen/E-Mails und keinen Wert mit @', () => {
    const d = diagnose();
    expect(Object.keys(d)).not.toContain('name');
    expect(Object.keys(d)).not.toContain('email');
    for (const wert of Object.values(d)) {
      expect(String(wert)).not.toContain('@');
    }
  });
  it('liefert Werte für alle dokumentierten Felder, auch ohne DOM (Node)', () => {
    const d = diagnose();
    expect(typeof d.version).toBe('string');
    expect(typeof d.userAgent).toBe('string');
    expect(typeof d.webglRenderer).toBe('string');
    expect(typeof d.bildschirm).toBe('string');
    expect(typeof d.devicePixelRatio).toBe('number');
    expect(typeof d.zeit).toBe('string');
    expect(() => new Date(d.zeit as string).toISOString()).not.toThrow();
  });
});

describe('anfrageStatistik', () => {
  // anfragenFehler ist HIER bewusst kein Feld mehr — anfrageStatistik() bekommt in
  // platform.ts nur das (nach 60s getrimmte) Anzeige-Array und darf den Fehlerzähler deshalb nicht
  // selbst berechnen (sonst verschwinden ältere Fehler beim nächsten Trim wieder). Siehe
  // zaehleFehler() weiter unten für den sitzungskumulativen Zähler.
  const eintrag = (endeMs: number, dauerMs: number, responseStatus = 200): AnfrageEintrag => ({
    endeMs, dauerMs, responseStatus,
  });

  it('leer → alles neutral', () => {
    expect(anfrageStatistik([], 1000)).toEqual({ anfragenLaufend: 0, letzteMs: null });
  });
  it('zählt nur Einträge im Fenster als "laufend"', () => {
    const liste = [eintrag(100, 10, 200), eintrag(9000, 20, 500), eintrag(9500, 5, 200)];
    const s = anfrageStatistik(liste, 10000, 1000);
    expect(s.anfragenLaufend).toBe(2); // 9000 und 9500 liegen im Fenster [9000,10000], 100 nicht
  });
  it('letzteMs ist die Dauer des Eintrags mit dem größten Zeitstempel, unabhängig von der Reihenfolge', () => {
    const liste = [eintrag(500, 42), eintrag(100, 7), eintrag(300, 9)];
    expect(anfrageStatistik(liste, 1000).letzteMs).toBe(42);
  });
});

describe('zaehleFehler (anfragenFehler muss sitzungskumulativ bleiben, auch wenn das '
  + '60s-Anzeigefenster ältere Einträge längst verworfen hat)', () => {
  const fehler = (status: number): Pick<AnfrageEintrag, 'responseStatus'> => ({ responseStatus: status });

  it('leer → 0, addiert sonst nur neue Fehler (>=400) zur bisherigen Summe', () => {
    expect(zaehleFehler(0, [])).toBe(0);
    expect(zaehleFehler(2, [fehler(200), fehler(404), fehler(500)])).toBe(4);
  });
  it('responseStatus 0 (Browser gibt ihn nicht preis) zählt nicht als Fehler', () => {
    expect(zaehleFehler(0, [fehler(0)])).toBe(0);
  });
  it('ein Fehler bleibt in der Summe erhalten, auch wenn er älter als 60s ist (Regressionstest '
    + 'für den Fix: platform.ts::anfrageQuelle() darf den Zähler nur mit NEUEN Einträgen je '
    + 'PerformanceObserver-Tick füttern, nie aus dem — ggf. schon getrimmten — Anzeige-Array neu '
    + 'berechnen)', () => {
    let summe = zaehleFehler(0, [fehler(500)]); // Tick 1, t ≈ 0s
    expect(summe).toBe(1);
    // Tick 2, t ≈ 65s — das 60s-Anzeigefenster hätte den ersten Eintrag längst verworfen; die neu
    // eingehende Charge enthält keinen Fehler mehr, die Summe darf trotzdem nicht sinken.
    summe = zaehleFehler(summe, [fehler(200)]);
    expect(summe).toBe(1);
  });
});

describe('fpsAusFrames', () => {
  it('60 Bilder in 1000ms → 60', () => {
    expect(fpsAusFrames(60, 1000)).toBe(60);
  });
  it('keine neuen Bilder → 0 (nicht die letzte Rate einfrieren)', () => {
    expect(fpsAusFrames(0, 500)).toBe(0);
  });
  it('deltaMs <= 0 → 0 (keine Division durch 0/negativ)', () => {
    expect(fpsAusFrames(5, 0)).toBe(0);
    expect(fpsAusFrames(5, -10)).toBe(0);
  });
});
