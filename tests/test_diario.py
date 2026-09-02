# -*- coding: utf-8 -*-
"""El diario es el producto del submit: si pierde datos, no hay evidencia."""
import json
import tempfile
import unittest
from pathlib import Path

from motor import diario as diario_mod


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.d = diario_mod.Diario(Path(self.dir.name))

    def tearDown(self):
        self.dir.cleanup()


class TestJSONL(Base):
    def test_append_y_lectura(self):
        self.d.decision({'accion': 'PASS', 'razon': 'NO_GO'})
        self.d.decision({'accion': 'OPEN'})
        regs = self.d.decisiones()
        self.assertEqual(len(regs), 2)
        self.assertEqual(regs[0]['razon'], 'NO_GO')

    def test_cada_registro_lleva_sello_de_tiempo_y_sesion(self):
        r = self.d.decision({'accion': 'PASS'})
        for campo in ('ts_utc', 'ts_et', 'sesion', 'tipo'):
            self.assertIn(campo, r)
        self.assertTrue(r['ts_et'].endswith('ET'))

    def test_una_linea_truncada_no_tumba_la_lectura(self):
        # Escenario kill -9: la ultima linea quedo a medias.
        self.d.evento('FILL', precio=1.0)
        with open(self.d.f_eventos, 'a', encoding='utf-8') as f:
            f.write('{"tipo": "FILL", "prec')
        eventos = self.d.eventos()
        self.assertEqual(len(eventos), 1, 'la linea buena se conserva')

    def test_acentos_y_unicode(self):
        self.d.evento('CIERRE', razon='posicion cerrada por señal débil ✓')
        self.assertIn('señal', self.d.eventos()[0]['razon'])

    def test_objeto_no_serializable_no_rompe_el_journal(self):
        class Raro(object):
            pass
        self.d.evento('X', cosa=Raro())
        self.assertEqual(len(self.d.eventos()), 1)


class TestEstadoAtomico(Base):
    def test_guardar_y_cargar(self):
        self.d.guardar_estado({'posiciones': [], 'trades_hoy': 3})
        self.assertEqual(self.d.cargar_estado()['trades_hoy'], 3)

    def test_sin_archivo_devuelve_vacio(self):
        self.assertEqual(self.d.cargar_estado(), {})

    def test_estado_corrupto_no_lanza(self):
        self.d.f_estado.write_text('{roto', encoding='utf-8')
        self.assertEqual(self.d.cargar_estado(), {})

    def test_no_queda_archivo_temporal(self):
        self.d.guardar_estado({'a': 1})
        self.assertFalse(self.d.f_estado.with_suffix('.json.tmp').exists())

    def test_sobrescribir_no_mezcla_estados(self):
        self.d.guardar_estado({'trades_hoy': 3, 'viejo': True})
        self.d.guardar_estado({'trades_hoy': 5})
        e = self.d.cargar_estado()
        self.assertEqual(e['trades_hoy'], 5)
        self.assertNotIn('viejo', e, 'os.replace sustituye, no fusiona')

    def test_el_json_es_valido_en_disco(self):
        self.d.guardar_estado({'picos': {'ref1': 42.0}})
        crudo = json.loads(self.d.f_estado.read_text(encoding='utf-8'))
        self.assertEqual(crudo['picos']['ref1'], 42.0)


class TestIntenciones(Base):
    def test_la_ultima_intencion_por_id_es_la_que_vale(self):
        self.d.intencion({'client_order_id': 'A', 'limit': 1.02})
        self.d.intencion({'client_order_id': 'A', 'limit': 1.04})
        self.d.intencion({'client_order_id': 'B', 'limit': 0.99})
        abiertas = self.d.intenciones_abiertas()
        self.assertEqual(sorted(abiertas), ['A', 'B'])
        self.assertAlmostEqual(abiertas['A']['limit'], 1.04)

    def test_sin_client_order_id_se_ignora(self):
        self.d.intencion({'limit': 1.0})
        self.assertEqual(self.d.intenciones_abiertas(), {})


if __name__ == '__main__':
    unittest.main()
