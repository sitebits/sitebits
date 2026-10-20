import { describe, expect, it } from 'vitest';
import { POI_LIMITS, RUECKMELDUNG } from '../src/poiStore';
import { rueckmeldungEntwurf, zuBefundZeile } from '../src/rueckmeldung';

const befund = { titel: 'Riss Nordwand', position: [1.5, -2, 3.25] as [number, number, number], station: 'S012' };

describe('rueckmeldungEntwurf', () => {
  it('leerer Text oder nur Leerzeichen → Fehler', () => {
    expect(rueckmeldungEntwurf(befund, '')).toEqual({ fehler: 'Bitte eine Rückmeldung eingeben.' });
    expect(rueckmeldungEntwurf(befund, '  \n\t ')).toEqual({ fehler: 'Bitte eine Rückmeldung eingeben.' });
  });

  it('normaler Text → Entwurf mit Position und Standpunkt des Befunds, Kategorie Rückmeldung, Bezugszeile zuerst', () => {
    const e = rueckmeldungEntwurf(befund, '  Ist seit März so.  ');
    expect(e).toEqual({
      title: 'Rückmeldung: Riss Nordwand',
      category: RUECKMELDUNG,
      note: 'Zu Befund: Riss Nordwand\nIst seit März so.',
      position: [1.5, -2, 3.25],
      station: 'S012',
    });
    if ('fehler' in e) throw new Error('unerwartet');
    expect(e.note.startsWith('Zu Befund: ')).toBe(true);
    // Kopie, keine geteilte Referenz auf die Befundposition.
    expect(e.position).not.toBe(befund.position);
  });

  it('ohne Standpunkt bleibt der Standpunkt leer', () => {
    const e = rueckmeldungEntwurf({ ...befund, station: null }, 'ok');
    expect('fehler' in e ? null : e.station).toBeNull();
  });

  it('langer Befundtitel → Titel genau so lang wie erlaubt, endet auf „…“', () => {
    const e = rueckmeldungEntwurf({ ...befund, titel: 'x'.repeat(200) }, 'ok');
    if ('fehler' in e) throw new Error(e.fehler);
    expect(e.title).toHaveLength(POI_LIMITS.title);
    expect(e.title.endsWith('…')).toBe(true);
    expect(e.title.startsWith('Rückmeldung: xxx')).toBe(true);
  });

  it('zu langer Text → Fehler mit der erlaubten Länge (Grenze minus Bezugszeile)', () => {
    const kopf = `${zuBefundZeile(befund.titel)}\n`.length;
    const erlaubt = POI_LIMITS.note - kopf;
    const genau = rueckmeldungEntwurf(befund, 'a'.repeat(erlaubt));
    expect('fehler' in genau).toBe(false);
    if (!('fehler' in genau)) expect(genau.note).toHaveLength(POI_LIMITS.note);
    expect(rueckmeldungEntwurf(befund, 'a'.repeat(erlaubt + 1))).toEqual({ fehler: `Rückmeldung zu lang (höchstens ${erlaubt} Zeichen).` });
  });

  it('Bezugszeile', () => {
    expect(zuBefundZeile('Feuchte Stelle')).toBe('Zu Befund: Feuchte Stelle');
  });
});
