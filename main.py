#!/usr/bin/env python3
# main.py
# MIT License - Copyright (c) 2026 Luc Prevost
"""Convenience launcher for a source checkout: ``python main.py --data-root examples --product Porsche_911``.

Installed users should run the ``fromversionzero`` console command instead.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from fromversionzero.cli import main

if __name__ == "__main__":
    main()
