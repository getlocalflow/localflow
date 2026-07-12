#!/usr/bin/env python3
"""LocalFlow launcher shim (macOS). Keeps ./venv/bin/python3 localflow.py
working after the core/macos/windows restructure."""
from macos.localflow import main

if __name__ == "__main__":
    main()
