# -*- coding: utf-8 -*-
"""Idempotencia del ejecutor: el escenario que este modulo existe para impedir
es "el proceso muere entre el POST y el apunte, reinicia y manda la orden otra
vez". Todo lo demas es secundario."""
import tempfile
import unittest
from pathlib import Path

from motor import diario as diario_mod
from motor import ejecutor
from motor.contratos import Quote, Spread
from motor.rest import Respuesta


class FakeCfg(object):
    def __init__(self, modo='real'):
        self.modo = modo

    @property
    def opera_de_verdad(self):
        return self.modo == 'real'

    def headers(self):
        return {}


class RelojFake(object):
    """Reloj que AVANZA. Con un reloj constante `_esperar_fill` no saldria
    nunca de su bucle de sondeo y la suite colgaria."""

    def __init__(self, paso=10.0):
        self.t = 0.0
        self.paso = paso

    def __call__(self):
        self.t += self.paso
        return self.t


class FakeCliente(object):
    """Broker de mentira. Registra cada llamada para poder afirmar que NO se
    mando una segunda orden.

    `estado_nuevo` es el estado con el que nace una orden al hacer POST; un
    DELETE la pasa a `canceled` de verdad, para que el camino de cancelacion
    confirmada se ejercite tal cual corre en produccion.
    """

    def __init__(self):
        self.cfg = FakeCfg()
        self.llamadas = []
        self.ordenes = {}            # client_order_id -> orden
        self.porid = {}              # id -> la MISMA orden (por referencia)
        self.posts = 0
        self.estado_nuevo = 'new'
        self.estado_al_cancelar = 'canceled'
        self.llenos_al_cancelar = 0

    def registrar(self, coid, oid, estado):
        orden = {'id': oid, 'client_order_id': coid, 'status': estado}
        self.ordenes[coid] = orden
        self.porid[oid] = orden
        return orden

    def trading(self, metodo, ruta, params=None, cuerpo=None, **kw):
        self.llamadas.append((metodo, ruta))
        if ruta == '/v2/orders:by_client_order_id':
            coid = (params or {}).get('client_order_id')
            if coid in self.ordenes:
                return Respuesta(True, dict(self.ordenes[coid]), 200)
            return Respuesta(False, None, 404, 'HTTP 404')
        if metodo == 'POST' and ruta == '/v2/orders':
            self.posts += 1
            orden = self.registrar(cuerpo['client_order_id'],
                                   'OID%d' % self.posts, self.estado_nuevo)
            orden['limit_price'] = cuerpo['limit_price']
            return Respuesta(True, dict(orden), 200)
        if metodo == 'GET' and ruta.startswith('/v2/orders/'):
            oid = ruta.rsplit('/', 1)[1]
            return Respuesta(True, dict(self.porid.get(oid) or {}), 200)
        if metodo == 'DELETE' and ruta.startswith('/v2/orders/'):
            oid = ruta.rsplit('/', 1)[1]
            if oid in self.porid:
                self.porid[oid]['status'] = self.estado_al_cancelar
                if self.llenos_al_cancelar:
                    self.porid[oid]['filled_qty'] = str(self.llenos_al_cancelar)
                    self.porid[oid]['filled_avg_price'] = '1.02'
            return Respuesta(True, {}, 200)
        if metodo == 'GET' and ruta == '/v2/orders':
            return Respuesta(True, [], 200)
        return Respuesta(False, None, 400, 'ruta no simulada')


def spread():
    larga = Quote('SPY260903C00500000', 3.00, 3.10, 500.0, 'call', 500, 100)
    corta = Quote('SPY260903C00502000', 2.00, 2.10, 502.0, 'call', 500, 100)
    return Spread('LONG', larga, corta, '2026-09-03', 'SPY', 500.5)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.diario = diario_mod.Diario(Path(self.dir.name))
        self.cli = FakeCliente()

    def tearDown(self):
        self.dir.cleanup()

    def ejec(self, modo='real'):
        cfg = FakeCfg(modo)
        self.cli.cfg = cfg
        return ejecutor.Ejecutor(self.cli, self.diario, cfg,
                                 dormir=lambda s: None, ahora=RelojFake())


class TestClaveOrden(unittest.TestCase):
    def test_es_determinista(self):
        a = ejecutor.clave_orden('2026-09-01', 'open', 'SPY-LONG-123', 0)
        b = ejecutor.clave_orden('2026-09-01', 'open', 'SPY-LONG-123', 0)
        self.assertEqual(a, b)

    def test_distingue_intentos_y_propositos(self):
        base = ('2026-09-01', 'open', 'SPY-LONG-123')
        self.assertNotEqual(ejecutor.clave_orden(*base, 0),
                            ejecutor.clave_orden(*base, 1))
        self.assertNotEqual(
            ejecutor.clave_orden('2026-09-01', 'open', 'X', 0),
            ejecutor.clave_orden('2026-09-01', 'close-sl', 'X', 0))

    def test_respeta_el_limite_de_128_chars(self):
        largo = ejecutor.clave_orden('2026-09-01', 'open', 'X' * 300, 0)
        self.assertLessEqual(len(largo), 128)


