"""Placeholder so CI has a runnable test from day one.

Real tests land with M1:
- golden `.ktest` fixtures (version 2 and 3, produced by real KLEE runs),
- hypothesis round-trip tests once a reference writer exists,
- replay acceptance test on a KLEE tutorial example (get_sign).
"""

import pytest


@pytest.mark.skip(reason="M1: golden fixtures land with the ktest parser")
def test_parse_minimal_ktest() -> None:
    assert False
