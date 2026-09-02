# -*- coding: utf-8 -*-
"""Agregacion y anti-lookahead. Si un bucket en formacion se cuela en la
serie, TODA la señal queda contaminada y el backtest miente."""
import unittest

from motor import datos, reloj

# 2026-09-01 (martes), 9:30 ET.
APERTURA = reloj.epoch_de_et(2026, 9, 1, 9, 30)


def minutos(n, inicio=APERTURA, precio=500.0):
    return [datos.Vela(inicio + i * 60, precio + i, precio + i + 0.5,
                       precio + i - 0.5, precio + i + 0.25)
            for i in range(n)]


class TestAgregacion(unittest.TestCase):
    def test_cinco_velas_forman_una_de_5m(self):
        v = datos.agregar(minutos(5), 5, ahora=APERTURA + 300)
        self.assertEqual(len(v), 1)
        b = v[0]
        self.assertEqual(b.ts, APERTURA)
        self.assertAlmostEqual(b.o, 500.0)          # open de la primera
        self.assertAlmostEqual(b.c, 504.25)         # close de la ultima
        self.assertAlmostEqual(b.h, 504.5)          # max de los highs
        self.assertAlmostEqual(b.l, 499.5)          # min de los lows

    def test_bucket_en_formacion_no_se_emite(self):
        # 7 minutos: el 2do bucket (5-10) aun no cerro.
        v = datos.agregar(minutos(7), 5, ahora=APERTURA + 420)
        self.assertEqual(len(v), 1, 'solo el bucket ya cerrado')

    def test_bucket_se_emite_justo_al_cerrar(self):
        v = datos.agregar(minutos(10), 5, ahora=APERTURA + 600)
        self.assertEqual(len(v), 2)

    def test_un_segundo_antes_todavia_no(self):
        v = datos.agregar(minutos(10), 5, ahora=APERTURA + 599)
        self.assertEqual(len(v), 1)

    def test_sin_ahora_emite_todo(self):
        # Camino de backtest: la serie ya es historica y esta cerrada entera.
        self.assertEqual(len(datos.agregar(minutos(10), 5)), 2)

    def test_alineacion_con_el_reloj_de_mercado(self):
        # 9:30 ET es multiplo de 5, 15 y 30 min, asi que los buckets caen en
        # 9:30 / 9:45 / 10:00 igual que en cualquier plataforma.
        v15 = datos.agregar(minutos(30), 15, ahora=APERTURA + 1800)
        self.assertEqual([b.ts for b in v15],
                         [APERTURA, APERTURA + 900])
        v30 = datos.agregar(minutos(60), 30, ahora=APERTURA + 3600)
        self.assertEqual([b.ts for b in v30], [APERTURA, APERTURA + 1800])

    def test_huecos_no_rompen_la_agregacion(self):
        # IEX es fino: faltan minutos. El bucket se forma con lo que hay.
        vs = minutos(10)
        del vs[2:4]
        v = datos.agregar(vs, 5, ahora=APERTURA + 600)
        self.assertEqual(len(v), 2)

    def test_serie_vacia(self):
        self.assertEqual(datos.agregar([], 5, ahora=APERTURA), [])


class TestFrescura(unittest.TestCase):
    def test_se_mide_contra_el_cierre_de_la_vela(self):
        # Vela que abrio hace 2 min cerro hace 1 min: 1 minuto de antiguedad.
        v = [datos.Vela(APERTURA, 500, 500, 500, 500)]
        self.assertAlmostEqual(datos.frescura_min(v, APERTURA + 120), 1.0)

    def test_sin_velas_es_infinito(self):
        self.assertEqual(datos.frescura_min([], APERTURA), float('inf'))

    def test_recien_cerrada_es_cero(self):
        v = [datos.Vela(APERTURA, 500, 500, 500, 500)]
        self.assertAlmostEqual(datos.frescura_min(v, APERTURA + 60), 0.0)


class TestSesionRegular(unittest.TestCase):
    def test_dentro_de_la_sesion(self):
        self.assertTrue(datos.en_sesion_regular(APERTURA))
        self.assertTrue(datos.en_sesion_regular(
            reloj.epoch_de_et(2026, 9, 1, 15, 59)))

    def test_premarket_fuera(self):
        self.assertFalse(datos.en_sesion_regular(
            reloj.epoch_de_et(2026, 9, 1, 8, 0)))

    def test_cierre_es_exclusivo(self):
        self.assertFalse(datos.en_sesion_regular(
            reloj.epoch_de_et(2026, 9, 1, 16, 0)))


if __name__ == '__main__':
    unittest.main()
