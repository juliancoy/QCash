# QCash experimental testnet deployment

QCash is an experimental chain, with the unresolved findings in
[SECURITY_REVIEW.md](SECURITY_REVIEW.md). Hosted access does not turn this into
an audited mainnet. There is no exchange rate, redemption or real-funds promise.

After building, testing, committing and pushing the reviewed revision, run:

```sh
python3 tools/deploy_testnet.py
```

This installs two bounded user systemd services, `qcash-testnet-node` and
`qcash-testnet-gateway`, from immutable revision directories under
`~/.local/share/qcash/releases/`. Private persistent data lives in
`~/.local/state/qcash-testnet/`, with owner-only permissions. The installer
refuses a dirty checkout or a revision different from remote `main`.

The native peer listens on loopback port 29700. The HTTP gateway listens on
loopback port 29702 and requires the private `gateway-token` file. Publish it
through a managed HTTPS tunnel, preserving existing ingress rules. Set the
portal's `QCASH_GATEWAY_URL`, exact `QCASH_CHAIN_ID`, and secret
`QCASH_GATEWAY_TOKEN` separately from its code release. Browser credentials are
never forwarded to the gateway. Unauthorized direct gateway requests return 401.
The portal remains the public signed-transaction adapter.

Each service has a 256 MiB memory ceiling, a 10% CPU quota and 32-task limit.
Mining waits at least 30 seconds between blocks, a local policy that changes no
consensus bytes or chain identity. Nodes restart every 20 hours to renew their
24-hour certificates with the same pinned transport key. The journal survives;
pending submissions are volatile and can be retried using the same signed bytes.
Watch resource use, journal size and replay time; full-state/fork growth remains
an experimental limitation. These limits protect the host by failing closed,
not by guaranteeing indefinite testnet availability.

The host has a separate CLI mining wallet, `miner.pem`, for testnet rewards;
its public address is recorded in the private deployment receipt. The portal
and gateway cannot sign for browser wallets. No automatic faucet, custody,
browser import of that CLI key, or automatic reward distribution is provided.
Keep that key and all node homes out of source control and logs.

Check services using `systemctl --user status qcash-testnet-node
qcash-testnet-gateway`; inspect the private deployment receipt for source commit,
binary digest and chain ID. Public portal `/api/org/qcash/network` checks are
read-only. Never run authenticated fixture tests against production.

For rollback, stop the gateway, repoint the services to a retained compatible
revision, and restart. Preserve node data and transport pins. Changing the chain
configuration creates another network and must not be presented as a rollback.
Portal migration 0130 has its own review, backup and restore rules in the
OrgPortal repository; binary rollback does not reverse a database migration.
