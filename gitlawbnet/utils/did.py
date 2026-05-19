"""
DID and signature helpers — aligned with the real gitlawb-node code
(`crates/gitlawb-core/src/did.rs` and `identity.rs`).

A gitlawb actor's canonical DID is `did:key:z<multibase58btc(0xed01 || pubkey)>`
where `0xed01` is the multicodec varint for Ed25519. We support `did:key`
for bootstrap and `did:gitlawb:<b58>` for post-DHT-anchor identities (the
suffix is just a base58btc-encoded key in that case).

Signatures use base64 (RFC 4648 standard with padding) to match RFC 9421
HTTP-Sig output from the node, BUT challenge-response signatures over a
raw nonce use base64-url-no-pad to match `Keypair::sign_b64` in the Rust
code. We accept both transparently.
"""

from __future__ import annotations

import base64
from typing import Tuple

import base58
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

# Multicodec varint prefix for ed25519-pub (0xed 0x01)
ED25519_MULTICODEC = b"\xed\x01"


def _b64_decode_any(s: str) -> bytes:
    """Decode either standard or url-safe base64, with or without padding."""
    s = s.strip()
    pad = (-len(s)) % 4
    s_padded = s + ("=" * pad)
    try:
        return base64.urlsafe_b64decode(s_padded)
    except Exception:
        return base64.b64decode(s_padded)


def pubkey_from_did(did: str) -> bytes:
    """Return the raw 32-byte Ed25519 public key bytes embedded in `did`.

    Supported DID methods:
        did:key:z<base58btc(0xed01 || pk)>      ← canonical
        did:gitlawb:<base58btc(pk)>             ← DHT-anchored form, raw pk
    """
    if did.startswith("did:key:"):
        body = did[len("did:key:"):].split("/", 1)[0]
        if not body.startswith("z"):
            raise ValueError(f"did:key body must start with 'z' (base58btc multibase): {body!r}")
        raw = base58.b58decode(body[1:])
        if not raw.startswith(ED25519_MULTICODEC):
            raise ValueError("did:key is not Ed25519 (multicodec prefix mismatch)")
        pk = raw[len(ED25519_MULTICODEC):]
        if len(pk) != 32:
            raise ValueError(f"Ed25519 pubkey must be 32 bytes, got {len(pk)}")
        return pk

    if did.startswith("did:gitlawb:"):
        body = did[len("did:gitlawb:"):].split("/", 1)[0]
        pk = base58.b58decode(body)
        if len(pk) != 32:
            raise ValueError(f"Ed25519 pubkey must be 32 bytes, got {len(pk)}")
        return pk

    raise ValueError(f"unsupported DID method: {did!r}")


def did_from_pubkey(pubkey: bytes, method: str = "key") -> str:
    """Inverse of `pubkey_from_did` — produce a DID from a 32-byte Ed25519 key."""
    if len(pubkey) != 32:
        raise ValueError("pubkey must be 32 bytes")
    if method == "key":
        body = base58.b58encode(ED25519_MULTICODEC + pubkey).decode()
        return f"did:key:z{body}"
    if method == "gitlawb":
        return f"did:gitlawb:{base58.b58encode(pubkey).decode()}"
    raise ValueError(f"unknown method: {method}")


def verify_signature(did: str, message: bytes, signature: str) -> bool:
    """Verify an Ed25519 signature.

    `signature` may be base64, base64url (with/without padding), or base58 —
    we try in that order. The gitlawb-node signs with base64url-no-pad
    (`sign_b64`) but RFC 9421 HTTP signatures use standard base64.
    """
    try:
        pk = pubkey_from_did(did)
    except ValueError:
        return False

    # Try base64 variants first (matches Rust output), fall back to base58.
    for decoder in (_b64_decode_any, base58.b58decode):
        try:
            sig = decoder(signature)
        except Exception:
            continue
        if len(sig) != 64:
            continue
        try:
            VerifyKey(pk).verify(message, sig)
            return True
        except BadSignatureError:
            continue
    return False


# ---------------------------------------------------------------------------
# Local signing — miners load their Ed25519 seed from disk (the same file
# the gitlawb-node uses) so we can sign challenges without round-tripping
# through HTTP. The node stores PKCS#8 PEM at GITLAWB_KEY; we accept either
# a raw 32-byte seed file or a PEM file.
# ---------------------------------------------------------------------------
def load_signing_key(path: str) -> Tuple[SigningKey, str]:
    """Load a SigningKey from disk and return (key, did:key)."""
    with open(path, "rb") as f:
        data = f.read()

    if data.startswith(b"-----BEGIN"):
        # PKCS#8 PEM — strip header/footer + decode base64.
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
        except ImportError as e:
            raise RuntimeError(
                "PEM key requires `cryptography`. `pip install cryptography` "
                "or export a raw 32-byte seed instead."
            ) from e
        priv = load_pem_private_key(data, password=None)
        seed = priv.private_bytes_raw()  # 32 bytes for Ed25519
    elif len(data) == 32:
        seed = data
    else:
        raise ValueError(
            f"key file at {path} is neither a 32-byte seed nor a PEM (got {len(data)} bytes)"
        )

    sk = SigningKey(seed)
    did = did_from_pubkey(bytes(sk.verify_key), method="key")
    return sk, did


def sign_b64url(sk: SigningKey, message: bytes) -> str:
    """Sign and return base64url-no-pad (matches Rust `sign_b64`)."""
    sig = sk.sign(message).signature
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
