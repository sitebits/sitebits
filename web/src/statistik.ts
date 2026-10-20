// Nutzungszähler des Viewers: oeffnen/standpunkt/messung/poi je Projekt,
// gebündelt höchstens alle 10 s, beim Verlassen/Verbergen sofort mit keepalive (fetch statt sendBeacon — sendBeacon
// kann den CSRF-Header nicht setzen). Keine Personendaten; Fehler werden verschluckt — die Statistik darf
// die Arbeit im Viewer nie stören.
import { standardUhr } from './abgleich';
import type { Uhr } from './abgleich';
import { anfrage } from './plattformApi';

export type ZaehlArt = 'oeffnen' | 'standpunkt' | 'messung' | 'poi';
export type Zaehlstand = Record<ZaehlArt, number>;
export const BUENDEL_MS = 10_000;

const leer = (): Zaehlstand => ({ oeffnen: 0, standpunkt: 0, messung: 0, poi: 0 });

export class StatistikZaehler {
  private stand: Zaehlstand = leer();
  private timer: unknown = null;

  constructor(
    private readonly senden: (z: Zaehlstand, keepalive: boolean) => Promise<void>,
    private readonly uhr: Uhr = standardUhr,
  ) {}

  zaehle(art: ZaehlArt): void {
    this.stand[art]++;
    if (this.timer !== null) return;
    this.timer = this.uhr.setTimeout(() => {
      this.timer = null;
      this.leeren(false);
    }, BUENDEL_MS);
  }

  leeren(keepalive: boolean): void {
    if (this.timer !== null) {
      this.uhr.clearTimeout(this.timer);
      this.timer = null;
    }
    const z = this.stand;
    if (Object.values(z).every((n) => n === 0)) return;
    this.stand = leer();
    void this.senden(z, keepalive).catch(() => undefined);
  }
}

export function statistikSender(pid: string, fetchFn: typeof fetch = fetch): (z: Zaehlstand, keepalive: boolean) => Promise<void> {
  return (z, keepalive) => anfrage<void>('POST', `/api/v1/statistik/${encodeURIComponent(pid)}`, z, fetchFn, keepalive);
}

/** Beim Verlassen UND beim Verbergen des Tabs (Final-Fix C/D) sofort mit keepalive senden: mobile Browser
 *  beenden verborgene Tabs oft ohne pagehide — sonst gingen bis zu 10 s Zählung verloren. */
export function leerenBeimVerlassen(
  z: Pick<StatistikZaehler, 'leeren'>,
  fenster: Pick<EventTarget, 'addEventListener'>,
  dok: Pick<EventTarget, 'addEventListener'> & { readonly visibilityState: string },
): void {
  fenster.addEventListener('pagehide', () => z.leeren(true));
  dok.addEventListener('visibilitychange', () => {
    if (dok.visibilityState === 'hidden') z.leeren(true);
  });
}
