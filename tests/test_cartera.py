# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from motor import cartera, diario
from motor.cartera import Posicion
from motor.rest import Respuesta


class ClienteVacio(object):
    pass


class Cfg(object):
    def __init__(self, real=False):
        self.opera_de_verdad = real


class ClienteCuenta(object):
    def __init__(self, numero='PA3YMXK3ZBP1', nivel=3, real=False):
        self.cfg = Cfg(real)
        self.numero = numero
        self.nivel = nivel

    def cuenta(self):
        return Respuesta(True, {'account_number': self.numero,
                                'options_trading_level': self.nivel,
                                'equity': '100000'}, 200)


def posicion(qty=3):
    return Posicion(ref='R', direccion='LONG', subyacente='SPY',
                    sim_larga='L', sim_corta='S', strike_larga=500,
                    strike_corta=502, expiracion='2026-09-03', qty=qty,
                    debito=1.0, ts_apertura=1000, peak_pct=0, lecturas=[],
                    mark=0.8, mark_pct=-20, cerrada=False)


class TestCartera(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = cartera.Cartera(
            ClienteVacio(), diario.Diario(Path(self.tmp.name)), ahora=lambda: 2000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pnl_total_no_duplica_flotante_incluido_en_equity(self):
        self.c.equity_inicio_total = 100000
        self.c.equity = 99000
        self.c.posiciones = [posicion(qty=5)]  # -$100 adicional local
        self.assertAlmostEqual(self.c.pnl_total_pct(), -1.0)

    def test_cierre_parcial_conserva_y_protege_el_residuo(self):
        p = posicion(qty=3)
        self.c.posiciones = [p]
        realizado = self.c.registrar_cierre(p, 0.8, 'SL', qty=1)
        self.assertAlmostEqual(realizado, -20.0)
        self.assertEqual(p.qty, 2)
        self.assertFalse(p.cerrada)
        self.assertIn(p, self.c.vivas())

    def test_cierre_total_elimina_la_posicion(self):
        p = posicion(qty=2)
        self.c.posiciones = [p]
        self.c.registrar_cierre(p, 1.2, 'EOD', qty=2)
        self.assertTrue(p.cerrada)
        self.assertEqual(self.c.vivas(), [])

    def test_cuenta_incorrecta_falla_cerrado(self):
        self.c.cli = ClienteCuenta(numero='OTRA')
        self.assertIn('CUENTA_INCORRECTA', self.c.refrescar_cuenta())

    def test_modo_real_exige_nivel_tres(self):
        self.c.cli = ClienteCuenta(nivel=2, real=True)
        self.assertIn('NIVEL_OPCIONES_INSUFICIENTE',
                      self.c.refrescar_cuenta())


if __name__ == '__main__':
    unittest.main()
