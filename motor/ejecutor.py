# -*- coding: utf-8 -*-
"""Ejecucion de ordenes mleg (LOGICA §8). JAMAS market en opciones.

IDEMPOTENCIA — por que este modulo es tan cuidadoso
---------------------------------------------------
`lanzar.cmd` reinicia el proceso ante un crash. Si el crash ocurre entre el
POST de una orden y el momento en que el motor apunta que la mando, al
reiniciar el motor no sabe que existe y la manda otra vez: dos spreads donde
debia haber uno, con la exposicion duplicada y los techos de §5 rotos. Un
`client_order_id` determinista es necesario pero NO suficiente. El protocolo
completo es:

  1. Escribir la **intencion** en `intenciones.jsonl` con fsync ANTES del POST.
  2. POST con `client_order_id` determinista (misma intencion => mismo id;
     Alpaca rechaza duplicados, asi que un reenvio no puede duplicar).
  3. Antes de reenviar cualquier cosa, **preguntarle al broker** por ese id.
  4. Al arrancar, `reconciliar()` resuelve toda intencion sin desenlace.
  5. Un lock de proceso impide que dos motores compitan por la misma cuenta.

Las SALIDAS insisten donde las entradas se rinden (Regla 6): proteger > abrir.
"""
import os
import time

from motor import config

ESTADOS_VIVOS = ('new', 'accepted', 'pending_new', 'accepted_for_bidding',
                 'partially_filled', 'held', 'pending_cancel',
                 'pending_replace', 'calculated', 'stopped', 'suspended')
# `pending_cancel` NO es terminal: la orden todavia puede llenar mientras el
# broker procesa la cancelacion. Tratarla como muerta permitiria mandar el
# siguiente limit y duplicar exposicion.
ESTADOS_TERMINALES = ('filled', 'canceled', 'expired', 'rejected',
                      'done_for_day', 'replaced')
ESTADOS_SIN_FILL = ('canceled', 'expired', 'rejected', 'done_for_day',
                    'replaced')


def clave_orden(sesion, proposito, referencia, intento):
    """`client_order_id` determinista y legible.

    Alpaca limita a 128 chars y exige unicidad. La misma intencion logica
    reproduce SIEMPRE el mismo id, de modo que un reenvio tras un crash choca
    contra el broker en vez de duplicar la posicion.
    """
    crudo = 'remora-%s-%s-%s-%d' % (sesion, proposito, referencia, intento)
    return crudo.replace(' ', '').replace(':', '')[:128]


def clave_reintento(base, ronda):
    """Version durable de un ID cuyo intento anterior ya es terminal.

    Un cierre puede agotar sus tres precios y volver a intentarse en el
    siguiente ciclo de monitor. Alpaca no permite reutilizar un
    `client_order_id` cancelado; la ronda conserva la idempotencia y evita que
    el cierre quede atascado adoptando eternamente una orden muerta.
    """
    if ronda <= 0:
        return base
    sufijo = '-r%d' % ronda
    return base[:128 - len(sufijo)] + sufijo


def cantidad_llena(orden, solicitada=0):
    """Cantidad ejecutada aun cuando el estado final sea `canceled`.

    Las mleg son atomicas por unidad del spread, pero una orden de varias
    unidades puede llenar parcialmente antes de que la cancelacion confirme.
    Ignorar `filled_qty` deja una posicion real fuera del estado local.
    """
    try:
        n = int(float((orden or {}).get('filled_qty') or 0))
    except (TypeError, ValueError):
        n = 0
    if n <= 0 and (orden or {}).get('status') == 'filled':
        try:
            n = int(float((orden or {}).get('qty') or solicitada or 0))
        except (TypeError, ValueError):
            n = int(solicitada or 0)
    return max(0, n)


