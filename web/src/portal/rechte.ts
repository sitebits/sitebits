// Rechte im Portal als positive Erlaubnisliste: Nur ausdrücklich genannte Rollen bekommen
// Bearbeiten, Export, Verlauf und Projekt-Einstellungen; jede andere oder unbekannte Rolle sieht nichts
// davon. Reine Logik (Vitest). Der Server prüft jede Anfrage trotzdem selbst — das hier steuert nur
// die Anzeige.

export type PortalRolle = 'betrachter' | 'rueckmelder' | 'bearbeiter' | 'projektleitung' | 'admin';

export interface PortalRechte {
  bearbeiten: boolean;
  export: boolean;
  verlauf: boolean;
  /** Seite „Projekt-Einstellungen“. */
  einstellungen: boolean;
}

/** Schalter des Projekts, soweit sie die Anzeige im Portal betreffen (der Server prüft selbst). */
export interface PortalSchalter {
  exportBearbeiter: boolean;
}

const VOLL: ReadonlySet<string> = new Set(['bearbeiter', 'projektleitung', 'admin']);
const LEITUNG: ReadonlySet<string> = new Set(['projektleitung', 'admin']);

export function rechteImPortal(rolle: string, schalter: PortalSchalter = { exportBearbeiter: true }): PortalRechte {
  const voll = VOLL.has(rolle);
  return {
    bearbeiten: voll,
    export: voll && (rolle !== 'bearbeiter' || schalter.exportBearbeiter),
    verlauf: voll,
    einstellungen: LEITUNG.has(rolle),
  };
}

/** Anzeigenamen der vergebbaren Projektrollen. */
export const ROLLEN_NAME: Record<'betrachter' | 'rueckmelder' | 'bearbeiter' | 'projektleitung', string> = {
  betrachter: 'Betrachter',
  rueckmelder: 'Rückmelder',
  bearbeiter: 'Bearbeiter',
  projektleitung: 'Projektleitung',
};

/** Anzeigename einer Rolle; Unbekanntes bleibt roh. */
export const rolleText = (rolle: string): string =>
  Object.hasOwn(ROLLEN_NAME, rolle) ? ROLLEN_NAME[rolle as keyof typeof ROLLEN_NAME] : rolle;
