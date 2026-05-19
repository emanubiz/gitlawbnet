"""Cert canonical-bytes verification.

The static fixture below is a real cert pulled from
https://node.gitlawb.com/api/v1/repos/.../wunb-llm-wiki/certs on
2026-05-18. If this test breaks, either the node's signing format
changed (check `crates/gitlawb-node/src/cert.rs`) or our
`canonical_cert_bytes` reconstruction drifted.
"""

from gitlawbnet.utils.cert import canonical_cert_bytes
from gitlawbnet.utils.did import verify_signature


LIVE_CERT = {
    "id": "8f95e025-ae2b-4db9-8c05-ab584081d733",
    "issued_at": "2026-05-18T18:00:10.383955909+00:00",
    "new_sha": "6fd675813138dfba1cc35a01fb2ad57c05198fb3",
    "node_did": "did:key:z6Mkicjkc95VcFx38Xg2SvFV2ENsu3dLDoWborjPGVodHXoH",
    "old_sha": "a988297684bc2840ad262808f57b85cd55f10295",
    "pusher_did": "did:key:z6Mkkrdot4pvdcvDKb8GfwiFyA8ZQoPFqpxZNqyfTghvEjPH",
    "ref_name": "refs/heads/main",
    "repo_id": "3a00fed2-ba23-4e68-88a4-c412efebe09f",
    "signature": "HCFqTifQnk9pUJkqBJiE8YEDaf0LbslV0K2RgZ1KQyhDs8sviJ0TegOmhb4njqnsf40OQ_yNb7kVpF1VNPmFDA",
}


def test_canonical_bytes_match_node_signature():
    payload = canonical_cert_bytes(LIVE_CERT)
    assert verify_signature(LIVE_CERT["node_did"], payload, LIVE_CERT["signature"]) is True


def test_canonical_bytes_use_sorted_keys_compact_separators():
    payload = canonical_cert_bytes(LIVE_CERT)
    # First key alphabetically is "new"
    assert payload.startswith(b'{"new":')
    # No spaces between separators
    assert b", " not in payload
    assert b": " not in payload


def test_tampered_cert_rejected():
    tampered = dict(LIVE_CERT, new_sha="0" * 40)
    payload = canonical_cert_bytes(tampered)
    assert verify_signature(tampered["node_did"], payload, tampered["signature"]) is False
