// Eingaberegeln für „Bearbeiten“ (Titel/Beschreibung) — gespiegelt aus server routen_v1.py
// (TITEL_MAX, BESCHREIBUNG_MAX, Steuerzeichen). Der Server prüft genauso; hier nur für die sofortige Meldung.
export const TITEL_MAX = 120;
export const BESCHREIBUNG_MAX = 2000;

const hatSteuerzeichen = (s: string, erlaubt: string): boolean =>
  [...s].some((c) => {
    const n = c.charCodeAt(0);
    return (n < 32 || n === 127) && !erlaubt.includes(c);
  });

export function pruefeMeta(titel: string, beschreibung: string): string | null {
  if (titel.length > TITEL_MAX) return `Der Titel darf höchstens ${TITEL_MAX} Zeichen haben.`;
  if (beschreibung.length > BESCHREIBUNG_MAX) return `Die Beschreibung darf höchstens ${BESCHREIBUNG_MAX} Zeichen haben.`;
  if (hatSteuerzeichen(titel, '')) return 'Der Titel darf keine Zeilenumbrüche oder Steuerzeichen enthalten.';
  if (hatSteuerzeichen(beschreibung, '\n\r\t')) return 'Die Beschreibung enthält unzulässige Steuerzeichen.';
  return null;
}

// Nur tatsächlich geänderte Felder ans PATCH schicken. Ohne das schreibt ein
// Speichern, das nur die Beschreibung ändern soll, den (unangetasteten, aufgelösten) Titel als
// stillen Override fest — der Server kann das für den Titel nur teilweise abfedern (gleicher Text
// wie der Katalog-Titel wird nicht gespeichert), bei individuellen Titeln aber nicht.
export interface MetaEingabe {
  titel: string;
  beschreibung: string;
}

export function geaenderteFelder(basis: MetaEingabe, eingabe: MetaEingabe): Partial<MetaEingabe> {
  const felder: Partial<MetaEingabe> = {};
  if (eingabe.titel !== basis.titel) felder.titel = eingabe.titel;
  if (eingabe.beschreibung !== basis.beschreibung) felder.beschreibung = eingabe.beschreibung;
  return felder;
}

// Der bei 409 gelieferte aktuelle Stand muss dauerhaft in das Projektobjekt
// der Karte übernommen werden (nicht nur in eine lokale Variable des Formulars) — sonst bleibt
// `version` nach „Abbrechen“ veraltet und ein erneutes „Bearbeiten“ läuft garantiert wieder in
// einen Konflikt.
export function uebernehmeKonflikt<T extends MetaEingabe & { version: number }>(p: T, aktuell: T): void {
  p.titel = aktuell.titel;
  p.beschreibung = aktuell.beschreibung;
  p.version = aktuell.version;
}