class LockProceso(object):
    """Un solo motor por cuenta. Dos loops compartiendo cuenta se pisan los
    contadores y duplican ordenes: es un modo de fallo, no una posibilidad.

    Usa un lock del sistema operativo, no la mera existencia del archivo. Asi
    el kernel lo libera automaticamente tras un crash y `lanzar.cmd` puede
    reiniciar sin quedar bloqueado por un `motor.lock` huerfano.
    """

    def __init__(self, ruta):
        self.ruta = str(ruta)
        self.fd = None

    def tomar(self):
        try:
            self.fd = os.open(self.ruta, os.O_CREAT | os.O_RDWR)
            os.lseek(self.fd, 0, os.SEEK_SET)
            if os.fstat(self.fd).st_size < 1:
                os.write(self.fd, b' ')
            os.lseek(self.fd, 0, os.SEEK_SET)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:                       # pragma: no cover - CI Windows actual
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(self.fd, 0)
            os.write(self.fd, str(os.getpid()).encode())
            os.fsync(self.fd)
            return True, ''
        except (OSError, IOError):
            try:
                with open(self.ruta) as f:
                    pid = f.read().strip()
            except OSError:
                pid = '?'
            if self.fd is not None:
                try:
                    os.close(self.fd)
                except OSError:
                    pass
                self.fd = None
            return False, 'LOCK_OCUPADO:pid=%s' % pid

    def soltar(self):
        if self.fd is not None:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
                else:                   # pragma: no cover - CI Windows actual
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


