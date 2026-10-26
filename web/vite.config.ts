import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, sep } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { defineConfig, type Plugin } from 'vite';
import { webAppDateien } from './scripts/webApp';

// laz-perf (LAZ-Decoder von Giro3D) lädt seine .wasm-Datei standardmäßig von cdn.jsdelivr.net.
// scanview ist selbst gehostet und soll ohne Drittanbieter-Aufruf laufen: Dieses Plugin liefert
// node_modules/laz-perf/lib/laz-perf.wasm im Devserver unter /wasm/laz-perf.wasm aus und legt sie
// beim Build unter demselben Pfad in dist/ ab. main.ts setzt dazu setLazPerfPath('<base>wasm').
// Kein eingecheckter Binärblob — die Datei kommt immer aus dem installierten npm-Paket.
const require = createRequire(import.meta.url);
const LAZ_PERF_WASM = require.resolve('laz-perf/lib/laz-perf.wasm');

function lazPerfWasm(): Plugin {
  return {
    name: 'scanview-laz-perf-wasm',
    configureServer(server) {
      server.middlewares.use('/wasm/laz-perf.wasm', (_req, res) => {
        res.setHeader('Content-Type', 'application/wasm');
        res.end(readFileSync(LAZ_PERF_WASM));
      });
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'wasm/laz-perf.wasm', source: readFileSync(LAZ_PERF_WASM) });
    },
  };
}

// Basis Universal als eigener Bau ohne Function()/eval (vendor/basis, README dort), sonst scheitert
// er an der CSP ohne 'unsafe-eval'; ausschließlich vom eigenen Ursprung.
// MediaPipe Tasks Vision (Fenster-Effekt F32): Wasm aus dem npm-Paket (wie laz-perf, kein Blob im Repo) und das
// Gesichtsmodell aus vendor/mediapipe (README dort: Herkunft, Prüfsumme) unter /wasm/mediapipe/. Nur SIMD- und
// No-SIMD-Variante — FilesetResolver.forVisionTasks wählt eine davon. Geladen wird erst beim Einschalten.
const MEDIAPIPE_WASM = dirname(require.resolve('@mediapipe/tasks-vision')) + '/wasm';
function mediapipe(): Plugin {
  const files = [
    ...['vision_wasm_internal.js', 'vision_wasm_internal.wasm', 'vision_wasm_nosimd_internal.js', 'vision_wasm_nosimd_internal.wasm']
      .map((name) => ({ name, source: join(MEDIAPIPE_WASM, name) })),
    { name: 'face_landmarker.task', source: fileURLToPath(new URL('./vendor/mediapipe/face_landmarker.task', import.meta.url)) },
  ];
  const typ = (n: string): string => (n.endsWith('.wasm') ? 'application/wasm' : n.endsWith('.js') ? 'text/javascript' : 'application/octet-stream');
  return {
    name: 'scanview-mediapipe',
    // Das Paket sammelt Nutzungsstatistik (Betriebssystem, Version, Laufzeiten) und schickt sie alle 60 s an
    // Google. Die CSP würde das blocken; abgeschaltet wird es trotzdem schon im Bau, damit auch Betreiber mit
    // gelockerter CSP nichts senden: flush() verwirft die Einträge, die Ziel-URL fehlt im Bundle. Passen die
    // Stellen nach einem Update nicht mehr genau einmal, bricht der Bau ab (Egress-Tor prüft das Ergebnis).
    transform(code, id) {
      if (!/@mediapipe[\\/]tasks-vision[\\/]vision_bundle\.mjs$/.test(id)) return null;
      const stellen: [string, string][] = [
        ['flush(t,e){if(this.error)', 'flush(t,e){if(this.h=[],!0)t?.();else if(this.error)'],
        ['"https://odml.pa.googleapis.com/v1/log"', '""'],
      ];
      for (const [alt, neu] of stellen) {
        if (code.split(alt).length !== 2) throw new Error(`MediaPipe-Nutzungsstatistik: Stelle ${alt} nicht genau einmal gefunden`);
        code = code.replace(alt, neu);
      }
      return { code, map: null };
    },
    configureServer(server) {
      for (const f of files) server.middlewares.use(`/wasm/mediapipe/${f.name}`, (_req, res) => {
        res.setHeader('Content-Type', typ(f.name));
        res.end(readFileSync(f.source));
      });
    },
    generateBundle() {
      for (const f of files) this.emitFile({ type: 'asset', fileName: `wasm/mediapipe/${f.name}`, source: readFileSync(f.source) });
    },
  };
}

