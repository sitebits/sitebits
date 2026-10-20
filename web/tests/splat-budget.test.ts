import { it, expect } from 'vitest';
import { splatBudget } from '../src/splatLayer';

it('begrenzt die dekodierte Sichtschicht, auf schwachen Geräten früher', () => {
  const layer = { splatCount: 1_000_000, sizeBytes: 56_000_000, shDegree: 0 };
  expect(splatBudget(layer, false)).toBe(true);
  expect(splatBudget(layer, true)).toBe(false);
  expect(splatBudget({ splatCount: 3_000_000, sizeBytes: 32 }, false)).toBe(false);
});

it('berücksichtigt SH-Puffer und sperrt zehn Millionen ohne Paging', () => {
  expect(splatBudget({ splatCount: 1_000_000, sizeBytes: 1_000_000, shDegree: 3 }, false)).toBe(false);
  expect(splatBudget({ splatCount: 10_000_000, sizeBytes: 1 }, false)).toBe(false);
});

it('lässt zehn Millionen RAD über den begrenzten Pager auf beiden Geräteklassen zu', () => {
  const layer = { format: 'rad' as const, chunkCount: 200, splatCount: 10_000_000,
    sizeBytes: 900_000_000, shDegree: 3 };
  expect(splatBudget(layer, false)).toBe(true);
  expect(splatBudget(layer, true)).toBe(true);
});
