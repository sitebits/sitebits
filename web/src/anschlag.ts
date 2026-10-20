// Tafel am Westportal des synthetischen Testpakets (import/scripts/synthetisch_testkirche.py). Für jedes andere
// Paket ohne Wirkung: Die Kennung wird einmal beim Laden geprüft, danach kostet ein Klick nichts. Die Tafel selbst
// (anschlagTafel.ts) lädt erst beim Treffer nach.

/** FNV-1a (32 bit) über die UTF-8-Bytes. */
export function kennung(text: string): number {
  let h = 0x811c9dc5;
  for (const b of new TextEncoder().encode(text)) h = Math.imul(h ^ b, 0x01000193) >>> 0;
  return h;
}

const PAKET = 0x936218ac;
/** Türfläche im Portal (Paketkoordinaten, m): x um 0,2 m zurückgesetzt, y −1…1, z 0…3. */
const FLAECHE = { x: [0.1, 0.35], y: [-1.05, 1.05], z: [-0.05, 3.05] } as const;

export function anschlagMoeglich(projectId: string): boolean {
  return kennung(projectId) === PAKET;
}

export function trifftAnschlag(p: readonly [number, number, number] | null): boolean {
  if (!p) return false;
  const [x, y, z] = p;
  return x >= FLAECHE.x[0] && x <= FLAECHE.x[1] && y >= FLAECHE.y[0] && y <= FLAECHE.y[1] && z >= FLAECHE.z[0] && z <= FLAECHE.z[1];
}

let offen = false;
export async function oeffneAnschlag(): Promise<void> {
  if (offen) return;
  offen = true;
  try {
    const { zeigeTafel } = await import('./anschlagTafel');
    await zeigeTafel();
  } finally {
    offen = false;
  }
}
