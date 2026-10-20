/// <reference types="node" />
// E2E-Smoke gegen den laufenden Deploy (deploy/docker-compose.yml), nicht gegen den Devserver.
// Bewusste Abweichungen von den Standard-Playwright-Vorgaben:
//   - timeout 300 s statt 120 s: Laden + 60-s-Kamerafahrt + Drosselung auf 50 Mbit/s passt nicht in 2 min.
//   - workers 1 / fullyParallel false: alle FPS-Läufe teilen sich eine GPU — parallel wären die
//     Messwerte wertlos.
//   - --ignore-gpu-blocklist zusätzlich: ohne fällt Chromium headless ggf. auf SwiftShader zurück;
//     der tatsächlich genutzte Renderer steht in jedem Ergebnis (Feld "renderer").
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'e2e',
  // Eigene Konfigurationen mit eigener Wegwerf-Instanz; hier eingesammelt würden sie nur übersprungen.
  testIgnore: ['plattform/**', 'a11y/**'],
  timeout: 300_000,
  workers: 1,
  fullyParallel: false,
  reporter: 'list',
  use: {
    baseURL: process.env.SCANVIEW_URL ?? 'http://localhost:8080',
    viewport: { width: 1920, height: 1080 },
    launchOptions: { args: ['--use-gl=angle', '--enable-gpu', '--ignore-gpu-blocklist'] },
  },
});
