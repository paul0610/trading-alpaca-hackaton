# -*- coding: utf-8 -*-
"""S1-S6 con indicadores INYECTADOS.

Se llama a `senal._evaluar_barra` con las series de EMA/RSI/ATR construidas a
mano. Asi cada condicion se prueba aislada y en su borde exacto, sin depender
de que 300 velas sinteticas produzcan por casualidad el RSI que hace falta.
"""
import unittest

from motor import config, senal
from motor.datos import Vela

T0 = 1_700_000_000
ANCHO5 = 300
NIVEL = 500.00           # max de los highs de las 6 velas previas


def _contexto(o, c, h=None, l=None, rsi5=60.0, rsi15=60.0, rsi30=60.0,
              ema_r=10.0, ema_l=1.0, atr=1.0):
    """Serie de 8 velas 5m; la 7 (indice 7) es la que se evalua.

    Las 6 previas (indices 1..6) forman el canal: highs 500.00, lows 499.00.
    """
    h = h if h is not None else max(o, c) + 0.05
    l = l if l is not None else min(o, c) - 0.05
    v5 = [Vela(T0 + i * ANCHO5, 499.50, NIVEL, 499.00, 499.50)
          for i in range(7)]
    v5.append(Vela(T0 + 7 * ANCHO5, o, h, l, c))
    n = len(v5)
    # 15m y 30m: dos velas cada uno, ya cerradas antes del cierre de la 5m.
    v15 = [Vela(T0, 499, 500, 499, 499.5), Vela(T0 + 900, 499, 500, 499, 499.5)]
    v30 = [Vela(T0 - 1800, 499, 500, 499, 499.5), Vela(T0, 499, 500, 499, 499.5)]
    return {
        'i': 7, 'v5': v5, 'v15': v15, 'v30': v30,
        'ema_r': [ema_r] * n, 'ema_l': [ema_l] * n,
        'rsi5': [rsi5] * n, 'atr5': [atr] * n,
        'rsi15': [rsi15] * len(v15), 'rsi30': [rsi30] * len(v30),
    }


def evaluar(**kw):
    ctx = _contexto(**kw)
    return senal._evaluar_barra(ctx['i'], ctx['v5'], ctx['v15'], ctx['v30'],
                                ctx['ema_r'], ctx['ema_l'], ctx['rsi5'],
                                ctx['atr5'], ctx['rsi15'], ctx['rsi30'])


