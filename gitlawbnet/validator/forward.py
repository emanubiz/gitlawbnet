"""
Validator forward pass: one full challenge cycle against a sample of miners.

All `dendrite()` interaction lives in this file. Challenge generation +
verification are imported from `challenges.py` (pure Python).
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Dict, List, Optional

import bittensor as bt
import httpx

from gitlawbnet.utils.gitlawb_client import GitlawbClient
from gitlawbnet.validator import challenges as ch
from gitlawbnet.validator.scoring import ChallengeOutcome
from gitlawbnet.validator.seed_corpus import SeedCorpus, load as load_corpus


PUBLIC_IPFS_GATEWAYS = [
    "https://ipfs.io",
    "https://dweb.link",
    "https://nftstorage.link",
]


async def _fetch_truth_block(
    clients: List[GitlawbClient], cid: str, http_client: httpx.AsyncClient
) -> Optional[bytes]:
    """Validator's own copy of a CID. Tries gitlawb trusted nodes first
    (fast, no rate limits), falls back to public IPFS gateways (slower
    but always-available — Pinata pins are mirrored on these gateways).
    """
    for client in clients:
        try:
            blk = await client.get_ipfs_block(cid)
            if blk is not None:
                return blk
        except httpx.HTTPError:
            continue
    for gw in PUBLIC_IPFS_GATEWAYS:
        try:
            r = await http_client.get(f"{gw}/ipfs/{cid}", follow_redirects=True, timeout=20.0)
            if r.status_code == 200:
                return r.content
        except httpx.HTTPError:
            continue
    return None


async def _trusted_head_sha(clients: List[GitlawbClient], owner: str, repo: str) -> Optional[str]:
    for client in clients:
        try:
            refs = await client.list_refs(owner, repo)
            if refs:
                return refs[0].get("sha") or refs[0].get("target")
        except httpx.HTTPError:
            continue
    return None


async def _recent_gossip_events(
    trusted_clients: List[GitlawbClient], limit: int = 100, max_age_s: float = 600.0
) -> List[str]:
    """Fetch the IDs of ref-update events the trusted nodes have received
    via gossipsub in the recent past. The validator uses these as the
    "exam" — a miner whose own node is in the mesh will have seen them too.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    ids: List[str] = []
    for client in trusted_clients:
        try:
            events = await client.list_ref_updates(limit=limit)
        except httpx.HTTPError:
            continue
        for e in events:
            ts = e.get("received_at") or e.get("timestamp")
            if not ts or not e.get("id"):
                continue
            try:
                # RFC3339 with nanoseconds → strip to microseconds for fromisoformat compat
                clean = ts[:26] + ts[-6:] if "+" in ts[20:] else ts
                age = (now - datetime.fromisoformat(clean.replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                age = 0
            if 0 <= age <= max_age_s:
                ids.append(e["id"])
        if ids:
            break
    # Dedupe preserving order, cap at 20 to keep synapse payload small
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
        if len(out) >= 20:
            break
    return out


async def _challenge_one_miner(
    dendrite: bt.dendrite,
    axon: bt.AxonInfo,
    corpus: SeedCorpus,
    truth_blocks: List[Optional[bytes]],
    timeout: float,
    trusted_clients: List[GitlawbClient],
    gossip_event_ids: List[str],
) -> ChallengeOutcome:
    out = ChallengeOutcome()
    from gitlawbnet.protocol import (
        GossipChallenge, IdentityHandshake, LatencyChallenge,
        RefIntegrityChallenge, StorageChallenge,
    )

    # 1. Handshake
    hs_req = ch.make_handshake()
    out.challenges_sent += 1
    hs_resp = await dendrite(axons=[axon], synapse=hs_req, deserialize=False, timeout=timeout)
    hs_resp = hs_resp[0] if hs_resp else None
    if hs_resp is None or not ch.verify_handshake(hs_req, hs_resp):
        bt.logging.debug(f"handshake failed for {axon.hotkey[:8]}")
        return out
    out.challenges_answered += 1
    out.miner_did = hs_resp.miner_did

    # 2. Storage
    if corpus.cids and any(b is not None for b in truth_blocks):
        st_req = ch.make_storage_challenge(corpus.cids)
        out.challenges_sent += 1
        out.cids_requested = sum(1 for b in truth_blocks if b is not None)
        st_resp = await dendrite(axons=[axon], synapse=st_req, deserialize=False, timeout=timeout)
        st_resp = st_resp[0] if st_resp else None
        if st_resp is not None and st_resp.digests:
            v = ch.verify_storage(st_req, st_resp, truth_blocks)
            out.cids_proven = v.proven
            out.challenges_answered += 1

    # 3. Latency
    if corpus.repos:
        target = random.choice(corpus.repos)
        lat_req = ch.make_latency_challenge(target.owner, target.repo)
        out.challenges_sent += 1
        # Validator's own fetch as a sanity floor.
        t0 = time.perf_counter()
        expected = await _trusted_head_sha(trusted_clients, target.owner, target.repo)
        validator_ms = (time.perf_counter() - t0) * 1000.0 or 1.0
        lat_resp = await dendrite(axons=[axon], synapse=lat_req, deserialize=False, timeout=timeout * 2)
        lat_resp = lat_resp[0] if lat_resp else None
        if lat_resp is not None and ch.latency_is_plausible(lat_resp, expected, validator_ms):
            out.latency_ms_samples.append(float(lat_resp.fetch_ms))
            out.challenges_answered += 1

    # 4. Gossip — passive probe against real network events
    if gossip_event_ids:
        gos_req = ch.make_gossip_challenge(gossip_event_ids)
        out.challenges_sent += 1
        out.gossip_sent = len(gossip_event_ids)
        gos_resp = await dendrite(axons=[axon], synapse=gos_req, deserialize=False, timeout=timeout * 2)
        gos_resp = gos_resp[0] if gos_resp else None
        if gos_resp is not None and gos_resp.seen_event_ids is not None:
            v = ch.verify_gossip(gos_req, gos_resp)
            out.gossip_received_in_time = v.seen
            out.peer_count_avg = float(gos_resp.peer_count or 0)
            out.challenges_answered += 1

    # 5. Ref integrity (bonus check folded into uptime — doesn't have its own weight).
    if corpus.repos:
        target = random.choice(corpus.repos)
        ref_req = ch.make_ref_challenge(target.owner, target.repo)
        out.challenges_sent += 1
        ref_resp = await dendrite(axons=[axon], synapse=ref_req, deserialize=False, timeout=timeout)
        ref_resp = ref_resp[0] if ref_resp else None
        if ref_resp is not None and ch.verify_ref_certificates(ref_resp):
            out.challenges_answered += 1

    return out


async def forward(self) -> Dict[int, ChallengeOutcome]:
    metagraph = self.metagraph
    n = int(metagraph.n)
    # Skip our own UID — can't dendrite to ourselves, and self-rewarding
    # would be ignored by Yuma anyway.
    candidates = [u for u in range(n) if u != self.uid]
    sample_size = min(self.config.neuron.sample_size, len(candidates))
    if sample_size == 0:
        return {}
    uids = random.sample(candidates, sample_size)

    corpus = load_corpus()
    trusted_clients = [GitlawbClient(url) for url in corpus.trusted_nodes]

    # The validator's own node is preferred as the trusted source (it's in
    # the same data center as the validator, so latency-floor measurements
    # are more accurate). Fall back to the public seed nodes if not set.
    own_node_url = ""
    if hasattr(self.config, "gitlawb"):
        own_node_url = getattr(self.config.gitlawb, "node_url", "") or ""
    if own_node_url:
        trusted_clients.insert(0, GitlawbClient(own_node_url))

    truth_blocks: List[Optional[bytes]] = []
    http_client = httpx.AsyncClient()
    if corpus.cids:
        truth_blocks = await asyncio.gather(
            *[_fetch_truth_block(trusted_clients, c, http_client) for c in corpus.cids]
        )

    gossip_event_ids = await _recent_gossip_events(trusted_clients)

    timeout = float(self.config.neuron.challenge_timeout)

    async def one(uid: int):
        axon = metagraph.axons[uid]
        try:
            outcome = await _challenge_one_miner(
                self.dendrite, axon, corpus, truth_blocks, timeout,
                trusted_clients, gossip_event_ids,
            )
            return uid, outcome
        except Exception as exc:  # noqa: BLE001
            bt.logging.warning(f"uid {uid} challenge raised: {exc!r}")
            return uid, ChallengeOutcome()

    try:
        results = await asyncio.gather(*[one(uid) for uid in uids])
    finally:
        for c in trusted_clients:
            await c.aclose()
        await http_client.aclose()

    return dict(results)
