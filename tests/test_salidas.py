# -*- coding: utf-8 -*-
"""R1-R7. Estas reglas son el unico freno real del desk: se prueban una a una
y luego en su orden de precedencia."""
import unittest

from motor import config, salidas
from motor.cartera import Posicion

T0 = 1_700_000_000


def pos(debito=1.0, direccion='LONG', qty=1, ts=T0, strikes=(500.0, 502.0)):
    return Posicion(ref='r', direccion=direccion, subyacente='SPY',
                    sim_larga='A', sim_corta='B', strike_larga=strikes[0],
                    strike_corta=strikes[1], expiracion='2026-09-03', qty=qty,
                    debito=debito, ts_apertura=ts, peak_pct=0.0, lecturas=[],
                    mark=debito, mark_pct=0.0, cerrada=False)


class TestMarkYPico(unittest.TestCase):
    def test_mark_pct_es_porcentaje_de_la_prima(self):
        p = pos(debito=1.0)
        self.assertAlmostEqual(p.actualizar_mark(1.5), 50.0)

    def test_el_pico_no_retrocede(self):
        p = pos(debito=1.0)
        p.actualizar_mark(1.8)          # +80
        p.actualizar_mark(1.2)          # +20
        self.assertAlmostEqual(p.peak_pct, 80.0)
        self.assertAlmostEqual(p.mark_pct, 20.0)

    def test_prima_en_dolares(self):
        self.assertAlmostEqual(pos(debito=1.10, qty=4).prima, 440.0)


class TestR1Escalera(unittest.TestCase):
    def test_sin_peldano_alcanzado_no_hace_nada(self):
        p = pos()
        p.actualizar_mark(1.30)          # +30, por debajo de +40
        self.assertFalse(salidas.r1_escalera(p).cierra)

    def test_candado_de_15_tras_tocar_40(self):
        p = pos()
        p.actualizar_mark(1.45)          # pico +45 => candado +15
        p.actualizar_mark(1.20)          # +20: aun por encima del candado
        self.assertFalse(salidas.r1_escalera(p).cierra)
        p.actualizar_mark(1.14)          # +14 <= +15 => cierra
        d = salidas.r1_escalera(p)
        self.assertTrue(d.cierra)
        self.assertEqual(d.motivo, 'ESCALERA')
        self.assertEqual(d.detalle['candado'], 15.0)

    def test_usa_el_peldano_mas_alto_alcanzado(self):
        p = pos()
        p.actualizar_mark(2.05)          # pico +105 => candado +65
        p.actualizar_mark(1.70)          # +70 > 65 => aguanta
        self.assertFalse(salidas.r1_escalera(p).cierra)
        p.actualizar_mark(1.60)          # +60 <= 65 => cierra
        d = salidas.r1_escalera(p)
        self.assertTrue(d.cierra)
        self.assertEqual(d.detalle['peldano'], 100.0)
        self.assertEqual(d.detalle['candado'], 65.0)

    def test_borde_exacto_del_candado_cierra(self):
        # Se fijan los porcentajes a mano: `1.15 - 1.00` da 14.99999999999999
        # en binario y probaria el borde equivocado.
        p = pos()
        p.peak_pct = 40.0
        p.mark_pct = 15.0                # exactamente en el candado => cierra
        self.assertTrue(salidas.r1_escalera(p).cierra)
        p.mark_pct = 15.01               # un pelo por encima => aguanta
        self.assertFalse(salidas.r1_escalera(p).cierra)


class TestEscaleraAlcanzable(unittest.TestCase):
    """El techo de un vertical de ancho $2 es $2: `mark_pct` no puede pasar de
    (ancho-debito)/debito*100. Varios peldanos de R1 son inalcanzables."""

    def test_debito_bajo_deja_casi_toda_la_escalera_viva(self):
        r = salidas.peldanos_alcanzables(0.70, 2.0)
        self.assertAlmostEqual(r['techo_pct'], 185.7, places=1)
        self.assertEqual(r['vivos'], [40.0, 65.0, 100.0, 161.0])
        self.assertEqual(r['muertos'], [261.0])

    def test_debito_alto_deja_un_solo_peldano(self):
        r = salidas.peldanos_alcanzables(1.30, 2.0)
        self.assertAlmostEqual(r['techo_pct'], 53.8, places=1)
        self.assertEqual(r['vivos'], [40.0])

    def test_el_peldano_261_nunca_es_alcanzable_en_el_rango_de_la_spec(self):
        for debito in (0.70, 0.85, 1.00, 1.15, 1.30):
            r = salidas.peldanos_alcanzables(debito, 2.0)
            self.assertIn(261.0, r['muertos'],
                          'debito %.2f' % debito)


