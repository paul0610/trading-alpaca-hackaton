# -*- coding: utf-8 -*-
"""DoD de I2: al menos un test por gate, incluidos los bordes."""
import tempfile
import unittest
from pathlib import Path

from motor import config, gates, reloj

# Martes 1-sep-2026, sesion normal.
def et(h, m, s=0):
    return reloj.epoch_de_et(2026, 9, 1, h, m, s)


def ctx(**kw):
    kw.setdefault('ahora', et(10, 30))
    return gates.Contexto(**kw)


class TestG1Halt(unittest.TestCase):
    def test_sin_halt_pasa(self):
        self.assertTrue(gates.g1_halt(ctx()).ok)

    def test_con_halt_bloquea(self):
        v = gates.g1_halt(ctx(halt='WATCHDOG_DIA'))
        self.assertFalse(v.ok)
        self.assertIn('WATCHDOG_DIA', v.razon)


class TestG2Horario(unittest.TestCase):
    def test_dentro_de_ventana(self):
        self.assertTrue(gates.g2_horario(ctx(ahora=et(10, 30))).ok)

    def test_borde_inferior_945_pasa(self):
        self.assertTrue(gates.g2_horario(ctx(ahora=et(9, 45))).ok)

    def test_un_minuto_antes_no_pasa(self):
        self.assertFalse(gates.g2_horario(ctx(ahora=et(9, 44))).ok)

    def test_borde_superior_1500_pasa(self):
        self.assertTrue(gates.g2_horario(ctx(ahora=et(15, 0))).ok)

    def test_despues_de_1500_no_pasa(self):
        self.assertFalse(gates.g2_horario(ctx(ahora=et(15, 1))).ok)

    def test_apertura_caotica_bloqueada(self):
        self.assertFalse(gates.g2_horario(ctx(ahora=et(9, 31))).ok)


class TestG3PresupuestoDia(unittest.TestCase):
    def test_por_debajo_del_tope(self):
        self.assertTrue(gates.g3_presupuesto_dia(ctx(trades_hoy=7)).ok)

    def test_en_el_tope_bloquea(self):
        self.assertFalse(gates.g3_presupuesto_dia(ctx(trades_hoy=8)).ok)


class TestG4Flota(unittest.TestCase):
    def test_dos_posiciones_pasa(self):
        self.assertTrue(gates.g4_flota(ctx(posiciones_vivas=2)).ok)

    def test_tres_posiciones_bloquea(self):
        self.assertFalse(gates.g4_flota(ctx(posiciones_vivas=3)).ok)


class TestG5Exposicion(unittest.TestCase):
    def test_justo_en_el_tope_pasa(self):
        self.assertTrue(gates.g5_exposicion(500.0, 1000.0, 0.0).ok)

    def test_un_dolar_sobre_el_tope_bloquea(self):
        self.assertFalse(gates.g5_exposicion(501.0, 1000.0, 0.0).ok)

    def test_la_prima_pendiente_reserva_exposicion(self):
        # 1000 vivo + 400 pendiente + 200 nuevo = 1600 > 1500.
        # Sin contar lo pendiente, dos ciclos seguidos romperian el techo.
        self.assertFalse(gates.g5_exposicion(200.0, 1000.0, 400.0).ok)


class TestG6Cooldown(unittest.TestCase):
    def test_sin_cierres_rojos_pasa(self):
        self.assertTrue(gates.g6_cooldown(ctx(ultimo_cierre_rojo=None)).ok)

    def test_a_los_30_minutos_exactos_pasa(self):
        ahora = et(10, 30)
        self.assertTrue(gates.g6_cooldown(
            ctx(ahora=ahora, ultimo_cierre_rojo=ahora - 30 * 60)).ok)

    def test_a_los_29_minutos_bloquea(self):
        ahora = et(10, 30)
        v = gates.g6_cooldown(ctx(ahora=ahora,
                                  ultimo_cierre_rojo=ahora - 29 * 60))
        self.assertFalse(v.ok)
        self.assertEqual(v.razon, 'COOLDOWN_REVANCHA')


