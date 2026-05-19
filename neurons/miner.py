"""
Gitlawbnet miner entry-point.

The miner is a thin Bittensor wrapper around a locally-running
gitlawb-node. All actual work (IPFS pinning, libp2p gossip, signed
ref-update certificates) is done by the Rust node — the miner just
exposes its capabilities to validators via 5 Synapse handlers.

Run with:
    python neurons/miner.py \
        --netuid <NETUID> \
        --subtensor.network finney \
        --wallet.name <coldkey> --wallet.hotkey <hotkey> \
        --gitlawb.node_url http://127.0.0.1:7545
"""

from __future__ import annotations

import asyncio
import threading
import time
import traceback
from typing import Tuple

import bittensor as bt

from gitlawbnet.base.neuron import BaseNeuron
from gitlawbnet.miner.handlers import Handlers
from gitlawbnet.protocol import (
    GossipChallenge,
    IdentityHandshake,
    LatencyChallenge,
    RefIntegrityChallenge,
    StorageChallenge,
)
from gitlawbnet.utils.config import build_config
from gitlawbnet.utils.gitlawb_client import GitlawbClient


class Miner(BaseNeuron):
    neuron_type: str = "MinerNeuron"

    def __init__(self, config: "bt.config"):
        super().__init__(config)
        self.client = GitlawbClient(config.gitlawb.node_url)
        self.handlers = Handlers(self.client, config.gitlawb.signing_key_path)

        self.axon = bt.axon(wallet=self.wallet, config=self.config)
        # One attach per Synapse subclass — bittensor's axon dispatches based
        # on the inbound Synapse type, so this is the documented pattern for
        # serving multiple protocol messages from one process.
        (self.axon
            .attach(forward_fn=self.handlers.handshake,     blacklist_fn=self._blacklist_handshake, priority_fn=self._priority_handshake)
            .attach(forward_fn=self.handlers.storage,       blacklist_fn=self._blacklist_storage,   priority_fn=self._priority_storage)
            .attach(forward_fn=self.handlers.latency,       blacklist_fn=self._blacklist_latency,   priority_fn=self._priority_latency)
            .attach(forward_fn=self.handlers.gossip,        blacklist_fn=self._blacklist_gossip,    priority_fn=self._priority_gossip)
            .attach(forward_fn=self.handlers.ref_integrity, blacklist_fn=self._blacklist_ref,       priority_fn=self._priority_ref))

        self.should_exit = False
        self.is_running = False
        self.thread: threading.Thread | None = None

    # ── access control ──────────────────────────────────────────────────
    #
    # bittensor 8.x's axon.attach() validates that blacklist_fn / priority_fn
    # signatures match the forward_fn's Synapse type EXACTLY (parameter
    # annotation + return type). We can't use a generic `bt.Synapse` here —
    # need one wrapper per Synapse subclass with the exact annotation.

    def _shared_blacklist(self, synapse) -> Tuple[bool, str]:
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            return True, "missing dendrite hotkey"
        hotkey = synapse.dendrite.hotkey
        if hotkey not in self.metagraph.hotkeys:
            if not self.config.blacklist.allow_non_registered:
                return True, f"unregistered hotkey {hotkey[:8]}"
            return False, "ok (unregistered allowed)"
        uid = self.metagraph.hotkeys.index(hotkey)
        if self.config.blacklist.force_validator_permit:
            if not bool(self.metagraph.validator_permit[uid]):
                if float(self.metagraph.S[uid]) < self.config.blacklist.min_stake:
                    return True, "no validator_permit and stake < min_stake"
        return False, "ok"

    def _shared_priority(self, synapse) -> float:
        if synapse.dendrite is None or synapse.dendrite.hotkey is None:
            return 0.0
        hotkey = synapse.dendrite.hotkey
        if hotkey not in self.metagraph.hotkeys:
            return 0.0
        uid = self.metagraph.hotkeys.index(hotkey)
        return float(self.metagraph.S[uid])

    async def _blacklist_handshake(self, synapse: IdentityHandshake) -> Tuple[bool, str]:
        return self._shared_blacklist(synapse)
    async def _blacklist_storage(self, synapse: StorageChallenge) -> Tuple[bool, str]:
        return self._shared_blacklist(synapse)
    async def _blacklist_latency(self, synapse: LatencyChallenge) -> Tuple[bool, str]:
        return self._shared_blacklist(synapse)
    async def _blacklist_gossip(self, synapse: GossipChallenge) -> Tuple[bool, str]:
        return self._shared_blacklist(synapse)
    async def _blacklist_ref(self, synapse: RefIntegrityChallenge) -> Tuple[bool, str]:
        return self._shared_blacklist(synapse)

    async def _priority_handshake(self, synapse: IdentityHandshake) -> float:
        return self._shared_priority(synapse)
    async def _priority_storage(self, synapse: StorageChallenge) -> float:
        return self._shared_priority(synapse)
    async def _priority_latency(self, synapse: LatencyChallenge) -> float:
        return self._shared_priority(synapse)
    async def _priority_gossip(self, synapse: GossipChallenge) -> float:
        return self._shared_priority(synapse)
    async def _priority_ref(self, synapse: RefIntegrityChallenge) -> float:
        return self._shared_priority(synapse)

    # ── lifecycle ───────────────────────────────────────────────────────
    def _preflight(self) -> None:
        ok = asyncio.get_event_loop().run_until_complete(self.client.health())
        if not ok:
            raise RuntimeError(
                f"gitlawb-node at {self.config.gitlawb.node_url} is not healthy. "
                f"Start it first (or check the URL)."
            )

    def run(self) -> None:
        self._preflight()
        self.axon.serve(netuid=self.config.netuid, subtensor=self.subtensor)
        self.axon.start()
        bt.logging.info(f"Miner axon serving on netuid {self.config.netuid}: {self.axon}")

        try:
            while not self.should_exit:
                # Block-based throttle (template style): sleep until the next epoch.
                last = int(self.metagraph.last_update[self.uid])
                while (self.block - last) < self.config.neuron.epoch_length:
                    time.sleep(2)
                    if self.should_exit:
                        break
                if self.should_sync_metagraph():
                    self.resync_metagraph()
                self.step += 1
        except KeyboardInterrupt:
            bt.logging.info("Miner stopped by KeyboardInterrupt")
        except Exception:
            bt.logging.error(f"Fatal in miner run loop:\n{traceback.format_exc()}")
        finally:
            try:
                self.axon.stop()
            except Exception:
                pass
            try:
                asyncio.get_event_loop().run_until_complete(self.client.aclose())
            except Exception:
                pass

    # Context manager
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
    config = build_config(role="miner")
    with Miner(config) as m:
        while True:
            bt.logging.info(f"Miner alive — step {m.step}")
            time.sleep(30)


if __name__ == "__main__":
    main()
