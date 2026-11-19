// Ansichtsprofile (P08-S6, ADR 0016 Nachtrag): legen fest, welche Oberfläche der Viewer zeigt.
//  - „ansehen“: 3D im Vollbild, schmale Leiste (Name, Grau, eine Strecke messen, Einstellungen), kein Editor, keine
//    Panels. Standard für Nur-Lese-Links; `?zeigen=1` (Messe-Links, QR-Codes) ist ein Alias dafür.
//  - „messen“: wie „ansehen“, dazu die Messwerkzeuge Strecke, Linienzug, Fläche und Höhe; mehrere Messungen bleiben
//    stehen, bis sie gelöscht werden. Keine POIs, Befunde, Panels oder Messliste.
//  - „arbeiten“: die heutige volle Oberfläche.
// Ein Gast bekommt sein Profil vom Server (am Link gespeichert, /api/sitzung); die Adresse kann es nur verkleinern
// (`?zeigen=1` → „ansehen“), nie vergrößern. Das Profil ist Oberfläche, keine Rechtegrenze: Welche Dateien ein Gast
// laden darf, entscheidet allein die Positivliste des Servers (teilen.py), für jedes Profil gleich.
import type { KeyAction } from './keymap';
import type { AktionId } from './tastenbelegung';
import { zeigenAn } from './zeigen';

export type Profil = 'ansehen' | 'messen' | 'arbeiten';
export const PROFILE: readonly Profil[] = ['ansehen', 'messen', 'arbeiten'];

/** Profil aus einer Server-Antwort; Unbekanntes oder Fehlendes wird „ansehen“ (das kleinste). */
export function profilAus(roh: unknown): Profil {
  return typeof roh === 'string' && (PROFILE as readonly string[]).includes(roh) ? (roh as Profil) : 'ansehen';
}

const rang = (p: Profil): number => PROFILE.indexOf(p);

/** Wirksames Profil: ohne Gast „arbeiten“ bzw. mit `?zeigen=1` „ansehen“; als Gast das Profil des Links, durch
 *  `?zeigen=1` höchstens verkleinert. Andere Adressparameter (etwa `profil=`) spielen keine Rolle. */
export function wirksamesProfil(search: string, gastProfil?: Profil): Profil {
  const wunsch: Profil = zeigenAn(search) ? 'ansehen' : 'arbeiten';
  if (gastProfil === undefined) return wunsch;
  return rang(wunsch) < rang(gastProfil) ? wunsch : gastProfil;
}

/** Schlanke Oberfläche (Show-Leiste statt Werkzeugleiste und Panels)? */
export const schlank = (p: Profil): boolean => p !== 'arbeiten';

/** Messwerkzeuge je Profil (Werkzeug der Leiste). „ansehen“: nur eine Strecke. */
export const MESSWERKZEUGE: Record<Profil, readonly ('distance' | 'polyline' | 'area' | 'height')[]> = {
  ansehen: ['distance'],
  messen: ['distance', 'polyline', 'area', 'height'],
  arbeiten: ['distance', 'polyline', 'area', 'height'],
};

// Positivliste der Kürzel in den schlanken Profilen: Navigieren, Messen im erlaubten Umfang, Hilfe. Alles andere
// (Panels, Ebenen 1–9, Schnittbox, Begehen, Befund, Teilen, Koordinaten, POI-Werkzeug, Standpunkte) bleibt still —
// sonst öffnete eine Taste, was die Oberfläche bewusst weglässt.
const SCHLANK_IMMER = new Set<KeyAction['type']>([
  'finish', 'cancel-drag', 'cancel-drawing', 'leave-panorama', 'undo', 'alles-zeigen', 'tastenhilfe', 'drehen-schritt', 'ansage',
  // Controller: Werkzeug vor/zurück — main.ts wechselt dann nur durch werkzeugReihe(profil).
  'werkzeug-wechsel',
]);
const NUR_MESSEN = new Set<KeyAction['type']>(['delete-selection', 'toggle-snap']);

/** Werkzeuge, durch die „Werkzeug vor/zurück“ (Controller) im Profil wechselt: Navigieren plus die Messwerkzeuge;
 *  in „arbeiten“ die ganze Werkzeugleiste. */
export function werkzeugReihe<T extends string>(p: Profil, alle: readonly T[]): T[] {
  if (p === 'arbeiten') return [...alle];
  return alle.filter((t) => t === 'navigate' || (MESSWERKZEUGE[p] as readonly string[]).includes(t));
}

/** Zeile der Tastenhilfe zeigen? In den schlanken Profilen nur, was dort auch wirkt (aktionErlaubt). */
export function hilfeZeigt(p: Profil, id: AktionId): boolean {
  if (p === 'arbeiten') return true;
  if (id === 'alles-zeigen' || id === 'tastenhilfe' || id === 'werkzeug.navigate') return true;
  if (id === 'kantenfang') return p === 'messen';
  const werkzeug = /^werkzeug\.(.+)$/.exec(id)?.[1];
  return werkzeug !== undefined && (MESSWERKZEUGE[p] as readonly string[]).includes(werkzeug);
}

/** Darf das Kürzel (oder der Controller-Knopf) im Profil wirken? */
export function aktionErlaubt(p: Profil, a: KeyAction): boolean {
  if (p === 'arbeiten') return true;
  if (a.type === 'tool') return a.tool === 'navigate' || (MESSWERKZEUGE[p] as readonly string[]).includes(a.tool);
  if (SCHLANK_IMMER.has(a.type)) return true;
  return p === 'messen' && NUR_MESSEN.has(a.type);
}
