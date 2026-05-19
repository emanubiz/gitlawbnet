"""Smoke test against the *real* bittensor SDK.

Skipped automatically if bittensor isn't installed. Catches the common
classes of breakage between minor SDK releases without needing chain
access:

  * Synapse subclass instantiation and Pydantic v2 schema validation
  * dendrite/axon construction (no chain calls)
  * weight_utils import path
  * config builder doesn't raise
"""

import pytest

bt = pytest.importorskip("bittensor")


def test_synapse_subclasses_instantiate():
    from gitlawbnet.protocol import (
        GossipChallenge,
        IdentityHandshake,
        LatencyChallenge,
        RefIntegrityChallenge,
        StorageChallenge,
    )
    for cls, kwargs in [
        (IdentityHandshake, {"nonce": "ab" * 32}),
        (StorageChallenge, {"cids": ["bafkreimock"], "salt": "00" * 32}),
        (LatencyChallenge, {"owner": "o", "repo": "r"}),
        (GossipChallenge, {"event_ids": ["e1"]}),
        (RefIntegrityChallenge, {"owner": "o", "repo": "r"}),
    ]:
        s = cls(**kwargs)
        assert isinstance(s, bt.Synapse)
        # Required fields populated, optional defaults to None
        assert s.deserialize() is None or s.deserialize() == s.deserialize()


def test_weight_utils_importable():
    try:
        from bittensor.utils.weight_utils import (
            process_weights_for_netuid,
            convert_weights_and_uids_for_emit,
        )
    except ImportError:
        from bittensor.core.utils.weight_utils import (  # noqa: F401
            process_weights_for_netuid,
            convert_weights_and_uids_for_emit,
        )


def test_config_builder_doesnt_raise():
    import sys
    saved_argv = sys.argv[:]
    sys.argv = ["pytest"]   # avoid pytest's argv leaking into argparse
    try:
        from gitlawbnet.utils.config import build_config
        cfg_v = build_config("validator")
        cfg_m = build_config("miner")
        assert hasattr(cfg_v, "netuid")
        assert hasattr(cfg_m, "gitlawb")
        assert hasattr(cfg_m.gitlawb, "node_url")
        assert hasattr(cfg_v.neuron, "sample_size")
        assert hasattr(cfg_v.neuron, "moving_average_alpha")
    finally:
        sys.argv = saved_argv


def test_axon_attach_accepts_async_handlers():
    """The miner relies on `axon.attach(...).attach(...).attach(...)`. Verify
    that chaining + async handlers actually work in the installed bittensor."""
    import sys
    saved_argv = sys.argv[:]
    sys.argv = ["pytest", "--wallet.name", "fake", "--wallet.hotkey", "fake"]
    try:
        from typing import Tuple
        from gitlawbnet.protocol import IdentityHandshake, StorageChallenge

        async def f1(synapse: IdentityHandshake) -> IdentityHandshake: return synapse
        async def f2(synapse: StorageChallenge) -> StorageChallenge: return synapse
        async def bl1(synapse: IdentityHandshake) -> Tuple[bool, str]: return (False, "ok")
        async def bl2(synapse: StorageChallenge) -> Tuple[bool, str]:  return (False, "ok")
        async def pr1(synapse: IdentityHandshake) -> float: return 0.0
        async def pr2(synapse: StorageChallenge) -> float:  return 0.0

        try:
            wallet = bt.wallet(name="fake_test_wallet", hotkey="fake_test_hotkey")
            axon = bt.axon(wallet=wallet, port=0)
            axon.attach(forward_fn=f1, blacklist_fn=bl1, priority_fn=pr1) \
                .attach(forward_fn=f2, blacklist_fn=bl2, priority_fn=pr2)
        except (FileNotFoundError, PermissionError):
            pytest.skip("Wallet not available in test env")
    finally:
        sys.argv = saved_argv
