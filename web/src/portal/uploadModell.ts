// Logik der Seite „Uploads“ ohne DOM (Vitest): Stückplanung, Kopfzeilen des Stückprotokolls,
// Zuordnung zum Fortsetzen, Zustandstexte, Prüfung der Formularwerte. Das Senden steht in uploadSenden.ts.

export type UploadDatei = { nr: number; pfad: string; groesse: number; stand: number; sha256: string | null; art: string | null };
export type Upload = {
  id: string; status: 'offen' | 'abgeschlossen'; groesse: number; stand: number;
  erstelltAm: number; geaendertAm: number; ablaufAm: number; stueckBytes: number; dateien: UploadDatei[];
};
export type ImportStatus = 'wartend' | 'laufend' | 'fertig' | 'fehlgeschlagen' | 'abgebrochen';
export type Import = {
  id: string; typ: string; projekt: string; titel: string | null; upload: string | null; datei: string | null;
  crs: string | null; status: ImportStatus; stufe: string | null; warteplatz: number | null; fehler: string | null;
  erstelltAm: number; begonnenAm: number | null; beendetAm: number | null;
};
export type Quelle = 'mesh' | 'splats';

/** Wie auftraege.PROJEKT_ID_RE im Server. */
export const PROJEKT_ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
export const CRS_RE = /^EPSG:[0-9]{4,6}$/;
export const TITEL_MAX = 120;
export const ENDUNGEN: Record<Quelle, readonly string[]> = { mesh: ['.glb', '.ply'], splats: ['.ply', '.spz', '.splat'] };
export const TYP: Record<Quelle, string> = { mesh: 'neu_mesh', splats: 'neu_splats' };
export const HOEHENSYSTEME: readonly { wert: string; text: string }[] = [
  { wert: 'dhhn2016', text: 'DHHN2016 (Normalhöhen, NHN)' },
  { wert: 'ellipsoidal-etrs89', text: 'Ellipsoidische Höhen (ETRS89)' },
  { wert: 'dhhn92', text: 'DHHN92 (Normalhöhen, älter)' },
];
const ARCHIV = /\.(zip|7z|rar|tar|gz|tgz|bz2|xz|zst)$/i;
/** Bewusst nicht offene Uploads und Importe gelten als offen, solange der Server sie nicht beendet meldet. */
export const POLL_MS = 5000;

/** Stücke [a, b] (einschließlich) in fester Größe, das letzte trägt den Rest; ab `ab` (Stückgrenze). */
export function stueckPlan(groesse: number, stueck: number, ab = 0): { a: number; b: number }[] {
  if (!(stueck > 0) || groesse <= 0 || ab >= groesse) return [];
  const plan: { a: number; b: number }[] = [];
  for (let a = ab - (ab % stueck); a < groesse; a += stueck) plan.push({ a, b: Math.min(a + stueck, groesse) - 1 });
  return plan;
}

export const contentRange = (a: number, b: number, gesamt: number): string => `bytes ${a}-${b}/${gesamt}`;

export function base64(bytes: ArrayBuffer): string {
  const u = new Uint8Array(bytes);
  let s = '';
  for (const x of u) s += String.fromCharCode(x);
  return btoa(s);
}

/** `Content-Digest: sha-256=:<base64>:` (RFC 9530) aus dem SHA-256 eines Stücks. */
export const digestKopf = (sha256: ArrayBuffer): string => `sha-256=:${base64(sha256)}:`;

export async function stueckDigest(daten: ArrayBuffer, subtle: SubtleCrypto = crypto.subtle): Promise<string> {
  return digestKopf(await subtle.digest('SHA-256', daten));
}

/** Schlüssel, unter dem eine gewählte Datei ihrem Upload zugeordnet wird (dieselbe Datei erneut gewählt). */
export const fortsetzenSchluessel = (f: { name: string; size: number; lastModified: number }): string =>
  `sv-upload:${f.name}:${f.size}:${f.lastModified}`;

export type Merker = { uploadId: string; projekt: string; titel: string; quelle: Quelle; crs: string; quellHoehe: string; zielHoehe: string };

type Speicher = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const speicher = (): Speicher | null => {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
};

export function merken(schluessel: string, m: Merker, s: Speicher | null = speicher()): void {
  try { s?.setItem(schluessel, JSON.stringify(m)); } catch { /* privater Modus: Fortsetzen nur ohne Neuladen */ }
}

export function vergessen(schluessel: string, s: Speicher | null = speicher()): void {
  try { s?.removeItem(schluessel); } catch { /* s. o. */ }
}

