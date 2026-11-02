# Reused implementations

The node links the unmodified CometBFT source at commit
`6aef1e12197f587d1e85bc4cf3b3db4bf8d1be43`, from the sibling reference checkout.
Its Apache-2.0 license and upstream NOTICE are retained in `cometbft/`.
The build script checks the exact revision and refuses tracked source changes.
No consensus state-machine code is translated or copied into the C++ ledger.
The Go module checksum file pins the dependency graph, including Cloudflare CIRCL
used by CometBFT's ML-DSA-65 validator keys. OpenSSL supplies the native C++ crypto.

Other reference repositories informed the design; their source is not linked.
This is a local source build. Any distribution must also preserve the notices
and licenses of the complete linked dependency graph, including OpenSSL.
