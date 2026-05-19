"""
Score aggregation for Gitlawbnet.

Each miner gets four component scores in [0, 1]:
    uptime   — fraction of recent challenges the miner answered at all
    storage  — fraction of requested CIDs that were proven pinned
    latency  — 1.0 at or below `target_ms`, decaying linearly to 0 at `max_ms`
    gossip   — fraction of gossip events received before the deadline,
               with a small bonus for healthy peer counts

The composite is the weighted sum (35 / 25 / 20 / 20) clamped to [0, 1].

Scores are passed through an EMA so a single bad epoch doesn't tank a
miner — and a single perfect epoch doesn't let a Sybil race to the top.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import numpy as np

UPTIME_W = 0.35
STORAGE_W = 0.25
LATENCY_W = 0.20
GOSSIP_W = 0.20

assert abs(UPTIME_W + STORAGE_W + LATENCY_W + GOSSIP_W - 1.0) < 1e-9


@dataclass
class ChallengeOutcome:
    """Per-miner aggregate for a single validation epoch."""

    challenges_sent: int = 0
    challenges_answered: int = 0

    cids_requested: int = 0
    cids_proven: int = 0

    latency_ms_samples: list = field(default_factory=list)

    gossip_sent: int = 0          # # of recent gossipsub event_ids the validator probed
    gossip_received_in_time: int = 0  # # of those the miner's node had in its events table
    peer_count_avg: float = 0.0

    miner_did: str | None = None  # Reported in the handshake; used for Sybil detection


# ── component scores ────────────────────────────────────────────────────────
def uptime_score(o: ChallengeOutcome) -> float:
    if o.challenges_sent == 0:
        return 0.0
    return o.challenges_answered / o.challenges_sent


def storage_score(o: ChallengeOutcome) -> float:
    # If the validator couldn't fetch any truth blocks itself, we can't
    # judge the miner — return a NEUTRAL score (0.5) instead of 0.0, so
    # the miner isn't punished for the validator's own connectivity.
    if o.cids_requested == 0:
        return 0.5
    return o.cids_proven / o.cids_requested


def latency_score(o: ChallengeOutcome, target_ms: float = 500.0, max_ms: float = 5000.0) -> float:
    if not o.latency_ms_samples:
        return 0.5    # neutral if no latency probe ran this round
    median = float(np.median(o.latency_ms_samples))
    if median <= target_ms:
        return 1.0
    if median >= max_ms:
        return 0.0
    return 1.0 - (median - target_ms) / (max_ms - target_ms)


def gossip_score(o: ChallengeOutcome) -> float:
    # No gossip events were probed this round (e.g. quiet network) — neutral.
    if o.gossip_sent == 0:
        return 0.5
    base = o.gossip_received_in_time / o.gossip_sent
    # Small bonus for being well-connected, capped at +0.05.
    peer_bonus = min(0.05, max(0.0, (o.peer_count_avg - 4.0) / 100.0))
    return max(0.0, min(1.0, base + peer_bonus))


def composite_score(o: ChallengeOutcome) -> float:
    return (
        UPTIME_W * uptime_score(o)
        + STORAGE_W * storage_score(o)
        + LATENCY_W * latency_score(o)
        + GOSSIP_W * gossip_score(o)
    )


# ── EMA-tracked score book ──────────────────────────────────────────────────
class ScoreBook:
    """Maintains an EMA score per miner UID; produces weight vectors.

    Also tracks which DID each UID has claimed across handshakes so the
    validator can detect Sybil farming (one gitlawb-node DID being
    operated by multiple hotkeys → all those hotkeys get score 0).
    """

    def __init__(self, num_uids: int, alpha: float = 0.1):
        self.alpha = alpha
        self.scores = np.zeros(num_uids, dtype=np.float32)
        self.hotkeys: list[str] = []
        self.uid_to_did: Dict[int, str] = {}

    def resize(self, num_uids: int, hotkeys: list[str] | None = None) -> None:
        """Resize for metagraph growth/shrink. Zero out UIDs whose hotkey
        changed (= deregistered → re-registered as someone else)."""
        if hotkeys is not None and self.hotkeys:
            overlap = min(len(self.hotkeys), len(hotkeys))
            for uid in range(overlap):
                if self.hotkeys[uid] != hotkeys[uid]:
                    self.scores[uid] = 0.0
                    self.uid_to_did.pop(uid, None)
        if num_uids != self.scores.size:
            new = np.zeros(num_uids, dtype=np.float32)
            n = min(num_uids, self.scores.size)
            new[:n] = self.scores[:n]
            self.scores = new
        if hotkeys is not None:
            self.hotkeys = list(hotkeys)

    def record_did(self, uid: int, did: str) -> None:
        self.uid_to_did[uid] = did

    def sybil_mask(self) -> Dict[int, bool]:
        """Return {uid: True} for every UID whose DID is claimed by ≥2 UIDs."""
        from collections import Counter
        counts = Counter(self.uid_to_did.values())
        return {uid: counts[did] >= 2 for uid, did in self.uid_to_did.items()}

    def update(self, outcomes: Dict[int, ChallengeOutcome]) -> None:
        sybils = self.sybil_mask()
        for uid, outcome in outcomes.items():
            if uid >= self.scores.size:
                continue
            new_score = 0.0 if sybils.get(uid) else composite_score(outcome)
            self.scores[uid] = (1.0 - self.alpha) * self.scores[uid] + self.alpha * new_score

    def weights(self) -> np.ndarray:
        """Return a normalised weight vector summing to 1.0 (or all zeros)."""
        total = float(self.scores.sum())
        if total <= 0.0:
            return np.zeros_like(self.scores)
        return self.scores / total

    # ── persistence ────────────────────────────────────────────────────
    def save(self, path: str) -> None:
        import json
        np.savez(
            path,
            scores=self.scores,
            hotkeys=np.array(self.hotkeys, dtype=object),
            uid_did_json=json.dumps(self.uid_to_did),
            alpha=np.array([self.alpha]),
        )

    def load(self, path: str) -> bool:
        """Load if file exists; returns True iff a state was loaded."""
        import json
        import os
        if not os.path.exists(path):
            return False
        state = np.load(path, allow_pickle=True)
        self.scores = state["scores"].astype(np.float32)
        self.hotkeys = list(state["hotkeys"])
        self.uid_to_did = {int(k): v for k, v in json.loads(str(state["uid_did_json"])).items()}
        return True
