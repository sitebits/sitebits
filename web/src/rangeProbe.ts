// Prüft, ob der Server HTTP-Range-Requests unterstützt (Voraussetzung für das Streamen der COPC-Datei
// per Byte-Bereich statt Volldownload). ok ist nur true bei Status 206 mit passendem Content-Range.
export type RangeProbe = { ok: true; size: number } | { ok: false; reason: string };

export async function probeRange(url: string, fetchFn: typeof fetch = fetch): Promise<RangeProbe> {
  let r: Response;
  try {
    r = await fetchFn(url, { headers: { Range: 'bytes=0-15' } });
  } catch {
    return { ok: false, reason: `Server nicht erreichbar (${url}).` };
  }
  // Den Körper nie lesen, sondern verwerfen: ohne Range-Support käme hier die ganze Datei (GB).
  // Der Abbruch schließt die Übertragung aktiv, statt sie nur per TCP-Gegendruck anzuhalten.
  void r.body?.cancel().catch(() => undefined);
  if (r.status !== 206)
    return { ok: false, reason: `Server liefert Status ${r.status} statt 206 – Range-Requests nicht unterstützt.` };
  const cr = r.headers.get('Content-Range') ?? '';
  const m = /^bytes 0-15\/(\d+)$/.exec(cr);
  if (!m) return { ok: false, reason: `Ungültiges Content-Range: "${cr}".` };
  return { ok: true, size: Number(m[1]) };
}
