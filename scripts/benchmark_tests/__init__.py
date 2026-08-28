"""Declarative benchmark-test components and the core task registry."""

from .registry import (
    BenchmarkComponentError,
    DEFAULT_SUITE,
    SUITE_CHOICES,
    core_task_catalog,
    get_core_task,
    list_core_components,
    suite_task_catalog,
)

__all__ = [
    "BenchmarkComponentError",
    "DEFAULT_SUITE",
    "SUITE_CHOICES",
    "core_task_catalog",
    "get_core_task",
    "list_core_components",
    "suite_task_catalog",
]