class TestSenalLong(unittest.TestCase):
    def test_cruce_limpio_completo_es_long(self):
        s = evaluar(o=500.00, c=500.10)
        self.assertEqual(s.direccion, senal.LONG)
        for k in ('S1', 'S2', 'S3', 'S4', 'S5'):
            self.assertTrue(s.cond[k], k)

    def test_s1_exige_cruce_no_estar_arriba(self):
        # Abre YA por encima del nivel: no es un cruce, es continuacion.
        s = evaluar(o=500.20, c=500.40)
        self.assertFalse(s.cond['S1'])
        self.assertIsNone(s.direccion)

    def test_s1_borde_abrir_justo_en_el_nivel_cuenta_como_cruce(self):
        s = evaluar(o=NIVEL, c=500.10)
        self.assertTrue(s.cond['S1'])

    def test_s2_knife_edge_exactamente_5_centavos_pasa(self):
        # La regla es ">= $0.05". Sin tolerancia binaria este caso falla:
        # 500.05 - 500.00 puede dar 0.04999999999997 en coma flotante.
        s = evaluar(o=499.99, c=500.05)
        self.assertTrue(s.cond['S2'])

    def test_s2_knife_edge_4_centavos_no_pasa(self):
        s = evaluar(o=499.99, c=500.04)
        self.assertFalse(s.cond['S2'])

    def test_s2_exige_cuerpo_alcista(self):
        # Cierra 10 centavos sobre el nivel pero es una vela bajista.
        s = evaluar(o=500.30, c=500.10)
        self.assertFalse(s.cond['S2'])

    def test_s3_veta_contra_tendencia(self):
        s = evaluar(o=500.00, c=500.10, ema_r=1.0, ema_l=10.0)
        self.assertFalse(s.cond['S3'])
        self.assertIsNone(s.direccion)

    def test_s4_rsi5m_por_debajo_de_55_falla(self):
        self.assertFalse(evaluar(o=500.00, c=500.10, rsi5=54.9).cond['S4'])

    def test_s4_rsi5m_borde_55_pasa(self):
        self.assertTrue(evaluar(o=500.00, c=500.10, rsi5=55.0).cond['S4'])

    def test_s4_rsi5m_borde_75_pasa_y_76_no(self):
        self.assertTrue(evaluar(o=500.00, c=500.10, rsi5=75.0).cond['S4'])
        self.assertFalse(evaluar(o=500.00, c=500.10, rsi5=75.1).cond['S4'])

    def test_s4_exige_los_tres_marcos(self):
        self.assertFalse(evaluar(o=500.00, c=500.10, rsi15=49.9).cond['S4'])
        self.assertFalse(evaluar(o=500.00, c=500.10, rsi30=44.9).cond['S4'])
        self.assertTrue(evaluar(o=500.00, c=500.10, rsi15=50.0,
                                rsi30=45.0).cond['S4'])

    def test_s5_veta_velon_de_panico(self):
        # ATR previo 1.0 => tope 2.2. Un rango de 3.0 es un velon.
        s = evaluar(o=500.00, c=500.10, h=502.00, l=499.00, atr=1.0)
        self.assertFalse(s.cond['S5'])

    def test_s5_usa_el_atr_previo_no_el_de_la_propia_vela(self):
        # Si usara el ATR que incluye esta vela, un velon inflaria su propio
        # umbral y el filtro se auto-anularia.
        ctx = _contexto(o=500.00, c=500.10, h=502.0, l=499.0)
        ctx['atr5'][6] = 1.0        # previo: el que se debe usar
        ctx['atr5'][7] = 5.0        # el de la propia vela: NO se debe usar
        s = senal._evaluar_barra(7, ctx['v5'], ctx['v15'], ctx['v30'],
                                 ctx['ema_r'], ctx['ema_l'], ctx['rsi5'],
                                 ctx['atr5'], ctx['rsi15'], ctx['rsi30'])
        self.assertFalse(s.cond['S5'])


class TestSenalShort(unittest.TestCase):
    """SHORT es el espejo exacto: nivel_bajo 499.00, EMA20<EMA50, RSI [25,45]."""

    def _corto(self, o, c, **kw):
        kw.setdefault('ema_r', 1.0)
        kw.setdefault('ema_l', 10.0)
        kw.setdefault('rsi5', 35.0)
        kw.setdefault('rsi15', 40.0)
        kw.setdefault('rsi30', 40.0)
        return evaluar(o=o, c=c, **kw)

    def test_cruce_bajista_completo(self):
        s = self._corto(o=499.00, c=498.90)
        self.assertEqual(s.direccion, senal.SHORT)
        for k in ('S1', 'S2', 'S3', 'S4', 'S5'):
            self.assertTrue(s.cond[k], k)

    def test_s2_knife_edge_bajista(self):
        self.assertTrue(self._corto(o=499.01, c=498.95).cond['S2'])
        self.assertFalse(self._corto(o=499.01, c=498.96).cond['S2'])

    def test_rsi_short_fuera_de_banda(self):
        self.assertFalse(self._corto(o=499.00, c=498.90, rsi5=45.1).cond['S4'])
        self.assertTrue(self._corto(o=499.00, c=498.90, rsi5=45.0).cond['S4'])
        self.assertFalse(self._corto(o=499.00, c=498.90, rsi30=55.1).cond['S4'])


class TestCalentamiento(unittest.TestCase):
    def test_sin_indicadores_no_hay_go(self):
        ctx = _contexto(o=500.00, c=500.10)
        ctx['ema_l'][7] = None
        s = senal._evaluar_barra(7, ctx['v5'], ctx['v15'], ctx['v30'],
                                 ctx['ema_r'], ctx['ema_l'], ctx['rsi5'],
                                 ctx['atr5'], ctx['rsi15'], ctx['rsi30'])
        self.assertEqual(s.motivo, 'CALENTANDO')
        self.assertFalse(s.go)

    def test_indices_iniciales_no_tienen_canal(self):
        ctx = _contexto(o=500.00, c=500.10)
        s = senal._evaluar_barra(2, ctx['v5'], ctx['v15'], ctx['v30'],
                                 ctx['ema_r'], ctx['ema_l'], ctx['rsi5'],
                                 ctx['atr5'], ctx['rsi15'], ctx['rsi30'])
        self.assertEqual(s.motivo, 'CALENTANDO')


