"""The audit hash-chain must survive concurrent appends (the enterprise audit
empirically reproduced a fork: a lock-free read-prev-then-append let two writers
record the same prev_hash, so verify_chain reported a permanent false-positive
tamper break under normal multi-user load). The append critical section is now
serialized (in-process lock + fcntl.flock + fsync), so the chain stays intact.
"""
from __future__ import annotations

import concurrent.futures as cf

from brand_ops.activity import append_event, read_events, verify_chain


def test_concurrent_appends_keep_chain_intact(tmp_path):
    n = 64

    def one(i):
        append_event(
            "actor", "user", "did", brand="b", target=str(i), ts="2026-01-01", kits_dir=tmp_path
        )

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(one, range(n)))

    report = verify_chain(kits_dir=tmp_path)
    assert report["ok"] is True, report
    # every concurrent append is a valid, linked entry (no fork): the chain
    # verifier counts all n as chained, with no break.
    assert report["chained"] == n
    assert report["break_at"] is None
    # read_events default-caps the page; a generous limit returns them all.
    assert len(read_events(limit=n + 10, kits_dir=tmp_path)) == n
