import { describe, expect, it } from 'vitest';
import { rechteImPortal, ROLLEN_NAME, rolleText } from '../src/portal/rechte';

const NICHTS = { bearbeiten: false, export: false, verlauf: false, einstellungen: false };

describe('rechteImPortal', () => {
  it('Bearbeiter: Bearbeiten, Export, Verlauf; keine Projekt-Einstellungen', () => {
    expect(rechteImPortal('bearbeiter')).toEqual({ bearbeiten: true, export: true, verlauf: true, einstellungen: false });
  });
  it('Projektleitung und Admin: alles', () => {
    for (const r of ['projektleitung', 'admin']) expect(rechteImPortal(r)).toEqual({ bearbeiten: true, export: true, verlauf: true, einstellungen: true });
  });
  it('Export für Bearbeiter ausgeschaltet: nur Bearbeiter verlieren den Export', () => {
    const aus = { exportBearbeiter: false };
    expect(rechteImPortal('bearbeiter', aus).export).toBe(false);
    expect(rechteImPortal('projektleitung', aus).export).toBe(true);
    expect(rechteImPortal('admin', aus).export).toBe(true);
  });
  it('Betrachter, Rückmelder und Unbekanntes: nichts', () => {
    for (const r of ['betrachter', 'rueckmelder', '', 'chef', 'Projektleitung', 'admin ', 'constructor', '__proto__']) {
      expect(rechteImPortal(r)).toEqual(NICHTS);
    }
  });
  it('Anzeigenamen', () => {
    expect(ROLLEN_NAME).toEqual({ betrachter: 'Betrachter', rueckmelder: 'Rückmelder', bearbeiter: 'Bearbeiter', projektleitung: 'Projektleitung' });
    expect(rolleText('projektleitung')).toBe('Projektleitung');
    expect(rolleText('rueckmelder')).toBe('Rückmelder');
    expect(rolleText('chef')).toBe('chef');
    expect(rolleText('constructor')).toBe('constructor');
  });
});