class TestIdempotencia(Base):
    def test_una_orden_preexistente_se_adopta_y_no_se_duplica(self):
        """El caso del crash: la orden YA llego al broker en la vida anterior
        del proceso. Al reintentar NO se debe crear una segunda."""
        e = self.ejec()
        coid = ejecutor.clave_orden('S1', 'open', 'SPY-LONG-7', 0)
        self.cli.registrar(coid, 'VIEJA', 'filled')

        res, err = e.abrir(spread(), 1, 'S1', 'SPY-LONG-7')
        self.assertEqual(err, '')
        self.assertEqual(self.cli.posts, 0, 'no debio hacer ningun POST')
        self.assertTrue(res['filled'])

    def test_se_pregunta_al_broker_antes_de_cada_envio(self):
        e = self.ejec()
        self.cli.estado_nuevo = 'filled'
        e.abrir(spread(), 1, 'S1', 'SPY-LONG-7')
        consultas = [r for m, r in self.cli.llamadas
                     if r == '/v2/orders:by_client_order_id']
        self.assertGreaterEqual(len(consultas), 1)

    def test_la_intencion_se_escribe_antes_del_post(self):
        e = self.ejec()
        self.cli.estado_nuevo = 'filled'
        e.abrir(spread(), 1, 'S1', 'SPY-LONG-7')
        intenciones = self.diario.leer(self.diario.f_intenciones)
        self.assertEqual(len(intenciones), 1)
        self.assertEqual(intenciones[0]['lado'], 'buy')
        self.assertIn('client_order_id', intenciones[0])

    def test_modo_sombra_nunca_hace_post(self):
        e = self.ejec('sombra')
        res, err = e.abrir(spread(), 2, 'S1', 'SPY-LONG-7')
        self.assertEqual(self.cli.posts, 0)
        self.assertTrue(res['sombra'])
        self.assertFalse(res['filled'])
        # Pero SI deja rastro en el diario: la sombra tambien es evidencia.
        self.assertEqual(len(self.diario.leer(self.diario.f_intenciones)), 1)


class TestEscaleraDePrecios(Base):
    def test_sin_fill_manda_los_dos_precios_y_se_rinde(self):
        e = self.ejec()
        self.cli.estado_nuevo = 'new'               # nunca llena
        res, _ = e.abrir(spread(), 1, 'S1', 'SPY-LONG-7')
        self.assertFalse(res['filled'])
        self.assertEqual(res['razon'], 'NO_FILL')
        self.assertEqual(self.cli.posts, 2, 'mid+0.02 y luego mid+0.04')
        limites = [i['limit'] for i in res['intentos']]
        self.assertAlmostEqual(limites[0], 1.02)    # mid = 3.05-2.05 = 1.00
        self.assertAlmostEqual(limites[1], 1.04)

    def test_no_manda_el_segundo_precio_si_no_confirma_la_cancelacion(self):
        """Dos limits vivos del mismo spread duplicarian la exposicion."""
        e = self.ejec()
        self.cli.estado_nuevo = 'new'

        def sin_cancelar(oid, insistir=3):
            return False, {'id': oid, 'status': 'new'}
        e.cancelar = sin_cancelar

        res, _ = e.abrir(spread(), 1, 'S1', 'SPY-LONG-7')
        self.assertEqual(res['razon'], 'CANCEL_NO_CONFIRMADO')
        self.assertEqual(self.cli.posts, 1, 'solo el primer precio')

    def test_fill_registra_slippage_contra_el_mid(self):
        e = self.ejec()
        self.cli.estado_nuevo = 'filled'
        res, _ = e.abrir(spread(), 1, 'S1', 'SPY-LONG-7')
        self.assertTrue(res['filled'])
        # mid = 1.00, limit = 1.02 => slippage 0.02
        self.assertAlmostEqual(res['slippage'], 0.02, places=6)
        eventos = self.diario.eventos()
        self.assertTrue(any(ev['tipo'] == 'FILL_APERTURA' for ev in eventos))

    def test_pending_cancel_no_autoriza_el_siguiente_precio(self):
        e = self.ejec()
        self.cli.estado_nuevo = 'new'
        self.cli.estado_al_cancelar = 'pending_cancel'
        res, _ = e.abrir(spread(), 1, 'S1', 'SPY-LONG-7')
        self.assertEqual(res['razon'], 'CANCEL_NO_CONFIRMADO')
        self.assertEqual(self.cli.posts, 1)

    def test_fill_parcial_al_cancelar_se_adopta(self):
        e = self.ejec()
        self.cli.estado_nuevo = 'new'
        self.cli.llenos_al_cancelar = 1
        res, _ = e.abrir(spread(), 2, 'S1', 'SPY-LONG-7')
        self.assertTrue(res['filled'])
        self.assertTrue(res['parcial'])
        self.assertEqual(res['qty'], 1)
        self.assertEqual(self.cli.posts, 1)

    def test_cierre_posterior_no_se_atasca_en_ids_cancelados(self):
        e = self.ejec()
        self.cli.estado_nuevo = 'new'
        primero, _ = e.cerrar(spread(), 1, 'S1', 'REF', 'EOD', 1.0)
        segundo, _ = e.cerrar(spread(), 1, 'S1', 'REF', 'EOD', 1.0)
        self.assertEqual(primero['razon'], 'CIERRE_FALLIDO')
        self.assertEqual(segundo['razon'], 'CIERRE_FALLIDO')
        self.assertEqual(self.cli.posts, 6)
        ids = list(self.cli.ordenes)
        self.assertTrue(any(x.endswith('-r1') for x in ids))


