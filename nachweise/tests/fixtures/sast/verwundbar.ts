// Absichtlich verwundbare Fixture der Negativkontrolle `sast-fund-unregistriert` (nie gebaut,
// nie ausgeführt): Auswertung fremder Eingabe als Code.
export function auswerten(eingabe: string): unknown {
  return eval(eingabe);
}
