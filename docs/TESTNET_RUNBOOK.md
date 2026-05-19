# Testnet runbook

Prereq: Ubuntu 22.04+/macOS, Python 3.10+, `git`, `curl`, ~2 GB disk.
Estimated total time: 60-90 min the first time, 10 min subsequent runs.

## 1. Environment

```bash
git clone https://github.com/<you>/gitlawbnet && cd gitlawbnet
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mock]"        # ~5 min, pulls bittensor + deps
pytest tests/                       # must show 22 passed
```

## 2. Smoke test — NO chain, NO real gitlawb-node (5 min)

This is the most important pre-flight check. It spawns an in-process mock
gitlawb-node and exercises every handler + validator verification path
with real bittensor 8.x classes.

```bash
python scripts/smoke_test.py
```

Expected last line: `✅ ALL SMOKE CHECKS PASSED — ...`

If this fails, do NOT attempt testnet — fix the bug locally first.

## 3. Bittensor wallets (10 min)

```bash
btcli wallet new_coldkey --wallet.name gitlawbnet_test
btcli wallet new_hotkey  --wallet.name gitlawbnet_test --wallet.hotkey owner_hk
btcli wallet new_hotkey  --wallet.name gitlawbnet_test --wallet.hotkey validator_hk
btcli wallet new_hotkey  --wallet.name gitlawbnet_test --wallet.hotkey miner_hk
```

Fund the coldkey from the testnet faucet. Recent btcli requires PoW:

```bash
btcli wallet faucet --subtensor.network test --wallet.name gitlawbnet_test
```

If the faucet is rate-limited or returns "no validator available", ask in
the Bittensor Discord channel `#testnet-faucet`.

## 4. Subnet creation on testnet (5 min)

```bash
btcli subnet lock_cost --subtensor.network test   # check current cost
btcli subnet create   --subtensor.network test --wallet.name gitlawbnet_test
btcli subnet list     --subtensor.network test    # find your new NETUID
```

Note the NETUID. Then register validator + miner hotkeys:

```bash
export NETUID=<your netuid>
btcli subnet register --subtensor.network test --netuid $NETUID \
    --wallet.name gitlawbnet_test --wallet.hotkey validator_hk
btcli subnet register --subtensor.network test --netuid $NETUID \
    --wallet.name gitlawbnet_test --wallet.hotkey miner_hk
```

## 5. Run the validator + miner against the mock node (15 min)

For testnet you can still use the mock gitlawb-node — the *Bittensor*
side is real, but the gitlawb-node side is simulated. This isolates
chain-related bugs from gitlawb-side bugs.

Terminal 1 — mock node:

```bash
python -m gitlawbnet.mock.gitlawb_node --port 17545
# Note the DID and the seed file path it prints.
```

Terminal 2 — miner:

```bash
source .venv/bin/activate
# Write a 32-byte seed for the miner to sign with — must match the mock
# node's DID. The mock writes the seed automatically; copy it:
cp /tmp/gitlawbnet_smoke.seed ~/.gitlawb/identity.seed   # or wherever

export SUBNET_NETUID=$NETUID
export WALLET_NAME=gitlawbnet_test
export WALLET_HOTKEY=miner_hk
export GITLAWB_NODE=http://127.0.0.1:17545
export GITLAWB_KEY=~/.gitlawb/identity.seed
export NETWORK=test
./scripts/run_miner.sh
```

Terminal 3 — validator:

```bash
source .venv/bin/activate
export SUBNET_NETUID=$NETUID
export WALLET_NAME=gitlawbnet_test
export WALLET_HOTKEY=validator_hk
export GITLAWB_NODE=http://127.0.0.1:17545
export NETWORK=test
./scripts/run_validator.sh
```

## 6. Verify it's working (after ~20 min = 1 epoch)

```bash
btcli subnet metagraph --subtensor.network test --netuid $NETUID
```

Look for:
- The miner UID has `Incentive > 0` (validator scored it)
- The validator UID has `Dividends > 0` (Yuma consensus accepted its weights)
- `Last update` is recent (within ~100 blocks for the validator)

Also check the validator logs for:
- `set_weights succeeded`
- per-round outcomes (`handshake failed for ...` should be rare or zero)

## 7. Run against a REAL gitlawb-node (optional, 1-2 hours setup)

When you're satisfied the bittensor side is healthy, you can swap the
mock for a real gitlawb-node. See `README.md` § "Quickstart — miner".
You'll need:

- Postgres 14+ (`docker run -d --name pg -e POSTGRES_PASSWORD=x postgres:14`)
- A Pinata account for `GITLAWB_PINATA_JWT`
- The `gitlawb-node` binary (`cargo build --release` in `github.com/Gitlawb/node`)

Restart the miner with `GITLAWB_NODE=http://127.0.0.1:7545`.

## 8. Hyperparameter tuning

Once the subnet runs cleanly, set sane defaults:

```bash
btcli sudo set --netuid $NETUID --subtensor.network test \
    --param immunity_period --value 7200
btcli sudo set --netuid $NETUID --subtensor.network test \
    --param min_allowed_weights --value 8
btcli sudo set --netuid $NETUID --subtensor.network test \
    --param max_weights_limit --value 65535
```

(Same commands work on `--subtensor.network finney` once you're on mainnet.)

## Common failures

| Symptom | Diagnosis | Fix |
|---|---|---|
| `set_weights failed: Invalid Transaction Custom error: 0` | `weights_rate_limit` exceeded | Lower validator's `--neuron.epoch_length` or wait |
| `set_weights failed: SetWeightsTooFast` | Same as above | Same |
| Miner shows `Incentive = 0` even after 1h | Validator never reached the miner OR scoring 0 | Check validator logs: `handshake failed` count; verify mock node is reachable from miner machine |
| Validator: `RuntimeError: hotkey not registered` | Skipped `subnet register` step | Run step 4 |
| `Pydantic validation error` on Synapse | Bittensor minor version drift | Pin `bittensor==8.5.2` (the version this was developed against) |
| `axon.attach` `AssertionError: must have signature ...` | blacklist_fn or priority_fn missing return type annotation | Already fixed in v0.1.0; if you customised handlers, add `-> Tuple[bool, str]` and `-> float` |

## Going to mainnet

Once 24h on testnet are stable:

1. Top up a mainnet coldkey with enough TAO to cover `btcli subnet lock_cost --subtensor.network finney` (currently hundreds of TAO, dynamic).
2. Run `./scripts/register_subnet.sh` — it walks through `subnet create` + `subnet register` for the owner hotkey.
3. Announce in Bittensor Discord `#subnets` and the gitlawb community channels.
4. The owner receives 18% of TAO emissions on the netuid via Yuma consensus, with the remaining 82% split between validators (41%) and miners (41%) per the canonical Yuma split.
