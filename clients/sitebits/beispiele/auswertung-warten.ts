// Auswertung starten und auf das Ergebnis warten.
//
//   SITEBITS_URL=https://… SITEBITS_TOKEN=sv_… node beispiele/auswertung-warten.ts <projekt> [profil]
//
// Reiht die Lieferprüfung ein (Projektleitung), fragt alle 2 s nach, bis sie fertig ist, und
// gibt Gesamturteil, Zählung und den Pfad des Berichts aus. Exit 0 = fertig, 1 = fehlgeschlagen
// oder abgebrochen, 2 = Fehler.
import { SitebitsFehler } from '../src/index.ts';
import { verbinden } from './verbindung.ts';

const [projekt, profil = 'bestandserfassung'] = process.argv.slice(2);
if (!projekt) {
  console.error('Aufruf: node beispiele/auswertung-warten.ts <projekt> [profil]');
  process.exit(2);
}
const sb = verbinden();

try {
  const start = await sb.auswertungStarten(projekt, { typ: 'auswerten_pruefen', optionen: { profil } });
  console.error(`eingereiht: ${start.id} (Warteplatz ${start.warteplatz ?? '–'}, Worker ${start.worker})`);
  const a = await sb.aufAuswertungWarten(projekt, start.id, { intervallMs: 2000 });
  if (a.status !== 'fertig') {
    console.log(`${a.id}: ${a.status}${a.fehler ? ` – ${a.fehler}` : ''}`);
    process.exitCode = 1;
  } else {
    const e = await sb.ergebnis(projekt, a.id);
    console.log(`${a.id}: ${e.gesamt.urteil}`);
    for (const [urteil, anzahl] of Object.entries(e.gesamt.zaehlung)) console.log(`  ${urteil}: ${anzahl}`);
    console.log(`Bericht: ${a.bericht ?? '–'}`);
  }
} catch (e) {
  console.error(e instanceof SitebitsFehler ? e.message : e);
  process.exitCode = 2;
}
