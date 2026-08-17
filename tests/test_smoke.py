from ron.__main__ import main


def test_main_entry_point_is_callable() -> None:
    # The real entry point now owns a long-running terminal application. Smoke
    # tests verify assembly without launching an interactive process.
    assert callable(main)
