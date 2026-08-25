"""Declarative benchmark-test components and the core task registry."""

from .registry import (
    BenchmarkComponentError,
    core_task_catalog,
    get_core_task,
    list_core_components,
)

__all__ = [
    "BenchmarkComponentError",
    "core_task_catalog",
    "get_core_task",
    "list_core_components",
]
