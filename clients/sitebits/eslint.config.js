import tseslint from 'typescript-eslint';

// Wie web/: empfohlene Regeln plus die typgestützten „unsafe“-/Promise-Regeln für den ganzen
// Client (Quelle, Tests, Beispiele). Die erzeugten Typen prüft tsc, nicht ESLint.
export default tseslint.config(
  { ignores: ['node_modules/', 'src/schema.d.ts'] },
  ...tseslint.configs.recommended,
  {
    files: ['**/*.ts'],
    languageOptions: { parserOptions: { projectService: true } },
    rules: Object.fromEntries([
      'no-floating-promises', 'no-misused-promises', 'await-thenable',
      'no-unsafe-assignment', 'no-unsafe-member-access', 'no-unsafe-call',
      'no-unsafe-return', 'no-unsafe-argument',
    ].map(rule => [`@typescript-eslint/${rule}`, 'error'])),
  },
  {
    // node:test meldet ein nicht abgewartetes test() selbst; die Promise ist dort bekannt sicher.
    files: ['test/**/*.ts'],
    rules: {
      '@typescript-eslint/no-floating-promises': ['error', {
        allowForKnownSafeCalls: [{ from: 'package', package: 'node:test', name: ['test', 'describe', 'it'] }],
      }],
    },
  },
);
