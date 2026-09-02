# -*- coding: utf-8 -*-
import unittest

from motor import indicadores
from motor.datos import Vela


def velas(rangos):
    """rangos: lista de (o,h,l,c). ts espaciados 300 s."""
    return [Vela(1_700_000_000 + i * 300, o, h, l, c)
            for i, (o, h, l, c) in enumerate(rangos)]


class TestEMA(unittest.TestCase):
    def test_semilla_es_sma_y_luego_suaviza(self):
        # periodo 3 => k = 2/4 = 0.5
        out = indicadores.ema([1, 2, 3, 4, 5], 3)
        self.assertEqual(out[:2], [None, None])
        self.assertAlmostEqual(out[2], 2.0)      # SMA(1,2,3)
        self.assertAlmostEqual(out[3], 3.0)      # (4-2)*0.5+2
        self.assertAlmostEqual(out[4], 4.0)      # (5-3)*0.5+3

    def test_serie_corta_devuelve_todo_none(self):
        self.assertEqual(indicadores.ema([1, 2], 5), [None, None])

    def test_longitud_alineada(self):
        datos = list(range(60))
        self.assertEqual(len(indicadores.ema(datos, 50)), 60)


class TestRSI(unittest.TestCase):
    def test_subida_monotona_da_100(self):
        out = indicadores.rsi_wilder(list(range(1, 40)), 14)
        self.assertIsNone(out[13])
        self.assertAlmostEqual(out[14], 100.0)
        self.assertAlmostEqual(out[-1], 100.0)

    def test_bajada_monotona_da_0(self):
        out = indicadores.rsi_wilder(list(range(40, 1, -1)), 14)
        self.assertAlmostEqual(out[-1], 0.0)

    def test_serie_plana_es_neutral_no_100(self):
        # Sin movimiento no hay fuerza: 50, no 100. Un RSI de 100 en una serie
        # plana dispararia S4 y el freno R2 con datos muertos.
        out = indicadores.rsi_wilder([100.0] * 40, 14)
        self.assertAlmostEqual(out[-1], 50.0)

    def test_calentamiento_es_none(self):
        out = indicadores.rsi_wilder(list(range(1, 40)), 14)
        self.assertTrue(all(v is None for v in out[:14]))

    def test_serie_demasiado_corta(self):
        self.assertTrue(all(v is None
                            for v in indicadores.rsi_wilder([1, 2, 3], 14)))


class TestATR(unittest.TestCase):
    def test_rango_constante_da_atr_constante(self):
        v = velas([(100.5, 101.0, 100.0, 100.5)] * 40)
        out = indicadores.atr_wilder(v, 20)
        self.assertIsNone(out[19])
        self.assertAlmostEqual(out[20], 1.0)
        self.assertAlmostEqual(out[-1], 1.0)

    def test_gap_cuenta_en_el_rango_verdadero(self):
        # Cierre previo 100.5; barra siguiente 110-111 => TR = 111-100.5 = 10.5
        v = velas([(100.5, 101.0, 100.0, 100.5),
                   (110.0, 111.0, 110.0, 110.5)])
        tr = indicadores.rango_verdadero(v)
        self.assertIsNone(tr[0])
        self.assertAlmostEqual(tr[1], 10.5)

    def test_alineacion(self):
        v = velas([(100.5, 101.0, 100.0, 100.5)] * 30)
        self.assertEqual(len(indicadores.atr_wilder(v, 20)), 30)


if __name__ == '__main__':
    unittest.main()
