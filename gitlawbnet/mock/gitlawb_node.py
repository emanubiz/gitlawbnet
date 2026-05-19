"""
In-process mock of `gitlawb-node` for offline testing.

Mirrors the real REST surface (`crates/gitlawb-node/src/server.rs`) for
the endpoints `gitlawbnet` actually calls. Used by tests and by the
manual smoke-test script `scripts/smoke_test.py`.

Spawn it programmatically:

    node = MockGitlawbNode(keypair=SigningKey.generate())
    await node.start(host="127.0.0.1", port=0)
    print("listening on", node.url)
    ...
    await node.stop()

Or via CLI:

    python -m gitlawbnet.mock.gitlawb_node --port 17545
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiohttp import web
from nacl.signing import SigningKey

from gitlawbnet.utils.did import did_from_pubkey


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b64url_no_pad(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class MockGitlawbNode:
    """Minimal HTTP server that imitates a healthy gitlawb-node."""

    def __init__(
        self,
        keypair: Optional[SigningKey] = None,
        seeded_blocks: Optional[Dict[str, bytes]] = None,
        seeded_events: Optional[List[Dict[str, Any]]] = None,
        seeded_certs: Optional[List[Dict[str, Any]]] = None,
    ):
        self.signing_key = keypair or SigningKey.generate()
        self.did = did_from_pubkey(bytes(self.signing_key.verify_key))
        self.blocks: Dict[str, bytes] = dict(seeded_blocks or {})
        self.events: List[Dict[str, Any]] = list(seeded_events or [])
        self.certs: List[Dict[str, Any]] = list(seeded_certs or [])
        self.peers: List[Dict[str, Any]] = []
        self.refs: Dict[str, List[Dict[str, Any]]] = {}

        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self.url: str = ""

    # ── seed helpers ────────────────────────────────────────────────────
    def add_block(self, cid: str, content: bytes) -> None:
        self.blocks[cid] = content

    def add_event(self, repo: str, ref_name: str, new_sha: str) -> str:
        """Add a synthetic ref-update event; returns the new event_id."""
        eid = str(uuid.uuid4())
        self.events.insert(0, {
            "id": eid,
            "node_did": self.did,
            "pusher_did": self.did,
            "repo": repo,
            "ref_name": ref_name,
            "old_sha": "0" * 40,
            "new_sha": new_sha,
            "timestamp": _now_rfc3339(),
            "cert_id": None,
            "received_at": _now_rfc3339(),
            "from_peer": self.did,
        })
        return eid

    def add_cert(self, repo_id: str, ref_name: str, new_sha: str,
                 old_sha: str = "0" * 40,
                 pusher_did: Optional[str] = None) -> Dict[str, Any]:
        """Issue a real cert signed by this mock node's key — useful so the
        validator's `verify_ref_certificates` can succeed in tests."""
        import json
        issued_at = _now_rfc3339()
        pusher = pusher_did or self.did
        payload = {
            "new":     new_sha,
            "node":    self.did,
            "old":     old_sha,
            "pusher":  pusher,
            "ref":     ref_name,
            "repo_id": repo_id,
            "ts":      issued_at,
        }
        body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        sig = _b64url_no_pad(self.signing_key.sign(body_bytes).signature)
        cert = {
            "id": str(uuid.uuid4()),
            "repo_id": repo_id,
            "ref_name": ref_name,
            "old_sha": old_sha,
            "new_sha": new_sha,
            "pusher_did": pusher,
            "node_did": self.did,
            "signature": sig,
            "issued_at": issued_at,
        }
        self.certs.append(cert)
        return cert

    def add_ref(self, owner: str, repo: str, ref_name: str, sha: str, cid: str) -> None:
        self.refs.setdefault(f"{owner}/{repo}", []).append({
            "repo": f"{owner}/{repo}", "ref_name": ref_name,
            "sha": sha, "cid": cid, "node_did": self.did,
            "updated_at": _now_rfc3339(),
        })

    # ── handlers ────────────────────────────────────────────────────────
    async def _health(self, _):    return web.json_response({"status": "ok"})
    async def _root(self, _):
        return web.json_response({
            "name": "mock-gitlawb-node", "version": "mock",
            "did": self.did, "p2p_peer_id": "12D3KooW" + "x" * 44,
            "protocols": ["git-smart-http", "mcp", "libp2p"],
        })
    async def _p2p_info(self, _):
        return web.json_response({
            "enabled": True, "peer_id": "12D3KooW" + "x" * 44,
            "topics": ["gitlawb/ref-updates/v1"],
        })
    async def _stats(self, _):
        return web.json_response({
            "repos": len(self.refs), "agents": 1,
            "pushes": len(self.certs), "version": "mock",
        })
    async def _ipfs_block(self, req):
        cid = req.match_info["cid"]
        if cid not in self.blocks:
            return web.Response(status=404, text="not pinned")
        return web.Response(body=self.blocks[cid], headers={
            "content-type": "application/octet-stream",
            "x-content-cid": cid,
        })
    async def _ipfs_pins(self, _):
        pins = [{"cid": c, "pinata_cid": c, "pinned_at": _now_rfc3339(),
                 "sha256_hex": hashlib.sha256(b).hexdigest()}
                for c, b in self.blocks.items()]
        return web.json_response({"pins": pins, "count": len(pins)})
    async def _list_repos(self, _):
        out = []
        for slug in self.refs:
            owner, name = slug.split("/", 1)
            out.append({
                "id": str(uuid.uuid4()), "name": name,
                "owner_did": f"did:key:{owner}" if not owner.startswith("did:") else owner,
                "is_public": True, "default_branch": "main",
            })
        return web.json_response(out)
    async def _list_refs(self, req):
        owner, repo = req.match_info["owner"], req.match_info["repo"]
        refs = self.refs.get(f"{owner}/{repo}", [])
        return web.json_response({"refs": refs, "count": len(refs)})
    async def _list_certs(self, _):
        return web.json_response({"certificates": self.certs})
    async def _ref_updates(self, req):
        limit = int(req.query.get("limit", 50))
        return web.json_response({"events": self.events[:limit], "count": min(limit, len(self.events))})
    async def _peers(self, _):
        return web.json_response({"peers": self.peers, "count": len(self.peers)})

    def _make_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self._health)
        app.router.add_get("/", self._root)
        app.router.add_get("/api/v1/p2p/info", self._p2p_info)
        app.router.add_get("/api/v1/stats", self._stats)
        app.router.add_get("/ipfs/{cid}", self._ipfs_block)
        app.router.add_get("/api/v1/ipfs/pins", self._ipfs_pins)
        app.router.add_get("/api/v1/repos", self._list_repos)
        app.router.add_get("/api/v1/repos/{owner}/{repo}/refs", self._list_refs)
        app.router.add_get("/api/v1/repos/{owner}/{repo}/certs", self._list_certs)
        app.router.add_get("/api/v1/events/ref-updates", self._ref_updates)
        app.router.add_get("/api/v1/peers", self._peers)
        return app

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        self._runner = web.AppRunner(self._make_app())
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        # Resolve the actual bound port (if we passed 0).
        actual_port = port
        for sock in self._site._server.sockets:  # type: ignore[attr-defined]
            actual_port = sock.getsockname()[1]
            break
        self.url = f"http://{host}:{actual_port}"
        return self.url

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------
async def _main_async(host: str, port: int) -> None:
    sk = SigningKey.generate()
    node = MockGitlawbNode(keypair=sk)
    # Seed a CID + a synthetic event + a cert so the surface is interesting.
    blob = b"mock object content"
    cid = "bafkreimock" + hashlib.sha256(blob).hexdigest()[:38]
    node.add_block(cid, blob)
    node.add_ref("ownerz", "demo", "refs/heads/main", "a" * 40, cid)
    node.add_cert(repo_id="ownerz/demo", ref_name="refs/heads/main", new_sha="a" * 40)
    node.add_event("ownerz/demo", "refs/heads/main", "a" * 40)

    url = await node.start(host=host, port=port)
    seed_path = "/tmp/mock_seed.txt"
    with open(seed_path, "w") as f:
        f.write(f"GITLAWB_NODE={url}\nMOCK_DID={node.did}\nMOCK_CID={cid}\n")
    print(f"mock gitlawb-node listening on {url}")
    print(f"  DID:  {node.did}")
    print(f"  CID:  {cid}")
    print(f"  cred written to {seed_path}")
    print("Press Ctrl-C to stop.")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        await node.stop()


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=17545)
    args = p.parse_args()
    asyncio.run(_main_async(args.host, args.port))


if __name__ == "__main__":
    main()
