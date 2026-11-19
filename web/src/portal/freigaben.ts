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
  freigabe: {
    art: 'anonymisiert' | 'personenfrei'; dateien: number; freigegebenAm: number;
    /** Nur in der Antwort mit ?pruefen=true: Dateien, deren Hash nicht mehr passt. */
    abweichend?: number;
    /** Letzter gespeicherter Prüfstand (Unix-Sekunden bzw. Anzahl); null = nie geprüft. */
    zuletztGeprueftAm: number | null; zuletztAbweichend: number | null;
  } | null;
}

export interface FreigabenAntwort { ebenen: EbenenStand[]; panoramenNurFreigegeben: boolean }

const NAME: Record<EbenenStand['ebene'], string> = { panos: 'Panoramen', fotos: 'Fotos' };

const ZEIT = new Intl.DateTimeFormat('de-DE', { timeZone: 'Europe/Berlin', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

/** „TT.MM. HH:MM“ in deutscher Zeit, unabhängig von der Zeitzone des Geräts. */
export function pruefZeit(sekunden: number): string {
  const t = Object.fromEntries(ZEIT.formatToParts(new Date(sekunden * 1000)).map((p) => [p.type, p.value]));
  return `${t.day}.${t.month}. ${t.hour}:${t.minute}`;
}

function freigabeText(n: string, f: NonNullable<EbenenStand['freigabe']>): string {
  const art = f.art === 'anonymisiert' ? 'anonymisiert und geprüft' : 'als personenfrei erklärt';
  // Frische Prüfung (abweichend) vor dem gespeicherten Stand; beide kommen aus derselben Prüfung.
  // `?? null`: fehlt das Feld ganz (ältere Antwort), gilt es wie „nie geprüft“.
  const ab = f.abweichend ?? f.zuletztAbweichend ?? null;
  if (ab === null) return `${n}: freigegeben (${art}), ${f.dateien} Dateien (noch nicht nachgeprüft).`;
  const wann = typeof f.zuletztGeprueftAm === 'number' ? ` (geprüft ${pruefZeit(f.zuletztGeprueftAm)})` : '';
  return ab > 0
    ? `${n}: freigegeben (${art}), aber ${ab} von ${f.dateien} Dateien abweichend – für Betrachter gesperrt${wann}.`
    : `${n}: freigegeben (${art}), alle ${f.dateien} Dateien unverändert${wann}.`;
}

/** Ein Satz je Ebene: was Betrachter und Rückmelder heute sehen. Rein, für Tests. */
export function standText(s: EbenenStand): string {
  const n = NAME[s.ebene];
  if (!s.vorhanden) return `${n}: nicht im Paket.`;
  if (s.freigabe) return freigabeText(n, s.freigabe);
  // Tor aus geht nur noch, wenn die Projektleitung den Schalter bewusst abgeschaltet hat (seit 0.13 an).
  if (!s.torAktiv) return `${n}: Achtung, für alle Beteiligten sichtbar, auch ohne Anonymisierung – „Panoramen nur freigegeben zeigen“ ist bewusst aus.`;
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
      // Immer sichtbar: auch nach einer Prüfung kann jemand Dateien tauschen.
      const schonGeprueft = s.freigabe.abweichend !== undefined || typeof s.freigabe.zuletztGeprueftAm === 'number';
      zeile.append(knopf(`${n}: Dateien ${schonGeprueft ? 'erneut' : 'jetzt'} prüfen`, async () => {
        await o.pruefen(zeile.id);
        o.status(`${n}: Dateien geprüft.`);
      }, o.melde));
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
