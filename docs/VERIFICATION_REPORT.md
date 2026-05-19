# Verification report

This document describes what has been verified, how, and what remains
unverified before mainnet launch. It is intended for reviewers from the
gitlawb and Bittensor ecosystems who want to audit our assumptions
without having to read the entire codebase.

Last updated: 2026-05-19.

## 1. gitlawb integration

Every endpoint the validator and miner call has been verified against
the `gitlawb-node` source (v0.3.8, MIT/Apache-2.0,
`github.com/Gitlawb/node`). The mapping lives in
`gitlawbnet/utils/gitlawb_client.py`.

| Operation                | HTTP route (verified)                              | Source file in gitlawb-node           |
|--------------------------|----------------------------------------------------|---------------------------------------|
| Health check             | `GET /health`                                      | `src/server.rs`                       |
| Node identity (DID, p2p) | `GET /`                                            | `src/server.rs::node_info`            |
| P2P info & topics        | `GET /api/v1/p2p/info`                             | `src/server.rs::p2p_info`             |
| Stats                    | `GET /api/v1/stats`                                | `src/server.rs::stats`                |
| IPFS block retrieval     | `GET /ipfs/{cid}`                                  | `src/api/ipfs.rs::get_by_cid`         |
| Pin enumeration          | `GET /api/v1/ipfs/pins`                            | `src/api/ipfs.rs::list_pins`          |
| Repo list                | `GET /api/v1/repos`                                | `src/api/repos.rs::list_repos`        |
| Branch refs              | `GET /api/v1/repos/{owner}/{repo}/refs`            | `src/api/repos.rs::list_refs`         |
| Ref certificates         | `GET /api/v1/repos/{owner}/{repo}/certs`           | `src/api/certs.rs::list_certs`        |
| Gossip events            | `GET /api/v1/events/ref-updates`                   | `src/api/events.rs::list_ref_updates` |
| Peer list                | `GET /api/v1/peers`                                | `src/api/peers.rs::list_peers`        |

No deprecated or speculative endpoints are referenced. The node has no
`/v1/sign` route, so the miner loads the same Ed25519 key file the node
loads (`~/.gitlawb/identity.pem` by default) and signs challenge nonces
in-process.

### Identity & signature format

Verified against `crates/gitlawb-core/src/{did,identity,cert}.rs`:

- **DID method**: canonical `did:key:z<multibase58btc(0xed01 ‖ pk)>`
  (Ed25519 multicodec). `did:gitlawb:` is also recognised as the
  DHT-anchored alias form.
- **Signature encoding**: base64url-no-pad (matches `Keypair::sign_b64`).
  Standard base64 is also accepted because RFC 9421 HTTP Signatures use
  it.
- **Ref-cert canonical bytes**: alphabetically-sorted JSON with compact
  separators of the seven fields `{new, node, old, pusher, ref, repo_id, ts}`.
  This format is derived from `src/cert.rs::issue_ref_certificate` —
  `serde_json::json!(…)` uses BTreeMap because `preserve_order` is not
  enabled in the workspace `Cargo.toml`. Verified end-to-end by
  `tests/test_cert.py::test_canonical_bytes_match_node_signature`,
  which reproduces the signature of a real cert pulled from
  `node.gitlawb.com` on 2026-05-18.

## 2. Bittensor integration

Aligned with `opentensor/bittensor-subnet-template` (canonical reference)
and verified against installed `bittensor==8.5.2`.

| Concern                          | Status                                                          |
|----------------------------------|-----------------------------------------------------------------|
| `bt.Synapse` Pydantic v2 schemas | 5 subclasses instantiate cleanly                                |
| `bt.axon().attach()` chaining    | Verified against 8.x — requires exact `synapse: X -> Tuple[bool, str]` signature on `blacklist_fn` and `-> float` on `priority_fn` (we generate one wrapper per Synapse subclass) |
| `bt.dendrite()` call pattern     | Matches template: `await dendrite(axons=[…], synapse=…, deserialize=False, timeout=…)` |
| `bt.subtensor.set_weights()`     | Returns `(result, msg)` tuple — unpacked correctly              |
| `process_weights_for_netuid`     | Called before `convert_weights_and_uids_for_emit` so subnet hyperparams (`max_weights_limit`, `min_allowed_weights`) are honoured |
| `bt.logging`                     | Configured via `bt.logging.set_config(config=…)` (the 8.x API)  |
| `metagraph.last_update[uid]`     | Used for both `should_sync_metagraph` and `should_set_weights`, matching template throttling |
| `weight_utils` import path       | Tried in this order: `bittensor.utils.weight_utils` then `bittensor.core.utils.weight_utils` for forward-compat |

