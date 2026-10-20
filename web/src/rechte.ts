// Rechte der eigenen Rolle im Viewer als Erlaubnisliste: was nicht ausdrücklich erlaubt ist, bleibt aus.
// Eine neue oder unbekannte Rolle bekommt so nur Leserecht. Nur Anzeige und Abgleich-Steuerung —
// durchgesetzt werden die Rechte vom Server.
export interface ViewerRechte {
  /** POIs am Server anlegen. */
  poisAnlegen: boolean;
  /** Fremde Server-POIs ändern und löschen. */
  alleAendern: boolean;
  /** Kategorie fest „Rückmeldung“, nur eigene POIs änderbar. */
  nurRueckmeldung: boolean;
  /** Messungen am Server speichern (gemeinsam für alle Projektbeteiligten). */
  messungenTeilen: boolean;
  /** „Browser-POIs in das Projekt übernehmen“. */
  uebernahme: boolean;
}

export const NUR_LESEN: Readonly<ViewerRechte> = {
  poisAnlegen: false,
  alleAendern: false,
  nurRueckmeldung: false,
  messungenTeilen: false,
  uebernahme: false,
};
const VOLL: Readonly<ViewerRechte> = { poisAnlegen: true, alleAendern: true, nurRueckmeldung: false, messungenTeilen: true, uebernahme: true };
const TABELLE: Readonly<Record<string, Readonly<ViewerRechte>>> = {
  bearbeiter: VOLL,
  projektleitung: VOLL,
  admin: VOLL,
  rueckmelder: { ...NUR_LESEN, poisAnlegen: true, nurRueckmeldung: true },
  betrachter: NUR_LESEN,
};

/** Rechte zu einer Rolle, je Aufruf eine eigene Kopie. Nur exakt bekannte Rollen (kein Prototyp-Schlüssel). */
export const rechte = (rolle: string): ViewerRechte => ({ ...(Object.hasOwn(TABELLE, rolle) ? TABELLE[rolle]! : NUR_LESEN) });
