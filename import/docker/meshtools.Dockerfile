# KTX-Software 4.4.2 (Apache-2.0), unverändert als externer Prozess (toktx, ktx extract).
# Gebaut bei Bedarf von scanview_import.mesh_textures.ensure_ktx_image() unter dem Tag
# mesh_textures.KTX_IMAGE = scanview-meshtools:<erste 12 Hex der SHA-256 über dieses Dockerfile
# und meshtools.conda-lock.txt>, wie scanview-pdal. Es wird nie verteilt (keine Registry).
#
# Aufbau wie scanview-pdal (import/docker/pdal.Dockerfile), Basis-Digests nachgeschlagen am
# 10.11.2026:
# - Bau-Stufe mambaorg/micromamba 2.10.0-debian13-slim (dieselbe wie scanview-pdal): installiert
#   meshtools.conda-lock.txt und entpackt das per SHA-256 geprüfte KTX-Release-Paket mit
#   dpkg-deb -x, unverändert nach /opt/ktx (bin, lib, include, share — samt der ungenutzten
#   Java-Bindung, Dateien des Release-Pakets bleiben vollständig).
# - Laufzeit gcr.io/distroless/base-nossl-debian13 (dieselbe wie scanview-pdal: libc6, tzdata,
#   ca-certificates, ohne Shell und Paketverwaltung). Die Basis python:3.12-slim brachte 55 Treffer
#   hoch und 3 unbekannt in der Debian-Schicht (B-SCAN-IMG-MESHTOOLS), distroless/python3-debian13
#   (31.10.2026) 51 hoch; die base-nossl-Schicht hat keinen.
# - meshtools.conda-lock.txt (@EXPLICIT, je Zeile URL + SHA-256), jede Zeile wörtlich aus
#   pdal.conda-lock.txt (dieselben Builds, Test in import/tests/test_mesh_textures.py), ohne pip,
#   ohne conda/rattler. Bewusst nur fünf Pakete statt des Abhängigkeitsabschlusses von python
#   (24): python 3.12 (statisch gelinkt, für den RSS-Messwrapper) sowie libstdcxx, libgcc, libgomp
#   und _openmp_mutex für libstdc++/libgcc_s der KTX-Programme. Mit ldd in der Bau-Stufe geprüft
#   (10.11.2026): python3.12, ktx, toktx, libktx und die vom Wrapper geladenen Module
#   (_posixsubprocess, fcntl, math, resource, select) brauchen sonst nur libc aus der Basis.
#   Module wie zlib, pyexpat, sqlite3 und ssl laufen hier deshalb nicht; der Wrapper braucht sie
#   nicht, und libexpat/libzlib mit bekannten OSV-Treffern (siehe B-SCAN-IMG-PDAL) bleiben draußen.
FROM mambaorg/micromamba:2.10.0-debian13-slim@sha256:e1476182cd40eb1f7c0cfae4adec9bfd78f3d7f05c61368306abfdc8939c1198 AS bau

COPY --chown=$MAMBA_USER:$MAMBA_USER meshtools.conda-lock.txt /tmp/meshtools.conda-lock.txt
RUN micromamba install -y -n base -f /tmp/meshtools.conda-lock.txt \
    && micromamba clean -afy
ADD --chown=$MAMBA_USER:$MAMBA_USER --checksum=sha256:ca635ed489d8bf54fac8d7687056c651193de0740830a7738cc034adc63e3027 https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Linux-x86_64.deb /tmp/ktx.deb
# Der klassische Docker-Builder übergeht ADD --checksum ohne Fehler (geprüft 10.11.2026, Docker
# 29.9.2 ohne buildx: falscher Wert baute durch); deshalb zusätzlich sha256sum -c.
RUN echo "ca635ed489d8bf54fac8d7687056c651193de0740830a7738cc034adc63e3027  /tmp/ktx.deb" | sha256sum -c - \
    && dpkg-deb -x /tmp/ktx.deb /tmp/ktx-deb && mv /tmp/ktx-deb/usr /tmp/ktx

FROM gcr.io/distroless/base-nossl-debian13:latest@sha256:d710a5c8bfe4cf7e726d7e1afd73e363a9fb02c2a11ad893d2d6185c1b9a89cb

COPY --from=bau /opt/conda /opt/conda
COPY --from=bau /tmp/ktx /opt/ktx

# Die KTX-Programme finden libktx über ihren RUNPATH ($ORIGIN/../lib), libstdc++ und libgcc_s
# kommen aus der conda-Umgebung.
ENV PATH=/opt/ktx/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LD_LIBRARY_PATH=/opt/conda/lib
ENTRYPOINT []
CMD ["toktx", "--version"]
