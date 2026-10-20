# Spleen

A few hidden quirks for curious developers: Braille point pictures, original sayings
and quotations. The canonical pictures and texts live here.

Pictures © 2026 André Stiebitz, licensed under [CC BY 4.0](../LICENSES/CC-BY-4.0.txt).
They were made from his own panoramas and little planets of Potsdam and Berlin.

Find them in the viewer and portal browser consoles, in `scanview-import --version`
(on a terminal), in its help epilogue, and at the bottom of the root READMEs.
Browser automation and test mode stay quiet. Nothing is added to the application UI.
The CLI ships copies in `import/src/scanview_import/spleen_daten/`; tests check them
against this directory. The web build imports this directory directly.

To make a picture, use an environment with Pillow (no new dependency is needed in
`import/`): `python spleen/punktbild.py panorama.jpg`. The converter writes Braille
to stdout (100 columns by default); call `braille(path, cols=72)` for the width used
here (panoramas; little planets use 48 columns). Keep attribution for new pictures.

- `python3 spleen/sync.py` copies pictures and sayings into the CLI package data.
- `python3 spleen/versionsbild.py <version>` picks the picture of a release (stable, evenly spread);
  it goes into the CHANGELOG section and the release notes.
- `nachweise/tests/test_spleen_grenzen.py` fails if a point picture or a saying shows up anywhere
  outside this directory, the CLI copy and the root READMEs/CHANGELOG.
