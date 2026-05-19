"""Canonical ref-update certificate bytes — bittensor-free so it can be
unit-tested without installing the full SDK.

Mirrors `crates/gitlawb-node/src/cert.rs::issue_ref_certificate`:
  serde_json::to_vec(json!({"repo_id","ref","old","new","pusher","node","ts"}))
where `serde_json::Value` uses BTreeMap (no `preserve_order` feature) →
alphabetical key order with compact separators. Signed by `node_did` with
`sign_b64` (base64url-no-pad)."""

from __future__ import annotations

import json
from typing import Dict


def canonical_cert_bytes(cert: Dict[str, str]) -> bytes:
    payload = {
        "new":     cert.get("new_sha"),
        "node":    cert.get("node_did"),
        "old":     cert.get("old_sha"),
        "pusher":  cert.get("pusher_did"),
        "ref":     cert.get("ref_name"),
        "repo_id": cert.get("repo_id"),
        "ts":      cert.get("issued_at"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