class Ejecutor(object):
    def __init__(self, cliente, diario, cfg, dormir=time.sleep,
                 ahora=time.time):
        self.cli = cliente
        self.diario = diario
        self.cfg = cfg
        self._dormir = dormir
        self._ahora = ahora

    # -- consultas ---------------------------------------------------------
    def por_client_id(self, coid):
        """La pregunta que hace idempotente todo lo demas."""
        r = self.cli.trading('GET', '/v2/orders:by_client_order_id',
                             {'client_order_id': coid})
        if r.ok:
            return r.datos, ''
        if r.codigo == 404:
            return None, ''            # no existe: nunca llego al broker
        return None, r.error

    def orden(self, oid):
        r = self.cli.trading('GET', '/v2/orders/%s' % oid)
        return (r.datos, '') if r.ok else (None, r.error)

    def cancelar(self, oid, insistir=3):
        """Cancela y CONFIRMA. Cancelar a ciegas es como no cancelar: hay que
        ver el estado terminal antes de mandar el siguiente precio."""
        self.cli.trading('DELETE', '/v2/orders/%s' % oid)
        for _ in range(insistir):
            o, err = self.orden(oid)
            if err:
                self._dormir(1.0)
                continue
            if (o or {}).get('status') in ESTADOS_TERMINALES:
                return True, o
            self._dormir(1.0)
        o, _ = self.orden(oid)
        return False, o

    # -- envio -------------------------------------------------------------
    def _mandar(self, patas, qty, precio, coid, lado, **meta):
        """POST idempotente de una mleg limit. Devuelve (orden|None, error).

        Si el id ya existe en el broker (reenvio tras crash), se ADOPTA la
        orden existente en vez de crear otra.
        """
        # Si el ID base ya termino sin fill, avanza a una ronda nueva. Si el
        # proceso cayo despues del POST, la consulta encuentra la ronda viva y
        # la adopta; si cayo antes del POST, el 404 permite reenviar el MISMO
        # ID. No hay ventana ambigua.
        ronda = 0
        while True:
            candidato = clave_reintento(coid, ronda)
            existente, err = self.por_client_id(candidato)
            if err:
                return None, 'API_DOWN:%s' % err[:80]
            if existente and existente.get('status') in ESTADOS_SIN_FILL:
                ronda += 1
                continue
            coid = candidato
            if existente:
                return existente, ''   # ya estaba: adoptarla, no duplicarla
            break

        intencion = {'client_order_id': coid, 'lado': lado,
                     'qty': qty, 'limit': round(precio, 2),
                     'patas': patas, 'modo': self.cfg.modo}
        intencion.update(meta)
        self.diario.intencion(intencion)
        if not self.cfg.opera_de_verdad:
            return {'id': 'SOMBRA-%s' % coid, 'client_order_id': coid,
                    'status': 'sombra', 'qty': str(qty),
                    'limit_price': '%.2f' % precio, 'sombra': True}, ''

        cuerpo = {'order_class': 'mleg', 'qty': str(qty), 'type': 'limit',
                  'time_in_force': 'day', 'limit_price': '%.2f' % precio,
                  'client_order_id': coid, 'legs': patas}
        r = self.cli.trading('POST', '/v2/orders', cuerpo=cuerpo)
        if r.ok:
            return r.datos, ''
        # Un fallo de red NO significa que la orden no llego. Preguntar.
        existente, _ = self.por_client_id(coid)
        if existente:
            return existente, ''
        return None, r.error

    def _esperar_fill(self, orden, segundos, paso=3.0):
        """Sondea hasta fill / muerte / timeout. Devuelve (estado, orden)."""
        if orden.get('sombra'):
            return 'sombra', orden
        oid = orden.get('id')
        limite = self._ahora() + segundos
        ultima = orden
        while self._ahora() < limite:
            self._dormir(min(paso, max(0.0, limite - self._ahora())))
            o, err = self.orden(oid)
            if err:
                continue
            ultima = o
            estado = o.get('status')
            if estado == 'filled':
                return 'filled', o
            if estado in ('canceled', 'expired', 'rejected'):
                return estado, o
        return 'timeout', ultima

    # -- apertura (§8) -----------------------------------------------------
    def abrir(self, spread, qty, sesion, referencia):
        """limit mid+0.02 -> 90 s -> cancel -> retry unico mid+0.04 -> 90 s
        -> cancel => PASS (`NO_FILL`). Nunca perseguir.

        Devuelve (resultado_dict, error). `resultado['filled']` dice si abrio.
        """
        patas = spread.patas_apertura()
        intentos = []
        for i, offset in enumerate((config.OFFSET_APERTURA_1,
                                    config.OFFSET_APERTURA_2)):
            precio = round(spread.mid + offset, 2)
            coid = clave_orden(sesion, 'open', referencia, i)
            orden, err = self._mandar(
                patas, qty, precio, coid, 'buy', proposito='open',
                referencia=referencia, spread=spread.dict())
            if err:
                intentos.append({'intento': i, 'limit': precio, 'error': err})
                return {'filled': False, 'razon': 'API_DOWN',
                        'intentos': intentos}, err
            estado, orden = self._esperar_fill(orden,
                                               config.ESPERA_APERTURA_S)
            coid_real = orden.get('client_order_id') or coid
            intentos.append({'intento': i, 'limit': precio, 'estado': estado,
                             'client_order_id': coid_real,
                             'orden_id': orden.get('id')})
            if estado == 'filled':
                lleno = float(orden.get('filled_avg_price') or precio)
                qty_llena = cantidad_llena(orden, qty) or qty
                self.diario.evento('FILL_APERTURA', client_order_id=coid_real,
                                   orden_id=orden.get('id'), qty=qty_llena,
                                   limit=precio, precio=lleno,
                                   mid_al_decidir=round(spread.mid, 4),
                                   slippage=round(lleno - spread.mid, 4),
                                   spread=spread.dict())
                return {'filled': True, 'precio': lleno, 'qty': qty_llena,
                        'orden': orden, 'intentos': intentos,
                        'slippage': round(lleno - spread.mid, 4)}, ''
            if estado == 'sombra':
                self.diario.evento('SOMBRA_APERTURA', client_order_id=coid_real,
                                   qty=qty, limit=precio,
                                   spread=spread.dict())
                return {'filled': False, 'razon': 'SOMBRA', 'sombra': True,
                        'precio': precio, 'qty': qty,
                        'intentos': intentos}, ''
            if estado == 'rejected':
                self.diario.evento('ORDEN_RECHAZADA', client_order_id=coid_real,
                                   detalle=orden.get('status'))
                return {'filled': False, 'razon': 'RECHAZADA',
                        'intentos': intentos}, ''
            if estado == 'timeout':
                ok, orden = self.cancelar(orden.get('id'))
                qty_llena = cantidad_llena(orden, qty)
                if qty_llena:
                    lleno = float(orden.get('filled_avg_price') or precio)
                    self.diario.evento(
                        'FILL_PARCIAL_APERTURA', client_order_id=coid_real,
                        orden_id=orden.get('id'), qty=qty_llena,
                        qty_solicitada=qty, precio=lleno)
                    return {'filled': True, 'precio': lleno, 'qty': qty_llena,
                            'parcial': qty_llena < qty,
                            'orden': orden, 'intentos': intentos,
                            'slippage': round(lleno - spread.mid, 4)}, ''
                if not ok:
                    # No se pudo confirmar la cancelacion: NO se manda otro
                    # precio. Dos limits vivos del mismo spread es exactamente
                    # el escenario que este modulo existe para impedir.
                    self.diario.evento('CANCEL_NO_CONFIRMADO',
                                       client_order_id=coid_real)
                    return {'filled': False, 'razon': 'CANCEL_NO_CONFIRMADO',
                            'intentos': intentos}, ''
        self.diario.evento('NO_FILL', referencia=referencia, intentos=intentos)
        return {'filled': False, 'razon': 'NO_FILL', 'intentos': intentos}, ''

    # -- cierre (§8) -------------------------------------------------------
    def cerrar(self, spread, qty, sesion, referencia, motivo, mid_actual):
        """limit mid-0.03 -> 60 s -> mid-0.06 -> 60 s -> bid neto (agresivo).

        Las salidas INSISTEN: si el broker falla se reintenta el escalon, no se
        abandona la posicion. Proteger > abrir.
        """
        patas = spread.patas_cierre()
        precios = [round(mid_actual - config.OFFSET_CIERRE_1, 2),
                   round(mid_actual - config.OFFSET_CIERRE_2, 2),
                   round(spread.bid_neto, 2)]
        intentos = []
        for i, precio in enumerate(precios):
            precio = max(precio, 0.01)
            coid = clave_orden(sesion, 'close-%s' % motivo.lower(),
                               referencia, i)
            orden, err = self._mandar(
                patas, qty, precio, coid, 'sell', proposito='close',
                referencia=referencia, motivo=motivo)
            if err:
                intentos.append({'intento': i, 'limit': precio, 'error': err})
                self._dormir(config.REST_BACKOFF_S)
                continue                      # insistir, no rendirse
            estado, orden = self._esperar_fill(orden, config.ESPERA_CIERRE_S)
            coid_real = orden.get('client_order_id') or coid
            intentos.append({'intento': i, 'limit': precio, 'estado': estado,
                             'client_order_id': coid_real})
            if estado == 'filled':
                lleno = float(orden.get('filled_avg_price') or precio)
                qty_llena = cantidad_llena(orden, qty) or qty
                self.diario.evento('CIERRE', motivo=motivo, qty=qty,
                                   precio=lleno, limit=precio,
                                   mid_al_decidir=round(mid_actual, 4),
                                   slippage=round(mid_actual - lleno, 4),
                                   intentos=intentos)
                return {'filled': True, 'precio': lleno, 'qty': qty_llena,
                        'slippage': round(mid_actual - lleno, 4),
                        'intentos': intentos}, ''
            if estado == 'sombra':
                self.diario.evento('SOMBRA_CIERRE', motivo=motivo, qty=qty,
                                   limit=precio)
                return {'filled': False, 'sombra': True, 'precio': precio,
                        'razon': 'SOMBRA', 'intentos': intentos}, ''
            if estado == 'timeout':
                ok, orden = self.cancelar(orden.get('id'))
                qty_llena = cantidad_llena(orden, qty)
                if qty_llena:
                    lleno = float(orden.get('filled_avg_price') or precio)
                    self.diario.evento(
                        'CIERRE_PARCIAL', motivo=motivo,
                        client_order_id=coid_real, qty=qty_llena,
                        qty_solicitada=qty, precio=lleno)
                    return {'filled': True, 'precio': lleno, 'qty': qty_llena,
                            'parcial': qty_llena < qty,
                            'slippage': round(mid_actual - lleno, 4),
                            'intentos': intentos}, ''
                if not ok:
                    self.diario.evento('CANCEL_NO_CONFIRMADO_CIERRE',
                                       client_order_id=coid_real, motivo=motivo)
                    return {'filled': False, 'razon': 'CANCEL_NO_CONFIRMADO',
                            'intentos': intentos}, ''
        self.diario.evento('CIERRE_FALLIDO', motivo=motivo,
                           referencia=referencia, intentos=intentos)
        return {'filled': False, 'razon': 'CIERRE_FALLIDO',
                'intentos': intentos}, ''

    # -- arranque ----------------------------------------------------------
    def reconciliar(self, sesion, referencias_vivas=None):
        """Resuelve toda intencion registrada antes de dejar operar al agente.

        Es lo primero que corre tras un reinicio. Devuelve un informe con las
        ordenes vivas encontradas; el agente NO opera hasta que esto pasa.
        """
        informe = {'revisadas': 0, 'vivas': [], 'llenas': [], 'muertas': [],
                   'desconocidas': [], 'errores': [], 'bloqueos': []}
        for coid, intencion in self.diario.intenciones_abiertas().items():
            if intencion.get('sesion') != sesion:
                continue
            informe['revisadas'] += 1
            if intencion.get('modo') != 'real':
                continue               # sombra/replay nunca toco al broker
            orden, err = self.por_client_id(coid)
            if err:
                informe['errores'].append({'coid': coid, 'error': err})
                continue
            if orden is None:
                informe['desconocidas'].append(coid)
                continue
            estado = orden.get('status')
            fila = {'coid': coid, 'id': orden.get('id'), 'estado': estado,
                    'proposito': intencion.get('proposito'),
                    'referencia': intencion.get('referencia'),
                    'filled_qty': orden.get('filled_qty')}
            if estado == 'filled':
                informe['llenas'].append(fila)
            elif estado in ESTADOS_TERMINALES:
                informe['muertas'].append(fila)
            else:
                informe['vivas'].append(fila)
        # Una orden viva, una intencion ambigua o un error de consulta impiden
        # afirmar que el estado local coincide con el broker. El agente debe
        # fallar cerrado en el arranque, no seguir abriendo posiciones.
        informe['bloqueos'] = (list(informe['vivas'])
                               + [{'coid': x, 'estado': 'DESCONOCIDA'}
                                  for x in informe['desconocidas']]
                               + list(informe['errores']))
        if referencias_vivas is not None:
            refs = set(referencias_vivas)
            for fila in informe['llenas']:
                proposito = fila.get('proposito')
                ref = fila.get('referencia')
                resuelta = ((proposito == 'open' and ref in refs)
                            or (proposito == 'close' and ref not in refs))
                if not resuelta:
                    copia = dict(fila)
                    copia['estado'] = 'FILL_NO_RECONCILIADO'
                    informe['bloqueos'].append(copia)
        self.diario.evento('RECONCILIACION', **informe)
        return informe

    def cancelar_abiertas(self, motivo='EOD'):
        """Cancela toda orden viva. Se usa antes del flat de fin de dia: una
        apertura pendiente a las 15:45 puede llenar despues del EOD."""
        r = self.cli.trading('GET', '/v2/orders', {'status': 'open',
                                                   'limit': 500})
        if not r.ok:
            return 0, r.error
        n = 0
        errores = []
        for o in (r.datos or []):
            coid = str(o.get('client_order_id') or '')
            if not coid.startswith('remora-'):
                continue
            ok, final = self.cancelar(o.get('id'))
            if ok:
                n += 1
            else:
                errores.append({'id': o.get('id'), 'coid': coid,
                                 'estado': (final or {}).get('status')})
        if n:
            self.diario.evento('CANCEL_MASIVO', motivo=motivo, canceladas=n)
        if errores:
            self.diario.evento('CANCEL_MASIVO_INCOMPLETO', motivo=motivo,
                               errores=errores)
            return n, 'CANCEL_NO_CONFIRMADO:%d' % len(errores)
        return n, ''
