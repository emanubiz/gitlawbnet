#!/usr/bin/env python3
"""
Regenerate seed_corpus.json from a live gitlawb-node.

Usage:
    python scripts/refresh_seed.py [--node https://node.gitlawb.com] \
                                   [--repos 5] [--cids 50] [--out seed_corpus.json]

Run weekly (cron or GitHub Action) to keep the validator's exam fresh —
a stale corpus is one of the few ways miners can "pre-cache" answers.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone

import httpx


def short_did(full_did: str) -> str:
    """Convert `did:key:z6Mk...` → `z6Mk...` (the URL/path form)."""
    return full_did.rsplit(":", 1)[-1]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--node", default="https://node.gitlawb.com")
    p.add_argument("--repos", type=int, default=5)
    p.add_argument("--cids", type=int, default=50)
    p.add_argument("--out", default="seed_corpus.json")
    args = p.parse_args()

    base = args.node.rstrip("/")
    with httpx.Client(timeout=30.0) as c:
        repos_raw = c.get(f"{base}/api/v1/repos").json()
        pins_raw = c.get(f"{base}/api/v1/ipfs/pins").json()

    public_repos = [r for r in repos_raw if r.get("is_public", True)]
    if not public_repos:
        print("no public repos found on node", file=sys.stderr)
        return 1

    chosen_repos = random.sample(public_repos, min(args.repos, len(public_repos)))
    repos_out = [{"owner": short_did(r["owner_did"]), "repo": r["name"]} for r in chosen_repos]

    # Use one repo's main ref as the gossip target — the cheapest meaningful event
    gossip_out = [{"owner": repos_out[0]["owner"], "repo": repos_out[0]["repo"], "ref": "refs/heads/main"}]

    pins = pins_raw.get("pins", [])
    chosen_pins = random.sample(pins, min(args.cids, len(pins))) if pins else []
    cids_out = [p["cid"] for p in chosen_pins]

    corpus = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trusted_nodes": [args.node],
        "cids": cids_out,
        "repos": repos_out,
        "gossip_repos": gossip_out,
    }
    with open(args.out, "w") as f:
        json.dump(corpus, f, indent=2)
    print(f"wrote {args.out}: {len(cids_out)} cids, {len(repos_out)} repos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
