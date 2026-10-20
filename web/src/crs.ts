// Reine CRS-Klassifikationslogik — OHNE jeglichen Import aus '@giro3d/giro3d' (Giro3D ist
// Bundler-only, extensionless ESM-Imports, und in Vitest/Node nicht ladbar). Diese Datei ist die
// vollständig unter Vitest testbare Hälfte; die dünne Giro3D-Hülle liegt in crsGiro3d.ts und wird
// nicht von Tests importiert, sondern nur über `npm run typecheck` abgesichert.
//
// CRS wird nie geraten (Binding Constraint): Quelle oder --crs, sonst crs.wkt = null → 'local'.

export type CrsKind = 'local' | 'epsg' | 'wkt';

/**
 * Ergebnis der Klassifikation. `id` ist für 'local'/'epsg' der Registrierungs-Schlüssel für
 * CoordinateSystem.register(id, def); für 'wkt' trägt `id` die vollständige WKT-Definition selbst
 * (Übergabe an CoordinateSystem.fromWkt(id)), und `def` ist dort null, da keine proj4-Registrierung
 * nötig/möglich ist.
 */
export interface ClassifiedCrs {
  kind: CrsKind;
  id: string;
  def: string | null;
  /**
   * Nur gesetzt, wenn eine CRS-Angabe vorhanden, aber nicht auswertbar war (Freitext, `EPSG::x`,
   * unbekannter EPSG-Code): dann ist kind 'local' — der Viewer öffnet trotzdem, weil er nichts
   * umprojiziert und das CRS nur als metrischen Träger braucht (I4). Nichts wird geraten; die
   * Angabe bleibt unverändert im Manifest stehen.
   */
  fallback?: string;
}

export const CRS_FALLBACK_HINT = 'Koordinatensystem nicht auswertbar – Anzeige in lokalen Koordinaten';

export const LOCAL_ID = 'scanview:local';
// Nur als metrischer Träger für lokale (nicht georeferenzierte) Punktwolken – keine Umprojektion.
export const LOCAL_DEF = '+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs';

// proj4-Definitionen für die unterstützten EPSG-Codes (25832/25833: UTM ETRS89;
// 3068: DHDN / Soldner Berlin inkl. towgs84-Transformation nach WGS84).
export const PROJ4: Record<string, string> = {
  'EPSG:25832': '+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs',
  'EPSG:25833': '+proj=utm +zone=33 +ellps=GRS80 +units=m +no_defs',
  'EPSG:3068':
    '+proj=cass +lat_0=52.4186482777778 +lon_0=13.6272036666667 +x_0=40000 +y_0=10000 ' +
    '+ellps=bessel +towgs84=598.1,73.7,418.2,0.202,0.045,-2.455,6.7 +units=m +no_defs',
};

const LOCAL_CS_RE = /^\s*LOCAL_CS\[/;
const EPSG_RE = /^EPSG:\d+$/;
// WKT1/WKT2 beginnen mit einem Schlüsselwort und „[“ (PROJCS[, GEOGCS[, PROJCRS[, COMPD_CS[ …).
const WKT_RE = /^[A-Z][A-Z0-9_]*\s*\[/;

/** Lokaler Träger mit Hinweis, warum die vorhandene Angabe nicht genutzt wird. */
export function localFallback(reason: string): ClassifiedCrs {
  return { kind: 'local', id: LOCAL_ID, def: LOCAL_DEF, fallback: `${CRS_FALLBACK_HINT} (${reason}).` };
}

/** Kurzer, anzeigbarer Anfang einer CRS-Angabe (Freitext kann lang sein). */
export function crsExcerpt(text: string, max = 40): string {
  const t = text.trim().replace(/\s+/g, ' ');
  return t.length > max ? `${t.slice(0, max)}…` : t;
}

export function classifyCrs(wkt: string | null): ClassifiedCrs {
  if (wkt === null || LOCAL_CS_RE.test(wkt)) {
    return { kind: 'local', id: LOCAL_ID, def: LOCAL_DEF };
  }
  const trimmed = wkt.trim();
  if (EPSG_RE.test(trimmed)) {
    const def = PROJ4[trimmed];
    if (!def) return localFallback(`${trimmed} ist nicht hinterlegt`);
    return { kind: 'epsg', id: trimmed, def };
  }
  if (WKT_RE.test(trimmed)) return { kind: 'wkt', id: wkt, def: null };
  return localFallback(`„${crsExcerpt(trimmed)}“`);
}
