# Tools to make the Cherry Move installers: NSIS (Windows Setup.exe), hfsprogs and libdmg-hfsplus (a Mac
# disk image, made without a Mac), rsvg and Pillow (icons and installer pictures). Ubuntu, because Debian
# has no hfsprogs.
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      nsis hfsprogs librsvg2-bin python3 python3-pil cmake make gcc g++ git ca-certificates zlib1g-dev libbz2-dev \
      libssl-dev \
    && rm -rf /var/lib/apt/lists/*
ARG LIBDMG_COMMIT=ec239599c1f234a4e01ae3fe51214d0c77e5baa3
RUN git clone -q https://github.com/mozilla/libdmg-hfsplus /tmp/libdmg && cd /tmp/libdmg \
    && git checkout -q "$LIBDMG_COMMIT" && cmake -B build . >/dev/null && make -C build -j4 >/dev/null 2>&1 \
    && install build/dmg/dmg build/hfs/hfsplus /usr/local/bin/ && rm -rf /tmp/libdmg
