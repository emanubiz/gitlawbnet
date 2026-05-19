"""
Shared bittensor.config() builder for validator and miner.

Mirrors the `add_args` / `config()` pattern from
`template/utils/config.py` in the canonical Opentensor template so that
flags like `--wallet.name`, `--subtensor.network`, `--logging.debug`,
`--axon.port`, `--netuid` behave exactly as Bittensor operators expect.
"""

from __future__ import annotations

import argparse
import os

import bittensor as bt


# ---------------------------------------------------------------------------
# Shared flags
# ---------------------------------------------------------------------------
def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--netuid",
        type=int,
        default=int(os.getenv("SUBNET_NETUID", "0")),
        help="Bittensor netuid for Gitlawbnet (set after registration)",
    )
    parser.add_argument(
        "--neuron.name",
        type=str,
        default="gitlawbnet",
        help="Sub-directory under ~/.bittensor for state files",
    )
    parser.add_argument(
        "--neuron.device",
        type=str,
        default="cpu",
        help="Device for any local computation (CPU is sufficient — no model inference here)",
    )
    parser.add_argument(
        "--neuron.epoch_length",
        type=int,
        default=100,
        help="Blocks between metagraph syncs / set_weights (12s/block → 100 blocks ≈ 20 min)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=False,
        help="(Reserved) future hook for MockSubtensor-based offline testing",
    )


# ---------------------------------------------------------------------------
# Validator-specific
# ---------------------------------------------------------------------------
def add_validator_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--neuron.sample_size",
        type=int,
        default=32,
        help="How many miner UIDs to challenge per forward pass",
    )
    parser.add_argument(
        "--neuron.num_concurrent_forwards",
        type=int,
        default=1,
        help="Number of `forward()` coroutines to run concurrently per step",
    )
    parser.add_argument(
        "--neuron.moving_average_alpha",
        type=float,
        default=0.1,
        help="EMA factor blending new scores into history (0=frozen, 1=no memory)",
    )
    parser.add_argument(
        "--neuron.challenge_timeout",
        type=float,
        default=12.0,
        help="Per-challenge dendrite timeout (seconds)",
    )
    parser.add_argument(
        "--neuron.disable_set_weights",
        action="store_true",
        default=False,
        help="Compute scores but never call set_weights (dry-run mode)",
    )
    parser.add_argument(
        "--neuron.axon_off",
        "--axon_off",
        action="store_true",
        default=False,
        help="Don't serve an axon as a validator (some subnets blacklist non-served validators)",
    )
    parser.add_argument(
        "--neuron.vpermit_tao_limit",
        type=int,
        default=4096,
        help="(Inherited from template) ignored by gitlawbnet currently",
    )
    parser.add_argument(
        "--gitlawb.node_url",
        type=str,
        default=os.getenv("GITLAWB_NODE", ""),
        help=(
            "URL of the validator's own gitlawb-node — used as the trusted "
            "source for IPFS truth blocks and gossip events. If empty, falls "
            "back to seed_corpus.json:trusted_nodes."
        ),
    )


# ---------------------------------------------------------------------------
# Miner-specific
# ---------------------------------------------------------------------------
def add_miner_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--blacklist.force_validator_permit",
        action="store_true",
        default=True,
        help="Only accept challenges from hotkeys with validator_permit (recommended)",
    )
    parser.add_argument(
        "--blacklist.allow_non_registered",
        action="store_true",
        default=False,
        help="Accept challenges from un-registered hotkeys (DANGEROUS — Sybil bait)",
    )
    parser.add_argument(
        "--blacklist.min_stake",
        type=float,
        default=1000.0,
        help="Minimum TAO stake required for callers without validator_permit",
    )
    parser.add_argument(
        "--gitlawb.node_url",
        type=str,
        default=os.getenv("GITLAWB_NODE", "http://127.0.0.1:7545"),
        help="HTTP base URL of the local gitlawb-node",
    )
    parser.add_argument(
        "--gitlawb.signing_key_path",
        type=str,
        default=os.getenv(
            "GITLAWB_KEY", os.path.expanduser("~/.gitlawb/identity.pem")
        ),
        help=(
            "Path to the Ed25519 signing key (PKCS#8 PEM or raw 32-byte seed). "
            "MUST correspond to the DID exposed by the gitlawb-node at GET /"
        ),
    )


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------
def build_config(role: str) -> "bt.config":
    """Build a bt.config for either 'validator' or 'miner'."""
    parser = argparse.ArgumentParser()
    add_args(parser)
    if role == "validator":
        add_validator_args(parser)
    elif role == "miner":
        add_miner_args(parser)
    else:
        raise ValueError(f"unknown role: {role}")

    bt.wallet.add_args(parser)
    bt.subtensor.add_args(parser)
    bt.logging.add_args(parser)
    bt.axon.add_args(parser)
    return bt.config(parser)
