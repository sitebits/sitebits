import { PlattformFehler } from '../plattformApi';
import type { Pruefliste } from '../vorschlagApi';
export const anzahl = (l: Pruefliste): number => l.pois.length + l.messungen.length + l.faden.length;
export interface Sammelergebnis {
  erledigt: number;
  gruende: string[];
}
/** Einzelentscheidungen nacheinander: ein Konflikt verwirft keine übrige Entscheidung. */
export async function gesammelt<T>(liste: readonly T[], entscheide: (x: T) => Promise<unknown>): Promise<Sammelergebnis> {
  const r: Sammelergebnis = { erledigt: 0, gruende: [] };
  for (const x of liste) {
    try {
      await entscheide(x);
      r.erledigt++;
    }
    catch (e) {
      r.gruende.push(e instanceof PlattformFehler && e.status === 409 && e.aktuell != null ? `${e.message} Bitte neu laden.` : e instanceof Error ? e.message : String(e));
    }
  }
  return r;
}
export const ergebnisText = (r: Sammelergebnis, aktion: 'annehmen' | 'verwerfen'): string => `${r.erledigt} ${aktion === 'annehmen' ? 'angenommen' : 'verworfen'}, ${r.gruende.length} übersprungen${r.gruende.length ? ` (${r.gruende.join('; ')})` : ''}.`;
import { befundlinkPfad, eintragInhalt, OHNE_NAMEN } from '../fadenModell';
import type { Namen } from '../fadenModell';
import { MEASURE_LABEL } from '../measureStore';
import type { Tabelle } from '../vorschlagApi';
export interface Pruefzeile {
  text: string;
  wer: string;
  wann: number;
  link: string;
  ziel: {
    tabelle: Tabelle;
    id: string;
    version: number;
  } | {
    tabelle: 'faden';
    poi: string;
    schluessel: string;
  };
}
export interface Pruefgruppe {
  name: string;
  zeilen: Pruefzeile[];
}
/** Gruppierung und menschenlesbare Texte unabhängig von DOM und Netzwerk. */
export function pruefgruppen(l: Pruefliste, pid: string, namen: Namen = OHNE_NAMEN): Pruefgruppe[] {
  return [
    { name: 'POIs', zeilen: l.pois.map(e => ({
        text: `${e.loeschenVorgeschlagen != null ? 'Löschen vorgeschlagen' : 'KI-Vorschlag'}: ${e.daten.title}`,
        wer: e.erstelltVonName ?? 'unbekannt (KI)', wann: e.geaendertAm, link: befundlinkPfad(pid, e.id),
        ziel: { tabelle: 'pois', id: e.id, version: e.version },
      })) },
    { name: 'Messungen', zeilen: l.messungen.map(e => ({
        text: `${e.loeschenVorgeschlagen != null ? 'Löschen vorgeschlagen' : 'KI-Vorschlag'}: ${MEASURE_LABEL[e.daten.kind]}`,
        wer: e.erstelltVonName ?? 'unbekannt (KI)', wann: e.geaendertAm, link: `/ansicht?p=/data/${encodeURIComponent(pid)}/`,
        ziel: { tabelle: 'messungen', id: e.id, version: e.version },
      })) },
    { name: 'Befund-Faden', zeilen: l.faden.map(({ poi, eintrag: e }) => ({
        text: `KI ${eintragInhalt(e, namen)}`, wer: e.erstellt_von.name ?? 'unbekannt (KI)', wann: e.erstellt_am,
        link: befundlinkPfad(pid, poi, e.schluessel), ziel: { tabelle: 'faden', poi, schluessel: e.schluessel },
      })) },
  ];
}
