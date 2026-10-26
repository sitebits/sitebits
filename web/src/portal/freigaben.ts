// Freigabe der Personenbilder (F20) in den Projekt-Einstellungen: je Ebene (Panoramen, Fotos)
// Stand der Anonymisierung im Paket, aktive Freigabe, abweichende Dateien; Freigeben und Zurücknehmen
// für Projektleitung/Admin. Die Freigabe bindet der Server an die SHA-256 der Dateien — ein danach
// ausgetauschtes Bild sehen Betrachter und Rückmelder nicht.

import { anfrage } from '../plattformApi';
import { abschnitt, el, knopf } from './verwaltung';

type Melde = (text: string) => void;

export interface EbenenStand {
  ebene: 'panos' | 'fotos';
  vorhanden: boolean;
  torAktiv: boolean;
  dateien: number;
  fehler: string | null;
  anonymisierung: { status: string; werkzeug: string | null; manifestSha256: string | null; geprueft: boolean };
  freigabe: { art: 'anonymisiert' | 'personenfrei'; dateien: number; abweichend?: number; freigegebenAm: number } | null;
}

export interface FreigabenAntwort { ebenen: EbenenStand[]; panoramenNurFreigegeben: boolean }

const NAME: Record<EbenenStand['ebene'], string> = { panos: 'Panoramen', fotos: 'Fotos' };

/** Ein Satz je Ebene: was Betrachter und Rückmelder heute sehen. Rein, für Tests. */
export function standText(s: EbenenStand): string {
  const n = NAME[s.ebene];
  if (!s.vorhanden) return `${n}: nicht im Paket.`;
  if (s.freigabe) {
    const art = s.freigabe.art === 'anonymisiert' ? 'anonymisiert und geprüft' : 'als personenfrei erklärt';
    const ab = s.freigabe.abweichend ?? 0;
    if (s.freigabe.abweichend === undefined) return `${n}: freigegeben (${art}), ${s.freigabe.dateien} Dateien (noch nicht nachgeprüft).`;
    return ab > 0
      ? `${n}: freigegeben (${art}), aber ${ab} von ${s.freigabe.dateien} Dateien wurden danach verändert und sind gesperrt.`
      : `${n}: freigegeben (${art}), ${s.freigabe.dateien} Dateien.`;
  }
  if (!s.torAktiv) return `${n}: Altbestand ohne Anonymisierung – für alle Beteiligten sichtbar, bis „Panoramen nur freigegeben zeigen“ an ist.`;
  return `${n}: nicht freigegeben – nur Bearbeiter, Projektleitung und Admins sehen sie.`;
}

export function freigabenAbschnitt(stand: FreigabenAntwort, o: {
  pfad: string; admin: boolean; melde: Melde; status: Melde; neu: (fokus?: string) => Promise<void>;
  /** Neu zeichnen mit Hash-Prüfung aller freigegebenen Dateien (Sekunden je GB). */
  pruefen: (fokus: string) => Promise<void>;
}): HTMLElement {
  const teile: HTMLElement[] = [
    el('p', 'sv-hinweis', 'Personen auf Panoramen und Fotos: Betrachter und Rückmelder sehen nur freigegebene Dateien. Die Freigabe gilt genau für die heute vorhandenen Dateien – ein später ausgetauschtes Bild ist gesperrt, bis neu freigegeben wird.'),
  ];
  for (const s of stand.ebenen) {
    if (!s.vorhanden) continue;
    const zeile = el('div', 'sv-freigabe-zeile');
    zeile.id = `sv-pe-freigabe-${s.ebene}`;
    zeile.append(el('p', undefined, standText(s)));
    if (s.fehler) zeile.append(el('p', 'sv-hinweis', `Hinweis: ${s.fehler}`));
    const n = NAME[s.ebene];
    if (s.freigabe) {
      if (s.freigabe.abweichend === undefined) {
        zeile.append(knopf(`${n}: Dateien jetzt prüfen`, async () => {
          await o.pruefen(zeile.id);
          o.status(`${n}: Dateien geprüft.`);
        }, o.melde));
      }
      zeile.append(knopf(`${n}: Freigabe zurücknehmen`, async () => {
        await anfrage('DELETE', `${o.pfad}/freigaben/${s.ebene}`);
        await o.neu(zeile.id);
        o.status(`${n}: Freigabe zurückgenommen.`);
      }, o.melde, `${n} für Betrachter und Rückmelder sperren?`));
    } else {
      if (s.anonymisierung.geprueft) {
        zeile.append(knopf(`${n} freigeben (anonymisiert, ${s.dateien} Dateien)`, async () => {
          await anfrage('POST', `${o.pfad}/freigaben/${s.ebene}`, { art: 'anonymisiert' });
          await o.neu(zeile.id);
          o.status(`${n} freigegeben.`);
        }, o.melde, `${n} nach Durchsicht für alle Beteiligten freigeben?`));
      } else {
        zeile.append(el('p', 'sv-hinweis', 'Keine geprüfte Anonymisierung im Paket.'));
      }
      if (o.admin) {
        zeile.append(knopf(`${n} als personenfrei erklären`, async () => {
          await anfrage('POST', `${o.pfad}/freigaben/${s.ebene}`, { art: 'personenfrei' });
          await o.neu(zeile.id);
          o.status(`${n} als personenfrei erklärt und freigegeben.`);
        }, o.melde, `Bestätigen: Auf keinem Bild dieser Ebene sind Personen oder Kennzeichen erkennbar. Die Erklärung wird mit Ihrem Namen protokolliert.`));
      }
    }
    teile.push(zeile);
  }
  if (teile.length === 1) teile.push(el('p', undefined, 'Das Paket enthält keine Panoramen oder Fotos.'));
  const s = abschnitt('Freigabe von Panoramen und Fotos', ...teile);
  s.id = 'sv-pe-freigaben';
  return s;
}
