"""
Gitlawbnet validator entry-point.

Run with:
    python neurons/validator.py \
        --netuid <NETUID> \
        --subtensor.network finney \
        --wallet.name <coldkey> --wallet.hotkey <hotkey> \
        --gitlawb.node_url http://127.0.0.1:7545
"""

from __future__ import annotations

import asyncio
import copy
import os
import threading
import time
import traceback

import bittensor as bt
import numpy as np

# Bittensor 8.x: weight_utils is exposed via `bittensor.utils.weight_utils`.
# We import defensively so the same code runs against minor-version drift.
try:
    from bittensor.utils.weight_utils import (
        process_weights_for_netuid,
        convert_weights_and_uids_for_emit,
    )
except ImportError:  # pragma: no cover — older / alt layout
    from bittensor.core.utils.weight_utils import (  # type: ignore
        process_weights_for_netuid,
        convert_weights_and_uids_for_emit,
    )

from gitlawbnet.base.neuron import BaseNeuron
from gitlawbnet.utils.config import build_config
from gitlawbnet.validator.forward import forward as run_forward
from gitlawbnet.validator.scoring import ScoreBook


class Validator(BaseNeuron):
    neuron_type: str = "ValidatorNeuron"

    def __init__(self, config: "bt.config"):
        super().__init__(config)

        self.hotkeys = copy.deepcopy(self.metagraph.hotkeys)
        self.dendrite = bt.dendrite(wallet=self.wallet)
        bt.logging.info(f"Dendrite: {self.dendrite}")

        self.scores = ScoreBook(
            num_uids=int(self.metagraph.n),
            alpha=self.config.neuron.moving_average_alpha,
        )
        # Try to resume from disk.
        if self.scores.load(self._state_path()):
            bt.logging.info(f"Resumed scores from {self._state_path()}")
            self.scores.resize(int(self.metagraph.n), self.metagraph.hotkeys)

        # Validators benefit from serving an axon: some subnets enforce a
        # firewall that drops un-served validators. Honour --neuron.axon_off
        # for headless deploys.
        self.axon: bt.axon | None = None
        if not self.config.neuron.axon_off:
            try:
                self.axon = bt.axon(wallet=self.wallet, config=self.config)
                self.subtensor.serve_axon(netuid=self.config.netuid, axon=self.axon)
                bt.logging.info(f"Validator axon served: {self.axon}")
            except Exception as exc:  # noqa: BLE001
                bt.logging.warning(f"Failed to serve validator axon: {exc!r}")

        self.should_exit = False
        self.is_running = False
        self.thread: threading.Thread | None = None
        self.loop = asyncio.new_event_loop()

    # ------------------------------------------------------------------
    def _state_path(self) -> str:
        return os.path.join(self.config.neuron.full_path, "state.npz")

    def save_state(self) -> None:
        bt.logging.trace("Saving validator state")
        self.scores.save(self._state_path())

    def load_state(self) -> None:
        self.scores.load(self._state_path())

    # ------------------------------------------------------------------
    def set_weights(self) -> None:
        if self.config.neuron.disable_set_weights:
            bt.logging.info("[dry-run] disable_set_weights is set; skipping")
            return

        raw = self.scores.scores
        if np.isnan(raw).any():
            bt.logging.warning("Score vector contains NaN; replacing with 0")
            raw = np.nan_to_num(raw, nan=0.0)

        norm_value = np.linalg.norm(raw, ord=1)
        if norm_value == 0 or np.isnan(norm_value):
            bt.logging.info("All-zero score vector — skipping set_weights")
            return
        raw_weights = raw / norm_value

        # process_weights_for_netuid applies on-chain hyperparams
        # (max_weights_limit, min_allowed_weights, immunity_period, etc.)
        processed_uids, processed_weights = process_weights_for_netuid(
            uids=self.metagraph.uids,
            weights=raw_weights,
            netuid=self.config.netuid,
            subtensor=self.subtensor,
            metagraph=self.metagraph,
        )
        uint_uids, uint_weights = convert_weights_and_uids_for_emit(
            uids=processed_uids, weights=processed_weights
        )

        result, msg = self.subtensor.set_weights(
            wallet=self.wallet,
            netuid=self.config.netuid,
            uids=uint_uids,
            weights=uint_weights,
            wait_for_inclusion=False,
            wait_for_finalization=False,
            version_key=self.spec_version,
        )
        if result is True:
            bt.logging.info("set_weights succeeded")
        else:
            bt.logging.error(f"set_weights failed: {msg}")

    # ------------------------------------------------------------------
    def resync_metagraph(self) -> None:
        super().resync_metagraph()
        if self.metagraph.axons == [None] * len(self.metagraph.axons):
            return
        self.scores.resize(int(self.metagraph.n), self.metagraph.hotkeys)
        self.hotkeys = copy.deepcopy(self.metagraph.hotkeys)

    # ------------------------------------------------------------------
    async def concurrent_forward(self) -> None:
        coros = [run_forward(self) for _ in range(self.config.neuron.num_concurrent_forwards)]
        results = await asyncio.gather(*coros)
        for outcomes in results:
            for uid, outcome in outcomes.items():
                if outcome.miner_did:
                    self.scores.record_did(uid, outcome.miner_did)
            self.scores.update(outcomes)

    def should_set_weights(self) -> bool:
        if self.step == 0 or self.config.neuron.disable_set_weights:
            return False
        return (
            self.block - int(self.metagraph.last_update[self.uid])
        ) > self.config.neuron.epoch_length

    # ------------------------------------------------------------------
    def run(self) -> None:
        bt.logging.info(f"Validator starting at block {self.block}")
        try:
            while not self.should_exit:
                bt.logging.info(f"step({self.step}) block({self.block})")
                self.loop.run_until_complete(self.concurrent_forward())

                if self.should_sync_metagraph():
                    self.resync_metagraph()
                if self.should_set_weights():
                    self.set_weights()

                self.save_state()
                self.step += 1
        except KeyboardInterrupt:
            bt.logging.info("Validator stopped by KeyboardInterrupt")
        except Exception:
            bt.logging.error(f"Fatal in validator run loop:\n{traceback.format_exc()}")
        finally:
            if self.axon is not None:
                try:
                    self.axon.stop()
                except Exception:
                    pass

    # Context manager — allows `with Validator(cfg) as v: ...`
    def __enter__(self):
        if not self.is_running:
            self.should_exit = False
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
            self.is_running = True
        return self

    def __exit__(self, exc_type, exc_value, tb):
        if self.is_running:
            self.should_exit = True
            if self.thread is not None:
                self.thread.join(10)
            self.is_running = False


def main() -> None:
    config = build_config(role="validator")
    with Validator(config) as v:
        while True:
            bt.logging.info(f"Validator alive — step {v.step}")
            time.sleep(30)


if __name__ == "__main__":
    main()
