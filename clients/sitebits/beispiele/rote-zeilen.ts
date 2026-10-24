// Alle Projekte prüfen, nur rote Zeilen ausgeben.
//
//   SITEBITS_URL=https://… SITEBITS_TOKEN=sv_… node beispiele/rote-zeilen.ts [profil]
//
// Startet in jedem Projekt, in dem das Konto Auswertungen starten darf, die Lieferprüfung
// (`auswerten_pruefen`), wartet auf alle und gibt je Zeile mit dem Urteil „nicht erfüllt“ eine
// Zeile aus: Projekt, Anforderung, Grund (Tabulator getrennt). Projekte ohne Recht nennt es auf
// stderr. Exit 0 = nichts rot, 1 = mindestens eine rote Zeile, 2 = Fehler.
import { SitebitsFehler } from '../src/index.ts';
import { darfAuswerten, verbinden } from './verbindung.ts';

const ROT = 'nicht erfüllt';
const profil = process.argv[2] ?? 'bestandserfassung';
const sb = verbinden();

try {
  const { projekte } = await sb.projekte();
  const laeufe = [];
  for (const p of projekte) {
    if (!darfAuswerten(p.rolle)) {
      console.error(`übersprungen: ${p.id} (Rolle ${p.rolle})`);
      continue;
    }
    laeufe.push(sb.auswertungStarten(p.id, { typ: 'auswerten_pruefen', optionen: { profil } })
      .then(a => sb.aufAuswertungWarten(p.id, a.id))
      .then(async a => {
        if (a.status !== 'fertig') return [`${p.id}\t(Auswertung ${a.status})\t${a.fehler ?? ''}`];
        const e = await sb.ergebnis(p.id, a.id);
        return e.zeilen.filter(z => z.urteil === ROT)
          .map(z => `${p.id}\t${z.id} ${z.anforderung ?? ''}\t${z.grund ?? ''}`);
      }));
  }
  const rot = (await Promise.all(laeufe)).flat();
  for (const zeile of rot) console.log(zeile);
  process.exitCode = rot.length ? 1 : 0;
} catch (e) {
  console.error(e instanceof SitebitsFehler ? e.message : e);
  process.exitCode = 2;
}
