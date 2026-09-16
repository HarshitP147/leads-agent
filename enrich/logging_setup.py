"""Stdlib logging configuration: one line per node start/end, domain + duration."""

from __future__ import annotations

import logging

# httpx logs an INFO line per request ("HTTP Request: GET ...") even without --debug —
# quiet it (and httpcore's wire-level DEBUG chatter) by default; --debug means "show me
# everything", so it lifts this suppression rather than adding to it (16 Sep, M1 review).
_QUIET_UNLESS_DEBUG = ("httpx", "httpcore")


def configure_logging(*, debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not debug:
        for name in _QUIET_UNLESS_DEBUG:
            logging.getLogger(name).setLevel(logging.WARNING)
