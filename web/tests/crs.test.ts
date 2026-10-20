import { describe, expect, it } from 'vitest';
// Giro3D ist Bundler-only (extensionless ESM-Imports) und in Vitest/Node nicht ladbar.
// Diese Tests dürfen daher NIE etwas aus '@giro3d/giro3d' importieren; geprüft wird ausschließlich
// die reine Klassifikationslogik `classifyCrs`. Die dünne Giro3D-Hülle `resolveCrs` liegt in
// crsGiro3d.ts und wird nur über `npm run typecheck` abgesichert.
import { classifyCrs, CRS_FALLBACK_HINT, LOCAL_DEF, LOCAL_ID, PROJ4 } from '../src/crs';

describe('classifyCrs', () => {
  it('wkt === null → lokal', () => {
    expect(classifyCrs(null)).toEqual({ kind: 'local', id: LOCAL_ID, def: LOCAL_DEF });
  });

  it('LOCAL_CS[...] → lokal', () => {
    const wkt = 'LOCAL_CS["Local Coordinates (m)",LOCAL_DATUM["Local Datum",0],UNIT["metre",1]]';
    expect(classifyCrs(wkt)).toEqual({ kind: 'local', id: LOCAL_ID, def: LOCAL_DEF });
  });

  it('EPSG:25832 → epsg mit proj4-Definition aus der Tabelle', () => {
    expect(classifyCrs('EPSG:25832')).toEqual({ kind: 'epsg', id: 'EPSG:25832', def: PROJ4['EPSG:25832'] });
    expect(PROJ4['EPSG:25832']).toBe('+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs');
  });

  it('EPSG:25833 → epsg mit proj4-Definition aus der Tabelle', () => {
    expect(classifyCrs('EPSG:25833')).toEqual({ kind: 'epsg', id: 'EPSG:25833', def: PROJ4['EPSG:25833'] });
    expect(PROJ4['EPSG:25833']).toBe('+proj=utm +zone=33 +ellps=GRS80 +units=m +no_defs');
  });

  it('EPSG:3068 (Soldner Berlin) → epsg mit proj4-Definition inkl. towgs84', () => {
    expect(classifyCrs('EPSG:3068')).toEqual({ kind: 'epsg', id: 'EPSG:3068', def: PROJ4['EPSG:3068'] });
    expect(PROJ4['EPSG:3068']).toBe(
      '+proj=cass +lat_0=52.4186482777778 +lon_0=13.6272036666667 +x_0=40000 +y_0=10000 ' +
        '+ellps=bessel +towgs84=598.1,73.7,418.2,0.202,0.045,-2.455,6.7 +units=m +no_defs',
    );
  });

  // Nicht auswertbare Angaben verhindern das Öffnen nicht — lokaler Träger + Hinweis, nichts geraten.
  const expectFallback = (input: string, detail: RegExp): void => {
    const c = classifyCrs(input);
    expect(c.kind).toBe('local');
    expect(c.id).toBe(LOCAL_ID);
    expect(c.def).toBe(LOCAL_DEF);
    expect(c.fallback).toContain(CRS_FALLBACK_HINT);
    expect(c.fallback).toMatch(detail);
  };

  it('unbekannter EPSG-Code → lokal mit Hinweis statt Fehler', () => {
    expectFallback('EPSG:9999', /EPSG:9999 ist nicht hinterlegt/);
  });

  it('EPSG::25833 (Doppelpunkt-Variante) → lokal mit Hinweis (nicht geraten)', () => {
    expectFallback('EPSG::25833', /EPSG::25833/);
  });

  it('Freitext → lokal mit Hinweis, langer Text gekürzt', () => {
    expectFallback('ETRS89 / UTM 33N', /„ETRS89 \/ UTM 33N“/);
    const long = 'x'.repeat(200);
    expect(classifyCrs(long).fallback).toContain(`${'x'.repeat(40)}…`);
  });

  it('Hinweistext ist deutsch und wie gefordert formuliert', () => {
    expect(CRS_FALLBACK_HINT).toBe('Koordinatensystem nicht auswertbar – Anzeige in lokalen Koordinaten');
  });

  it('gültige Angaben tragen keinen Hinweis', () => {
    expect(classifyCrs(null).fallback).toBeUndefined();
    expect(classifyCrs('EPSG:25833').fallback).toBeUndefined();
  });

  it('PROJCS[...] → wkt (Übergabe der reinen WKT-Definition)', () => {
    const wkt =
      'PROJCS["DHDN / Soldner Berlin",GEOGCS["DHDN",DATUM["Deutsches_Hauptdreiecksnetz",' +
      'SPHEROID["Bessel 1841",6377397.155,299.1528128]]],UNIT["metre",1],AUTHORITY["EPSG","3068"]]';
    expect(classifyCrs(wkt)).toEqual({ kind: 'wkt', id: wkt, def: null });
  });

  it('WKT2 (PROJCRS[...]) und COMPD_CS[...] → wkt', () => {
    expect(classifyCrs('PROJCRS["ETRS89 / UTM zone 33N",BASEGEOGCRS["ETRS89"]]').kind).toBe('wkt');
    expect(classifyCrs('COMPD_CS["x",PROJCS["y"]]').kind).toBe('wkt');
  });
});
