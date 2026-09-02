# -*- coding: utf-8 -*-
"""Cartera: posiciones vivas, marks, picos y P&L (ARQUITECTURA §3).

Los picos (`peak_pct`) se persisten en `estado.json` con escritura atomica:
leccion del 11-ago del laboratorio — un reinicio que olvida el pico convierte
una escalera de candados en una posicion sin proteccion.
"""
import time

from motor import config, contratos, reloj


class Posicion(object):
    """Un vertical vivo. Todo lo necesario para gestionarlo sin volver a
    consultar de donde salio."""

    CAMPOS = ('ref', 'direccion', 'subyacente', 'sim_larga', 'sim_corta',
              'strike_larga', 'strike_corta', 'expiracion', 'qty', 'debito',
              'ts_apertura', 'peak_pct', 'lecturas', 'mark', 'mark_pct',
              'cerrada', 'motivo_cierre', 'precio_cierre', 'ts_cierre',
              'sombra')

    def __init__(self, **kw):
        for c in self.CAMPOS:
            setattr(self, c, kw.get(c))
        self.qty = int(self.qty or 0)
        self.debito = float(self.debito or 0.0)
        self.peak_pct = float(self.peak_pct or 0.0)
        self.lecturas = list(self.lecturas or [])
        self.cerrada = bool(self.cerrada)
        self.sombra = bool(self.sombra)

    @classmethod
    def de_spread(cls, spread, qty, precio, ts, ref, sombra=False):
        return cls(ref=ref, direccion=spread.direccion,
                   subyacente=spread.subyacente,
                   sim_larga=spread.larga.simbolo,
                   sim_corta=spread.corta.simbolo,
                   strike_larga=spread.larga.strike,
                   strike_corta=spread.corta.strike,
                   expiracion=spread.expiracion, qty=qty, debito=precio,
                   ts_apertura=ts, peak_pct=0.0, lecturas=[], mark=precio,
                   mark_pct=0.0, cerrada=False, sombra=sombra)

    @property
    def ancho(self):
        return abs((self.strike_larga or 0) - (self.strike_corta or 0))

    @property
    def prima(self):
        """Dolares realmente en riesgo: debito x 100 x contratos."""
        return self.debito * 100.0 * self.qty

    def edad_h(self, ahora):
        return (ahora - (self.ts_apertura or ahora)) / 3600.0

    def pnl(self):
        return (self.mark - self.debito) * 100.0 * self.qty

    def actualizar_mark(self, mark):
        """Registra un mark nuevo y mueve el pico. Devuelve mark_pct."""
        self.mark = mark
        self.mark_pct = ((mark - self.debito) / self.debito * 100.0
                         if self.debito > 0 else 0.0)
        self.peak_pct = max(self.peak_pct, self.mark_pct)
        self.lecturas.append(round(self.mark_pct, 3))
        if len(self.lecturas) > 10:
            self.lecturas = self.lecturas[-10:]
        return self.mark_pct

    def dict(self):
        return {c: getattr(self, c) for c in self.CAMPOS}

    def spread_para_cierre(self, quotes):
        """Reconstruye un contratos.Spread con quotes frescas, para cotizar el
        cierre. Los OI/volumen no importan aqui: G10 es un gate de entrada."""
        bl, al = quotes.get(self.sim_larga, (0.0, 0.0))
        bc, ac = quotes.get(self.sim_corta, (0.0, 0.0))
        tipo = 'call' if self.direccion == 'LONG' else 'put'
        larga = contratos.Quote(self.sim_larga, bl, al, self.strike_larga,
                                tipo)
        corta = contratos.Quote(self.sim_corta, bc, ac, self.strike_corta,
                                tipo)
        return contratos.Spread(self.direccion, larga, corta, self.expiracion,
                                self.subyacente, 0.0)


