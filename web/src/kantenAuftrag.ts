import { kantenKern } from './kantenKern';
import type { FeatureEdge, Teil } from './kantenKern';
import { kopiereKantenTeile } from './kantenDaten';
import type { KantenAnfrage } from './kantenDaten';
/** Ein Worker je laufender Gruppe; Abschluss/Verwerfen gibt Worker und Nachrichtenhandler frei. */
export function starteKanten(teile: readonly Teil[], geschweisst: boolean, fertig: (listen: FeatureEdge[][]) => void): () => void {
  let worker: Worker | undefined;
  let aktiv = true;
  const stoppen = (): void => {
    if (worker) { worker.onmessage = null; worker.onerror = null; worker.onmessageerror = null; worker.terminate(); worker = undefined; }
  };
  const rueckfall = (): void => {
    stoppen();
    if (aktiv) { aktiv = false; fertig(kantenKern(teile, geschweisst)); }
  };
  try {
    worker = new Worker(new URL('./kantenWorker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (ev: MessageEvent<Uint32Array[]>) => {
      if (!aktiv) return;
      aktiv = false; stoppen();
      fertig(ev.data.map((paare) => {
        const liste: FeatureEdge[] = [];
        for (let i = 0; i < paare.length; i += 2) liste.push({ a: paare[i]!, b: paare[i + 1]! });
        return liste;
      }));
    };
    worker.onerror = (ev) => { ev.preventDefault(); rueckfall(); };
    worker.onmessageerror = rueckfall;
    const kopien = kopiereKantenTeile(teile);
    const transfer: ArrayBuffer[] = [];
    for (const t of kopien) {
      transfer.push(t.positionen.buffer as ArrayBuffer);
      if (t.indizes) transfer.push(t.indizes.buffer as ArrayBuffer);
    }
    worker.postMessage({ teile: kopien, geschweisst } satisfies KantenAnfrage, transfer);
  } catch { rueckfall(); }
  return () => { aktiv = false; stoppen(); };
}
