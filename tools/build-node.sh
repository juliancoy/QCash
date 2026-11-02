#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
reference="$root/../crypto-references/cometbft"
expected=6aef1e12197f587d1e85bc4cf3b3db4bf8d1be43
if [[ "$(git -C "$reference" rev-parse HEAD)" != "$expected" ]] || [[ -n "$(git -C "$reference" status --porcelain --untracked-files=all)" ]]; then
  echo "CometBFT reference must be unmodified at $expected" >&2
  exit 1
fi
mkdir -p "$root/.deps"
if [[ ! -x "$root/.deps/go/bin/go" ]]; then
  if [[ ! -f "$root/.deps/go.tar.gz" ]]; then
    curl --fail --location --retry 3 https://go.dev/dl/go1.26.10.linux-amd64.tar.gz -o "$root/.deps/go.tar.gz"
  fi
  printf '%s  %s\n' ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5 "$root/.deps/go.tar.gz" | sha256sum --check -
  tar -xzf "$root/.deps/go.tar.gz" -C "$root/.deps"
fi
if [[ "$("$root/.deps/go/bin/go" version)" != "go version go1.26.10 linux/amd64" ]]; then
  echo "This bootstrap pins Go 1.26.10 for Linux amd64" >&2; exit 1
fi
cd "$root"
if [[ ! -f .deps/openssl/lib/libcrypto.a ]]; then ./tools/bootstrap-openssl.sh; fi
cmake --preset local
cmake --build --preset local --parallel "${PQCOIN_BUILD_JOBS:-4}"
cd node
../.deps/go/bin/go mod verify
../.deps/go/bin/go build -mod=readonly -p "${PQCOIN_BUILD_JOBS:-4}" -trimpath -o ../build/pqcoind .
echo "Built $root/build/pqcoind"
