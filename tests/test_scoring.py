"""Sanity tests for the scoring math — these don't need a chain or axon."""

from gitlawbnet.validator.scoring import (
    ChallengeOutcome,
    ScoreBook,
    composite_score,
    gossip_score,
    latency_score,
    storage_score,
    uptime_score,
)


def test_perfect_miner_scores_one():
    o = ChallengeOutcome(
        challenges_sent=5, challenges_answered=5,
        cids_requested=10, cids_proven=10,
        latency_ms_samples=[100.0, 150.0],
        gossip_sent=3, gossip_received_in_time=3, peer_count_avg=20.0,
    )
    assert uptime_score(o) == 1.0
    assert storage_score(o) == 1.0
    assert latency_score(o) == 1.0
    assert 1.0 <= gossip_score(o) <= 1.0 + 1e-6  # peer bonus is clipped to 1
    assert abs(composite_score(o) - 1.0) < 1e-6


def test_silent_miner_scores_below_uptime_weight():
    """Silent miner has uptime=0 but neutral 0.5 on components the validator
    couldn't probe (storage/latency/gossip). Composite = 0.35*0 + 0.25*0.5 +
    0.20*0.5 + 0.20*0.5 = 0.325 worst-case."""
    o = ChallengeOutcome(challenges_sent=5, challenges_answered=0)
    score = composite_score(o)
    assert 0.3 < score < 0.4   # neutral on the three "couldn't probe" components

def test_silent_miner_with_probes_scores_zero():
    """If the validator DID probe but got nothing back, that's real failure."""
    o = ChallengeOutcome(
        challenges_sent=5, challenges_answered=0,
        cids_requested=3, cids_proven=0,
        gossip_sent=2, gossip_received_in_time=0,
        latency_ms_samples=[8000.0],
    )
    assert composite_score(o) == 0.0


def test_partial_storage_only():
    """uptime=1, storage=0.5, latency=neutral 0.5, gossip=neutral 0.5."""
    o = ChallengeOutcome(
        challenges_sent=2, challenges_answered=2,
        cids_requested=10, cids_proven=5,
    )
    # 0.35*1 + 0.25*0.5 + 0.20*0.5 + 0.20*0.5 = 0.675
    assert abs(composite_score(o) - 0.675) < 1e-6


def test_score_book_ema_blends():
    book = ScoreBook(num_uids=3, alpha=0.5)
    perfect = ChallengeOutcome(
        challenges_sent=1, challenges_answered=1,
        cids_requested=1, cids_proven=1,
        latency_ms_samples=[100.0],
        gossip_sent=1, gossip_received_in_time=1, peer_count_avg=5.0,
    )
    book.update({0: perfect})
    first = book.scores[0]
    book.update({0: ChallengeOutcome(challenges_sent=1)})  # silent epoch
    assert book.scores[0] < first  # decayed


def test_weights_sum_to_one_when_nonzero():
    book = ScoreBook(num_uids=4, alpha=1.0)
    book.scores[:] = [0.0, 0.25, 0.5, 0.25]
    w = book.weights()
    assert abs(w.sum() - 1.0) < 1e-6