class TestReconciliacion(Base):
    def test_clasifica_las_intenciones_previas(self):
        e = self.ejec()
        coid_viva = ejecutor.clave_orden('S1', 'open', 'A', 0)
        coid_llena = ejecutor.clave_orden('S1', 'open', 'B', 0)
        coid_fantasma = ejecutor.clave_orden('S1', 'open', 'C', 0)
        for coid in (coid_viva, coid_llena, coid_fantasma):
            self.diario.intencion({'client_order_id': coid, 'modo': 'real',
                                   'sesion': 'S1'})
        self.cli.ordenes[coid_viva] = {'id': '1', 'status': 'new'}
        self.cli.ordenes[coid_llena] = {'id': '2', 'status': 'filled'}

        informe = e.reconciliar('S1')
        self.assertEqual(informe['revisadas'], 3)
        self.assertEqual(len(informe['vivas']), 1)
        self.assertEqual(len(informe['llenas']), 1)
        self.assertEqual(informe['desconocidas'], [coid_fantasma])

    def test_ignora_intenciones_de_sombra(self):
        e = self.ejec()
        self.diario.intencion({'client_order_id': 'x', 'modo': 'sombra',
                               'sesion': 'S1'})
        informe = e.reconciliar('S1')
        self.assertEqual(informe['revisadas'], 1)
        self.assertEqual(informe['vivas'], [])

    def test_ignora_otras_sesiones(self):
        e = self.ejec()
        self.diario.intencion({'client_order_id': 'x', 'modo': 'real',
                               'sesion': 'OTRA'})
        self.assertEqual(e.reconciliar('S1')['revisadas'], 0)

    def test_orden_viva_bloquea_el_arranque(self):
        e = self.ejec()
        coid = ejecutor.clave_orden('S1', 'open', 'A', 0)
        self.diario.intencion({'client_order_id': coid, 'modo': 'real',
                               'sesion': 'S1', 'proposito': 'open',
                               'referencia': 'A'})
        self.cli.ordenes[coid] = {'id': '1', 'status': 'pending_cancel'}
        informe = e.reconciliar('S1', [])
        self.assertEqual(len(informe['bloqueos']), 1)

    def test_fill_de_apertura_sin_posicion_local_bloquea(self):
        e = self.ejec()
        coid = ejecutor.clave_orden('S1', 'open', 'A', 0)
        self.diario.intencion({'client_order_id': coid, 'modo': 'real',
                               'sesion': 'S1', 'proposito': 'open',
                               'referencia': 'A'})
        self.cli.ordenes[coid] = {'id': '1', 'status': 'filled'}
        informe = e.reconciliar('S1', [])
        self.assertEqual(informe['bloqueos'][0]['estado'],
                         'FILL_NO_RECONCILIADO')


class TestLock(unittest.TestCase):
    def test_dos_procesos_no_pueden_tomar_el_mismo_lock(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / 'motor.lock'
            a = ejecutor.LockProceso(ruta)
            b = ejecutor.LockProceso(ruta)
            self.assertTrue(a.tomar()[0])
            tomado, razon = b.tomar()
            self.assertFalse(tomado)
            self.assertIn('LOCK_OCUPADO', razon)
            a.soltar()
            self.assertTrue(b.tomar()[0])
            b.soltar()

    def test_archivo_huerfano_no_bloquea_un_nuevo_proceso(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / 'motor.lock'
            ruta.write_text('999999', encoding='utf-8')
            lock = ejecutor.LockProceso(ruta)
            self.assertTrue(lock.tomar()[0])
            lock.soltar()


if __name__ == '__main__':
    unittest.main()
