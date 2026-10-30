# Terrain mip-pyramid build + publish — portable batch container.
#
# Builds the GLO-30 mip pyramid over a bind-mounted tile tree, then builds and
# uploads region packs to R2 (re-signing the manifest), and exits. The SAME
# image runs on a QNAP (Container Station), any Docker host, or AWS ECS/EC2.
# See README.md for QNAP setup (native volume paths are the gotcha).
#
# Runtime deps: numpy (mip downsample), boto3 (R2/S3 upload), PyNaCl
# (ed25519 / minisign-compatible signing — pure Python, no binary), PyYAML
# (regions.yaml), and PyQt6 + libGL/libEGL for the publish gate only: its
# device acceptance check (ACC-001, blocking) opens each pack with pyEfis's
# SVS reader, which imports PyQt6 (AER-4101). The build tools themselves
# still need no Qt. NO GDAL, NO minisign binary.
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
       libgl1 libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir numpy boto3 "PyNaCl>=1.5" "PyYAML>=6.0" "requests>=2.28" PyQt6

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Code (pyEfis + makerplane-data) is cloned fresh at runtime so re-runs pick up
# ongoing map/rendering work without an image rebuild — this build context only
# supplies entrypoint.sh. The tile tree + pack scratch are bind mounts.
WORKDIR /opt
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
