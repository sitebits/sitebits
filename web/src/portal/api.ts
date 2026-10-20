// Dünner Zugriff auf die Server-API (Tasks 3–6): POST immer mit CSRF-Header + JSON, Fehler als
// deutsche Servertexte. GET-Aufrufe (sitzung/projekte) brauchen keinen CSRF-Header (nicht
// ändernd), aber `credentials: 'same-origin'`, damit das Sitzungs-Cookie mitgeschickt wird.

import { quelltextLink } from './karten';
import type { PortalRolle } from './rechte';

export type Projekt = {
  id: string;
  titel: string;
  beschreibung: string;
  punkte: number | null;
  standpunkte: number | null;
  ausdehnung: [number, number, number] | null;
  crs: string;
  scandatum: string | null;
  groesseBytes: number;
  draufsicht: boolean;
  rolle: PortalRolle;
  /** Optimistische Sperre der Metadaten: beim PATCH mitschicken, 0 = nie geändert. */
  version: number;
  /** Schalter und Vorlage des Projekts; fehlt bei älteren Servern (dann gelten die Vorgaben). */
  einstellungen?: { vorlage: 'bestandsaufnahme' | 'bauprojekt' | null; rueckmeldungen: boolean; exportBearbeiter: boolean; befundFaeden?: boolean };
  /** Projekt-Schalter „Befund-Fäden“; fehlt bei älteren Servern (dann aus). */
  befund_faeden?: boolean;
  /** Überfällige Befunde, für die ich oder mein Gewerk zuständig bin (nur bei eingeschalteten Befund-Fäden). */
  ueberfaellig_meine?: number;
};

export class ApiFehler extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiFehler';
    this.status = status;
  }
}

function hatFehlertext(d: unknown): d is { fehler: string } {
  return typeof d === 'object' && d !== null && typeof (d as Record<string, unknown>).fehler === 'string';
}

async function leseJson(antwort: Response): Promise<unknown> {
  try {
    return await antwort.json();
  } catch {
    return null;
  }
}

export async function post<T>(pfad: string, body: unknown): Promise<T> {
  let antwort: Response;
  try {
    antwort = await fetch(pfad, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Scanview': '1' },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiFehler(0, 'Verbindung zum Server fehlgeschlagen.');
  }
  const daten = await leseJson(antwort);
  if (!antwort.ok) {
    throw new ApiFehler(antwort.status, hatFehlertext(daten) ? daten.fehler : `Fehler ${antwort.status}`);
  }
  return daten as T;
}

/** Angemeldeter Nutzer plus Instanz-Angaben aus /api/sitzung (quelltextUrl: null = kein Link). */
export type Sitzung = { id?: number; name: string; email: string; admin: boolean; quelltextUrl: string | null };

export async function sitzung(): Promise<Sitzung | null> {
  let antwort: Response;
  try {
    antwort = await fetch('/api/sitzung', { credentials: 'same-origin' });
  } catch {
    return null;
  }
  if (!antwort.ok) return null;
  const daten = await leseJson(antwort);
  if (daten === null || typeof daten !== 'object') return null;
  const roh = daten as { nutzer?: { id?: number; name: string; email: string; admin: boolean }; quelltext_url?: unknown };
  if (!roh.nutzer) return null;
  return { ...roh.nutzer, quelltextUrl: quelltextLink(roh.quelltext_url) };
}

export async function projekte(): Promise<Projekt[]> {
  const antwort = await fetch('/api/projekte', { credentials: 'same-origin' });
  const daten = await leseJson(antwort);
  if (!antwort.ok) {
    throw new ApiFehler(antwort.status, hatFehlertext(daten) ? daten.fehler : `Fehler ${antwort.status}`);
  }
  const liste = (daten as { projekte?: Projekt[] } | null)?.projekte;
  return liste ?? [];
}
