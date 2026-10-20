import { berechneKanten } from './kantenDaten';
import type { KantenAnfrage } from './kantenDaten';
// Kein DOM-/three-Zustand im Worker, nur eigene übertragene Puffer.
self.onmessage = (ev: MessageEvent<KantenAnfrage>) => {
  const listen = berechneKanten(ev.data);
  self.postMessage(listen, { transfer: listen.map((a) => a.buffer as ArrayBuffer) });
};
