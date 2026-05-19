"""
Async HTTP client for the real gitlawb-node REST surface.

Endpoint mapping (verified against `crates/gitlawb-node/src/server.rs` and
the `api/*.rs` handlers as of node v0.x, 2026-05):

    GET  /                                       → node_info (did, peer_id, version)
    GET  /health                                 → {"status":"ok"}
    GET  /api/v1/p2p/info                        → {enabled, peer_id, topics}
    GET  /api/v1/stats                           → {repos, agents, pushes, version}
    GET  /api/v1/ipfs/pins                       → {pins:[...], count}
    GET  /ipfs/{cid}                             → raw bytes (404 if not pinned)
    GET  /api/v1/repos                           → list of RepoResponse
    GET  /api/v1/repos/{owner}/{repo}            → RepoResponse
    GET  /api/v1/repos/{owner}/{repo}/refs       → ref list
    GET  /api/v1/repos/{owner}/{repo}/certs      → {certificates:[...]}
    GET  /api/v1/repos/{owner}/{repo}/certs/{id} → single cert
    GET  /api/v1/peers                           → {peers:[...], count}
    POST /api/v1/peers/announce  (HTTP-Sig)      → {status, node_did, ...}
    GET  /api/v1/peers/{did}/ping                → {did, http_url, reachable}
    POST /api/v1/sync/notify                     → enqueue sync from a peer
    POST /api/v1/sync/trigger                    → manual pull from all peers

There is NO `/v1/sign` endpoint — signing happens locally in the miner
process from a key loaded with `utils/did.load_signing_key`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx


@dataclass
class FetchResult:
    head_cid: str       # we use ref tip SHA here — the node addresses git objects by CIDv1(sha256)
    fetch_ms: float
    bytes_transferred: int


class GitlawbClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, base_url=self.base_url)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── meta ───────────────────────────────────────────────────────────
    async def health(self) -> bool:
        try:
            r = await self._client.get("/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def node_info(self) -> Dict[str, Any]:
        """`GET /` — returns name, version, did, p2p_peer_id, etc."""
        r = await self._client.get("/")
        r.raise_for_status()
        return r.json()

    async def p2p_info(self) -> Dict[str, Any]:
        r = await self._client.get("/api/v1/p2p/info")
        r.raise_for_status()
        return r.json()

    async def stats(self) -> Dict[str, Any]:
        r = await self._client.get("/api/v1/stats")
        r.raise_for_status()
        return r.json()

    # ── IPFS / storage proof ───────────────────────────────────────────
    async def get_ipfs_block(self, cid: str) -> Optional[bytes]:
        """Returns raw git object bytes, or None if not pinned (404)."""
        r = await self._client.get(f"/ipfs/{cid}", timeout=15.0)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    async def list_pins(self) -> Tuple[int, List[Dict[str, Any]]]:
        r = await self._client.get("/api/v1/ipfs/pins")
        r.raise_for_status()
        body = r.json()
        return int(body.get("count", 0)), list(body.get("pins", []))

    # ── repos / latency ────────────────────────────────────────────────
    async def list_repos(self) -> List[Dict[str, Any]]:
        r = await self._client.get("/api/v1/repos")
        r.raise_for_status()
        return r.json()

    async def list_refs(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        """Returns `BranchCid` rows from `/api/v1/repos/.../refs`.

        Response shape (verified against node v0.3.8 source `db/mod.rs::BranchCid`):
            {"refs":[{repo, ref_name, sha, cid, node_did, updated_at}, ...], "count":N}
        """
        r = await self._client.get(f"/api/v1/repos/{owner}/{repo}/refs")
        r.raise_for_status()
        body = r.json()
        return list(body.get("refs", []))

    async def fetch_repo_metadata(self, owner: str, repo: str) -> FetchResult:
        """Latency probe: time how long the node takes to serve the ref list."""
        t0 = time.perf_counter()
        r = await self._client.get(f"/api/v1/repos/{owner}/{repo}/refs", timeout=30.0)
        r.raise_for_status()
        body = r.json()
        refs = body.get("refs", [])
        head = refs[0].get("sha", "") if refs else ""
        return FetchResult(
            head_cid=head,
            fetch_ms=(time.perf_counter() - t0) * 1000.0,
            bytes_transferred=len(r.content),
        )

    # ── gossip events (real, not injected) ─────────────────────────────
    async def list_ref_updates(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent gossipsub-received ref-updates from any repo.

        Response shape (from `api/events.rs::list_ref_updates`):
            {"events":[{id, node_did, pusher_did, repo, ref_name, old_sha,
                        new_sha, timestamp, cert_id, received_at, from_peer}, ...],
             "count":N}
        """
        r = await self._client.get("/api/v1/events/ref-updates", params={"limit": limit})
        r.raise_for_status()
        return list(r.json().get("events", []))

    # ── certs / ref integrity ──────────────────────────────────────────
    async def list_certs(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        r = await self._client.get(f"/api/v1/repos/{owner}/{repo}/certs")
        r.raise_for_status()
        body = r.json()
        return list(body.get("certificates", []))

    async def get_cert(self, owner: str, repo: str, cert_id: str) -> Dict[str, Any]:
        r = await self._client.get(f"/api/v1/repos/{owner}/{repo}/certs/{cert_id}")
        r.raise_for_status()
        return r.json()

    # ── peers / gossip propagation ─────────────────────────────────────
    async def list_peers(self) -> Tuple[int, List[Dict[str, Any]]]:
        r = await self._client.get("/api/v1/peers")
        r.raise_for_status()
        body = r.json()
        return int(body.get("count", 0)), list(body.get("peers", []))

    async def peer_is_known(self, did: str) -> bool:
        _, peers = await self.list_peers()
        return any(p.get("did") == did for p in peers)

    async def ping_peer(self, did: str) -> bool:
        r = await self._client.get(f"/api/v1/peers/{did}/ping", timeout=10.0)
        if r.status_code != 200:
            return False
        return bool(r.json().get("reachable", False))