class TestG7Noticias(unittest.TestCase):
    def test_hora_limpia_pasa(self):
        self.assertTrue(gates.g7_noticias(ctx(ahora=et(11, 30))).ok)

    def test_ventana_fallback_de_la_tarde_bloquea(self):
        self.assertFalse(gates.g7_noticias(ctx(ahora=et(14, 0))).ok)

    def test_ventana_de_las_10_bloquea(self):
        # Franja anadida: ISM / JOLTS / Confianza salen a las 10:00 ET y la
        # spec original la dejaba descubierta dentro de la ventana de entradas.
        self.assertFalse(gates.g7_noticias(ctx(ahora=et(10, 0))).ok)

    def test_evento_del_csv_bloquea_mas_menos_15_min(self):
        eventos = [('2026-09-01', reloj.hhmm('11:00'), 'ISM')]
        self.assertFalse(gates.g7_noticias(
            ctx(ahora=et(11, 14), noticias=eventos)).ok)
        self.assertFalse(gates.g7_noticias(
            ctx(ahora=et(10, 46), noticias=eventos)).ok)
        self.assertTrue(gates.g7_noticias(
            ctx(ahora=et(11, 16), noticias=eventos)).ok)

    def test_evento_de_otra_fecha_no_bloquea(self):
        eventos = [('2026-09-02', reloj.hhmm('11:00'), 'ISM')]
        self.assertTrue(gates.g7_noticias(
            ctx(ahora=et(11, 0), noticias=eventos)).ok)

    def test_csv_roto_no_relaja_el_fallback(self):
        # Fail-closed: un archivo invalido degrada a las ventanas genericas,
        # nunca abre un hueco.
        self.assertFalse(gates.g7_noticias(
            ctx(ahora=et(14, 0), noticias=[],
                noticias_problema='ESQUEMA_INVALIDO')).ok)


