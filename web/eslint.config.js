import tseslint from 'typescript-eslint';
import playwright from 'eslint-plugin-playwright';

// `npm run lint` prüft den ganzen Ordner (`eslint .`) statt einer festen Ordnerliste: so läuft er
// auch im öffentlichen Export, dem e2e/ und die Screenshot-Skripte fehlen. Bau-, Test- und
// Paketausgaben sind ausgenommen.
export default tseslint.config(
  { ignores: ['dist/', 'node_modules/', 'public/', 'test-results/', 'playwright-report/', 'e2e-results/', 'vendor/'] },
  ...tseslint.configs.recommended,
  {
    ...playwright.configs['flat/recommended'],
    files: ['e2e/**/*.ts'],
    rules: {
      // Bulk Suppressions erfasst nur Fehler; auch empfohlene Warnungen müssen ratcheten.
      ...Object.fromEntries(Object.entries(playwright.configs['flat/recommended'].rules)
        .map(([name, value]) => [name, value === 'off' || value === 0 ? value
          : Array.isArray(value) ? ['error', ...value.slice(1)] : 'error'])),
      'playwright/no-focused-test': 'error',
      'no-restricted-syntax': ['error', {
        selector: 'CallExpression[callee.object.name="test"][callee.property.name="skip"][arguments.length<2]',
        message: 'Bedingter Skip braucht eine Bedingung und einen Grund.',
      }],
      'playwright/no-skipped-test': ['error', { allowConditional: true }],
      'playwright/missing-playwright-await': 'error',
      'playwright/no-wait-for-timeout': 'error',
      'playwright/no-force-option': 'error',
      'playwright/valid-expect': 'error',
    },
  },
  {
    files: ['src/**/*.ts', 'tests/**/*.ts'],
    languageOptions: { parserOptions: { projectService: true } },
    rules: Object.fromEntries([
      'no-floating-promises', 'no-misused-promises', 'await-thenable',
      'no-unsafe-assignment', 'no-unsafe-member-access', 'no-unsafe-call',
      'no-unsafe-return', 'no-unsafe-argument',
    ].map(rule => [`@typescript-eslint/${rule}`, 'error'])),
  },
);
