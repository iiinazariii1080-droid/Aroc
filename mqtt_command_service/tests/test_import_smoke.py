"""
Smoke tests to ensure key top-level modules can be imported.

This also helps coverage include these modules (they are not imported by unit tests otherwise).
"""


def test_import_main_module() -> None:
    import main  # noqa: F401


def test_import_telemetry_module() -> None:
    import telemetry  # noqa: F401