class TestAntiLookahead(unittest.TestCase):
    def test_valor_hasta_ignora_la_vela_que_aun_no_cierra(self):
        v15 = [Vela(T0, 1, 1, 1, 1), Vela(T0 + 900, 1, 1, 1, 1)]
        serie = [10.0, 99.0]
        A = senal.ANCHO_15M
        # Limite justo cuando la 2da vela AUN no cerro (cierra en T0+1800).
        self.assertEqual(senal._valor_hasta(v15, serie, T0 + 1799, A), 10.0)
        # Ya cerrada: se puede usar.
        self.assertEqual(senal._valor_hasta(v15, serie, T0 + 1800, A), 99.0)

    def test_valor_hasta_salta_los_none_del_calentamiento(self):
        v15 = [Vela(T0, 1, 1, 1, 1), Vela(T0 + 900, 1, 1, 1, 1)]
        self.assertIsNone(
            senal._valor_hasta(v15, [None, None], T0 + 1800, senal.ANCHO_15M))
        self.assertEqual(
            senal._valor_hasta(v15, [7.0, None], T0 + 1800, senal.ANCHO_15M),
            7.0)

    def test_valor_hasta_no_infiere_el_ancho_de_la_serie(self):
        # Falta el primer bucket: inferir el ancho de las dos primeras velas
        # daria 1800 donde son 900 y correria el limite media hora.
        v15 = [Vela(T0, 1, 1, 1, 1), Vela(T0 + 1800, 1, 1, 1, 1)]
        self.assertEqual(
            senal._valor_hasta(v15, [10.0, 99.0], T0 + 2700, senal.ANCHO_15M),
            99.0)


class TestAntiSerrucho(unittest.TestCase):
    """S6 sobre historial construido a mano."""

    def _s(self, ts, direccion, crudo):
        cond = {k: crudo for k in ('S1', 'S2', 'S3', 'S4', 'S5')}
        cond['S6'] = None
        return senal.Senal(ts, direccion, False, cond)

    def test_segundo_cruce_del_mismo_lado_es_vetado(self):
        serie = [self._s(T0, senal.LONG, True),
                 self._s(T0 + 300, senal.LONG, True)]
        senal.aplicar_s6(serie)
        self.assertTrue(serie[0].go)
        self.assertFalse(serie[1].go)
        self.assertEqual(serie[1].motivo, 'SERRUCHO')

    def test_lado_contrario_no_se_veta(self):
        serie = [self._s(T0, senal.LONG, True),
                 self._s(T0 + 300, senal.SHORT, True)]
        senal.aplicar_s6(serie)
        self.assertTrue(serie[0].go)
        self.assertTrue(serie[1].go)

    def test_tras_3_velas_se_libera(self):
        serie = [self._s(T0, senal.LONG, True),
                 self._s(T0 + 300, senal.LONG, False),
                 self._s(T0 + 600, senal.LONG, False),
                 self._s(T0 + 900, senal.LONG, False),
                 self._s(T0 + 1200, senal.LONG, True)]
        senal.aplicar_s6(serie)
        self.assertTrue(serie[0].go)
        self.assertTrue(serie[4].go, 'a 4 velas del cruce previo, ya se libera')

    def test_veta_dentro_de_la_ventana_de_3(self):
        serie = [self._s(T0, senal.LONG, True),
                 self._s(T0 + 300, senal.LONG, False),
                 self._s(T0 + 600, senal.LONG, False),
                 self._s(T0 + 900, senal.LONG, True)]
        senal.aplicar_s6(serie)
        self.assertFalse(serie[3].go, 'a 3 velas todavia esta dentro del veto')

    def test_un_cruce_vetado_tambien_bloquea_al_siguiente(self):
        # "sin senal previa (tomada O VETADA)": si solo contaran las tomadas,
        # el serrucho volveria por la puerta de atras.
        serie = [self._s(T0, senal.LONG, True),
                 self._s(T0 + 300, senal.LONG, True),
                 self._s(T0 + 600, senal.LONG, True)]
        senal.aplicar_s6(serie)
        self.assertTrue(serie[0].go)
        self.assertFalse(serie[1].go)
        self.assertFalse(serie[2].go)


if __name__ == '__main__':
    unittest.main()
