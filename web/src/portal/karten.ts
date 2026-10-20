// Reine, testbare Projektkarten-Logik: Formatierung + Suche. Keine DOM-Zugriffe hier (die liegen
// in seiten.ts), damit sich alles ohne Browser/DOM per Vitest prüfen lässt.
import type { Projekt } from './api';

// Werte < 10 bekommen eine Nachkommastelle, ab 10 wird auf ganze Einheiten gerundet — dieselbe
// Regel für Ausdehnung (m) und Größe (GB/MB/…) („< 10 m eine Nachkommastelle“).
function rund1(n: number): string {
  const opts: Intl.NumberFormatOptions =
    Math.abs(n) < 10 ? { minimumFractionDigits: 1, maximumFractionDigits: 1 } : { maximumFractionDigits: 0 };
  return new Intl.NumberFormat('de-DE', opts).format(n);
}

export function formatPunkte(n: number | null): string {
  if (n === null) return '–';
  if (n >= 1_000_000) {
    const mio = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(
      n / 1_000_000,
    );
    return `${mio} Mio. Punkte`;
  }
  return `${new Intl.NumberFormat('de-DE').format(n)} Punkte`;
}

export function formatStandpunkte(n: number | null): string {
  return n === null ? '–' : `${new Intl.NumberFormat('de-DE').format(n)} Standpunkte`;
}

const GROESSE_EINHEITEN: Array<[number, string]> = [
  [1e12, 'TB'],
  [1e9, 'GB'],
  [1e6, 'MB'],
  [1e3, 'KB'],
];

export function formatGroesse(b: number): string {
  for (const [groesse, einheit] of GROESSE_EINHEITEN) {
    if (b >= groesse) return `${rund1(b / groesse)} ${einheit}`;
  }
  return `${Math.round(b)} B`;
}

export function formatAusdehnung(a: [number, number, number] | null): string {
  if (a === null) return '–';
  return `${a.map(rund1).join(' × ')} m`;
}

const norm = (s: string): string => s.toLocaleLowerCase('de').normalize('NFD').replace(/\p{M}/gu, '');

export function filtere(p: Projekt[], suche: string): Projekt[] {
  const q = norm(suche.trim());
  if (!q) return p;
  return p.filter((x) => norm(`${x.titel} ${x.beschreibung}`).includes(q));
}

/** Statusmeldung der Projektsuche (WCAG 4.1.3). */
export function trefferText(n: number): string {
  if (n === 0) return 'Kein Projekt gefunden.';
  return n === 1 ? '1 Projekt gefunden.' : `${n} Projekte gefunden.`;
}

export function viewerLink(id: string): string {
  return `/ansicht?p=/data/${id}/`;
}

export function draufsichtUrl(id: string): string {
  return `/data/${id}/topview.png`;
}

// Quelltext-Link (EUPL-1.2, Art. 5): Die Adresse ist eine Instanz-Einstellung des Servers
// (SCANVIEW_QUELLTEXT_URL, geliefert über /api/sitzung). Nur https-Adressen werden übernommen —
// eine javascript:-Adresse darf nie zum Link werden; ohne Adresse gibt es keinen Link.
export function quelltextLink(wert: unknown): string | null {
  if (typeof wert !== 'string') return null;
  const url = wert.trim();
  return /^https:\/\/[^\s/?#]+([/?#]\S*)?$/.test(url) ? url : null;
}

/** Links der Portal-Fußzeile: Lizenzen immer, Quelltext nur mit konfigurierter Adresse. */
export function fussLinks(quelltextUrl: string | null): { href: string; text: string }[] {
  const links = [{ href: '/THIRD_PARTY_LICENSES.txt', text: 'Lizenzen' }];
  if (quelltextUrl) links.push({ href: quelltextUrl, text: 'Quelltext (EUPL-1.2)' });
  return links;
}
