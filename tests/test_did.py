"""Round-trip DID + signature tests across both supported methods."""

import base64

from nacl.signing import SigningKey

from gitlawbnet.utils.did import (
    did_from_pubkey,
    pubkey_from_did,
    sign_b64url,
    verify_signature,
)


def _new_keypair():
    sk = SigningKey.generate()
    pk = bytes(sk.verify_key)
    return sk, pk


def test_did_key_roundtrip():
    sk, pk = _new_keypair()
    did = did_from_pubkey(pk, method="key")
    assert did.startswith("did:key:z")
    assert pubkey_from_did(did) == pk


def test_did_gitlawb_roundtrip():
    sk, pk = _new_keypair()
    did = did_from_pubkey(pk, method="gitlawb")
    assert did.startswith("did:gitlawb:")
    assert pubkey_from_did(did) == pk


def test_signature_b64url_verifies():
    sk, pk = _new_keypair()
    did = did_from_pubkey(pk)
    msg = b"hello gitlawbnet"
    sig_b64u = sign_b64url(sk, msg)
    assert verify_signature(did, msg, sig_b64u)
    assert not verify_signature(did, b"tampered", sig_b64u)


def test_signature_b64_standard_also_accepted():
    sk, pk = _new_keypair()
    did = did_from_pubkey(pk)
    msg = b"x"
    sig = sk.sign(msg).signature
    assert verify_signature(did, msg, base64.b64encode(sig).decode())


def test_live_public_node_did_parses():
    """Smoke-test against the DID that node.gitlawb.com actually returns."""
    did = "did:key:z6Mkicjkc95VcFx38Xg2SvFV2ENsu3dLDoWborjPGVodHXoH"
    pk = pubkey_from_did(did)
    assert len(pk) == 32
