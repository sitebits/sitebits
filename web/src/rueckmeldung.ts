// Rückmeldung zu einem Befund (rein, ohne DOM): Wer eine Rückmeldung als Text gibt, bekommt einen POI an genau der
// Stelle des Befunds — Position und Standpunkt sind eine Kopie des Befunds, nie aus Kamera oder Ansicht. Die
// Kennzeichnung ist die Kategorie „Rückmeldung“; der Bezug zum Befund steht als erste Notizzeile „Zu Befund: …“.
import type { Vec3 } from './manifest';
import { POI_LIMITS, RUECKMELDUNG } from './poiStore';
import type { PoiDraft } from './poiStore';

/** Erste Notizzeile einer Rückmeldung: der Titel des Befunds, auf den sie sich bezieht. */
export const zuBefundZeile = (titel: string): string => `Zu Befund: ${titel}`;

const LEER = 'Bitte eine Rückmeldung eingeben.';
const TITEL_VORSATZ = 'Rückmeldung: ';

/** Text auf höchstens `max` Zeichen, gekürzt mit „…“ am Ende. */
const kuerze = (t: string, max: number): string => (t.length <= max ? t : `${t.slice(0, max - 1)}…`);

/**
 * Entwurf einer Rückmeldung zum Befund aus dem eingegebenen Text: getrimmt; leer → Fehler; Titel „Rückmeldung:
 * <Befundtitel>“ (gekürzt auf die Titelgrenze); Notiz = Bezugszeile + Zeilenumbruch + Text, länger als die
 * Notizgrenze → Fehler mit der für den Text verbleibenden Länge.
 */
export function rueckmeldungEntwurf(
  befund: { titel: string; position: Vec3; station: string | null },
  text: string,
): PoiDraft | { fehler: string } {
  const t = text.trim();
  if (t === '') return { fehler: LEER };
  const kopf = `${zuBefundZeile(befund.titel)}\n`;
  const erlaubt = POI_LIMITS.note - kopf.length;
  if (t.length > erlaubt) return { fehler: `Rückmeldung zu lang (höchstens ${Math.max(0, erlaubt)} Zeichen).` };
  return {
    title: kuerze(`${TITEL_VORSATZ}${befund.titel}`, POI_LIMITS.title),
    category: RUECKMELDUNG,
    note: `${kopf}${t}`,
    position: [befund.position[0], befund.position[1], befund.position[2]],
    station: befund.station,
  };
}

/** Was der Empfang mit der Antwort des Servers zu einer eigenen Anlage tut. */
export interface AnlageFolge {
  /** Bestätigt: als Text gesendete Rückmeldung ist gespeichert; abgelehnt: nicht gespeichert; sonst nur Status leeren. */
  art: 'gespeichert' | 'abgelehnt' | 'erledigt';
  /** Die lokale Kopie entfernen (nur eine, die der Server nie bestätigt hat). */
  entfernen: boolean;
  /** Text der als Text gesendeten Rückmeldung (null: per „Hier markieren“ angelegt). */
  text: string | null;
  grund: string | null;
}

/**
 * Im Empfang angelegte POIs, deren Antwort vom Server noch aussteht (Text je ID bei einer als Text gesendeten
 * Rückmeldung). Nur für sie entscheidet der Empfang über eine Ablehnung, und nur bis zur ersten Antwort: Einen POI,
 * den der Server bestätigt hat, lokal zu entfernen, hieße ihn beim nächsten Abgleich am Server zu löschen.
 */
export class WartendeAnlagen {
  private readonly offen = new Map<string, string | null>();

  constructor(private readonly bestaetigt: (id: string) => boolean) {}

  /** Neu angelegt; ein schon bestätigter POI (z. B. erneut gespeichert) wartet nicht. */
  merke(id: string, text: string | null = null): void {
    if (!this.bestaetigt(id)) this.offen.set(id, text);
  }

  /** Antwort des Servers (fehler null = bestätigt); null, wenn die ID hier nicht wartet. Jede Antwort beendet das Warten. */
  ergebnis(id: string, fehler: string | null): AnlageFolge | null {
    if (!this.offen.has(id)) return null;
    const text = this.offen.get(id) ?? null;
    this.offen.delete(id);
    if (fehler === null) return { art: 'gespeichert', entfernen: false, text, grund: null };
    // Trotz Fehler gebunden (Konflikt, dessen Stand übernommen wurde): bleibt, wie er ist.
    if (this.bestaetigt(id)) return { art: 'erledigt', entfernen: false, text, grund: fehler };
    return { art: 'abgelehnt', entfernen: true, text, grund: fehler };
  }
}
