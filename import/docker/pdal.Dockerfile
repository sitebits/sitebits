# PDAL (BSD) und untwine (GPL-3) als ein Werkzeug-Image `scanview-pdal` — beide nur als externe
# Prozesse genutzt, nie gelinkt oder importiert. Gebaut bei Bedarf von
# scanview_import.pdal_runner.ensure_image(); das Image wird nie verteilt (keine Registry).
#
# Reproduzierbar ohne Registry-Digest des Ergebnisses:
# - Zwei Stufen, beide per Digest gepinnt (nachgeschlagen am 10.11.2026): micromamba
#   (Tag 2.10.0-debian13-slim, Docker Hub 27.10.2026) installiert nur; ausgeliefert wird die
#   Umgebung auf distroless base-nossl-debian13 (Tag latest, gcr.io 31.10.2026: libc6, tzdata,
#   ca-certificates, ohne Shell und Paketverwaltung). Die Debian-Schicht von micromamba brachte
#   47 Treffer hoch (util-linux, sqlite, systemd, ncurses, perl, zlib, apt), distroless keinen.
# - Inhalt allein aus pdal.conda-lock.txt (@EXPLICIT, je Zeile URL + SHA-256, conda-forge
#   linux-64): libpdal-core/libpdal-e57 2.10.2 und untwine 1.5.1 in denselben Builds wie die
#   PDAL-Umgebung des Servers (import/pdal-umgebung/pixi.lock), dazu python 3.12 für den
#   RSS-Messwrapper; ohne pip, ohne conda/rattler. Alle Pakete vor dem 29.10.2026 veröffentlicht.
#   Erzeugt am 10.11.2026: Specs = jedes Paket aus pixi.lock als `name==version=build`, dazu
#   `python=3.12` und `libuuid==2.42.4=hcfc3c73_0` (der neueste Build war jünger als 7 Tage);
#   gelöst mit `micromamba create --dry-run --json --override-channels -c conda-forge
#   --platform linux-64` in der Bau-Stufe, MAMBA_ADD_PIP_AS_PYTHON_DEPENDENCY=false; je Paket
#   `url#sha256:<sha256>` aus der Lösung, nach URL sortiert. micromamba prüft die SHA-256 beim
#   Laden (falscher Hash → Abbruch, geprüft).
# - Tag = erste 12 Hex der SHA-256 über diese Datei und das Lock (pdal_runner.IMAGE, Drift-Test
#   in import/tests/test_pdal_runner.py); ein geändertes Dockerfile oder Lock ergibt ein neues Image.
FROM mambaorg/micromamba:2.10.0-debian13-slim@sha256:e1476182cd40eb1f7c0cfae4adec9bfd78f3d7f05c61368306abfdc8939c1198 AS bau

COPY --chown=$MAMBA_USER:$MAMBA_USER pdal.conda-lock.txt /tmp/pdal.conda-lock.txt
RUN micromamba install -y -n base -f /tmp/pdal.conda-lock.txt \
    && micromamba clean -afy

FROM gcr.io/distroless/base-nossl-debian13:latest@sha256:d710a5c8bfe4cf7e726d7e1afd73e363a9fb02c2a11ad893d2d6185c1b9a89cb

COPY --from=bau /opt/conda /opt/conda

# Ohne Entrypoint-Skript (läuft mit --user <uid> und --read-only); die Variablen der
# Aktivierungsskripte fest wie pdal_runner.lokale_variablen().
ENV PATH=/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PROJ_DATA=/opt/conda/share/proj \
    PROJ_NETWORK=OFF \
    GDAL_DATA=/opt/conda/share/gdal \
    GDAL_DRIVER_PATH=/opt/conda/lib/gdalplugins \
    GEOTIFF_CSV=/opt/conda/share/epsg_csv \
    PDAL_DRIVER_PATH=/opt/conda/lib \
    CPL_ZIP_ENCODING=UTF-8 \
    XML_CATALOG_FILES=file:///opt/conda/etc/xml/catalog
ENTRYPOINT []
CMD ["pdal", "--version"]
