"""Small command-line entry point for Ron."""

from ron.ai import SettingsError
from ron.app import RonApplication


def main() -> None:
    """Assemble Ron, start every system, and keep him running."""
    try:
        application = RonApplication()
    except SettingsError as error:
        raise SystemExit(f"Ron configuration error: {error}") from error
    raise SystemExit(application.run())


if __name__ == "__main__":
    main()
