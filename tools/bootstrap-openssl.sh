#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
version=3.5.9
checksum=603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a
mkdir -p "$root/.deps"
cd "$root/.deps"
if [[ ! -f openssl.tar.gz ]]; then
  curl --fail --location --retry 3 "https://github.com/openssl/openssl/releases/download/openssl-$version/openssl-$version.tar.gz" -o openssl.tar.gz
fi
printf '%s  openssl.tar.gz\n' "$checksum" | sha256sum --check -
if [[ ! -d openssl-$version ]]; then tar -xzf openssl.tar.gz; fi
cd "openssl-$version"
./Configure --prefix="$root/.deps/openssl" --libdir=lib no-shared no-tests
make -j"${PQCOIN_BUILD_JOBS:-4}"
make install_sw