function basisTranscoder(): Plugin {
  const files = ['basis_transcoder.js', 'basis_transcoder.wasm'].map((name) => ({
    name, source: fileURLToPath(new URL(`./vendor/basis/${name}`, import.meta.url)),
    mime: name.endsWith('.wasm') ? 'application/wasm' : 'text/javascript',
  }));
  return {
    name: 'scanview-basis-transcoder',
    configureServer(server) {
      for (const f of files) server.middlewares.use(`/wasm/basis/${f.name}`, (_req, res) => {
        res.setHeader('Content-Type', f.mime);
        res.end(readFileSync(f.source));
      });
    },
    generateBundle() {
      for (const f of files) this.emitFile({ type: 'asset', fileName: `wasm/basis/${f.name}`, source: readFileSync(f.source) });
    },
  };
}

// Web-App (scripts/webApp.ts): Manifest und Symbole beim Build erzeugen und unter festen Pfaden ablegen
// (/manifest.webmanifest, /icons/…) — public/ wird nicht kopiert (s. u.), der Devserver liefert sie direkt.
function webApp(): Plugin {
  const dateien = webAppDateien();
  return {
    name: 'scanview-web-app',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const d = dateien[(req.url ?? '').split('?')[0] ?? ''];
        if (!d) return next();
        res.setHeader('Content-Type', d.typ);
        res.end(d.inhalt);
      });
    },
    generateBundle() {
      for (const [pfad, d] of Object.entries(dateien)) this.emitFile({ type: 'asset', fileName: pfad.slice(1), source: d.inhalt });
    },
  };
}

// Weitergabe von dist/ ist Redistribution: MIT/BSD/Apache-2.0 verlangen, dass Lizenztexte (und bei
// Apache-2.0 eine NOTICE-Datei) mitgeliefert werden. Dieses Plugin sammelt beim Build aus genau den
// Modulen, die im Bundle landen (plus laz-perf für die .wasm-Datei), Name, Version, Lizenz und die
// Lizenz-/NOTICE-Dateien jedes npm-Pakets und legt sie als dist/THIRD_PARTY_LICENSES.txt ab.
// Keine zusätzliche Abhängigkeit (bewusst kein Lizenz-Plugin von npm).
const LICENSE_FILE_RE = /^(licen[cs]e|copying|notice)(\.|$)/i;
// Standardtext der Apache-2.0 (unverändert, aus dem typescript-Paket) für Pakete, die ihn nicht
// selbst mitbringen (z. B. laz-perf 0.0.7) — die Lizenz verlangt eine Kopie bei Weitergabe.
const APACHE_2_TEXT = require.resolve('typescript/LICENSE.txt');

function packageRoot(file: string): string | null {
  const marker = `${sep}node_modules${sep}`;
  const i = file.lastIndexOf(marker);
  if (i < 0) return null;
  const rest = file.slice(i + marker.length).split(sep);
  const depth = rest[0].startsWith('@') ? 2 : 1;
  return file.slice(0, i + marker.length) + rest.slice(0, depth).join(sep);
}

function thirdPartyLicenses(): Plugin {
  return {
    name: 'scanview-third-party-licenses',
    apply: 'build',
    generateBundle() {
      const roots = new Set<string>([dirname(dirname(LAZ_PERF_WASM))]);
      for (const id of this.getModuleIds()) {
        const root = packageRoot(id.split('?')[0].replace(/^\0/, ''));
        if (root && existsSync(join(root, 'package.json'))) roots.add(root);
      }
      const entries = [...roots]
        .map((root) => {
          const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')) as {
            name: string;
            version: string;
            license?: string;
          };
          const files = readdirSync(root).filter((f) => LICENSE_FILE_RE.test(f)).sort();
          return { root, pkg, files };
        })
        .sort((a, b) => a.pkg.name.localeCompare(b.pkg.name));
      const parts = [
        'scanview – Lizenzen der im Viewer-Bundle ausgelieferten Drittkomponenten',
        `(automatisch beim Build erzeugt, ${entries.length} Pakete; Übersicht in docs/THIRD_PARTY.md)`,
        '',
      ];
      for (const { root, pkg, files } of entries) {
        parts.push('='.repeat(78), `${pkg.name}@${pkg.version} — ${pkg.license ?? 'Lizenzfeld fehlt'}`, '='.repeat(78));
        if (files.length === 0 && pkg.license === 'Apache-2.0')
          parts.push('--- Apache-2.0 (Standardtext; das Paket bringt keine Lizenzdatei mit) ---', readFileSync(APACHE_2_TEXT, 'utf8').trimEnd(), '');
        else if (files.length === 0) parts.push('(Kein Lizenztext im Paket enthalten; Lizenz laut package.json siehe oben.)', '');
        for (const f of files) parts.push(`--- ${f} ---`, readFileSync(join(root, f), 'utf8').trimEnd(), '');
      }
      this.emitFile({ type: 'asset', fileName: 'THIRD_PARTY_LICENSES.txt', source: [...parts, 'Basis Universal 1.50.0 (Transcoder, eigener Bau, vendor/basis) — Apache-2.0', readFileSync(APACHE_2_TEXT, 'utf8'), 'Zstandard (im Basis-Transcoder enthalten) — BSD-3-Clause', readFileSync(new URL('./licenses/zstd-BSD-3-Clause.txt', import.meta.url), 'utf8'), 'meshoptimizer 0.22 (MeshoptDecoder) — MIT', readFileSync(new URL('./licenses/meshoptimizer-MIT.txt', import.meta.url), 'utf8'), 'MediaPipe Face Landmarker (Modell face_landmarker.task, vendor/mediapipe) — Apache-2.0, Google LLC (Lizenztext siehe oben)'].join('\n') });
    },
  };
}

