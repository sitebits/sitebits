// Dünne Giro3D-Hülle um die reine Logik aus crs.ts. Diese Datei importiert
// '@giro3d/giro3d' und ist deshalb NICHT unter Vitest/Node ladbar — sie wird ausschließlich über
// `npm run typecheck` abgesichert. Verifiziert gegen node_modules/@giro3d/giro3d/core/geographic/
// CoordinateSystem.d.ts (Stand Giro3D 2.0.4, kein "exports"-Feld in package.json, daher greift der
// tiefe Unterpfad-Import über Bundler-Moduleauflösung):
//   - register(id, definition, options?) ist idempotent (gibt bei bereits registrierter id die
//     vorhandene Instanz zurück) und synchron; ruft intern fromWkt(definition, {id}) auf.
//   - get(srid) wirft, wenn die id nicht registriert ist (kein undefined-Rückgabewert).
//   - fromWkt(wkt, overrides?) ist synchron und wirft bei nicht parsbarer Definition. Für proj4-
//     Strings (unsere LOCAL_DEF/PROJ4[...]-Werte) parst intern proj4/lib/parseCode() über den
//     testProj()-Zweig (code[0] === '+') zu einem Objekt ohne 'ID'/'PROJCS'; CoordinateSystem.js
//     fängt das im letzten else-Zweig von fromWkt ab und liefert ein gültiges CoordinateSystem,
//     wirft also NICHT — register() funktioniert damit für proj4-Strings wie erwartet.
//   - WICHTIG: fromWkt() registriert NICHT bei proj4/OpenLayers
//     (laut Doc-Kommentar in CoordinateSystem.d.ts: "this does not register the coordinate system
//     with proj4.js. Use register instead."). Der 'wkt'-Zweig unten (echte PROJCS[...]-Definitionen,
//     z. B. DHDN/Soldner Berlin aus Datei) nutzt bewusst fromWkt, das Ergebnis ist
//     damit ein CoordinateSystem-Objekt, das proj4/OpenLayers selbst nicht kennt. Für den aktuellen
//     Task (Datenvertrag/CRS-Klassifikation) unschädlich; für eine spätere Giro3D-Integration, die
//     tatsächlich umprojiziert, muss das ggf. zusätzlich über register() nachgezogen werden.
import CoordinateSystem from '@giro3d/giro3d/core/geographic/CoordinateSystem.js';
import { classifyCrs, crsExcerpt, localFallback } from './crs';
import type { ClassifiedCrs } from './crs';
import type { Project } from './manifest';

/**
 * CRS für die Giro3D-Instanz. Nie werfen (I4): ist die Angabe nicht auswertbar — schon in der
 * Klassifikation oder erst beim Parsen durch Giro3D/proj4 —, gilt der lokale metrische Träger, und
 * `hint` enthält die Meldung für den Nutzer. scanview projiziert nicht um; die Angabe wird nicht
 * geraten, sondern nur nicht verwendet.
 */
export function resolveCrs(m: Pick<Project, 'crs'>): { crs: CoordinateSystem; local: boolean; hint?: string } {
  const c = classifyCrs(m.crs.wkt);
  try {
    return { ...toGiro3d(c), hint: c.fallback };
  } catch {
    const f = localFallback(`„${crsExcerpt(m.crs.wkt ?? '')}“ nicht lesbar`);
    return { ...toGiro3d(f), hint: f.fallback };
  }
}

function toGiro3d({ kind, id, def }: ClassifiedCrs): { crs: CoordinateSystem; local: boolean } {
  if (kind === 'wkt') {
    // id trägt hier die vollständige WKT-Definition (siehe ClassifiedCrs in crs.ts).
    return { crs: CoordinateSystem.fromWkt(id), local: false };
  }
  // register() ist idempotent: bei erneutem Aufruf mit derselben id wird die vorhandene
  // Instanz zurückgegeben, kein doppeltes Registrieren nötig.
  return { crs: CoordinateSystem.register(id, def as string), local: kind === 'local' };
}
