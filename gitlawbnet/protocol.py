"""
Wire protocol between validators and miners on Gitlawbnet.

Aligned with the real gitlawb-node REST API (see `utils/gitlawb_client.py`).
Five Synapse types, each carrying a `miner_did` + Ed25519 `miner_signature`
over a deterministic challenge digest so the validator can bind the
Bittensor hotkey to a `did:key:z...` identity.
"""

from __future__ import annotations

from typing import List, Optional

import bittensor as bt
from pydantic import Field


class IdentityHandshake(bt.Synapse):
    """One per epoch: pin the miner's `did:key:z...` and prove control of it."""

    # Request
    nonce: str = Field(..., description="32 random bytes, hex-encoded; the miner must sign these raw bytes")

    # Response
    miner_did: Optional[str] = Field(default=None, description="did:key:z... reported by the node at GET /")
    miner_signature: Optional[str] = Field(
        default=None,
        description="Ed25519 signature over bytes.fromhex(nonce) — base64url-no-pad (matches Rust sign_b64)",
    )
    node_version: Optional[str] = None
    node_p2p_peer_id: Optional[str] = Field(
        default=None, description="libp2p PeerId reported at GET /api/v1/p2p/info",
    )

    def deserialize(self) -> Optional[str]:
        return self.miner_did


class StorageChallenge(bt.Synapse):
    """Proof-of-storage over a set of IPFS CIDs the miner should be serving."""

    # Request
    cids: List[str] = Field(..., description="CIDv1(raw, sha2-256) git object addresses")
    salt: str = Field(..., description="32 random bytes, hex-encoded")

    # Response
    digests: Optional[List[str]] = Field(
        default=None,
        description="For each cid, hex(sha256(bytes.fromhex(salt) || raw_block_bytes)). Sentinel '0'*64 means not pinned.",
    )
    pin_count: Optional[int] = Field(default=None, description="GET /api/v1/ipfs/pins → count")
    miner_did: Optional[str] = None
    miner_signature: Optional[str] = Field(
        default=None,
        description="Sign sha256(bytes.fromhex(salt) || ','.join(digests).encode())",
    )

    def deserialize(self) -> Optional[List[str]]:
        return self.digests


class LatencyChallenge(bt.Synapse):
    """Time how long the miner's node takes to serve the ref list of a repo."""

    # Request
    owner: str = Field(..., description="repo owner short name or DID")
    repo: str = Field(..., description="repo name")

    # Response
    head_sha: Optional[str] = Field(default=None, description="SHA of any ref tip returned by /api/v1/repos/{owner}/{repo}/refs")
    fetch_ms: Optional[float] = None
    bytes_transferred: Optional[int] = None
    miner_did: Optional[str] = None
    miner_signature: Optional[str] = Field(
        default=None, description="Sign head_sha.encode()",
    )

    def deserialize(self) -> Optional[str]:
        return self.head_sha


class GossipChallenge(bt.Synapse):
    """Verify the miner's node is subscribed to `gitlawb/ref-updates/v1`.

    The node only publishes RefUpdateEvents on real pushes (no injection
    endpoint — verified in `crates/gitlawb-node/src/api/repos.rs`). So we
    use a passive probe:

    1. Validator polls `GET /api/v1/events/ref-updates` on a trusted
       reference node and picks a recent event_id (received in the last
       ~5 minutes).
    2. Validator asks the miner to confirm having received the same event.
    3. The miner queries its OWN `/api/v1/events/ref-updates?limit=200`
       and checks whether `event_id` is present.

    Score = miner is actually in the gossipsub mesh AND keeping up with it.
    """

    # Request
    event_ids: List[str] = Field(..., description="Recent gossip event UUIDs known to be propagating")

    # Response
    seen_event_ids: Optional[List[str]] = Field(
        default=None, description="Subset of event_ids the miner's node has in its received_ref_updates",
    )
    peer_count: Optional[int] = Field(default=None, description="GET /api/v1/peers → count")
    miner_did: Optional[str] = None
    miner_signature: Optional[str] = Field(
        default=None, description="Sign hashlib.sha256(','.join(sorted(event_ids)).encode()).digest()",
    )

    def deserialize(self) -> Optional[List[str]]:
        return self.seen_event_ids


class RefIntegrityChallenge(bt.Synapse):
    """Fetch and verify a signed ref-update certificate the miner serves."""

    # Request
    owner: str
    repo: str

    # Response — mirrors the node's certificate JSON shape exactly so the
    # validator can verify signatures against pusher_did + node_did.
    certificates: Optional[List[dict]] = Field(
        default=None,
        description=(
            "GET /api/v1/repos/{owner}/{repo}/certs → list of "
            "{id, repo_id, ref_name, old_sha, new_sha, pusher_did, node_did, signature, issued_at}"
        ),
    )
    miner_did: Optional[str] = None

    def deserialize(self) -> Optional[List[dict]]:
        return self.certificates
