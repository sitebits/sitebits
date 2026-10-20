import { describe, expect, it } from 'vitest';
import { rechte } from '../src/rechte';

const NICHTS = { poisAnlegen: false, alleAendern: false, nurRueckmeldung: false, messungenTeilen: false, uebernahme: false };

describe('rechte', () => {
  it('Bearbeiter und Admin: alles', () => {
    for (const r of ['bearbeiter', 'admin'])
      expect(rechte(r)).toEqual({ poisAnlegen: true, alleAendern: true, nurRueckmeldung: false, messungenTeilen: true, uebernahme: true });
  });
  it('Projektleitung hat im Viewer dieselben Rechte wie ein Bearbeiter', () => {
    expect(rechte('projektleitung')).toEqual(rechte('bearbeiter'));
  });
  it('Rückmelder: nur eigene Rückmeldungen, keine Messungen, keine Übernahme', () => {
    expect(rechte('rueckmelder')).toEqual({ poisAnlegen: true, alleAendern: false, nurRueckmeldung: true, messungenTeilen: false, uebernahme: false });
  });
  it('Betrachter und jede unbekannte Rolle: nur lesen', () => {
    for (const r of ['betrachter', '', 'chef', 'Bearbeiter', 'rueckmelder ', 'toString', 'constructor', '__proto__'])
      expect(rechte(r)).toEqual(NICHTS);
  });
  it('liefert je Aufruf eine eigene Kopie (die Tabelle bleibt unverändert)', () => {
    const a = rechte('bearbeiter');
    a.alleAendern = false;
    expect(rechte('bearbeiter').alleAendern).toBe(true);
  });
});
