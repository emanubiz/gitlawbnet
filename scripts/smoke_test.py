#!/usr/bin/env python3
"""
End-to-end loopback smoke test — exercises the FULL stack:

  mock gitlawb-node ◀──── miner Handlers ──── bt.axon
        ▲                                       ▲
        │                                       │ dendrite
        │ HTTP                                  │
        ▼                                       ▼
  trusted source for ◀──── validator forward + scoring
  validator (same node)

This requires bittensor installed. Run BEFORE testnet to catch SDK drift,
wiring bugs, and Synapse schema mismatches without spending TAO.

Usage:
    python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import sys
import tempfile
from typing import List

import bittensor as bt
from nacl.signing import SigningKey

from gitlawbnet.miner.handlers import Handlers
from gitlawbnet.mock.gitlawb_node import MockGitlawbNode
from gitlawbnet.protocol import (
    GossipChallenge, IdentityHandshake, LatencyChallenge,
    RefIntegrityChallenge, StorageChallenge,
)
from gitlawbnet.utils.did import sign_b64url
from gitlawbnet.utils.gitlawb_client import GitlawbClient
from gitlawbnet.validator import challenges as ch
from gitlawbnet.validator.scoring import (
    ChallengeOutcome, ScoreBook, composite_score,
)


async def main() -> int:
    print("Starting mock gitlawb-node...")
    sk = SigningKey.generate()
    node = MockGitlawbNode(keypair=sk)
    blob = b"smoke test block"
    cid = "bafkreismoke" + hashlib.sha256(blob).hexdigest()[:38]
    node.add_block(cid, blob)
    node.add_ref("alice", "demo", "refs/heads/main", "a" * 40, cid)
    node.add_cert("alice/demo", "refs/heads/main", "a" * 40)
    eid = node.add_event("alice/demo", "refs/heads/main", "a" * 40)
    url = await node.start(port=0)
    print(f"  → listening on {url}, DID={node.did}")

    # Write the same key to disk so the miner handlers can load it.
    seed_path = os.path.join(tempfile.gettempdir(), "gitlawbnet_smoke.seed")
    with open(seed_path, "wb") as f:
        f.write(bytes(sk))
    print(f"  → seed written to {seed_path}")

    # Build miner handlers pointing at the mock node.
    client = GitlawbClient(url)
    handlers = Handlers(client, seed_path)
    assert handlers.miner_did == node.did, "DID mismatch between key and node"

    print("\n[1/5] handshake")
    hs_req = ch.make_handshake()
    hs_resp = await handlers.handshake(IdentityHandshake(nonce=hs_req.nonce))
    ok = ch.verify_handshake(hs_req, hs_resp)
    print(f"  handshake verify: {ok}    did={hs_resp.miner_did}")
    assert ok

    print("\n[2/5] storage")
    st_req = ch.make_storage_challenge([cid])
    st_resp = await handlers.storage(StorageChallenge(cids=st_req.cids, salt=st_req.salt))
    v = ch.verify_storage(st_req, st_resp, [blob])
    print(f"  proven {v.proven}/{v.requested}")
    assert v.proven == 1

    print("\n[3/5] latency")
    lat_req = ch.make_latency_challenge("alice", "demo")
    # The validator measures its OWN fetch first so we have a realistic floor.
    val_meta = await client.fetch_repo_metadata("alice", "demo")
    lat_resp = await handlers.latency(LatencyChallenge(owner="alice", repo="demo"))
    plausible = ch.latency_is_plausible(lat_resp, "a" * 40, validator_fetch_ms=val_meta.fetch_ms)
    print(f"  validator_ms={val_meta.fetch_ms:.2f}  miner_ms={lat_resp.fetch_ms:.2f}  plausible={plausible}")
    assert plausible

    print("\n[4/5] gossip")
    gos_req = ch.make_gossip_challenge([eid, "unknown-event-id"])
    gos_resp = await handlers.gossip(GossipChallenge(event_ids=gos_req.event_ids))
    gv = ch.verify_gossip(gos_req, gos_resp)
    print(f"  seen {gv.seen}/{gv.sent}  peer_count={gos_resp.peer_count}")
    assert gv.seen == 1

    print("\n[5/5] ref integrity")
    ref_req = ch.make_ref_challenge("alice", "demo")
    ref_resp = await handlers.ref_integrity(RefIntegrityChallenge(owner="alice", repo="demo"))
    ref_ok = ch.verify_ref_certificates(ref_resp)
    print(f"  cert verification: {ref_ok}  certs={len(ref_resp.certificates or [])}")
    assert ref_ok

    print("\n[scoring] composite")
    out = ChallengeOutcome(
        challenges_sent=5, challenges_answered=5,
        cids_requested=1, cids_proven=1,
        latency_ms_samples=[lat_resp.fetch_ms],
        gossip_sent=2, gossip_received_in_time=1, peer_count_avg=4.0,
        miner_did=node.did,
    )
    score = composite_score(out)
    print(f"  composite = {score:.3f}")
    assert 0.5 < score <= 1.0

    print("\n[sybil] DID collision detection")
    book = ScoreBook(num_uids=2, alpha=1.0)
    book.record_did(0, node.did)
    book.record_did(1, node.did)
    book.update({0: out, 1: out})
    print(f"  uid 0 score after sybil: {book.scores[0]} (expect 0)")
    print(f"  uid 1 score after sybil: {book.scores[1]} (expect 0)")
    assert book.scores[0] == 0.0 and book.scores[1] == 0.0

    await client.aclose()
    await node.stop()
    print("\n✅ ALL SMOKE CHECKS PASSED — wiring + handlers + scoring + sybil + cert verification all work.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
