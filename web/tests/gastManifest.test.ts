// Gast-Manifest (ADR 0016, Nachtrag 2026-11-10): der Server gibt je Ebene nur die Felder weiter, die der Viewer liest
// (teilen.py GAST_FELDER). Beleg am Viewer-Code: parseManifest liest aus dem vollen und dem gekürzten
// Manifest dasselbe Projekt. Die Fixture leitet server/tests/test_teilen.py aus gast_felder ab.
import { describe, expect, it } from 'vitest';
import { parseManifest } from '../src/manifest';
import fixture from './fixtures/gast-manifest.json';

describe('Gast-Manifest mit Feld-Positivliste', () => {
  it('Viewer liest aus voll und gekürzt dasselbe Projekt', () => {
    const voll = parseManifest(fixture.voll);
    const gast = parseManifest(fixture.gast);
    expect(gast).toEqual(voll);
    // Nicht leer verglichen: alle vier Ebenenarten sind erkannt, keine fiel auf „nicht unterstützt“.
    expect(gast.layers.map((l) => l.kind)).toEqual(['pointcloud', 'mesh', 'mesh-tiles', 'splats']);
    expect(gast.layers.some((l) => 'unsupported' in l)).toBe(false);
    expect(gast.initialView.layers).toEqual(['splats', 'mesh']);
    // Draufsicht: gekürzt (ohne colorFrom/image) liest der Viewer dieselbe Georeferenz.
    expect(gast.topview).toBeDefined();
    expect(gast.topview).toEqual(voll.topview);
    expect(fixture.gast.topview).not.toHaveProperty('colorFrom');
    expect(fixture.gast.topview).not.toHaveProperty('image');
  });
  it('gekürzt fehlen Quelldatei, Prüfsummen, Importoptionen und Prüfberichte', () => {
    const intern = ['source', 'sourceOptions', 'verify', 'alignment', 'buildLod', 'readerPointCount', 'invalidDropped', 'conversion'];
    // Die volle Fassung trägt jedes interne Feld mindestens einmal, sonst prüfte die Schleife unten nichts.
    for (const k of intern) expect(fixture.voll.layers.some((l) => k in l), k).toBe(true);
    expect(fixture.voll.layers.every((l) => 'source' in l && 'verify' in l)).toBe(true);
    for (const l of fixture.gast.layers) for (const k of intern) expect(l, `${l.id}.${k}`).not.toHaveProperty(k);
    const splat = fixture.gast.layers.find((l) => l.kind === 'splats');
    expect(Object.keys(splat?.frameCheck ?? {}).sort()).toEqual(['ok', 'p50M', 'p95M', 'samples']);
  });
});
