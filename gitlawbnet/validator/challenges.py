"""
Validator-side challenge generation + verification.

Pure-Python, no Bittensor imports — easy to unit-test against synthetic
miner responses.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from gitlawbnet.protocol import (
    GossipChallenge,
    IdentityHandshake,
    LatencyChallenge,
    RefIntegrityChallenge,
    StorageChallenge,
)
from gitlawbnet.utils.cert import canonical_cert_bytes
from gitlawbnet.utils.did import verify_signature


# ── handshake ───────────────────────────────────────────────────────────────
def make_handshake() -> IdentityHandshake:
    return IdentityHandshake(nonce=secrets.token_hex(32))


def verify_handshake(req: IdentityHandshake, resp: IdentityHandshake) -> bool:
    if not resp.miner_did or not resp.miner_signature:
        return False
    return verify_signature(resp.miner_did, bytes.fromhex(req.nonce), resp.miner_signature)


# ── storage ─────────────────────────────────────────────────────────────────
def make_storage_challenge(cids: Sequence[str]) -> StorageChallenge:
    return StorageChallenge(cids=list(cids), salt=secrets.token_hex(32))


def expected_storage_digest(salt_hex: str, block_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update(bytes.fromhex(salt_hex))
    h.update(block_bytes)
    return h.hexdigest()


@dataclass
class StorageVerification:
    proven: int
    requested: int


def verify_storage(
    req: StorageChallenge,
    resp: StorageChallenge,
    truth_blocks: List[Optional[bytes]],
) -> StorageVerification:
    """`truth_blocks[i]` is the byte content of `req.cids[i]` as fetched by
    the validator from an independent source (None if validator-side fetch
    failed — those CIDs are dropped from both numerator and denominator)."""
    if not resp.digests or len(resp.digests) != len(req.cids):
        return StorageVerification(proven=0, requested=len(req.cids))
    proven = 0
    requested = 0
    for i, truth in enumerate(truth_blocks):
        if truth is None:
            continue
        requested += 1
        if resp.digests[i] == expected_storage_digest(req.salt, truth):
            proven += 1
    return StorageVerification(proven=proven, requested=requested)


# ── latency ─────────────────────────────────────────────────────────────────
def make_latency_challenge(owner: str, repo: str) -> LatencyChallenge:
    return LatencyChallenge(owner=owner, repo=repo)


def latency_is_plausible(
    resp: LatencyChallenge,
    expected_head: Optional[str],
    validator_fetch_ms: float,
    tolerance: float = 0.25,
) -> bool:
    """Reject obvious lies. The miner can't claim a fetch that's
    impossibly faster than the validator's own measurement, and the
    returned head SHA must match what the validator sees from a trusted
    replica (when we have one)."""
    if resp.fetch_ms is None or resp.fetch_ms <= 0:
        return False
    if expected_head and resp.head_sha and resp.head_sha != expected_head:
        return False
    floor = validator_fetch_ms * (1.0 - tolerance)
    return resp.fetch_ms >= floor


# ── gossip ──────────────────────────────────────────────────────────────────
def make_gossip_challenge(event_ids: Sequence[str]) -> GossipChallenge:
    return GossipChallenge(event_ids=list(event_ids))


@dataclass
class GossipVerification:
    sent: int
    seen: int


def verify_gossip(req: GossipChallenge, resp: GossipChallenge) -> GossipVerification:
    """How many of the challenged event_ids did the miner actually report?"""
    if not resp.seen_event_ids:
        return GossipVerification(sent=len(req.event_ids), seen=0)
    requested = set(req.event_ids)
    seen = sum(1 for eid in resp.seen_event_ids if eid in requested)
    return GossipVerification(sent=len(req.event_ids), seen=seen)


# ── ref integrity ───────────────────────────────────────────────────────────
def make_ref_challenge(owner: str, repo: str) -> RefIntegrityChallenge:
    return RefIntegrityChallenge(owner=owner, repo=repo)


def verify_ref_certificates(resp: RefIntegrityChallenge) -> bool:
    """At least one cert in the list must have a valid Ed25519 signature
    by its `node_did` over the canonical bytes."""
    if not resp.certificates:
        return False
    for c in resp.certificates:
        sig = c.get("signature")
        node_did = c.get("node_did") or ""
        if not sig or not node_did:
            continue
        if verify_signature(node_did, canonical_cert_bytes(c), sig):
            return True
    return False


def now_ms() -> int:
    return int(time.time() * 1000)
