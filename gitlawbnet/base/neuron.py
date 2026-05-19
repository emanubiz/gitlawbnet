"""
Shared neuron bootstrap (wallet, subtensor, metagraph, logging) for the
validator and the miner.

Closely mirrors `template/base/neuron.py` from
github.com/opentensor/bittensor-subnet-template so that operators familiar
with the canonical Bittensor template find the same hooks and config
namespaces here.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

import bittensor as bt

from gitlawbnet import __spec_version__


class BaseNeuron(ABC):
    """Wallet + subtensor + metagraph setup that both roles need."""

    neuron_type: str = "BaseNeuron"
    spec_version: int = __spec_version__

    def __init__(self, config: "bt.config"):
        self.config = config
        self.step = 0

        # bt.logging is configured via its own argparse group; in bittensor 8.x
        # the right entry-point is `set_config`, not calling `bt.logging` directly.
        bt.logging.set_config(config=self.config.logging)
        bt.logging.info(self.config)

        # Build a per-neuron working directory used for state checkpoints.
        full_path = os.path.expanduser(
            "{}/{}/{}/netuid{}/{}".format(
                self.config.logging.logging_dir,
                self.config.wallet.name,
                self.config.wallet.hotkey,
                self.config.netuid,
                self.config.neuron.name,
            )
        )
        self.config.neuron.full_path = full_path
        os.makedirs(full_path, exist_ok=True)

        # ── chain objects ────────────────────────────────────────────────
        if self.config.mock:
            # The template ships a MockSubtensor; we don't import it here
            # because gitlawbnet has its own mock infrastructure
            # (gitlawbnet.mock) for unit-testing the gitlawb-side handlers.
            raise NotImplementedError(
                "--mock flag is recognised but a MockSubtensor is not yet wired up. "
                "Use the bittensor template's mock harness for chain-only smoke tests."
            )

        self.wallet = bt.wallet(config=self.config)
        self.subtensor = bt.subtensor(config=self.config)
        self.metagraph = self.subtensor.metagraph(self.config.netuid)

        bt.logging.info(f"Wallet: {self.wallet}")
        bt.logging.info(f"Subtensor: {self.subtensor}")
        bt.logging.info(f"Metagraph: {self.metagraph}")

        self._check_registered()
        self.uid: int = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
        bt.logging.info(
            f"Running on netuid {self.config.netuid} as uid {self.uid} "
            f"via {self.subtensor.chain_endpoint}"
        )

    # ------------------------------------------------------------------
    # Properties & registration check
    # ------------------------------------------------------------------
    @property
    def block(self) -> int:
        return int(self.subtensor.get_current_block())

    def _check_registered(self) -> None:
        registered = self.subtensor.is_hotkey_registered(
            netuid=self.config.netuid,
            hotkey_ss58=self.wallet.hotkey.ss58_address,
        )
        if not registered:
            raise RuntimeError(
                f"Hotkey {self.wallet.hotkey.ss58_address} is not registered on "
                f"netuid {self.config.netuid}. Run "
                f"`btcli subnet register --netuid {self.config.netuid}` first."
            )

    # ------------------------------------------------------------------
    # Metagraph sync — block-based throttling identical to the canonical
    # template (uses metagraph.last_update[uid] as the watermark).
    # ------------------------------------------------------------------
    def should_sync_metagraph(self) -> bool:
        return (
            self.block - int(self.metagraph.last_update[self.uid])
        ) > self.config.neuron.epoch_length

    def resync_metagraph(self) -> None:
        bt.logging.info("Resyncing metagraph...")
        self.metagraph.sync(subtensor=self.subtensor)

    # ------------------------------------------------------------------
    # State checkpointing — subclasses override to persist scores etc.
    # ------------------------------------------------------------------
    def save_state(self) -> None:
        pass

    def load_state(self) -> None:
        pass

    @abstractmethod
    def run(self) -> None: ...
