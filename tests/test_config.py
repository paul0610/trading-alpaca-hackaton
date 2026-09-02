# -*- coding: utf-8 -*-
import unittest

from motor import config


class TestModos(unittest.TestCase):
    def test_replay_incompleto_falla_cerrado(self):
        cfg = config.Config({'ALPACA_API_KEY': 'PK_OK',
                             'ALPACA_SECRET_KEY': 'SECRET_OK',
                             'ALPACA_PAPER_TRADE': 'true',
                             'MODO': 'replay', 'SIMBOLOS': 'SPY'})
        self.assertTrue(any('MODO invalido' in p for p in cfg.validar()))


if __name__ == '__main__':
    unittest.main()
