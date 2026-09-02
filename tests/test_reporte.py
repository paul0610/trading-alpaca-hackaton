# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

import reporte


class TestReporte(unittest.TestCase):
    def test_estado_vacio_es_honesto(self):
        h = reporte.renderizar([], [], {})
        self.assertIn('No decisions yet', h)
        self.assertIn('no synthetic trades', h)

    def test_escapa_texto_no_confiable_del_diario(self):
        decisiones = [{'accion': 'PASS', 'simbolo': '<script>x</script>',
                       'detalle': '<img src=x onerror=alert(1)>', 'gates': {}}]
        h = reporte.renderizar(decisiones, [], {})
        self.assertNotIn('<script>x</script>', h)
        self.assertNotIn('<img src=x', h)
        self.assertIn('&lt;script&gt;x&lt;/script&gt;', h)

    def test_metricas_y_curva_con_cierre(self):
        eventos = [{'tipo': 'POSICION_CERRADA', 'realizado_usd': 25},
                   {'tipo': 'CIERRE', 'slippage': 0.03}]
        h = reporte.renderizar([], eventos, {'equity': 100025})
        self.assertIn('$25.00', h)
        self.assertIn('<svg', h)
        self.assertIn('$0.030', h)

    def test_genera_archivo_autocontenido(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d)
            salida, nd, ne = reporte.generar(raiz / 'diario',
                                              raiz / 'docs' / 'index.html')
            self.assertTrue(salida.exists())
            self.assertEqual((nd, ne), (0, 0))
            self.assertIn('<!doctype html>', salida.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
