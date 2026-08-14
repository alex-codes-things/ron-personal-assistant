"""Small command-line entry point for Ron."""

from ron.app import RonApplication


def main() -> None:
    """Assemble Ron, start every system, and keep him running."""
    application = RonApplication()
    raise SystemExit(application.run())


if __name__ == "__main__":
    main()
