// POIs eines Projekts als CSV (RFC 4180, Komma, UTF-8) auf stdout.
//
//   SITEBITS_URL=https://… SITEBITS_TOKEN=sv_… node beispiele/pois-csv.ts <projekt> > pois.csv
//
// Spalten: id, titel, kategorie, notiz, x, y, z, status, frist. Status und Frist nur, wenn das
// Projekt Befund-Fäden nutzt. Koordinaten in Weltkoordinaten [m] wie in der API.
import type { Poi } from '../src/index.ts';
import { SitebitsFehler } from '../src/index.ts';
import { verbinden } from './verbindung.ts';

const projekt = process.argv[2];
if (!projekt) {
  console.error('Aufruf: node beispiele/pois-csv.ts <projekt>');
  process.exit(2);
}

/** Feld nach RFC 4180; führende Formelzeichen entschärft (Tabellenprogramme). */
function feld(wert: string | number | null | undefined): string {
  let s = wert === null || wert === undefined ? '' : String(wert);
  if (/^[=+\-@\t\r]/.test(s) && typeof wert === 'string') s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

const zeile = (p: Poi): string => [
  p.id, p.daten.title, p.daten.category, p.daten.note, ...p.daten.position,
  p.stand?.status, p.stand?.frist,
].map(feld).join(',');

try {
  const { pois } = await verbinden().pois(projekt);
  process.stdout.write(['id,titel,kategorie,notiz,x,y,z,status,frist', ...pois.map(zeile)].join('\r\n') + '\r\n');
} catch (e) {
  console.error(e instanceof SitebitsFehler ? e.message : e);
  process.exitCode = 2;
}
