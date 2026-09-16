"""Stdlib logging configuration: one line per node start/end, domain + duration."""

from __future__ import annotations

import logging


def configure_logging(*, debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