These are covered by `tests/test_bittensor_smoke.py`, which is skipped
automatically when bittensor is not installed and runs four checks when
it is.

## 3. Incentive design

Composite score: `0.35·uptime + 0.25·storage + 0.20·latency + 0.20·gossip`,
EMA-blended (`α = 0.1` default).

### Sybil resistance

- The handshake binds a Bittensor hotkey to a `did:key:` identity via
  fresh-nonce Ed25519 signature.
- `ScoreBook.record_did(uid, did)` tracks the binding across rounds.
- `ScoreBook.sybil_mask()` returns the set of UIDs sharing a DID with
  any other UID; those UIDs receive score 0.
- A Sybil farmer would therefore need a distinct gitlawb-node process
  (with its own Postgres database, IPFS bandwidth, and libp2p peer ID)
  for every Bittensor hotkey they register. That is exactly the
  property gitlawbnet wants to incentivise.

### Truth-block independence

For storage proofs the validator fetches each CID from a trusted
gitlawb-node and falls back to the public IPFS gateways
(`ipfs.io`, `dweb.link`, `nftstorage.link`) so a miner cannot win by
lying about content the validator can independently verify.

### Neutral scoring on unprobed components

If the validator could not probe a particular dimension this round (no
truth blocks fetchable, no gossip events seen, no latency sample), the
miner receives a neutral 0.5 for that component instead of 0. This
prevents Yuma from being biased by transient validator-side
connectivity issues. The miner still needs to *actually answer* the
handshake to earn anything on uptime.

## 4. Test coverage

23 unit + integration tests in `tests/`. Categories:

- `test_did.py` — DID parsing for both methods, signature verification
  in both encodings, includes a smoke check against the real DID of
  `node.gitlawb.com`.
- `test_cert.py` — canonical-bytes reconstruction and Ed25519
  verification of a real cert from `node.gitlawb.com`.
- `test_challenges.py` — pure-Python challenge generation and
  verification logic.
- `test_scoring.py` — composite score, EMA, weight normalisation,
  neutral-on-unprobed semantics.
- `test_mock_node_integration.py` — full pipeline against an in-process
  mock gitlawb-node, plus Sybil detection and state round-trip.
- `test_bittensor_smoke.py` — real bittensor 8.x: Synapse
  instantiation, `axon.attach` signature, `weight_utils` import,
  config builder.

End-to-end smoke test: `scripts/smoke_test.py` spawns the mock node and
exercises every handler with bittensor installed. This is the
recommended pre-flight check before any testnet run.

## 5. What is not yet verified

These items require chain access and will be exercised in the testnet
phase before mainnet launch.

- **Actual `subtensor.set_weights` round-trip on chain.** The call is
  constructed and unit-tested but not yet executed against a live
  subnet.
- **`process_weights_for_netuid` against real hyperparams.** Same.
- **Gossip propagation timing in a busy mesh.** The current
  `max_age_s=600` filter in `validator/forward.py::_recent_gossip_events`
  is a parameter we will tune from real telemetry once the validator
  has seen a sampling of network activity.
- **Validator/miner Pydantic compatibility against future bittensor
  releases.** We pin `bittensor>=8.5,<9.0` and recommend `==8.5.2` for
  reproducibility.

## 6. Reproducing this report

```bash
git clone <repo> && cd gitlawbnet
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mock]"
pytest tests/                  # → 23 passed
python scripts/smoke_test.py   # → ✅ ALL SMOKE CHECKS PASSED
```

Live-data tests (`test_did.py::test_live_public_node_did_parses`,
`test_cert.py::test_canonical_bytes_match_node_signature`) only require
that the static fixtures match what `node.gitlawb.com` returns; they
don't make outbound HTTP at test time.
