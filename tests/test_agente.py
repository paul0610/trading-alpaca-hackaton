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
