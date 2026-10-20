// LRU-Texturbudget in Byte — reine Buchhaltung ohne three.js; der Freigabe-Rückruf
// entsorgt die Textur. Angeheftete Einträge werden nie verdrängt; passt ein neuer Eintrag neben den
// angehefteten nicht, wird er abgewiesen (und freigegeben) statt das Budget zu überschreiten — geprüft
// VOR jeder Verdrängung, damit ein aussichtsloser Eintrag keine brauchbaren Texturen mitreißt.
interface Entry<T> {
  value: T;
  bytes: number;
  pinned: boolean;
  lastUsed: number;
}

export class TextureBudget<T> {
  private readonly entries = new Map<string, Entry<T>>();
  private used = 0;
  private clock = 0;

  constructor(
    readonly limitBytes: number,
    private readonly release: (value: T, key: string) => void,
  ) {}

  get usedBytes(): number {
    return this.used;
  }

  keys(): string[] {
    return [...this.entries.keys()];
  }

  has(key: string): boolean {
    return this.entries.has(key);
  }

  get(key: string): T | undefined {
    const e = this.entries.get(key);
    if (!e) return undefined;
    e.lastUsed = ++this.clock;
    return e.value;
  }

  /** Passt ein Eintrag dieser Größe neben alle angehefteten (außer ``replacing``)? */
  fits(bytes: number, replacing?: string): boolean {
    let pinned = 0;
    for (const [k, e] of this.entries) if (e.pinned && k !== replacing) pinned += e.bytes;
    return pinned + bytes <= this.limitBytes;
  }

  add(key: string, value: T, bytes: number, pinned = false): boolean {
    if (!this.fits(bytes, key)) {
      this.release(value, key);
      return false;
    }
    this.delete(key);
    while (this.used + bytes > this.limitBytes) {
      const victim = this.leastRecentlyUsed();
      if (victim === null) break;
      this.delete(victim);
    }
    if (this.used + bytes > this.limitBytes) {
      this.release(value, key);
      return false;
    }
    this.entries.set(key, { value, bytes, pinned, lastUsed: ++this.clock });
    this.used += bytes;
    return true;
  }

  pin(key: string, pinned: boolean): void {
    const e = this.entries.get(key);
    if (e) e.pinned = pinned;
  }

  delete(key: string): void {
    const e = this.entries.get(key);
    if (!e) return;
    this.entries.delete(key);
    this.used -= e.bytes;
    this.release(e.value, key);
  }

  retain(keep: (key: string) => boolean): void {
    for (const k of [...this.entries.keys()]) if (!keep(k)) this.delete(k);
  }

  clear(): void {
    this.retain(() => false);
  }

  private leastRecentlyUsed(): string | null {
    let best: string | null = null;
    let t = Infinity;
    for (const [k, e] of this.entries) {
      if (!e.pinned && e.lastUsed < t) {
        best = k;
        t = e.lastUsed;
      }
    }
    return best;
  }
}