class Cartera(object):
    def __init__(self, cliente, diario, ahora=time.time):
        self.cli = cliente
        self.diario = diario
        self._ahora = ahora
        self.posiciones = []
        self.equity = 0.0
        self.equity_inicio_dia = 0.0
        self.equity_inicio_total = 0.0
        self.pnl_realizado_dia = 0.0
        self.trades_hoy = 0
        self.ultimo_cierre_rojo = None
        self.halt = None
        self.sesion = reloj.fecha_et(self._ahora())
        self.prima_pendiente = 0.0

    # -- persistencia ------------------------------------------------------
    def cargar(self):
        e = self.diario.cargar_estado()
        self.posiciones = [Posicion(**p) for p in e.get('posiciones', [])
                           if not p.get('cerrada')]
        self.equity_inicio_total = float(e.get('equity_inicio_total') or 0.0)
        self.halt = e.get('halt')
        guardada = e.get('sesion')
        if guardada == self.sesion:
            self.equity_inicio_dia = float(e.get('equity_inicio_dia') or 0.0)
            self.pnl_realizado_dia = float(e.get('pnl_realizado_dia') or 0.0)
            self.trades_hoy = int(e.get('trades_hoy') or 0)
            self.ultimo_cierre_rojo = e.get('ultimo_cierre_rojo')
        else:
            # Sesion nueva: los contadores diarios y el HALT de dia se limpian.
            # El HALT total (R7) NO: solo Paul lo re-arma con sus manos.
            self.equity_inicio_dia = 0.0
            self.pnl_realizado_dia = 0.0
            self.trades_hoy = 0
            self.ultimo_cierre_rojo = None
            if self.halt == 'WATCHDOG_DIA':
                self.halt = None
        return self

    def guardar(self):
        self.diario.guardar_estado({
            'sesion': self.sesion,
            'actualizado_et': reloj.iso_et(self._ahora()),
            'posiciones': [p.dict() for p in self.posiciones],
            'equity': self.equity,
            'equity_inicio_dia': self.equity_inicio_dia,
            'equity_inicio_total': self.equity_inicio_total,
            'pnl_realizado_dia': self.pnl_realizado_dia,
            'trades_hoy': self.trades_hoy,
            'ultimo_cierre_rojo': self.ultimo_cierre_rojo,
            'halt': self.halt,
        })

    # -- cuenta ------------------------------------------------------------
    def refrescar_cuenta(self):
        r = self.cli.cuenta()
        if not r.ok:
            return r.error
        cuenta = r.datos or {}
        numero = str(cuenta.get('account_number') or '')
        if numero != config.CUENTA_ESPERADA:
            return 'CUENTA_INCORRECTA:%s' % (numero or 'SIN_NUMERO')
        if getattr(self.cli.cfg, 'opera_de_verdad', False):
            try:
                nivel = int(cuenta.get('options_trading_level') or 0)
            except (TypeError, ValueError):
                nivel = 0
            if nivel < 3:
                return 'NIVEL_OPCIONES_INSUFICIENTE:%d' % nivel
        self.equity = float(cuenta.get('equity') or 0.0)
        if not self.equity_inicio_total:
            self.equity_inicio_total = self.equity
        if not self.equity_inicio_dia:
            self.equity_inicio_dia = self.equity
        return ''

    # -- marks -------------------------------------------------------------
    def vivas(self):
        return [p for p in self.posiciones if not p.cerrada]

    def simbolos_vivos(self):
        s = []
        for p in self.vivas():
            s.extend([p.sim_larga, p.sim_corta])
        return s

    def marcar(self):
        """Re-cotiza todas las patas vivas y actualiza marks/picos.

        Devuelve (quotes, error). Si la cotizacion falla NO se tocan los marks:
        un mark viejo es mejor que un mark inventado, y las salidas prefieren
        no disparar a disparar con datos falsos.
        """
        simbolos = self.simbolos_vivos()
        if not simbolos:
            return {}, ''
        quotes, err = contratos.cotizar(self.cli, sorted(set(simbolos)))
        if err:
            return {}, err
        for p in self.vivas():
            if p.sim_larga in quotes and p.sim_corta in quotes:
                sp = p.spread_para_cierre(quotes)
                p.actualizar_mark(sp.mid)
        return quotes, ''

    # -- P&L ---------------------------------------------------------------
    def pnl_flotante(self):
        return sum(p.pnl() for p in self.vivas())

    def pnl_dia_pct(self):
        base = self.equity_inicio_dia or self.equity or 1.0
        return (self.pnl_realizado_dia + self.pnl_flotante()) / base * 100.0

    def pnl_total_pct(self):
        base = self.equity_inicio_total or self.equity or 1.0
        # `equity` de Alpaca ya incluye el valor de mercado y el P&L no
        # realizado de las posiciones abiertas. Sumar `pnl_flotante()` aqui lo
        # contaba dos veces y podia disparar R7 antes de su umbral real.
        return (self.equity - base) / base * 100.0

    def prima_viva(self):
        return sum(p.prima for p in self.vivas())

    # -- mutaciones --------------------------------------------------------
    def registrar_apertura(self, spread, qty, precio, ref, sombra=False):
        p = Posicion.de_spread(spread, qty, precio, self._ahora(), ref, sombra)
        self.posiciones.append(p)
        self.trades_hoy += 1
        self.guardar()
        return p

    def registrar_cierre(self, pos, precio, motivo, qty=None):
        """Registra un fill de cierre completo o parcial.

        Una orden mleg de varias unidades puede llenar parcialmente antes de
        cancelarse. La cantidad residual debe seguir viva y protegida por el
        monitor; borrarla del estado seria dejar riesgo real sin supervision.
        """
        cantidad = min(pos.qty, max(0, int(qty if qty is not None else pos.qty)))
        if cantidad < 1:
            return 0.0
        ahora = self._ahora()
        realizado = (precio - pos.debito) * 100.0 * cantidad
        self.pnl_realizado_dia += realizado
        if realizado < 0:
            self.ultimo_cierre_rojo = ahora           # G6 anti-revancha
        if cantidad >= pos.qty:
            pos.cerrada = True
            pos.motivo_cierre = motivo
            pos.precio_cierre = precio
            pos.ts_cierre = ahora
            self.posiciones = [p for p in self.posiciones if p is not pos]
        else:
            pos.qty -= cantidad
        self.guardar()
        return realizado
