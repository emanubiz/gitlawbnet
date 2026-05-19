# Incentive mechanism — Gitlawbnet

This document describes **what miners are paid for**, **how scores become
weights**, and **how the design resists known attacks** on a subnet where
the "useful work" is operating a real `gitlawb-node`.

## 1. Scoring formula

Every validator forward pass produces, per miner UID, a `ChallengeOutcome`
that is collapsed to a composite score `s ∈ [0, 1]`:

```
s = 0.35·uptime + 0.25·storage + 0.20·latency + 0.20·gossip
```

| Component | Source | Meaning |
|-----------|--------|---------|
| `uptime`  | answered/sent ratio across all challenge types | Is the miner reachable at all? |
| `storage` | proven/requested CIDs (salted SHA-256 digest match) | Is the miner really pinning the IPFS objects it claims? |
| `latency` | median fetch time vs. (target_ms=500, max_ms=5000) | Is the miner serving git efficiently? |
| `gossip`  | events received within deadline, +small peer-count bonus | Is the miner an active libp2p mesh participant? |

Per-UID scores are blended into history by an EMA (`alpha=0.1` by default)
in `validator/scoring.py:ScoreBook`. The weight vector emitted on chain
is the EMA vector divided by its sum.

## 2. Why each challenge is hard to fake

* **Handshake** — A miner that hasn't actually run a gitlawb-node can't
  produce a `did:gitlawb:<pk>` whose Ed25519 key signs the validator's
  fresh nonce. We verify `verify_signature(did, nonce, sig)` and check
  the pubkey matches the DID body. No node → no DID → uptime=0.

* **Storage proof of pinning** — Validators salt every challenge with a
  fresh `secrets.token_hex(32)` and ask for `sha256(salt ‖ block_bytes)`.
  Pre-computed answers are useless. The validator independently fetches
  the same CID from a public IPFS gateway to compute the expected
  digest, so even a miner sitting on the bytes can't lie about them.

* **Latency** — Validator measures its own fetch wall-clock against the
  same repo URL. A miner claiming `fetch_ms` < `0.75 * validator_ms` is
  flagged implausible and the latency sample is discarded. This punishes
  miners that just return constants.

* **Gossip propagation** — The node has no public publish endpoint
  (verified in `crates/gitlawb-node/src/api/repos.rs`: `publish_ref_update`
  is only called from inside `git_receive_pack`). So we use a **passive
  probe**: the validator polls `GET /api/v1/events/ref-updates` on a
  trusted reference node to collect ~20 fresh UUID `event_id`s from the
  last 10 minutes, then asks each miner which of those IDs its own node
  has in its `received_ref_updates` table. A miner not subscribed to
  `gitlawb/ref-updates/v1` will have zero overlap; a healthy mesh
  participant will have ~all of them.

* **Signed ref-update certificates** — The miner must serve real
  certificates signed by `node_did` over the canonical bytes
  `{"new","node","old","pusher","ref","repo_id","ts"}` (alphabetical key
  order, compact separators, base64url-no-pad Ed25519 signature). This
  is verified against the exact same code path as the node — see
  `tests/test_cert.py` which validates a live cert pulled from
  `node.gitlawb.com`. Forging requires the node's private key.

## 3. Edge cases the design handles

| Risk | Mitigation |
|------|------------|
| Miner answers handshake but stubs the others | Handshake counts for 1 of N challenges → uptime stays low. Storage/latency/gossip return 0. Composite ≤ 0.35. |
| Validator can't fetch a "truth" CID itself | `verify_storage` decrements `requested` for that CID — it doesn't penalise the miner for the validator's own connectivity. |
| Network spike causes one bad epoch | EMA with alpha=0.1 limits damage; ~30 epochs to fully recover or fall. |
| Brand-new miner UID just registered | Starts at score 0 (deregistered after immunity_period if it never proves anything). |
| Sybil farm registering many hotkeys | Each hotkey needs its own gitlawb-node DID, IPFS bandwidth, and registration burn. The marginal cost is real Rust/IPFS infrastructure, not just a Python process. |
| Replay of last epoch's signatures | Nonces and salts are fresh on every challenge; signatures bind to those nonces. |
| Validator collusion | Subnet inherits Yuma consensus — outlier validators get their weights clipped. Owner can also set `min_stake_required_to_set_weights`. |

## 4. Scaling considerations

* **Sample size**: `--neuron.sample_size 32` works for small networks
  (<256 miners). Once the metagraph exceeds ~512 miners, raise it or
  stratify the sample so every miner is challenged at least every few
  epochs.
* **Seed corpus**: `SEED_CIDS` / `SEED_REPOS` in
  `gitlawbnet/validator/forward.py` should grow over time and rotate.
  Hard-coding a tiny corpus turns the challenge into a fixed exam that
  miners can pre-cache; rotating from the live network forces real
  pinning behaviour.
* **Gossip publish sidecar**: The validator currently *expects* the
  challenge event to be published out-of-band by the validator's own
  gitlawb-node. A future revision should bundle a small publisher that
  the validator drives via `/v1/gossip/publish` (TODO in
  `forward.py`).
* **Cross-validator agreement**: All validators draw `SEED_CIDS` from
  the same on-chain registry (TODO: implement) so their score vectors
  converge under Yuma. Until that registry exists, the seed list is
  configured statically and validator operators should coordinate on
  it.

## 5. What the owner (you) needs to monitor

* `set_weights` succeeded each epoch (look for `set_weights result:`).
* The EMA score distribution isn't collapsing to a single miner
  (degenerate consensus).
* The validators are seeing >50% of miners answer the handshake; if
  not, your seed gossip topics or CIDs are stale.
* Subnet registration fee hasn't deregistered the owner hotkey.