class TestR2Freno(unittest.TestCase):
    def _armado(self, direccion='LONG'):
        p = pos(direccion=direccion)
        for m in (1.30, 1.31, 1.30):      # +30, +31, +30: quieto y cerca del pico
            p.actualizar_mark(m)
        return p

    def test_dispara_con_todas_las_condiciones(self):
        d = salidas.r2_freno(self._armado(), rsi1m=70.0)
        self.assertTrue(d.cierra)
        self.assertEqual(d.motivo, 'FRENO')

    def test_no_dispara_sin_rsi(self):
        # Sin la revalidacion (c) no se cosecha: inventar la condicion que no
        # se pudo comprobar es exactamente lo que el oficial de riesgo no hace.
        self.assertFalse(salidas.r2_freno(self._armado(), rsi1m=None).cierra)

    def test_no_dispara_si_el_rsi_no_confirma(self):
        self.assertFalse(salidas.r2_freno(self._armado(), rsi1m=60.0).cierra)

    def test_no_dispara_por_debajo_del_armado(self):
        p = pos()
        for m in (1.20, 1.21, 1.20):     # +20 < +25
            p.actualizar_mark(m)
        self.assertFalse(salidas.r2_freno(p, rsi1m=90.0).cierra)

    def test_no_dispara_si_todavia_se_mueve(self):
        p = pos()
        for m in (1.30, 1.40, 1.50):     # 10 puntos de dispersion > 4
            p.actualizar_mark(m)
        self.assertFalse(salidas.r2_freno(p, rsi1m=90.0).cierra)

    def test_no_dispara_lejos_del_pico(self):
        p = pos()
        p.actualizar_mark(2.00)          # pico +100
        for m in (1.30, 1.31, 1.30):     # +30: a 70 puntos del pico
            p.actualizar_mark(m)
        self.assertFalse(salidas.r2_freno(p, rsi1m=90.0).cierra)

    def test_short_usa_el_umbral_espejo(self):
        p = self._armado('SHORT')
        self.assertFalse(salidas.r2_freno(p, rsi1m=70.0).cierra)
        self.assertTrue(salidas.r2_freno(p, rsi1m=30.0).cierra)

    def test_necesita_tres_lecturas(self):
        p = pos()
        p.actualizar_mark(1.30)
        p.actualizar_mark(1.30)
        self.assertFalse(salidas.r2_freno(p, rsi1m=90.0).cierra)


class TestR3Stop(unittest.TestCase):
    def test_borde_exacto_menos_50_cierra(self):
        p = pos()
        p.actualizar_mark(0.50)
        d = salidas.r3_stop(p)
        self.assertTrue(d.cierra)
        self.assertEqual(d.motivo, 'SL')

    def test_menos_49_aguanta(self):
        p = pos()
        p.actualizar_mark(0.51)
        self.assertFalse(salidas.r3_stop(p).cierra)

    def test_escala_con_la_prima(self):
        # -50% de una prima de 0.60 son 0.30, no un stop fijo en dolares.
        p = pos(debito=0.60)
        p.actualizar_mark(0.30)
        self.assertTrue(salidas.r3_stop(p).cierra)


