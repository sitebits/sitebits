// Welche Ebene darf in initialView.layers stehen? Gemeinsame Fälle mit dem Import
// (import/tests/test_manifest_io.py, _als_startebene_zeigbar für `start-sichtbar … an`): beide Seiten lesen
// fixtures/startebenen.json, damit die Annahmeregel des Imports nicht von der des Viewers abweicht.
import { describe, expect, it } from 'vitest';
import { parseManifest } from '../src/manifest';
import daten from './fixtures/startebenen.json';

type Ebene = Record<string, unknown> & { id: string };
const manifest = (ebene: Ebene, startebenen?: string[]): unknown => ({
  ...daten.basis,
  initialView: { ...daten.basis.initialView, ...(startebenen ? { layers: startebenen } : {}) },
  layers: ebene.id === daten.basisWolke.id ? [daten.basisWolke] : [daten.basisWolke, ebene],
});

describe('Startebenen: Viewer und Import nach denselben Fällen', () => {
  it('jede Fallart ist vertreten', () => {
    expect(new Set(daten.faelle.map((f) => f.viewer))).toEqual(new Set(['unterstuetzt', 'nicht-unterstuetzt', 'ungueltig']));
  });
  for (const f of daten.faelle) {
    it(`${f.name}: ${f.viewer}`, () => {
      const ebene = f.ebene as Ebene;
      if (f.viewer === 'ungueltig') {
        // Schon ohne Startebene lehnt der Viewer das Manifest ab.
        expect(() => parseManifest(manifest(ebene))).toThrow();
        return;
      }
      const ohne = parseManifest(manifest(ebene));
      expect(ohne.layers.some((l) => l.id === ebene.id && 'unsupported' in l)).toBe(f.viewer === 'nicht-unterstuetzt');
      if (f.viewer === 'unterstuetzt') expect(parseManifest(manifest(ebene, [ebene.id])).initialView.layers).toEqual([ebene.id]);
      else expect(() => parseManifest(manifest(ebene, [ebene.id]))).toThrow(/keine unterstützte Ebene/);
    });
  }
});
