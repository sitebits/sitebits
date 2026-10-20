# KTX-Software 4.4.2 (Apache-2.0), unverändert als externer Prozess.
FROM python:3.12-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e
ADD --checksum=sha256:ca635ed489d8bf54fac8d7687056c651193de0740830a7738cc034adc63e3027 https://github.com/KhronosGroup/KTX-Software/releases/download/v4.4.2/KTX-Software-4.4.2-Linux-x86_64.deb /tmp/ktx.deb
RUN apt-get update && apt-get install -y --no-install-recommends /tmp/ktx.deb && rm -rf /var/lib/apt/lists/* /tmp/ktx.deb
