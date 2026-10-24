// Name der Browser-Datenbank für lokale Befundentwürfe. Ohne Importe: das Portal löscht die Datenbank beim
// Abmelden und soll dafür nicht den Viewer-Code (POIs, Standpunkte, Manifest) mitbündeln.
export const ENTWURF_DB = 'sv-befundentwurf';
export const ENTWURF_STORE = 'entwuerfe';

// Entwürfe gepickter Kontrollpunkte (kontrollpunktEntwurf.ts): localStorage-Schlüssel mit diesem Präfix, einer je
// Projekt und SHA-256 der Soll-Liste. Das Portal räumt sie beim Abmelden mit den Befundentwürfen weg.
export const KP_ENTWURF_PRAEFIX = 'sv-kp-entwurf:';

/** Alle Kontrollpunkt-Entwürfe aus `speicher` löschen; ohne Speicher oder bei Fehlern still (Abmelden hängt nie daran). */
export function kpEntwuerfeLoeschen(speicher: Storage | null | undefined): void {
  try {
    if (!speicher) return;
    const weg: string[] = [];
    for (let i = 0; i < speicher.length; i++) {
      const k = speicher.key(i);
      if (k?.startsWith(KP_ENTWURF_PRAEFIX)) weg.push(k);
    }
    for (const k of weg) speicher.removeItem(k);
  } catch {
    /* gesperrter Speicher: nichts zu löschen */
  }
}
