// Genauigkeitsnachweis einer Ebene (F15): `provenance.accuracy`, geschrieben von `scanview-import genauigkeit`
// (import/genauigkeit.py `bewerte`). Hier nur Lesen und Texte für Ebenen-Panel und Messhinweise.
// Grundsätze (F15): Lage und Maßstab getrennt; nur Kontrollpunkte/-strecken zählen als Prüfung;
// Ausgleichsresiduen und Kamera-GNSS ergeben nie einen ±-Wert; fehlender Nachweis wird genannt.

export type AbsolutStatus = 'checked' | 'adjustedOnly' | 'none';
export type MassstabStatus = 'checked' | 'deviates' | 'set' | 'none';

export interface Statistik {
  n: number;
  rmse: { x: number; y: number; z: number };
  max: { xy: number; z: number };
}

export interface Strecke {
  name: string;
  role: 'control' | 'check';
  errorPct: number | null;
}

export interface Genauigkeit {
  absolute: {
    status: AbsolutStatus;
    /** Nur bei checked: Kontrollpunkte. */
    statistik?: Statistik;
    /** Passpunkt-Residuen (im Ausgleich verwendet), nur zur Information. */
    passpunkte?: number;
    kameragruppen?: number;
  };
  scale: { status: MassstabStatus; n?: number; maxErrorPct?: number; setFrom?: string[] };
  distances: Strecke[];
  source: { software?: string; version?: string; created?: string; projectSha256?: string; crs?: string };
  report: { filename: string; sha256: string } | null;
}

const ABSOLUT: readonly string[] = ['checked', 'adjustedOnly', 'none'];
const MASSSTAB: readonly string[] = ['checked', 'deviates', 'set', 'none'];

const obj = (x: unknown): Record<string, unknown> | null =>
  x && typeof x === 'object' && !Array.isArray(x) ? (x as Record<string, unknown>) : null;
const zahl = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const text = (x: unknown): string | undefined => (typeof x === 'string' ? x : undefined);

function statistik(x: unknown): Statistik | undefined {
  const s = obj(x);
  const r = obj(s?.rmse);
  const m = obj(s?.max);
  if (!s || !r || !m || !zahl(s.n) || !zahl(r.x) || !zahl(r.y) || !zahl(r.z) || !zahl(m.xy) || !zahl(m.z)) return undefined;
  return { n: s.n, rmse: { x: r.x, y: r.y, z: r.z }, max: { xy: m.xy, z: m.z } };
}

/** `provenance.accuracy` lesen; fehlt es → undefined, ist es kaputt → Fehlertext (manifest.ts wirft). */
export function leseGenauigkeit(raw: unknown): Genauigkeit | undefined | string {
  if (raw === undefined) return undefined;
  const a = obj(raw);
  const abs = obj(a?.absolute);
  const sc = obj(a?.scale);
  if (!a || !abs || !sc) return 'accuracy braucht absolute und scale';
  if (typeof abs.status !== 'string' || !ABSOLUT.includes(abs.status)) return `accuracy.absolute.status "${String(abs.status)}" unbekannt`;
  if (typeof sc.status !== 'string' || !MASSSTAB.includes(sc.status)) return `accuracy.scale.status "${String(sc.status)}" unbekannt`;
  const absolut: Genauigkeit['absolute'] = { status: abs.status as AbsolutStatus };
  if (absolut.status === 'checked') {
    const st = statistik(abs);
    if (!st) return 'accuracy.absolute: Kennzahlen der Kontrollpunkte fehlen';
    absolut.statistik = st;
  }
  const pass = statistik(abs.controlResiduals);
  if (pass) absolut.passpunkte = pass.n;
  if (Array.isArray(abs.cameraGroups) && abs.cameraGroups.length) absolut.kameragruppen = abs.cameraGroups.length;
  const scale: Genauigkeit['scale'] = { status: sc.status as MassstabStatus };
  if (zahl(sc.n)) scale.n = sc.n;
  if (zahl(sc.maxErrorPct)) scale.maxErrorPct = sc.maxErrorPct;
  if (Array.isArray(sc.setFrom)) scale.setFrom = sc.setFrom.filter((s): s is string => typeof s === 'string');
  if ((scale.status === 'checked' || scale.status === 'deviates') && scale.maxErrorPct === undefined)
    return 'accuracy.scale: maxErrorPct fehlt';
  const distances: Strecke[] = [];
  for (const d of Array.isArray(a.distances) ? a.distances : []) {
    const e = obj(d);
    if (!e || typeof e.name !== 'string' || (e.role !== 'control' && e.role !== 'check')) continue;
    distances.push({ name: e.name, role: e.role, errorPct: zahl(e.errorPct) ? e.errorPct : null });
  }
  const q = obj(a.source) ?? {};
  const rep = obj(a.report);
  return {
    absolute: absolut,
    scale,
    distances,
    source: { software: text(q.software), version: text(q.version), created: text(q.created),
      projectSha256: text(q.projectSha256), crs: text(q.crs) },
    report: rep && typeof rep.filename === 'string' && typeof rep.sha256 === 'string'
      ? { filename: rep.filename, sha256: rep.sha256 } : null,
  };
}