// Versionskennung für die Diagnose (debug.ts, __SCANVIEW_VERSION__): package.json-Version, falls
// gesetzt, plus kurzer Git-Hash zur Build-Zeit. Ohne Git (z. B. Tarball) bleibt nur die Version.
function scanviewVersion(): string {
  const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as { version?: string };
  const version = pkg.version ?? '0.0.0';
  try {
    const hash = execFileSync('git', ['rev-parse', '--short', 'HEAD'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    return hash ? `${version}+${hash}` : version;
  } catch {
    return version;
  }
}

export default defineConfig({
  define: { __SCANVIEW_VERSION__: JSON.stringify(scanviewVersion()) },
  plugins: [lazPerfWasm(), basisTranscoder(), mediapipe(), webApp(), thirdPartyLicenses()],
  server: {
    port: 5173,
    // public/data ist ein Symlink auf den Datenordner; liegt er außerhalb des Repos, nennt
    // SCANVIEW_DATEN ihn, damit der Devserver dorthin lesen darf.
    fs: { allow: ['..', ...(process.env.SCANVIEW_DATEN ? [process.env.SCANVIEW_DATEN] : [])] },
    // Das Portal spricht den scanview-server unter 127.0.0.1:8803 an; im Devserver per Proxy
    // erreichbar, damit `same-origin`-Cookies und CSRF (Origin-Prüfung) lokal funktionieren.
    proxy: {
      '/api': 'http://127.0.0.1:8803',
      '/auth': 'http://127.0.0.1:8803',
    },
  },
  // public/data ist ein Symlink auf den Datenordner (mehrere GB, teils von laufenden Importen
  // beschrieben). Vite würde ihn beim Build rekursiv nach dist/ kopieren — deshalb aus.
  // public/ enthält sonst nichts; die Daten liefert im Betrieb der Webserver aus.
  build: {
    copyPublicDir: false,
    // Multi-Page-Build: Viewer bleibt unter index.html, das Portal kommt als zweiter
    // Einstieg dazu (web/portal.html + web/src/portal/*).
    rollupOptions: {
      input: { viewer: 'index.html', portal: 'portal.html' },
      // Fremdbibliotheken (Giro3D, three, proj4) in einen eigenen Chunk: Sie ändern sich seltener als
      // der eigene Code; nach einem Update lädt der Browser nur den eigenen Teil neu (/assets ist
      // immutable, der Hash im Namen wechselt nur bei geändertem Inhalt). SBOM: die Datei fällt wie
      // jedes Bundle unter die Hauptkomponente, die Pakete selbst stehen aus der Lockdatei im SBOM.
      output: {
        // Der Preload-Helfer von Vite (dynamische import()) bekommt einen eigenen Chunk: Landet er in
        // 'vendor', lädt das Portal die ganze 3D-Bibliothek mit, und deren Meshopt-Dekoder kompiliert
        // beim Laden WebAssembly, was die Portal-CSP (ohne 'wasm-unsafe-eval') zu Recht verbietet.
        manualChunks(id: string): string | undefined {
          if (id.includes('vite/preload-helper')) return 'preload';
          return /[\\/]node_modules[\\/](?:three|@giro3d[\\/]giro3d|proj4)[\\/]/.test(id) ? 'vendor' : undefined;
        },
      },
    },
  },
  // e2e/a11y/*.vitest.ts: reine Teile der Barrierefreiheitsprüfung. Im öffentlichen Export fehlt
  // web/e2e — das Muster findet dort nichts, die exportierten Tests bleiben eigenständig.
  test: { environment: 'node', include: ['tests/**/*.test.ts', 'e2e/a11y/**/*.vitest.ts', 'e2e/plattform/**/*.vitest.ts'] },
});
