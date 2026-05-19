"""Challenge generation + verification logic (bittensor-free path via protocol stubs)."""

import sys
import types
import pytest

# Stub `bittensor` so we can import `protocol` without the full SDK.
if "bittensor" not in sys.modules:
    bt_stub = types.ModuleType("bittensor")

    class _Synapse:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    bt_stub.Synapse = _Synapse
    sys.modules["bittensor"] = bt_stub


def test_canonical_cert_bytes_only_needed_keys():
    from gitlawbnet.utils.cert import canonical_cert_bytes
    out = canonical_cert_bytes({
        "new_sha": "n", "node_did": "nd", "old_sha": "o",
        "pusher_did": "p", "ref_name": "r", "repo_id": "rid", "issued_at": "t",
        "extra": "ignored",
    })
    assert b"extra" not in out
    assert out == b'{"new":"n","node":"nd","old":"o","pusher":"p","ref":"r","repo_id":"rid","ts":"t"}'


def test_gossip_verify_counts_intersection():
    from gitlawbnet.validator.challenges import make_gossip_challenge, verify_gossip

    req = make_gossip_challenge(["a", "b", "c", "d"])
    resp_cls = type(req)
    resp = resp_cls(event_ids=["a", "b", "c", "d"])
    resp.seen_event_ids = ["b", "c", "z"]   # 'z' is not in request, ignored
    v = verify_gossip(req, resp)
    assert v.sent == 4 and v.seen == 2


def test_storage_verify_ignores_unverifiable_truth():
    from gitlawbnet.validator.challenges import (
        expected_storage_digest, make_storage_challenge, verify_storage,
    )

    req = make_storage_challenge(["cid1", "cid2", "cid3"])
    resp_cls = type(req)
    truths = [b"block1", None, b"block3"]   # validator couldn't fetch cid2
    correct_digests = [
        expected_storage_digest(req.salt, b"block1"),
        "0" * 64,
        expected_storage_digest(req.salt, b"block3"),
    ]
    resp = resp_cls(cids=req.cids, salt=req.salt)
    resp.digests = correct_digests
    v = verify_storage(req, resp, truths)
    assert v.requested == 2 and v.proven == 2   # cid2 dropped from both