const cm = (m: number): string => `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(m * 100)} cm`;
const prozent = (p: number, vorzeichen: boolean): string =>
  `${vorzeichen && p > 0 ? '+' : ''}${new Intl.NumberFormat('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(p)} %`;
const anzahl = (n: number, eins: string, viele: string): string => `${n} ${n === 1 ? eins : viele}`;

/** Lage im Koordinatensystem (absolut). */
export function lageText(g: Genauigkeit): string {
  const a = g.absolute;
  if (a.status === 'checked' && a.statistik) {
    const s = a.statistik;
    return `Lage geprüft an ${anzahl(s.n, 'Kontrollpunkt', 'Kontrollpunkten')}: RMSE Lage ${cm(Math.hypot(s.rmse.x, s.rmse.y))}, `
      + `Höhe ${cm(s.rmse.z)} (max. ${cm(s.max.xy)} / ${cm(s.max.z)})`;
  }
  if (a.status === 'adjustedOnly') {
    return a.passpunkte !== undefined
      ? `Lage nicht unabhängig geprüft (nur Ausgleichsresiduen an ${anzahl(a.passpunkte, 'Passpunkt', 'Passpunkten')})`
      : 'Lage nicht unabhängig geprüft (nur Drohnen-GNSS)';
  }
  return 'Lage: kein Nachweis';
}

/** Maßstab (relativ, Strecken). */
export function massstabText(g: Genauigkeit): string {
  const s = g.scale;
  const geprueft = g.distances.filter((d) => d.role === 'check' && d.errorPct !== null);
  const n = s.n ?? geprueft.length;
  const wert = n === 1 && geprueft.length === 1 ? prozent(geprueft[0]!.errorPct!, true) : `max. ${prozent(s.maxErrorPct ?? 0, false)}`;
  if (s.status === 'checked') return `Maßstab geprüft: ${wert} an ${anzahl(n, 'Kontrollstrecke', 'Kontrollstrecken')}`;
  if (s.status === 'deviates') return `Maßstab weicht ab: ${wert} an ${anzahl(n, 'Kontrollstrecke', 'Kontrollstrecken')}`;
  if (s.status === 'set') {
    const namen = s.setFrom ?? [];
    return `Maßstab aus ${anzahl(namen.length || 1, 'Strecke', 'Strecken')} gesetzt${namen.length ? ` (${namen.join(', ')})` : ''}, nicht geprüft`;
  }
  return 'Maßstab: kein Nachweis';
}

/** Herkunft der Zahlen: Software, Datum, Projekt-Hash (gekürzt), Bericht. */
export function herkunftText(g: Genauigkeit): string[] {
  const q = g.source;
  const teile = [[q.software, q.version].filter(Boolean).join(' '), q.created?.slice(0, 10),
    q.projectSha256 ? `Projekt ${q.projectSha256.slice(0, 8)}…` : undefined].filter(Boolean);
  const out = teile.length ? [`Quelle: ${teile.join(', ')}`] : [];
  if (g.report) out.push(`Bericht: ${g.report.filename} (SHA-256 ${g.report.sha256.slice(0, 8)}…)`);
  return out;
}

/** Zeilen für den Abschnitt „Genauigkeit“ im Ebenen-Panel. */
export function panelZeilen(g: Genauigkeit | undefined): string[] {
  if (!g) return ['kein Genauigkeitsnachweis'];
  return [lageText(g), massstabText(g), ...herkunftText(g)];
}

/** Hinweise an Messwerten aus einer Ebene mit Nachweis (ersetzen „Maßstab nicht belegt“). */
export function messhinweise(g: Genauigkeit): string[] {
  const out = [massstabText(g)];
  if (g.absolute.status !== 'checked') out.push(lageText(g));
  return out;
}
