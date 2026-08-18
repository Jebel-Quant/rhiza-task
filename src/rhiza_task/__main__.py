"""Support ``python -m rhiza_task``, for a checkout without the console script installed."""

from .cli import main

if __name__ == "__main__":  # pragma: no cover - trivial delegation
    main()
