# -*- coding: utf-8 -*-
import unittest

from motor.contratos import Quote, Spread, cotizar
from motor.rest import Respuesta


def spread(oi_larga, oi_corta, volumen=100):
    larga = Quote('L', 3.00, 3.05, 500, 'call', oi_larga, volumen, 0.21)
    corta = Quote('S', 2.00, 2.05, 502, 'call', oi_corta, volumen, 0.19)
    return Spread('LONG', larga, corta, '2026-09-03', 'SPY', 500.5)


class TestLiquidezReal(unittest.TestCase):
    def test_oi_conocido_bajo_no_se_perdona_por_volumen(self):
        ok, razon, _ = spread(20, 20, volumen=500).liquidez()
        self.assertFalse(ok)
        self.assertEqual(razon, 'SIN_LIQUIDEZ:OI_BAJO')

    def test_volumen_es_fallback_si_oi_no_esta_disponible(self):
        self.assertTrue(spread(None, None, volumen=50).liquidez()[0])
        self.assertFalse(spread(None, None, volumen=49).liquidez()[0])

    def test_iv_de_ambas_patas_llega_al_snapshot(self):
        d = spread(100, 100).dict()
        self.assertEqual(d['larga']['iv'], 0.21)
        self.assertEqual(d['corta']['iv'], 0.19)


class ClienteQuotes(object):
    def datos(self, *args, **kwargs):
        return Respuesta(True, {'quotes': {'L': {'bp': 1, 'ap': 1.1}}}, 200)


class TestQuotesCompletas(unittest.TestCase):
    def test_una_pata_ausente_falla_cerrado(self):
        q, err = cotizar(ClienteQuotes(), ['L', 'S'])
        self.assertEqual(q, {})
        self.assertIn('SIN_QUOTE:S', err)


if __name__ == '__main__':
    unittest.main()
