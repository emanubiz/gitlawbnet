"""
Miner-side handlers — one per Synapse.

The miner loads its Ed25519 seed locally (same file the gitlawb-node uses)
and signs challenge digests in-process. No `/v1/sign` round-trip is needed
because the protocol doesn't require the *node* to do the signing — only
that the same identity that the node reports at `GET /` can sign.

If you really want the key to never leave the node process, point
`--gitlawb.signing_key_path` at a separate key whose DID you've authorised
as a delegate (UCAN) of the node's DID.
"""

from __future__ import annotations

import hashlib
from typing import Optional

import bittensor as bt
from nacl.signing import SigningKey

from gitlawbnet.protocol import (
    GossipChallenge,
    IdentityHandshake,
    LatencyChallenge,
    RefIntegrityChallenge,
    StorageChallenge,
)
from gitlawbnet.utils.did import load_signing_key, sign_b64url
from gitlawbnet.utils.gitlawb_client import GitlawbClient


class Handlers:
    def __init__(self, client: GitlawbClient, signing_key_path: str):
        self.client = client
        self.signing_key: SigningKey
        self.miner_did: str
        self.signing_key, self.miner_did = load_signing_key(signing_key_path)
        self._cached_node_info: Optional[dict] = None
        self._cached_p2p_info: Optional[dict] = None

    def _sign(self, msg: bytes) -> str:
        return sign_b64url(self.signing_key, msg)

    async def _node_info(self) -> dict:
        if self._cached_node_info is None:
            self._cached_node_info = await self.client.node_info()
        return self._cached_node_info

    async def _p2p_info(self) -> dict:
        if self._cached_p2p_info is None:
            try:
                self._cached_p2p_info = await self.client.p2p_info()
            except Exception:
                self._cached_p2p_info = {"enabled": False}
        return self._cached_p2p_info

    # ── handshake ────────────────────────────────────────────────────────
    async def handshake(self, synapse: IdentityHandshake) -> IdentityHandshake:
        try:
            info = await self._node_info()
            # The miner's DID must match what the node reports — otherwise the
            # validator's storage/cert proofs (which trust the node's identity)
            # would be inconsistent with the hotkey binding.
            if info.get("did") != self.miner_did:
                bt.logging.warning(
                    f"signing key DID ({self.miner_did}) != node DID ({info.get('did')}); "
                    f"validator will reject the handshake"
                )
            p2p = await self._p2p_info()
            synapse.miner_did = self.miner_did
            synapse.miner_signature = self._sign(bytes.fromhex(synapse.nonce))
            synapse.node_version = info.get("version")
            synapse.node_p2p_peer_id = p2p.get("peer_id")
        except Exception as exc:
            bt.logging.warning(f"handshake handler failed: {exc!r}")
        return synapse

    # ── storage ──────────────────────────────────────────────────────────
    async def storage(self, synapse: StorageChallenge) -> StorageChallenge:
        try:
            digests: list[str] = []
            salt_bytes = bytes.fromhex(synapse.salt)
            for cid in synapse.cids:
                block = await self.client.get_ipfs_block(cid)
                if block is None:
                    digests.append("0" * 64)
                    continue
                h = hashlib.sha256()
                h.update(salt_bytes)
                h.update(block)
                digests.append(h.hexdigest())
            synapse.digests = digests
            synapse.miner_did = self.miner_did
            pin_count, _ = await self.client.list_pins()
            synapse.pin_count = pin_count
            payload = hashlib.sha256(salt_bytes + ",".join(digests).encode()).digest()
            synapse.miner_signature = self._sign(payload)
        except Exception as exc:
            bt.logging.warning(f"storage handler failed: {exc!r}")
        return synapse

    # ── latency ──────────────────────────────────────────────────────────
    async def latency(self, synapse: LatencyChallenge) -> LatencyChallenge:
        try:
            r = await self.client.fetch_repo_metadata(synapse.owner, synapse.repo)
            synapse.head_sha = r.head_cid
            synapse.fetch_ms = r.fetch_ms
            synapse.bytes_transferred = r.bytes_transferred
            synapse.miner_did = self.miner_did
            synapse.miner_signature = self._sign(r.head_cid.encode())
        except Exception as exc:
            bt.logging.warning(f"latency handler failed: {exc!r}")
        return synapse

    # ── gossip ───────────────────────────────────────────────────────────
    async def gossip(self, synapse: GossipChallenge) -> GossipChallenge:
        """Check which of the challenged event_ids our node has actually
        received via gossipsub (queries `received_ref_updates` table)."""
        try:
            events = await self.client.list_ref_updates(limit=200)
            seen_ids = {e.get("id") for e in events if e.get("id")}
            synapse.seen_event_ids = [eid for eid in synapse.event_ids if eid in seen_ids]
            peer_count, _ = await self.client.list_peers()
            synapse.peer_count = peer_count
            synapse.miner_did = self.miner_did
            payload = hashlib.sha256(",".join(sorted(synapse.event_ids)).encode()).digest()
            synapse.miner_signature = self._sign(payload)
        except Exception as exc:
            bt.logging.warning(f"gossip handler failed: {exc!r}")
        return synapse

    # ── ref integrity ────────────────────────────────────────────────────
    async def ref_integrity(self, synapse: RefIntegrityChallenge) -> RefIntegrityChallenge:
        try:
            certs = await self.client.list_certs(synapse.owner, synapse.repo)
            synapse.certificates = certs
            synapse.miner_did = self.miner_did
        except Exception as exc:
            bt.logging.warning(f"ref_integrity handler failed: {exc!r}")
        return synapse
