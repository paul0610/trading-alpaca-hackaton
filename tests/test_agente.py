# -*- coding: utf-8 -*-
import threading
import unittest
from unittest import mock

from motor import agente, reloj


class TestCierresParalelos(unittest.TestCase):
    def test_dos_posiciones_empiezan_sin_esperarse(self):
        ag = agente.Agente.__new__(agente.Agente)
        lock = threading.Lock()
        ambas = threading.Event()
        liberar = threading.Event()
        iniciadas = []

        def cerrar(pos, decision):
            with lock:
                iniciadas.append(pos)
                if len(iniciadas) == 2:
                    ambas.set()
            liberar.wait(2)

        ag._cerrar = cerrar
        exterior = threading.Thread(
            target=ag._cerrar_varias, args=([('A', 'D'), ('B', 'D')],))
        exterior.start()
        self.assertTrue(ambas.wait(1), 'el segundo cierre quedo serializado')
        liberar.set()
        exterior.join(2)
        self.assertEqual(set(iniciadas), {'A', 'B'})


class EjecEOD(object):
    def __init__(self):
        self.llamadas = 0

    def cancelar_abiertas(self, motivo):
        self.llamadas += 1
        return ((0, 'API_DOWN') if self.llamadas == 1 else (0, ''))


class CarteraVacia(object):
    def vivas(self):
        return []


class DiarioFake(object):
    def __init__(self):
        self.eventos = []

    def evento(self, tipo, **campos):
        self.eventos.append((tipo, campos))


class TestEOD(unittest.TestCase):
    def test_cancelacion_fallida_se_reintenta(self):
        ahora = reloj.epoch_de_et(2026, 9, 1, 15, 45)
        ag = agente.Agente.__new__(agente.Agente)
        ag._sesion_eod = reloj.fecha_et(ahora)
        ag._eod_cancelado = False
        ag.ejec = EjecEOD()
        ag.cartera = CarteraVacia()
        ag.diario = DiarioFake()
        ag.lock_estado = threading.RLock()
        with mock.patch('motor.agente.time.time', return_value=ahora):
            ag.ciclo_monitor()
            self.assertFalse(ag._eod_cancelado)
            ag.ciclo_monitor()
        self.assertTrue(ag._eod_cancelado)
        self.assertEqual(ag.ejec.llamadas, 2)
        self.assertEqual(ag.diario.eventos[0][0], 'EOD_CANCEL_PENDIENTE')


if __name__ == '__main__':
    unittest.main()


class TestProximoCiclo(unittest.TestCase):
    """La cadencia sale de config.CICLO_MIN, no de un literal.

    Regresion: `proximo_ciclo` tenia el 10 hardcodeado en la aritmetica de
    alineacion, asi que bajar CICLO_MIN a 5 no movia nada — el motor seguia
    despertando en la rejilla de 10 min y se perdia la mitad de los cierres
    de vela 5m.
    """

    def _minutos_de_un_dia(self, paso):
        """Bordes que produce el planificador durante una sesion completa."""
        from motor import config
        with mock.patch.object(config, 'CICLO_MIN', paso):
            # 2026-09-02 09:30:00 ET en epoch, avanzando ciclo a ciclo.
            t = reloj.epoch_de_et(2026, 9, 2, 9, 30, 0)
            fin = reloj.epoch_de_et(2026, 9, 2, 16, 0, 0)
            vistos = []
            while t < fin:
                t = agente.proximo_ciclo(t)
                if t >= fin:
                    break
                et = reloj.et(t)
                vistos.append((et.minute, et.second))
            return vistos

    def test_cadencia_5_cae_en_cada_cierre_de_vela_5m(self):
        vistos = self._minutos_de_un_dia(5)
        self.assertTrue(vistos, 'el planificador no produjo ciclos')
        for minuto, segundo in vistos:
            self.assertEqual(minuto % 5, 0,
                             'ciclo en :%02d, fuera del cierre de vela 5m' % minuto)
            self.assertEqual(segundo, 15, 'el desfase de 15 s se perdio')

    def test_cadencia_10_conserva_la_rejilla_original(self):
        for minuto, segundo in self._minutos_de_un_dia(10):
            self.assertEqual(minuto % 10, 5,
                             'ciclo en :%02d, fuera de la rejilla :x5' % minuto)
            self.assertEqual(segundo, 15)

    def test_cinco_da_el_doble_de_ciclos_que_diez(self):
        self.assertEqual(len(self._minutos_de_un_dia(5)),
                         2 * len(self._minutos_de_un_dia(10)))

    def test_nunca_devuelve_un_instante_pasado(self):
        from motor import config
        with mock.patch.object(config, 'CICLO_MIN', 5):
            base = reloj.epoch_de_et(2026, 9, 2, 10, 5, 0)
            # justo en el borde, justo despues y justo antes del desfase
            for delta in (-0.5, 0.0, 14.9, 15.0, 15.1, 299.0):
                ahora = base + delta
                self.assertGreater(agente.proximo_ciclo(ahora), ahora)
