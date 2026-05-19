"""End-to-end (offline) test: mock gitlawb-node + GitlawbClient + miner handlers
+ validator verifiers. Confirms the full data path works without chain or
network.
"""

import asyncio
import hashlib
import os
import tempfile

import pytest
from nacl.signing import SigningKey

# Stub bittensor so we can import protocol/handlers without the SDK.
import sys, types
if "bittensor" not in sys.modules:
    bt_stub = types.ModuleType("bittensor")
    class _Synapse:
        def __init__(self, **kw): self.__dict__.update(kw)
    bt_stub.Synapse = _Synapse
    class _Log:
        def __getattr__(self, _): return lambda *a, **kw: None
    bt_stub.logging = _Log()
    sys.modules["bittensor"] = bt_stub

from gitlawbnet.mock.gitlawb_node import MockGitlawbNode
from gitlawbnet.utils.gitlawb_client import GitlawbClient
from gitlawbnet.validator import challenges as ch
from gitlawbnet.validator.scoring import (
    ChallengeOutcome, ScoreBook, composite_score,
)


@pytest.mark.asyncio
async def test_full_pipeline_against_mock_node():
    sk = SigningKey.generate()
    node = MockGitlawbNode(keypair=sk)
    blob = b"hello gitlawbnet integration"
    cid = "bafkreimock" + hashlib.sha256(blob).hexdigest()[:38]
    node.add_block(cid, blob)
    node.add_ref("alice", "repo", "refs/heads/main", "a" * 40, cid)
    cert = node.add_cert(repo_id="alice/repo", ref_name="refs/heads/main", new_sha="a" * 40)
    eid = node.add_event("alice/repo", "refs/heads/main", "a" * 40)

    url = await node.start(port=0)
    try:
        c = GitlawbClient(url)

        # 1. Identity/health
        assert await c.health()
        info = await c.node_info()
        assert info["did"] == node.did

        # 2. Storage challenge round-trip
        st_req = ch.make_storage_challenge([cid])
        # miner side: hash with salt
        blk = await c.get_ipfs_block(cid)
        salt = bytes.fromhex(st_req.salt)
        from gitlawbnet.protocol import StorageChallenge
        st_resp = StorageChallenge(cids=[cid], salt=st_req.salt)
        st_resp.digests = [hashlib.sha256(salt + blk).hexdigest()]
        v = ch.verify_storage(st_req, st_resp, [blk])
        assert v.proven == 1 and v.requested == 1

        # 3. Cert verification — must succeed because the mock signs properly
        from gitlawbnet.protocol import RefIntegrityChallenge
        ref_resp = RefIntegrityChallenge(owner="alice", repo="repo")
        ref_resp.certificates = [cert]
        assert ch.verify_ref_certificates(ref_resp) is True

        # 4. Gossip events probe
        events = await c.list_ref_updates(limit=10)
        assert any(e["id"] == eid for e in events)

        # 5. Refs latency
        meta = await c.fetch_repo_metadata("alice", "repo")
        assert meta.head_cid == "a" * 40
        assert meta.fetch_ms > 0

        await c.aclose()
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_scoring_state_roundtrip(tmp_path):
    book = ScoreBook(num_uids=4, alpha=0.5)
    book.hotkeys = ["h0", "h1", "h2", "h3"]
    book.record_did(0, "did:key:zA")
    book.record_did(1, "did:key:zA")  # same DID → Sybil
    book.record_did(2, "did:key:zB")
    perfect = ChallengeOutcome(
        challenges_sent=1, challenges_answered=1,
        cids_requested=1, cids_proven=1,
        latency_ms_samples=[100.0],
        gossip_sent=1, gossip_received_in_time=1, peer_count_avg=5.0,
    )
    book.update({0: perfect, 1: perfect, 2: perfect})
    # uid 0 and 1 share a DID → both penalised; uid 2 unaffected
    assert book.scores[0] == 0.0
    assert book.scores[1] == 0.0
    assert book.scores[2] > 0.0

    p = str(tmp_path / "state.npz")
    book.save(p)
    other = ScoreBook(num_uids=4, alpha=0.5)
    assert other.load(p) is True
    assert (other.scores == book.scores).all()
    assert other.uid_to_did == {0: "did:key:zA", 1: "did:key:zA", 2: "did:key:zB"}