export function gemerkt(schluessel: string, s: Speicher | null = speicher()): Merker | null {
  let roh: string | null = null;
  try { roh = s?.getItem(schluessel) ?? null; } catch { return null; }
  if (!roh) return null;
  try {
    const m = JSON.parse(roh) as Partial<Merker>;
    if (typeof m.uploadId !== 'string' || !/^[0-9a-f]{32}$/.test(m.uploadId) || typeof m.projekt !== 'string') return null;
    return {
      uploadId: m.uploadId, projekt: m.projekt, titel: typeof m.titel === 'string' ? m.titel : '',
      quelle: m.quelle === 'splats' ? 'splats' : 'mesh', crs: typeof m.crs === 'string' ? m.crs : '',
      quellHoehe: typeof m.quellHoehe === 'string' ? m.quellHoehe : '', zielHoehe: typeof m.zielHoehe === 'string' ? m.zielHoehe : '',
    };
  } catch {
    return null;
  }
}

export type Eingabe = { projekt: string; titel: string; quelle: Quelle; datei: { name: string; size: number } | null; crs: string; quellHoehe: string; zielHoehe: string };
export type Fehler = Partial<Record<'projekt' | 'titel' | 'datei' | 'crs' | 'hoehe', string>>;

export function pruefeEingabe(e: Eingabe): Fehler {
  const f: Fehler = {};
  if (!PROJEKT_ID_RE.test(e.projekt)) f.projekt = 'Projekt-ID: Kleinbuchstaben, Ziffern und Bindestrich, höchstens 64 Zeichen, nicht mit Bindestrich beginnen.';
  if (e.titel.trim().length > TITEL_MAX) f.titel = `Titel: höchstens ${TITEL_MAX} Zeichen.`;
  if (!e.datei) f.datei = 'Bitte eine Datei wählen.';
  else if (ARCHIV.test(e.datei.name)) f.datei = 'Archive werden nicht angenommen – Ordner entpacken und als Ordner hochladen.';
  else if (!ENDUNGEN[e.quelle].some((x) => e.datei!.name.toLowerCase().endsWith(x)))
    f.datei = `Für ${e.quelle === 'mesh' ? 'ein Mesh' : 'Splats'} sind diese Dateitypen möglich: ${ENDUNGEN[e.quelle].join(', ')}.`;
  else if (e.datei.size <= 0) f.datei = 'Die Datei ist leer.';
  const crs = e.crs.trim();
  if (crs && !CRS_RE.test(crs)) f.crs = 'Koordinatensystem als EPSG:<Nummer>, z. B. EPSG:25833 – oder leer lassen.';
  if ((e.quellHoehe || e.zielHoehe) && !crs) f.hoehe = 'Höhensysteme nur zusammen mit einem Koordinatensystem.';
  else if (e.zielHoehe && !e.quellHoehe) f.hoehe = 'Zuerst das Höhensystem der Datei wählen.';
  return f;
}

/** Optionen für POST /api/v1/importe — nur gesetzte Werte. */
export function importOptionen(e: Eingabe): Record<string, string> {
  const o: Record<string, string> = { projekt: e.projekt };
  if (e.titel.trim()) o.titel = e.titel.trim();
  if (e.crs.trim()) {
    o.crs = e.crs.trim();
    if (e.quellHoehe) o.quellHoehe = e.quellHoehe;
    if (e.zielHoehe) o.zielHoehe = e.zielHoehe;
  }
  return o;
}

const STUFEN: Record<string, string> = { konvertiert: 'wird umgewandelt', prueft: 'wird geprüft', freischalten: 'wird freigeschaltet' };

export function importText(i: Pick<Import, 'status' | 'stufe' | 'warteplatz'>): string {
  switch (i.status) {
    case 'wartend': return i.warteplatz ? `wartet (Platz ${i.warteplatz})` : 'wartet';
    case 'laufend': return (i.stufe ? STUFEN[i.stufe] : undefined) ?? 'läuft';
    case 'fertig': return 'freigeschaltet';
    case 'fehlgeschlagen': return 'fehlgeschlagen';
    case 'abgebrochen': return 'abgebrochen';
    default: return 'unbekannt';
  }
}

export const importOffen = (i: Pick<Import, 'status'>): boolean => i.status === 'wartend' || i.status === 'laufend';

export function groesseText(bytes: number): string {
  const e = ['Byte', 'KB', 'MB', 'GB', 'TB'];
  let w = bytes;
  let i = 0;
  while (w >= 1000 && i < e.length - 1) { w /= 1000; i += 1; }
  return `${i === 0 ? w : w.toLocaleString('de-DE', { maximumFractionDigits: 1 })} ${e[i]}`;
}

export function uploadText(u: Pick<Upload, 'status' | 'stand' | 'groesse'>): string {
  if (u.status === 'abgeschlossen') return 'vollständig, geprüft';
  const p = u.groesse > 0 ? Math.floor((u.stand / u.groesse) * 100) : 0;
  return `unvollständig (${p} %)`;
}

export function zeitText(sek: number): string {
  return new Date(sek * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' });
}

/** Stand aus einer 409-Antwort (`Upload-Offset`) oder null. */
export function offsetAus(kopf: string | null): number | null {
  if (kopf === null || !/^[0-9]{1,15}$/.test(kopf)) return null;
  return Number(kopf);
}
