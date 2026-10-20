# untwine (GPL-3) als eigenständiges Werkzeug-Image — nur als externer Prozess genutzt,
# nie gelinkt oder importiert. Gebaut bei Bedarf von scanview_import.pdal_runner.ensure_untwine_image().
# Basis per Digest gepinnt (lokal geprüft 26.09.2026) — reproduzierbarer Bau, kein stilles "latest".
FROM condaforge/miniforge3:latest@sha256:3a41fcca7d6740df102951d9f122bf1a5f81170fdba234243060f72eb4891901

RUN conda install -y -c conda-forge untwine=1.5.1 \
    && conda clean -afy

ENTRYPOINT []
CMD ["untwine", "--help"]
