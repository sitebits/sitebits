// Lokaler Entwurf gepickter Kontrollpunkte (J3b): übersteht ein Neuladen der Seite. localStorage, ein Schlüssel je
// Projekt und SHA-256 der geladenen Soll-Liste (derselbe Text wie beim Prüfer gehasht: nach dem Dekodieren, ohne BOM).
// Inhalt nur die Picks (ID → Ist, Ebene, grobe Stufe) mit Schema-Version, höchstens ENTWURF_MAX_ZEICHEN. Jeder Zugriff
// ist abgefangen: ohne Speicher (gesperrt, privates Fenster) gibt es keinen Entwurf, die Seite läuft trotzdem.
// Angeboten wird er nur beim Laden derselben Liste und nie still übernommen (kontrollpunktePanel.ts). Das Portal löscht
// alle Schlüssel mit KP_ENTWURF_PRAEFIX beim Abmelden (portal/rahmen.ts, entwuerfeLoeschen). Rein (Vitest).
import { KP_ENTWURF_PRAEFIX } from './entwurfKonstanten';
import type { IstWert } from './kontrollpunkte';

export const KP_ENTWURF_SCHEMA = 1;
/** Obergrenze des gespeicherten JSON (Zeichen); 10 000 Punkte mit langen Ebenennamen passen hinein. */
export const ENTWURF_MAX_ZEICHEN = 2_000_000;
const MAX_QUELLE = 200;

export const entwurfSchluessel = (projectId: string, sha256: string): string => `${KP_ENTWURF_PRAEFIX}${projectId}:${sha256}`;

/** SHA-256 (hex) des Listentexts; null ohne WebCrypto (dann gibt es keinen Entwurf). */
export async function listenSha(text: string): Promise<string | null> {
  try {
    const roh = text.startsWith('﻿') ? text.slice(1) : text;
    const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(roh));
    return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch {
    return null;
  }
}

export function entwurfJson(picks: ReadonlyMap<string, IstWert>): string {
  return JSON.stringify({
    schema: KP_ENTWURF_SCHEMA,
    picks: Object.fromEntries([...picks].map(([id, w]) => [id, { p: w.punkt, quelle: w.quelle, ...(w.grob ? { grob: true } : {}) }])),
  });
}

/** Gespeicherten Entwurf streng prüfen: Schema, nur IDs der Liste, endliche Koordinaten. Sonst null (verwerfen). */
export function pruefeKpEntwurf(roh: string, ids: ReadonlySet<string>): Map<string, IstWert> | null {
  if (roh.length > ENTWURF_MAX_ZEICHEN) return null;
  let d: unknown;
  try {
    d = JSON.parse(roh);
  } catch {
    return null;
  }
  if (!d || typeof d !== 'object' || Array.isArray(d)) return null;
  const { schema, picks } = d as Record<string, unknown>;
  if (schema !== KP_ENTWURF_SCHEMA || !picks || typeof picks !== 'object' || Array.isArray(picks)) return null;
  const out = new Map<string, IstWert>();
  for (const [id, w] of Object.entries(picks as Record<string, unknown>)) {
    if (!ids.has(id) || !w || typeof w !== 'object' || Array.isArray(w)) return null;
    const { p, quelle, grob, ...rest } = w as Record<string, unknown>;
    if (Object.keys(rest).length) return null;
    if (!Array.isArray(p) || p.length !== 3 || !p.every((x) => typeof x === 'number' && Number.isFinite(x))) return null;
    if (typeof quelle !== 'string' || quelle.length > MAX_QUELLE) return null;
    if (grob !== undefined && grob !== true) return null;
    out.set(id, { punkt: [p[0], p[1], p[2]] as [number, number, number], quelle, ...(grob ? { grob: true } : {}) });
  }
  return out;
}

export interface KpEntwurfSpeicher {
  /** null = kein (brauchbarer) Entwurf; ein unbrauchbarer wird dabei gelöscht. */
  lies(schluessel: string, ids: ReadonlySet<string>): Map<string, IstWert> | null;
  /** false = nicht gespeichert (Speicher gesperrt, voll oder Entwurf zu groß). Leere Picks löschen den Entwurf. */
  schreibe(schluessel: string, picks: ReadonlyMap<string, IstWert>): boolean;
  loesche(schluessel: string): void;
}

export function kpEntwurfSpeicher(speicher: () => Storage | null | undefined): KpEntwurfSpeicher {
  const s = (): Storage | null => {
    try {
      return speicher() ?? null;
    } catch {
      return null;
    }
  };
  const loesche = (k: string): void => {
    try {
      s()?.removeItem(k);
    } catch {
      /* gesperrt */
    }
  };
  return {
    lies(k, ids) {
      let roh: string | null = null;
      try {
        roh = s()?.getItem(k) ?? null;
      } catch {
        return null;
      }
      if (roh === null) return null;
      const e = pruefeKpEntwurf(roh, ids);
      if (!e) loesche(k);
      return e && e.size ? e : null;
    },
    schreibe(k, picks) {
      if (!picks.size) {
        loesche(k);
        return true;
      }
      const json = entwurfJson(picks);
      if (json.length > ENTWURF_MAX_ZEICHEN) return false;
      try {
        const sp = s();
        if (!sp) return false;
        sp.setItem(k, json);
        return true;
      } catch {
        return false;
      }
    },
    loesche,
  };
}