class TestR4TimeStop(unittest.TestCase):
    def test_dispara_a_las_35_horas_dentro_de_banda(self):
        p = pos(ts=T0)
        p.actualizar_mark(1.05)          # +5, dentro de (-15, +15)
        d = salidas.r4_timestop(p, T0 + int(3.5 * 3600))
        self.assertTrue(d.cierra)
        self.assertEqual(d.motivo, 'IMPULSO_MUERTO')

    def test_no_dispara_antes_de_tiempo(self):
        p = pos(ts=T0)
        p.actualizar_mark(1.05)
        self.assertFalse(salidas.r4_timestop(p, T0 + 3 * 3600).cierra)

    def test_no_dispara_fuera_de_banda(self):
        p = pos(ts=T0)
        p.actualizar_mark(1.20)          # +20: hay tesis viva
        self.assertFalse(salidas.r4_timestop(p, T0 + 4 * 3600).cierra)

    def test_bordes_de_banda_son_abiertos(self):
        # La banda de §13 es (-15, +15): abierta en ambos extremos. Se fijan
        # los porcentajes a mano porque `1.15 - 1.00` da 14.99999999999999.
        p = pos(ts=T0)
        viejo = T0 + 4 * 3600
        p.mark_pct = 15.0
        self.assertFalse(salidas.r4_timestop(p, viejo).cierra)
        p.mark_pct = -15.0
        self.assertFalse(salidas.r4_timestop(p, viejo).cierra)
        p.mark_pct = 14.9
        self.assertTrue(salidas.r4_timestop(p, viejo).cierra)


class TestR5EOD(unittest.TestCase):
    def test_arranca_a_las_1545_no_a_las_1550(self):
        # El cierre de §8 es 60 s + 60 s + limit al bid: disparar a las 15:50
        # hace imposible estar plano a las 15:50.
        self.assertTrue(salidas.r5_eod(config.EOD_INICIO_ET).cierra)
        self.assertFalse(salidas.r5_eod(config.EOD_INICIO_ET - 1).cierra)

    def test_sigue_cerrando_pasadas_las_1550(self):
        self.assertTrue(salidas.r5_eod(config.EOD_ET).cierra)


class TestWatchdogs(unittest.TestCase):
    def test_r6_dia(self):
        d = salidas.r6_watchdog_dia(-1.5)
        self.assertTrue(d.cierra)
        self.assertEqual(d.motivo, 'WATCHDOG_DIA')
        self.assertFalse(salidas.r6_watchdog_dia(-1.49).cierra)

    def test_r7_total(self):
        d = salidas.r7_circuit_breaker(-4.0)
        self.assertTrue(d.cierra)
        self.assertEqual(d.motivo, 'WATCHDOG_SEMANA')

    def test_r7_manda_sobre_r6(self):
        d = salidas.evaluar_flota(-2.0, -5.0)
        self.assertEqual(d.motivo, 'WATCHDOG_SEMANA')

    def test_flota_limpia_no_cierra(self):
        self.assertFalse(salidas.evaluar_flota(-0.5, -1.0).cierra)


class TestPrecedencia(unittest.TestCase):
    """R5 -> R3 -> R1 -> R2 -> R4."""

    def test_eod_manda_sobre_todo_lo_demas(self):
        p = pos()
        p.actualizar_mark(0.40)          # tambien estaria en SL
        d = salidas.evaluar_posicion(p, config.EOD_INICIO_ET, T0)
        self.assertEqual(d.motivo, 'EOD')

    def test_sl_manda_sobre_la_escalera(self):
        p = pos()
        p.actualizar_mark(1.50)          # pico +50 => candado +15
        p.actualizar_mark(0.45)          # -55: SL y escalera a la vez
        d = salidas.evaluar_posicion(p, 700, T0)
        self.assertEqual(d.motivo, 'SL')

    def test_escalera_y_freno_no_pueden_dispararse_a_la_vez(self):
        # Todo candado esta mas de FRENO_BANDA_PTS por debajo de su peldano
        # (40->15, 65->40, 100->65, 161->100, 261->161: huecos de 25 a 100).
        # Entonces "mark bajo el candado" y "mark a <=10 puntos del pico" son
        # incompatibles: la precedencia R1 -> R2 nunca llega a arbitrar nada.
        for peldano, candado in config.ESCALERA:
            self.assertGreater(peldano - candado, config.FRENO_BANDA_PTS,
                               'peldano %s' % peldano)

    def test_escalera_cierra_cuando_toca(self):
        p = pos()
        p.actualizar_mark(1.45)          # pico +45 => candado +15
        p.actualizar_mark(1.10)          # +10: bajo el candado
        d = salidas.evaluar_posicion(p, 700, T0, rsi1m=90.0)
        self.assertEqual(d.motivo, 'ESCALERA')

    def test_posicion_sana_se_mantiene(self):
        p = pos()
        p.actualizar_mark(1.10)
        d = salidas.evaluar_posicion(p, 700, T0 + 600)
        self.assertFalse(d.cierra)


if __name__ == '__main__':
    unittest.main()
