import { describe, expect, it } from 'vitest';
import { leseGenauigkeit, messhinweise, panelZeilen } from '../src/genauigkeit';
import type { Genauigkeit } from '../src/genauigkeit';
import { ManifestError, parseManifest } from '../src/manifest';
import { measurementNotes } from '../src/pointSources';
import v2 from './fixtures/manifest-v2.json';

// Form wie import/genauigkeit.py `bewerte` (Pilot-Fall: Maßstab aus A–B gesetzt, Lage nur Kamera-GNSS).
const PILOT = {
  formatVersion: 1,
  absolute: { status: 'adjustedOnly', controlResiduals: null, cameraGroups: [{ name: 'Nadir', n: 900 }] },
  scale: { status: 'set', setFrom: ['A–B Laser'] },
  points: [],
  distances: [{ name: 'A–B Laser', role: 'control', reference: 53.745, referenceAccuracy: 0.02, estimated: 53.745,
    error: 0, errorPct: 0 }],
  source: { software: 'Photogrammetrie-Werkzeug', version: '2.1.3', created: '2026-10-11T07:00:00Z',
    projectSha256: 'ab'.repeat(32), crs: 'EPSG:25833' },
  report: { filename: 'report.pdf', sha256: 'cd'.repeat(32) },
};

const GEPRUEFT = {
  ...PILOT,
  absolute: { status: 'checked', n: 4, rmse: { x: 0.03, y: 0.04, z: 0.021 }, max: { xy: 0.08, z: 0.04 },
    withoutEstimate: 0, controlResiduals: { n: 6, rmse: { x: 0.01, y: 0.01, z: 0.01 }, max: { xy: 0.02, z: 0.01 } } },
  scale: { status: 'checked', n: 1, maxErrorPct: 0.05, toleranceFactor: 3, setFrom: ['A–B Laser'] },
  distances: [...PILOT.distances, { name: 'C–D', role: 'check', errorPct: 0.05 }],
  report: null,
};

const lies = (raw: unknown): Genauigkeit => {
  const g = leseGenauigkeit(raw);
  if (!g || typeof g === 'string') throw new Error(`nicht gelesen: ${String(g)}`);
  return g;
};

describe('Genauigkeitsnachweis (F15)', () => {
  it('Pilot: Maßstab gesetzt, nicht geprüft; Lage nur Drohnen-GNSS, ohne ±-Wert', () => {
    const z = panelZeilen(lies(PILOT));
    expect(z[0]).toBe('Lage nicht unabhängig geprüft (nur Drohnen-GNSS)');
    expect(z[1]).toBe('Maßstab aus 1 Strecke gesetzt (A–B Laser), nicht geprüft');
    expect(z[2]).toBe('Quelle: Photogrammetrie-Werkzeug 2.1.3, 2026-10-11, Projekt abababab…');
    expect(z[3]).toBe('Bericht: report.pdf (SHA-256 cdcdcdcd…)');
    expect(z.join(' ')).not.toMatch(/±|RMSE/);
  });

  it('Kontrollpunkte und Kontrollstrecke: Kennzahlen mit Vorzeichen und Anzahl', () => {
    const g = lies(GEPRUEFT);
    expect(panelZeilen(g).slice(0, 2)).toEqual([
      'Lage geprüft an 4 Kontrollpunkten: RMSE Lage 5 cm, Höhe 2,1 cm (max. 8 cm / 4 cm)',
      'Maßstab geprüft: +0,05 % an 1 Kontrollstrecke',
    ]);
    // Lage geprüft → am Messwert nur der Maßstab.
    expect(messhinweise(g)).toEqual(['Maßstab geprüft: +0,05 % an 1 Kontrollstrecke']);
  });

  it('nur Passpunkte → „nur Ausgleichsresiduen“; abweichende Strecke; kein Nachweis', () => {
    const pass = lies({ ...PILOT, absolute: { status: 'adjustedOnly', controlResiduals: GEPRUEFT.absolute.controlResiduals } });
    expect(panelZeilen(pass)[0]).toBe('Lage nicht unabhängig geprüft (nur Ausgleichsresiduen an 6 Passpunkten)');
    const ab = lies({ ...GEPRUEFT, scale: { status: 'deviates', n: 2, maxErrorPct: 0.4 } });
    expect(panelZeilen(ab)[1]).toBe('Maßstab weicht ab: max. 0,40 % an 2 Kontrollstrecken');
    expect(panelZeilen(undefined)).toEqual(['kein Genauigkeitsnachweis']);
  });

  it('kaputter Nachweis bricht das Laden ab, fehlender nicht', () => {
    expect(leseGenauigkeit(undefined)).toBeUndefined();
    expect(leseGenauigkeit({ ...PILOT, scale: { status: 'gut' } })).toMatch(/scale.status/);
    expect(leseGenauigkeit({ ...GEPRUEFT, absolute: { status: 'checked' } })).toMatch(/Kontrollpunkte/);
    const j = structuredClone(v2) as { layers: { provenance: Record<string, unknown> }[] };
    j.layers[0]!.provenance.accuracy = { absolute: { status: 'checked' }, scale: { status: 'none' } };
    expect(() => parseManifest(j)).toThrow(ManifestError);
    j.layers[0]!.provenance.accuracy = PILOT;
    expect(parseManifest(j).cloud!.provenance.accuracy?.scale.status).toBe('set');
  });

  it('Messhinweis ersetzt „Maßstab nicht belegt“ bei Ebenen mit Nachweis', () => {
    const layers = [{ id: 'w', title: 'Wolke', provenance: { scaleVerified: true, interpolated: false, accuracy: lies(PILOT) } }];
    const notes = measurementNotes({ pointSources: [{ source: 'pointcloud', layerId: 'w' }] }, layers);
    expect(notes).toEqual(['Maßstab aus 1 Strecke gesetzt (A–B Laser), nicht geprüft',
      'Lage nicht unabhängig geprüft (nur Drohnen-GNSS)']);
  });
});
