# -*- coding: utf-8 -*-
"""El reloj ET es la base de todo: si el offset esta mal, la ventana de
entradas y el flat de EOD se corren una hora y el desk opera fuera de horario."""
import unittest
from datetime import date

from motor import reloj


class TestDST(unittest.TestCase):
    def test_segundo_domingo_de_marzo_2026(self):
        # 2026-03-01 cae domingo, asi que el 2do domingo es el 8.
        self.assertEqual(reloj._domingo_n(2026, 3, 2), date(2026, 3, 8))

    def test_primer_domingo_de_noviembre_2026(self):
        self.assertEqual(reloj._domingo_n(2026, 11, 1), date(2026, 11, 1))

    def test_verano_es_utc_menos_4(self):
        e = reloj.epoch_de_iso('2026-09-01T18:00:00Z')
        self.assertEqual(reloj.offset_et(e), -4 * 3600)
        self.assertTrue(reloj.es_horario_verano(e))

    def test_invierno_es_utc_menos_5(self):
        e = reloj.epoch_de_iso('2026-01-15T18:00:00Z')
        self.assertEqual(reloj.offset_et(e), -5 * 3600)
        self.assertFalse(reloj.es_horario_verano(e))

    def test_borde_exacto_de_entrada_a_dst(self):
        # A las 06:59 UTC del 8-mar-2026 todavia es EST; a las 07:00 ya es EDT.
        antes = reloj.epoch_de_iso('2026-03-08T06:59:00Z')
        despues = reloj.epoch_de_iso('2026-03-08T07:00:00Z')
        self.assertEqual(reloj.offset_et(antes), -5 * 3600)
        self.assertEqual(reloj.offset_et(despues), -4 * 3600)

    def test_borde_exacto_de_salida_de_dst(self):
        antes = reloj.epoch_de_iso('2026-11-01T05:59:00Z')
        despues = reloj.epoch_de_iso('2026-11-01T06:00:00Z')
        self.assertEqual(reloj.offset_et(antes), -4 * 3600)
        self.assertEqual(reloj.offset_et(despues), -5 * 3600)


class TestConversiones(unittest.TestCase):
    def test_ida_y_vuelta(self):
        e = reloj.epoch_de_et(2026, 9, 1, 15, 50)
        t = reloj.et(e)
        self.assertEqual((t.hour, t.minute), (15, 50))
        self.assertEqual(reloj.minutos_et(e), 950.0)

    def test_apertura_de_mercado_en_verano(self):
        # 13:30 UTC == 9:30 ET en horario de verano.
        e = reloj.epoch_de_iso('2026-09-01T13:30:00Z')
        self.assertEqual(reloj.minutos_et(e), 570.0)

    def test_apertura_de_mercado_en_invierno(self):
        # 14:30 UTC == 9:30 ET en horario estandar.
        e = reloj.epoch_de_iso('2026-01-15T14:30:00Z')
        self.assertEqual(reloj.minutos_et(e), 570.0)

    def test_hhmm(self):
        self.assertEqual(reloj.hhmm('9:45'), 585)
        self.assertEqual(reloj.hhmm('15:50'), 950)

    def test_epoch_de_iso_acepta_fracciones_y_offset(self):
        a = reloj.epoch_de_iso('2026-09-01T13:30:00Z')
        b = reloj.epoch_de_iso('2026-09-01T13:30:00.123456789Z')
        c = reloj.epoch_de_iso('2026-09-01T09:30:00-04:00')
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_fecha_et_usa_la_sesion_no_utc(self):
        # 2026-09-02 00:30 UTC son las 20:30 ET del 1-sep: la sesion es la del 1.
        e = reloj.epoch_de_iso('2026-09-02T00:30:00Z')
        self.assertEqual(reloj.fecha_et(e), '2026-09-01')


if __name__ == '__main__':
    unittest.main()
