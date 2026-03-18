"""Pytest plugin: kill test process if memory exceeds 2 GB."""
import resource

MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


def pytest_configure(config):
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, hard))
