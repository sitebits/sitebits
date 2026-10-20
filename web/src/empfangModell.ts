// Reines Modell der Empfängeransicht (ohne DOM, in Vitest testbar): Einträge der Befundliste aus den POIs
// und ihren Server-Angaben, Blättern zum nächsten/vorigen Befund und die Texte für Leinwandname,
// Koordinate und Datum. Die Anzeige baut empfang.ts bzw. befundListe.ts daraus.
import type { BefundMeta } from './poiPersistenz';
import { istRueckmeldung } from './poiStore';
import type { Poi } from './poiStore';

export interface BefundEintrag {
  id: string; titel: string; kategorie: string; notiz: string; frage: string | null;
  standpunkt: string | null; koordinate: [number, number, number];
  datum: number | null; urheber: string | null; rueckmeldung: boolean;
  /** Befund-Fäden: Status, Frist, „überfällig“ als Text; null ohne Fäden oder ohne Stand. */
  kennzeichen: string | null;
}

/** Befund-Fäden im Empfang: Filter und Kennzeichen je Server-ID (fadenStaende.ts). */
export interface FadenSicht {
  passt(serverId: string | undefined): boolean;
  kennzeichen(serverId: string | undefined): string | null;
}

/** Zeile, ab der die Notiz eine Frage an den Empfänger ist („Frage:“ am Zeilenanfang, groß oder klein). */
const FRAGE_ZEILE = /^\s*frage:\s*/i;

/**
 * Teilt eine Notiz in den Text davor und die „Frage an Sie“: ab der ersten Zeile, die mit „Frage:“ beginnt
 * (ohne das Wort), bis zum Ende. Ohne solche Zeile oder mit leerer Frage: frage = null.
 */
export function frageAus(notiz: string): { notiz: string; frage: string | null } {
  const zeilen = notiz.replace(/\r\n?/g, '\n').split('\n');
  const i = zeilen.findIndex((z) => FRAGE_ZEILE.test(z));
  if (i < 0) return { notiz: zeilen.join('\n').trim(), frage: null };
  const frage = [zeilen[i]!.replace(FRAGE_ZEILE, ''), ...zeilen.slice(i + 1)].join('\n').trim();
  return { notiz: zeilen.slice(0, i).join('\n').trim(), frage: frage === '' ? null : frage };
}

/** Einträge in der Reihenfolge der POI-Liste; Urheber und Datum nur, wo der Server sie liefert. Mit Befund-Fäden
 *  nur die zum Filter passenden Befunde, mit Kennzeichen. */
export function befundEintraege(pois: readonly Poi[], meta: (id: string) => BefundMeta | null, faden?: FadenSicht): BefundEintrag[] {
  return pois.flatMap((p) => {
    const m = meta(p.id);
    if (faden && !faden.passt(m?.serverId)) return [];
    const { notiz, frage } = frageAus(p.note);
    return {
      id: p.id,
      titel: p.title,
      kategorie: p.category,
      notiz,
      frage,
      standpunkt: p.station,
      koordinate: [p.position[0], p.position[1], p.position[2]],
      datum: m?.geaendertAm ?? null,
      urheber: m?.erstelltVonName ?? null,
      rueckmeldung: istRueckmeldung(p.category),
      kennzeichen: faden?.kennzeichen(m?.serverId) ?? null,
    };
  });
}

/**
 * ID des nächsten (richtung 1) bzw. vorigen (-1) Befunds; an den Enden null (kein Umlauf, damit „Nächster“
 * am letzten Befund erkennbar endet). Unbekannte oder fehlende aktuelle ID: erster bzw. letzter.
 */
export function nachbar(liste: readonly BefundEintrag[], aktuell: string | null, richtung: -1 | 1): string | null {
  if (liste.length === 0) return null;
  const i = aktuell === null ? -1 : liste.findIndex((e) => e.id === aktuell);
  if (i < 0) return (richtung === 1 ? liste[0] : liste[liste.length - 1])!.id;
  return liste[i + richtung]?.id ?? null;
}

/** Zugänglicher Name der Leinwand in der Empfängeransicht. */
export function leinwandName(o: { standpunkt: string | null; befund: string | null }): string {
  const teile = ['3D-Ansicht'];
  if (o.standpunkt !== null) teile.push(`Standpunkt ${o.standpunkt}`);
  if (o.befund !== null) teile.push(`Blick auf Befund „${o.befund}“`);
  return teile.join(', ');
}

const METER = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 3, maximumFractionDigits: 3 });

/** Auf Millimeter gerundet; ein auf 0 gerundeter Wert ohne Vorzeichen (kein „-0,000“). */
const meter = (x: number): string => {
  const r = Math.round(x * 1000) / 1000;
  return METER.format(r === 0 ? 0 : r);
};

/** Weltkoordinate als Text; Ost/Nord/Höhe benennen die Weltachsen x/y/z. */
export function koordinateText(p: readonly [number, number, number]): string {
  return `Ost ${meter(p[0])} m, Nord ${meter(p[1])} m, Höhe ${meter(p[2])} m`;
}

// Feste Zeitzone: das Datum eines Befunds hängt nicht von der Uhr des anzeigenden Geräts ab.
const DATUM = new Intl.DateTimeFormat('de-DE', { timeZone: 'Europe/Berlin', day: '2-digit', month: '2-digit', year: 'numeric' });

/** Datum zu Sekunden seit 1970 (Server-Zeitstempel), leer ohne gültigen Wert. */
export function datumText(sekunden: number | null): string {
  if (sekunden === null || !Number.isFinite(sekunden)) return '';
  const d = new Date(sekunden * 1000);
  return Number.isNaN(d.getTime()) ? '' : DATUM.format(d);
}
