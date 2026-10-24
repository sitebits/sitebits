// Integrationstest gegen eine echte lokale Instanz (test/instanz.py: echter Webdienst und Worker
// mit Test-Läufer, synthetische Projekte, ohne Docker). Führt auch die drei Beispielskripte aus.
// Braucht `uv` und die Server-Umgebung (server/uv.lock); fehlt `uv`, wird übersprungen.
import assert from 'node:assert/strict';
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { after, before, describe, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { Sitebits, SitebitsFehler } from '../src/index.ts';

const HIER = fileURLToPath(new URL('..', import.meta.url));
const SERVER = fileURLToPath(new URL('../../../server', import.meta.url));

function uvDa(): boolean {
  try { execFileSync('uv', ['--version'], { stdio: 'ignore' }); return true; } catch { return false; }
}

interface Instanz { basis: string; token: string; projekte: Record<string, string> }

async function starten(arbeit: string): Promise<{ prozess: ChildProcess; daten: Instanz }> {
  const prozess = spawn('uv', ['run', '--locked', '--project', SERVER, 'python', 'test/instanz.py', arbeit],
    { cwd: HIER, stdio: ['ignore', 'pipe', 'inherit'], env: { PATH: process.env.PATH, HOME: process.env.HOME } });
  const zeilen = createInterface({ input: prozess.stdout! });
  const zeit = setTimeout(() => prozess.kill('SIGTERM'), 120_000);
  try {
    for await (const zeile of zeilen) return { prozess, daten: JSON.parse(zeile) as Instanz };
  } finally {
    clearTimeout(zeit);
  }
  throw new Error('Instanz ohne Startzeile beendet');
}

function beispiel(i: Instanz, skript: string, ...args: string[]): { status: number; aus: string } {
  try {
    const aus = execFileSync(process.execPath, [`beispiele/${skript}`, ...args], {
      cwd: HIER, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
      env: { PATH: process.env.PATH, SITEBITS_URL: i.basis, SITEBITS_TOKEN: i.token },
    });
    return { status: 0, aus };
  } catch (e) {
    const f = e as { status: number; stdout: string };
    return { status: f.status, aus: f.stdout };
  }
}

describe('gegen eine echte lokale Instanz', { skip: uvDa() ? false : 'uv fehlt: keine Server-Umgebung für die Wegwerf-Instanz' }, () => {
  const wurzel = join(homedir(), '.cache/scanview-nachweise/tmp');
  let arbeit = '';
  let prozess: ChildProcess | undefined;
  let i: Instanz;
  let sb: Sitebits;

  before(async () => {
    mkdirSync(wurzel, { recursive: true, mode: 0o700 });
    arbeit = mkdtempSync(join(wurzel, 'sitebits-client-'));
    const s = await starten(arbeit);
    prozess = s.prozess;
    i = s.daten;
    sb = new Sitebits({ basis: i.basis, token: i.token });
  });

  after(() => {
    prozess?.kill('SIGTERM');
    if (arbeit) rmSync(arbeit, { recursive: true, force: true });
  });

  test('Sitzung, Version, Projekte', async () => {
    assert.equal((await sb.sitzung()).nutzer.name, 'Skript');
    assert.match((await sb.version()).version, /^\d+\.\d+\.\d+/);
    const { projekte } = await sb.projekte();
    assert.deepEqual(Object.fromEntries(projekte.map(p => [p.id, p.rolle])), i.projekte);
    assert.equal((await sb.projekt('pruef-eins')).kategorien.length > 0, true);
  });

  test('POI anlegen, ändern, lesen, als CSV exportieren, löschen', async () => {
    const p = await sb.poiAnlegen('pruef-eins', { daten: { title: 'Riss, Attika', category: 'Mangel', position: [1, 2, 3] } });
    assert.equal(p.daten.title, 'Riss, Attika');
    const g = await sb.poiAendern('pruef-eins', p.id, { daten: { ...p.daten, note: 'Notiz' }, version: p.version });
    assert.equal(g.version, p.version + 1);
    assert.deepEqual((await sb.pois('pruef-eins')).pois.map(x => x.id), [p.id]);
    assert.match(await sb.exportCsv('pruef-eins'), /Riss, Attika|Riss; Attika|"Riss, Attika"/);
    assert.equal(await sb.poiLoeschen('pruef-eins', p.id, g.version), null);
    assert.deepEqual((await sb.pois('pruef-eins')).pois, []);
  });

  test('Fehler: fremdes Projekt 404, nur lesend 403, Konflikt 409 mit Stand, falsches Token 401', async () => {
    await assert.rejects(sb.projekt('gibtesnicht'), (e: unknown) => e instanceof SitebitsFehler && e.status === 404);
    await assert.rejects(sb.poiAnlegen('pruef-zwei', { daten: { title: 'x', position: [0, 0, 0] } }),
      (e: unknown) => e instanceof SitebitsFehler && e.status === 403 && typeof e.fehler === 'string');
    const k = await sb.projekt('pruef-eins');
    await sb.projektAendern('pruef-eins', { beschreibung: 'eins', version: k.version });
    await assert.rejects(sb.projektAendern('pruef-eins', { beschreibung: 'zwei', version: k.version }),
      (e: unknown) => e instanceof SitebitsFehler && e.status === 409
        && (e.aktuell as { beschreibung?: string }).beschreibung === 'eins');
    const falsch = new Sitebits({ basis: i.basis, token: 'sv_falsch' });
    await assert.rejects(falsch.sitzung(), (e: unknown) => e instanceof SitebitsFehler && e.status === 401);
  });

  test('Auswertung starten, warten, Ergebnis und Bericht lesen', async () => {
    const a = await sb.auswertungStarten('pruef-eins', { typ: 'auswerten_pruefen' });
    assert.equal(a.status, 'wartend');
    const fertig = await sb.aufAuswertungWarten('pruef-eins', a.id, { intervallMs: 250, zeitMs: 60_000 });
    assert.equal(fertig.status, 'fertig');
    const e = await sb.ergebnis('pruef-eins', a.id);
    assert.equal(e.gesamt.urteil, 'nicht erfüllt');
    assert.match(await sb.bericht('pruef-eins', a.id), /<!doctype html>/);
    assert.ok((await sb.auswertungen('pruef-eins')).auswertungen.some(x => x.id === a.id));
  });

  test('Beispiel: rote Zeilen aller Projekte', () => {
    const r = beispiel(i, 'rote-zeilen.ts');
    assert.equal(r.status, 1);
    assert.equal(r.aus, 'pruef-eins\tL09 Lagegenauigkeit\tAbweichung zu groß\n');
  });

  test('Beispiel: POIs als CSV', async () => {
    await sb.poiAnlegen('pruef-eins', { id: 'P-csv', daten: { title: '=Formel, "zitiert"', category: 'Mangel', position: [1.5, -2, 3] } });
    const r = beispiel(i, 'pois-csv.ts', 'pruef-eins');
    assert.equal(r.status, 0);
    assert.equal(r.aus, 'id,titel,kategorie,notiz,x,y,z,status,frist\r\n'
      + 'P-csv,"\'=Formel, ""zitiert""",Mangel,,1.5,-2,3,,\r\n');
  });

  test('Beispiel: Auswertung starten und warten', () => {
    const r = beispiel(i, 'auswertung-warten.ts', 'pruef-eins');
    assert.equal(r.status, 0);
    assert.match(r.aus, /^[0-9a-f]{16}: nicht erfüllt\n {2}erfüllt: 1\n {2}nicht erfüllt: 1\n {2}nicht prüfbar: 0\nBericht: \/api\/v1\/projekte\/pruef-eins\/auswertungen\/[0-9a-f]{16}\/bericht\n$/);
  });

  test('Beispiele ohne Zugangsdaten brechen mit Exit 2 ab', () => {
    const r = beispiel({ basis: '', token: '', projekte: {} }, 'rote-zeilen.ts');
    assert.equal(r.status, 2);
  });
});
