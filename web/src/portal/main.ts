import '../spleenStart';
// Portal-Einstieg: reine Routing-Verdrahtung nach location.pathname, DOM-Aufbau liegt in
// seiten.ts, Netzwerk in api.ts, Titel/Fußzeile/Instanz-Angaben in rahmen.ts.
import { ApiFehler, projekte, sitzung, type Sitzung } from './api';
import { zeigeBarrierefreiheit } from './barrierefreiheit';
import { zeigeZuPruefen } from './zuPruefen';
import { zeigeBefunde } from './befunde';
import { baueFusszeile, ladeInstanz, OHNE_INSTANZ, seitenTitel, type Instanz } from './rahmen';
import { zeigeAnmelden, zeigeEinladung, zeigeStart } from './seiten';
import { zeigeProjektEinstellungen } from './projektEinstellungen';
import { zeigeMaengelliste } from './maengelliste';
import { zeigeTeilen } from './teilen';
import { zeigeVerlauf } from './verlauf';
import { zeigeUploads } from './uploads';
import { zeigeVerwaltung } from './verwaltung';
import { zielPfad } from './weiter';

async function fusszeile(instanzP: Promise<Instanz | null>, nutzer: Sitzung | null): Promise<void> {
  const fuss = document.getElementById('sv-fuss');
  if (!fuss) return;
  const instanz = (await instanzP) ?? OHNE_INSTANZ;
  baueFusszeile(fuss, {
    kontakt: instanz.kontakt, quelltextUrl: nutzer?.quelltextUrl ?? null, angemeldet: nutzer !== null,
    impressum: instanz.impressum, datenschutz: instanz.datenschutz,
  });
}

// Einladungslink hat die Form /einladung#t=<token> — der Token steht bewusst im Fragment, damit er
// nie im Server-/Proxy-Log landet (der Browser schickt Fragmente nie mit). token_urlsafe(32)
// erzeugt ausschließlich [A-Za-z0-9_-]; alles andere ist keine gültige Einladung.
function tokenAusHash(hash: string): string {
  return /^#t=([A-Za-z0-9_-]+)$/.exec(hash)?.[1] ?? '';
}

function zuAnmelden(): void {
  const weiter = encodeURIComponent(zielPfad(location.pathname + location.search, location.origin));
  location.href = `/anmelden?weiter=${weiter}`;
}

async function start(): Promise<void> {
  const root = document.getElementById('portal');
  if (!root) return;

  document.title = seitenTitel(location.pathname);
  // Öffentlich, ohne Anmeldung (wer an der Anmeldung scheitert, muss Barrieren melden können) — parallel zum Seitenaufbau.
  const instanzP = ladeInstanz();

  if (location.pathname === '/barrierefreiheit') {
    // Öffentlich: kein zuAnmelden(); angemeldet gibt es zusätzlich Quelltext-Link und Abmelden.
    const n = await sitzung();
    zeigeBarrierefreiheit(root, await instanzP);
    await fusszeile(instanzP, n);
    return;
  }

  if (location.pathname === '/einladung') {
    // Kein Fragment (z. B. nach Neuladen, s. u. replaceState) → null: eigene Meldung in seiten.ts.
    const token = location.hash === '' ? null : tokenAusHash(location.hash);
    history.replaceState(null, '', location.pathname + location.search);
    zeigeEinladung(root, token);
    await fusszeile(instanzP, null);
    return;
  }

  if (location.pathname === '/teilen') {
    // Nur-Lese-Link (F28): Token im Fragment, ohne Anmeldung; die Seite nimmt das Fragment selbst weg.
    zeigeTeilen(root, location.hash);
    await fusszeile(instanzP, null);
    return;
  }

  if (location.pathname === '/anmelden') {
    zeigeAnmelden(root);
    await fusszeile(instanzP, null);
    return;
  }

  if (location.pathname === '/verlauf' || location.pathname === '/maengelliste') {
    const n = await sitzung();
    if (!n) {
      zuAnmelden();
      return;
    }
    const zeigen = location.pathname === '/maengelliste' ? zeigeMaengelliste : zeigeVerlauf;
    await zeigen(root, new URLSearchParams(location.search).get('p') ?? '');
    await fusszeile(instanzP, n);
    return;
  }

  if (location.pathname === '/zu-pruefen') {
    const n = await sitzung();
    if (!n) { zuAnmelden(); return; }
    await zeigeZuPruefen(root, new URLSearchParams(location.search).get('p') ?? '');
    await fusszeile(instanzP, n);
    return;
  }

  if (location.pathname === '/befunde') {
    const n = await sitzung();
    if (!n) {
      zuAnmelden();
      return;
    }
    await zeigeBefunde(root, new URLSearchParams(location.search).get('p') ?? '', n.id ?? null);
    await fusszeile(instanzP, n);
    return;
  }

  if (location.pathname === '/projekt-einstellungen') {
    const n = await sitzung();
    if (!n) {
      zuAnmelden();
      return;
    }
    await zeigeProjektEinstellungen(root, new URLSearchParams(location.search).get('p') ?? '');
    await fusszeile(instanzP, n);
    return;
  }

  if (location.pathname === '/verwaltung') {
    const n = await sitzung();
    if (!n) {
      zuAnmelden();
      return;
    }
    await zeigeVerwaltung(root, n);
    await fusszeile(instanzP, n);
    return;
  }

  if (location.pathname === '/uploads') {
    const n = await sitzung();
    if (!n) {
      zuAnmelden();
      return;
    }
    await zeigeUploads(root, { importAn: n.importAn === true });
    await fusszeile(instanzP, n);
    return;
  }

  const nutzer = await sitzung();
  if (!nutzer) {
    zuAnmelden();
    return;
  }

  try {
    const liste = await projekte();
    zeigeStart(root, nutzer, liste);
    await fusszeile(instanzP, nutzer);
  } catch (e) {
    if (e instanceof ApiFehler && e.status === 401) {
      zuAnmelden();
      return;
    }
    // Kopf-/Fußzeile (Abmelden, „?“) sollen auch dann bedienbar bleiben, wenn nur das Laden der
    // Projekte fehlschlägt — deshalb die normale Startseite mit leerer Liste aufbauen und danach
    // gezielt den Kartenbereich durch die Fehlermeldung ersetzen, statt die ganze Seite wegzuwerfen.
    zeigeStart(root, nutzer, []);
    const raster = root.querySelector('.sv-karten-raster');
    if (raster) {
      raster.replaceChildren();
      const fehlerEl = document.createElement('p');
      fehlerEl.className = 'sv-fehler';
      fehlerEl.textContent = e instanceof ApiFehler ? e.message : 'Die Projekte konnten nicht geladen werden.';
      raster.appendChild(fehlerEl);
    }
    // Die Suche filtert nur die (hier leere) Liste aus zeigeStart und würde die Fehlermeldung beim
    // ersten Tastendruck mit „Keine Projekte gefunden.“ überschreiben — deshalb deaktiviert, solange
    // gar keine Projekte geladen werden konnten.
    const suche = root.querySelector<HTMLInputElement>('.sv-suche');
    if (suche) suche.disabled = true;
    await fusszeile(instanzP, nutzer);
  }
}

void start();
