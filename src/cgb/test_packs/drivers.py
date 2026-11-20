"""Reviewed installed execution adapters. Discovery imports no executor code.

New drivers enter through a source PR, never a manifest import path or Python
entry point. Keep an executor and its data-only grader under one immutable ID.
"""

from dataclasses import dataclass
from types import MappingProxyType

from ..jsonio import require


def _paired_execute(*args, **kwargs):
    from .runtime import _paired_execute_pair
    return _paired_execute_pair(*args, **kwargs)


def _paired_grade(*args, **kwargs):
    from .grading import grade_pair
    return grade_pair(*args, **kwargs)


@dataclass(frozen=True)
class Driver:
    id: str
    grader: str
    execute: object
    grade: object


DRIVERS = MappingProxyType({"paired-agent.v1": Driver("paired-agent.v1", "safe-useful-family-macro.v1", _paired_execute, _paired_grade)})


def get(identity, grader=None):
    require(identity in DRIVERS, "unreviewed execution driver")
    driver = DRIVERS[identity]
    require(grader is None or driver.grader == grader, "driver/grader contract differs")
    return driver