class TestCargarNoticias(unittest.TestCase):
    def test_archivo_ausente(self):
        eventos, problema = gates.cargar_noticias(
            Path(tempfile.gettempdir()) / 'no_existe_remora.csv')
        self.assertEqual(problema, 'SIN_ARCHIVO')
        self.assertEqual(eventos, [])

    def test_archivo_valido(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'noticias.csv'
            p.write_text('fecha,hora_et,impacto,evento\n'
                         '2026-09-02,08:30,high,CPI\n'
                         '2026-09-02,10:00,low,Ruido\n', encoding='utf-8')
            eventos, problema = gates.cargar_noticias(p)
        self.assertEqual(problema, '')
        self.assertEqual(eventos, [('2026-09-02', 510, 'CPI')])

    def test_esquema_invalido(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'noticias.csv'
            p.write_text('cuando,que\n2026-09-02,CPI\n', encoding='utf-8')
            _, problema = gates.cargar_noticias(p)
        self.assertEqual(problema, 'ESQUEMA_INVALIDO')


class TestG8Frescura(unittest.TestCase):
    def test_datos_frescos_pasan(self):
        self.assertTrue(gates.g8_datos_frescos(ctx(frescura_min=1.0)).ok)

    def test_tres_minutos_exactos_bloquea(self):
        self.assertFalse(gates.g8_datos_frescos(ctx(frescura_min=3.0)).ok)

    def test_sin_velas_bloquea(self):
        v = gates.g8_datos_frescos(ctx(frescura_min=float('inf')))
        self.assertFalse(v.ok)
        self.assertIsNone(v.detalle['frescura_min'])


class FakeSpread(object):
    def __init__(self, ok, razon='SIN_LIQUIDEZ'):
        self._ok = ok
        self._razon = razon

    def liquidez(self):
        return self._ok, ('' if self._ok else self._razon), {'x': 1}


class TestG10Liquidez(unittest.TestCase):
    def test_liquido_pasa(self):
        self.assertTrue(gates.g10_liquidez(FakeSpread(True)).ok)

    def test_iliquido_bloquea_con_razon(self):
        v = gates.g10_liquidez(FakeSpread(False, 'SIN_LIQUIDEZ:SPREAD_ANCHO'))
        self.assertFalse(v.ok)
        self.assertEqual(v.razon, 'SIN_LIQUIDEZ:SPREAD_ANCHO')


class TestEvaluarPrevios(unittest.TestCase):
    def test_todos_los_gates_se_evaluan_aunque_uno_falle(self):
        # El diario debe mostrar el cuadro completo, no solo el primer "no".
        ok, veredictos = gates.evaluar_previos(
            ctx(halt='WATCHDOG_DIA', trades_hoy=99))
        self.assertFalse(ok)
        self.assertEqual(len(veredictos), 7)
        fallidos = [v.gate for v in veredictos if not v.ok]
        self.assertIn('G1', fallidos)
        self.assertIn('G3', fallidos)

    def test_camino_limpio(self):
        ok, veredictos = gates.evaluar_previos(ctx(ahora=et(11, 30)))
        self.assertTrue(ok, [v.razon for v in veredictos if not v.ok])


class TestCuadro(unittest.TestCase):
    def test_lo_no_evaluado_se_marca_explicitamente(self):
        # LOGICA §11 pide G1-G10 cada ciclo, pero un NO-GO termina antes de los
        # gates. Sin NOT_EVALUATED el schema seria imposible de cumplir.
        c = gates.cuadro([])
        self.assertEqual(sorted(c.keys()), sorted(gates.ORDEN_GATES))
        self.assertTrue(all(v['estado'] == gates.NO_EVALUADO
                            for v in c.values()))

    def test_mezcla_evaluados_y_no_evaluados(self):
        ok, veredictos = gates.evaluar_previos(ctx(ahora=et(11, 30)))
        c = gates.cuadro(veredictos)
        self.assertEqual(c['G1']['estado'], gates.PASA)
        self.assertEqual(c['G5']['estado'], gates.NO_EVALUADO)
        self.assertEqual(c['G9']['estado'], gates.NO_EVALUADO)


class TestTamano(unittest.TestCase):
    def test_se_dimensiona_al_peor_precio_no_al_mid(self):
        # mid 0.96 => peor 1.00 => 500/100 = 5 contratos exactos.
        qty, prima, _ = gates.tamano(100_000.0, 0.96)
        self.assertEqual(qty, 5)
        self.assertAlmostEqual(prima, 500.0)

    def test_dimensionar_al_mid_habria_roto_el_techo(self):
        # Al mid 0.96 cabrian 5 contratos (480), pero si llenan al retry
        # (1.00) son 500: exactamente el techo. Con mid 0.97 el mid daria 5
        # (485) y el retry 505 => se rompe. Aqui debe dar 4.
        qty, prima, _ = gates.tamano(100_000.0, 0.97)
        self.assertEqual(qty, 4)
        self.assertLessEqual(prima, config.PRIMA_MAX_TRADE_ABS)

    def test_equity_pequena_manda_sobre_los_500(self):
        # 0.5% de 40.000 = 200 => con peor precio 1.00 => 2 contratos.
        qty, prima, det = gates.tamano(40_000.0, 0.96)
        self.assertEqual(qty, 2)
        self.assertAlmostEqual(det['techo_trade'], 200.0)

    def test_qty_cero_en_vez_de_forzar_un_contrato(self):
        # La formula literal `max(1, ...)` abriria 1 contrato de $600 con un
        # presupuesto de $500: convierte un techo de riesgo en un piso.
        qty, prima, det = gates.tamano(100_000.0, 5.96)
        self.assertEqual(qty, 0)
        self.assertEqual(prima, 0.0)
        self.assertIn('PRESUPUESTO', det['razon'])

    def test_sin_presupuesto_agregado_da_cero(self):
        qty, _, det = gates.tamano(100_000.0, 0.96, prima_viva=1500.0)
        self.assertEqual(qty, 0)
        self.assertEqual(det['razon'], 'SIN_PRESUPUESTO')

    def test_el_techo_agregado_limita_aunque_quepa_por_trade(self):
        # 1300 vivo => quedan 200 => 2 contratos, no 5.
        qty, _, _ = gates.tamano(100_000.0, 0.96, prima_viva=1300.0)
        self.assertEqual(qty, 2)


if __name__ == '__main__':
    unittest.main()
