# -*- coding: utf-8 -*-
import unittest

from motor import llm


class TestContratoEstricto(unittest.TestCase):
    def test_open_valido(self):
        d = llm._validar({'decision': 'OPEN', 'confianza': 72,
                          'razon': 'Setup and liquidity align.'}, 'm', 10)
        self.assertTrue(d.abre)
        self.assertEqual(d.confianza, 72)

    def test_faltan_campos(self):
        with self.assertRaises(ValueError):
            llm._validar({'decision': 'OPEN'}, 'm', 0)

    def test_confianza_no_entera_o_fuera_de_rango(self):
        for valor in ('alta', 101, -1, 42.5, True):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                llm._validar({'decision': 'OPEN', 'confianza': valor,
                              'razon': 'x'}, 'm', 0)

    def test_razon_vacia_o_demasiado_larga(self):
        for razon in ('', 'x' * 401):
            with self.subTest(longitud=len(razon)), self.assertRaises(ValueError):
                llm._validar({'decision': 'OPEN', 'confianza': 50,
                              'razon': razon}, 'm', 0)


if __name__ == '__main__':
    unittest.main()
