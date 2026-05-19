# Gitlawbnet — Bittensor subnet for the gitlawb decentralised git network

Gitlawbnet incentivises operators who run **real `gitlawb-node` instances** —
the Rust daemon that powers [gitlawb.com](https://gitlawb.com): a
decentralised git platform where repositories are pinned on IPFS,
identities are Ed25519-backed `did:key:z…` DIDs (with optional
`did:gitlawb:` DHT-anchored aliases), and nodes synchronise via libp2p
Gossipsub on the topic `gitlawb/ref-updates/v1`.

Miners earn TAO emissions by **actually serving the network**. Validators
periodically challenge each miner along five axes (handshake, storage
proof, latency, gossip mesh participation, ref-cert integrity), aggregate
the results into a single composite score, and submit weights on chain
via Yuma consensus. The subnet owner receives the standard ~18% Yuma
owner cut.

```
┌──────────────────────────────────────────────────────────────────┐
│  Bittensor (finney) ── netuid N ── Yuma consensus                │
│        ▲                              ▲                          │
│  set_weights()                      axon                         │
│        │                              │                          │
│  ┌─────┴─────┐    5 Synapse     ┌─────┴────────┐                 │
│  │ Validator │───challenges────▶│  Miner (this)│                 │
│  └─────┬─────┘                  └──────┬───────┘                 │
│        │ HTTP                          │ HTTP :7545              │
│        ▼                               ▼                         │
│  ┌─────────────┐  gossip mesh   ┌─────────────┐  libp2p :7546    │
│  │ gitlawb-node│◀──────────────▶│ gitlawb-node│──────────────▶   │
│  │ (validator) │                │  (miner)    │  gitlawb network │
│  └─────────────┘                └─────────────┘                  │
└──────────────────────────────────────────────────────────────────┘
```

## Repo layout

```
gitlawbnet/
├── gitlawbnet/                  # python package
│   ├── protocol.py              # 5 Synapse classes (wire protocol)
│   ├── base/neuron.py           # wallet / subtensor / metagraph bootstrap
│   ├── validator/
│   │   ├── challenges.py        # generate + verify challenges (pure-Python)
│   │   ├── scoring.py           # 35/25/20/20 composite, EMA, sybil mask, save/load
│   │   ├── forward.py           # one challenge round via dendrite
│   │   └── seed_corpus.py       # loader for seed_corpus.json
│   ├── miner/handlers.py        # 5 axon handlers, one per Synapse
│   ├── mock/gitlawb_node.py     # aiohttp mock — offline testing
│   └── utils/
│       ├── config.py            # add_args / build_config (template pattern)
│       ├── did.py               # did:key + did:gitlawb parsing, Ed25519 verify
│       ├── cert.py              # canonical ref-cert bytes (sorted-keys JSON)
│       └── gitlawb_client.py    # async HTTP client for gitlawb-node
├── neurons/
│   ├── validator.py             # `python neurons/validator.py …`
│   └── miner.py                 # `python neurons/miner.py …`
├── scripts/
│   ├── register_subnet.sh       # mainnet registration (owner)
│   ├── refresh_seed.py          # regenerate seed_corpus.json
│   ├── run_validator.sh         # convenience wrapper
│   ├── run_miner.sh             # convenience wrapper
│   └── smoke_test.py            # end-to-end loopback test (mock node + real bittensor)
├── tests/                       # 23 tests, including live-data verification
├── docs/
│   ├── TESTNET_RUNBOOK.md       # step-by-step pre-mainnet flow
│   ├── VERIFICATION_REPORT.md   # what's verified vs assumed (for reviewers)
│   └── INCENTIVES.md            # scoring, edge cases, sybil resistance
├── seed_corpus.json             # validator's "exam" — rotate weekly
├── min_compute.yml
├── pyproject.toml
└── requirements.txt
```

## Installation

```bash
git clone https://github.com/<you>/gitlawbnet
cd gitlawbnet
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mock]"          # ~5 min, pulls bittensor>=8.5
pytest tests/                          # → 23 passed
python scripts/smoke_test.py           # → ✅ ALL SMOKE CHECKS PASSED
```

The smoke test spawns an in-process mock `gitlawb-node` and exercises
the full pipeline (handshake / storage / latency / gossip / ref-cert /
scoring / sybil detection) against the real bittensor 8.x SDK. **Always
run it before touching testnet.**

## Quickstart — miner

You need: a funded Bittensor coldkey + hotkey, and a healthy `gitlawb-node`
on the same machine (or reachable on the LAN).

### 1. Run a gitlawb-node

```bash
# See https://github.com/Gitlawb/node for build instructions.
export DATABASE_URL=postgres://...
export GITLAWB_PINATA_JWT=...
export GITLAWB_BOOTSTRAP_PEERS=/dnsaddr/node.gitlawb.com/...
gitlawb-node serve                     # :7545 (HTTP) + :7546 (libp2p)
curl http://127.0.0.1:7545/health      # → {"status":"ok"}
curl http://127.0.0.1:7545/ | jq .did  # → did:key:z6Mk…
```

The node mints (or loads) its Ed25519 identity at `~/.gitlawb/identity.pem`
and exposes it at `GET /`. The miner process loads the **same key** from
disk to sign challenge nonces — there is no `/v1/sign` endpoint on the
node.

### 2. Register a hotkey on the subnet

```bash
btcli subnet register \
    --subtensor.network finney \
    --netuid <NETUID> \
    --wallet.name <coldkey> --wallet.hotkey <hotkey>
```

### 3. Launch the miner

```bash
export SUBNET_NETUID=<NETUID>
export WALLET_NAME=<coldkey>
export WALLET_HOTKEY=<hotkey>
export NETWORK=finney
export GITLAWB_NODE=http://127.0.0.1:7545
export GITLAWB_KEY=$HOME/.gitlawb/identity.pem   # PKCS#8 PEM or raw 32-byte seed
./scripts/run_miner.sh
```

The miner exposes a Bittensor axon, registers it on chain via
`axon.serve(...)`, and forwards each incoming challenge to the local
gitlawb-node. The signing key path must correspond to the DID the node
reports at `GET /` — otherwise the validator's handshake verification
will fail and the miner scores 0 on uptime.

### 4. Stay healthy

* Keep your gitlawb-node `/health` returning 200 and pinning the CIDs
  validators are seeding (see `seed_corpus.json`).
* Make sure your node is subscribed to `gitlawb/ref-updates/v1` and has
  active libp2p peers (check `GET /api/v1/p2p/info` and `/api/v1/peers`).
* Watch logs for `blacklist` rejections — they usually mean
  `--blacklist.min_stake` is set too high for your validators.

## Quickstart — validator

The validator should run its own `gitlawb-node` as the trusted source for:
- IPFS truth blocks (validator's `storage` challenge cross-checks)
- recent gossip event IDs (passive probe — validator polls
  `GET /api/v1/events/ref-updates` and asks miners which ones they've seen)

```bash
export SUBNET_NETUID=<NETUID>
export WALLET_NAME=<coldkey>
export WALLET_HOTKEY=<validator_hotkey>
export NETWORK=finney
export GITLAWB_NODE=http://127.0.0.1:7545   # the validator's own node
./scripts/run_validator.sh
```

Rotate the seed corpus weekly so miners can't pre-cache answers:

```bash
python scripts/refresh_seed.py --repos 10 --cids 100 --out seed_corpus.json
```

You need stake above the subnet's `min_stake_required_to_set_weights`
to have weight submissions accepted (the validator falls back to the
public IPFS gateways for truth blocks if `--gitlawb.node_url` is empty,
but always run your own node in production).

## Testnet first

Before mainnet, follow **`docs/TESTNET_RUNBOOK.md`** end-to-end. It
covers faucet, wallets, registration, running validator + miner against
either the mock node or a real gitlawb-node, and verifying via
`btcli subnet metagraph`. The most common failure modes have a
troubleshooting table at the bottom.

## Subnet owner (mainnet)

1. Read `docs/INCENTIVES.md` end-to-end before mainnet registration.
2. Verify all 23 tests + the smoke test pass on your machine.
3. Top up the owner coldkey with `lock_cost` + ~50 TAO buffer
   (currently hundreds of TAO, dynamic — check via
   `btcli subnet lock_cost --subtensor.network finney`).
4. Run `./scripts/register_subnet.sh` (walks through `subnet create` +
   `subnet register` for the owner hotkey).
5. Set sane hyperparameters (`immunity_period`, `min_allowed_weights`,
   `max_weights_limit`) — examples in the register script.
6. Announce in `#subnets` on the Bittensor Discord and in the gitlawb
   community channels.

Owner emission share: **~18%** of TAO minted on this netuid, paid to the
registering coldkey as long as the netuid stays active. (Yuma split:
~18% owner, ~41% validators, ~41% miners — exact percentages depend on
current dTAO / consensus parameters.)

## Status

- ✅ All endpoints verified against `gitlawb-node` v0.3.8 source
- ✅ Cert canonical bytes verified against a real cert from
  `node.gitlawb.com`
- ✅ Sybil detection (DID collision across hotkeys)
- ✅ State persistence (EMA scores survive validator restart)
- ✅ Mock gitlawb-node + offline smoke test
- ✅ 23 tests pass, including real bittensor 8.5.2 integration
- ⏳ Testnet wiring (chain calls) not yet exercised — see TESTNET_RUNBOOK

## Licence

MIT.
