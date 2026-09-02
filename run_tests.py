# -*- coding: utf-8 -*-
"""Corre toda la suite. Solo stdlib: `python run_tests.py`."""
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))


def main():
    suite = unittest.defaultTestLoader.discover(
        str(RAIZ / 'tests'), pattern='test_*.py', top_level_dir=str(RAIZ))
    resultado = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if resultado.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
