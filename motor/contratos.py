# -*- coding: utf-8 -*-
"""Seleccion de contrato: debit spread vertical de ancho $2 (LOGICA §7).

LONG  = call debit spread  (compra call <= spot, vende call +$2)
SHORT = put  debit spread  (compra put  >= spot, vende put  -$2)

La perdida maxima es el debito pagado, por construccion. Ese es el corazon de
la tesis del proyecto: la perdida se compra por adelantado.
"""
from datetime import date

from motor import config, reloj


class Quote(object):
    __slots__ = ('simbolo', 'bid', 'ask', 'strike', 'tipo', 'oi', 'volumen',
                 'iv')

    def __init__(self, simbolo, bid, ask, strike, tipo, oi=None, volumen=0,
                 iv=None):
        self.simbolo = simbolo
        self.bid = bid
        self.ask = ask
        self.strike = strike
        self.tipo = tipo
        self.oi = oi
        self.volumen = volumen
        self.iv = iv

    @property
    def mid(self):
        return (self.bid + self.ask) / 2.0

    def dict(self):
        return {'simbolo': self.simbolo, 'bid': self.bid, 'ask': self.ask,
                'mid': round(self.mid, 4), 'strike': self.strike,
                'tipo': self.tipo, 'oi': self.oi, 'volumen': self.volumen,
                'iv': self.iv}


class Spread(object):
    """Un vertical de debito ya cotizado, listo para evaluar y ordenar."""

    def __init__(self, direccion, larga, corta, expiracion, subyacente, spot):
        self.direccion = direccion
        self.larga = larga            # pata que se COMPRA
        self.corta = corta            # pata que se VENDE
        self.expiracion = expiracion
        self.subyacente = subyacente
        self.spot = spot

    @property
    def ancho(self):
        return abs(self.larga.strike - self.corta.strike)

    @property
    def mid(self):
        """Debito teorico: mid(larga) - mid(corta) (§7.3)."""
        return self.larga.mid - self.corta.mid

    @property
    def ask_neto(self):
        """Lo peor que puede costar comprarlo: pago el ask, vendo al bid."""
        return self.larga.ask - self.corta.bid

    @property
    def bid_neto(self):
        """Lo peor que puede rendir venderlo (cierre agresivo final, §8)."""
        return self.larga.bid - self.corta.ask

    @property
    def ganancia_max(self):
        """Beneficio maximo posible del vertical: ancho - debito."""
        return self.ancho - self.mid

    def liquidez(self):
        """G10 (§7.4). Devuelve (ok, razon, detalle)."""
        ancho_rel = ((self.ask_neto - self.bid_neto) / self.mid
                     if self.mid > 0 else float('inf'))
        oi_disponibles = [q.oi for q in (self.larga, self.corta)
                          if q.oi is not None]
        oi_min = min(oi_disponibles) if oi_disponibles else None
        vol_min = min(self.larga.volumen, self.corta.volumen)
        detalle = {'bid_larga': self.larga.bid, 'bid_corta': self.corta.bid,
                   'mid_neto': round(self.mid, 4),
                   'ask_neto': round(self.ask_neto, 4),
                   'bid_neto': round(self.bid_neto, 4),
                   'spread_rel': (round(ancho_rel, 4)
                                  if ancho_rel != float('inf') else None),
                   'oi_min': oi_min, 'volumen_min': vol_min,
                   'criterio_larga': ('OI' if self.larga.oi is not None
                                      else 'VOLUMEN'),
                   'criterio_corta': ('OI' if self.corta.oi is not None
                                      else 'VOLUMEN')}
        if self.larga.bid <= 0 or self.corta.bid <= 0:
            return False, 'SIN_LIQUIDEZ:BID_CERO', detalle
        if self.mid <= 0:
            return False, 'SIN_LIQUIDEZ:MID_NO_POSITIVO', detalle
        if ancho_rel > config.SPREAD_REL_MAX:
            return False, 'SIN_LIQUIDEZ:SPREAD_ANCHO', detalle
        # El volumen es fallback solo cuando OI NO esta disponible, no una
        # forma de perdonar un OI conocido pero bajo (§7.4).
        def pata_liquida(q):
            if q.oi is None:
                return q.volumen >= config.VOLUMEN_MIN_ALTERNO
            return q.oi >= config.OI_MIN

        if not (pata_liquida(self.larga) and pata_liquida(self.corta)):
            return False, 'SIN_LIQUIDEZ:OI_BAJO', detalle
        return True, '', detalle

    def dict(self):
        return {'direccion': self.direccion, 'subyacente': self.subyacente,
                'expiracion': self.expiracion, 'spot': self.spot,
                'ancho': self.ancho, 'mid': round(self.mid, 4),
                'ask_neto': round(self.ask_neto, 4),
                'bid_neto': round(self.bid_neto, 4),
                'ganancia_max': round(self.ganancia_max, 4),
                'larga': self.larga.dict(), 'corta': self.corta.dict()}

    def patas_apertura(self):
        """Patas para una orden mleg de APERTURA (comprar el spread)."""
        return [{'symbol': self.larga.simbolo, 'ratio_qty': '1',
                 'side': 'buy', 'position_intent': 'buy_to_open'},
                {'symbol': self.corta.simbolo, 'ratio_qty': '1',
                 'side': 'sell', 'position_intent': 'sell_to_open'}]

    def patas_cierre(self):
        """Patas para CERRAR (vender el spread): espejo de la apertura."""
        return [{'symbol': self.larga.simbolo, 'ratio_qty': '1',
                 'side': 'sell', 'position_intent': 'sell_to_close'},
                {'symbol': self.corta.simbolo, 'ratio_qty': '1',
                 'side': 'buy', 'position_intent': 'buy_to_close'}]


# ---------------------------------------------------------------------------
# Descarga de cadena
# ---------------------------------------------------------------------------

def dte_de(expiracion, hoy_et):
    """Dias calendario entre la sesion ET de hoy y la expiracion."""
    a, m, d = [int(x) for x in expiracion.split('-')]
    return (date(a, m, d) - date(*[int(x) for x in hoy_et.split('-')])).days


def listar_contratos(cliente, subyacente, tipo, spot, hoy_et, margen=8.0):
    """Contratos activos del subyacente con DTE 1-2 y strikes cerca del spot."""
    r = cliente.trading('GET', '/v2/options/contracts', {
        'underlying_symbols': subyacente,
        'status': 'active',
        'type': tipo,
        'style': 'american',
        'strike_price_gte': round(spot - margen, 2),
        'strike_price_lte': round(spot + margen, 2),
        'limit': 1000,
    })
    if not r.ok:
        return [], r.error
    fuera = []
    for c in ((r.datos or {}).get('option_contracts') or []):
        exp = c.get('expiration_date', '')
        if not exp:
            continue
        dte = dte_de(exp, hoy_et)
        if config.DTE_MIN <= dte <= config.DTE_MAX:
            fuera.append(c)
    return fuera, ''


def cotizar(cliente, simbolos):
    """Quotes de opciones (bid/ask). Devuelve (dict simbolo -> (bid, ask), err).

    Se usa en el camino de CIERRE, donde solo hacen falta los precios.
    """
    if not simbolos:
        return {}, 'SIN_SIMBOLOS'
    r = cliente.datos('GET', '/v1beta1/options/quotes/latest',
                      {'symbols': ','.join(simbolos)})
    if not r.ok:
        return {}, r.error
    fuera = {}
    for sim, q in ((r.datos or {}).get('quotes') or {}).items():
        fuera[sim] = (float(q.get('bp', 0) or 0), float(q.get('ap', 0) or 0))
    faltantes = sorted(set(simbolos) - set(fuera))
    if faltantes:
        return {}, 'SIN_QUOTE:%s' % ','.join(faltantes[:4])
    return fuera, ''


def snapshots(cliente, simbolos):
    """Quote + volumen del dia en UNA llamada. Devuelve (dict, err).

    Se usa en el camino de ENTRADA porque G10 admite "volumen del dia >= 50"
    como alternativa al OI (§7.4), y `/v2/options/contracts` NO expone volumen
    diario: solo `open_interest`, que en muchas cuentas llega nulo. Sin este
    endpoint la via alterna de G10 seria letra muerta y el gate bloquearia
    todo por `OI_BAJO`.
    """
    if not simbolos:
        return {}, 'SIN_SIMBOLOS'
    r = cliente.datos('GET', '/v1beta1/options/snapshots',
                      {'symbols': ','.join(simbolos), 'feed': 'indicative'})
    if not r.ok:
        return {}, r.error
    fuera = {}
    for sim, s in ((r.datos or {}).get('snapshots') or {}).items():
        q = s.get('latestQuote') or {}
        barra = s.get('dailyBar') or {}
        fuera[sim] = {'bid': float(q.get('bp', 0) or 0),
                      'ask': float(q.get('ap', 0) or 0),
                      'volumen': int(float(barra.get('v', 0) or 0)),
                      'iv': (float(s['impliedVolatility'])
                             if s.get('impliedVolatility') is not None
                             else None)}
    return fuera, ''


def refrescar_spread(cliente, spread):
    """Mismo contrato con quote/volumen/IV frescos antes del POST."""
    simbolos = [spread.larga.simbolo, spread.corta.simbolo]
    snaps, err = snapshots(cliente, simbolos)
    if err:
        return None, err
    if any(s not in snaps for s in simbolos):
        return None, 'SIN_QUOTE'

    def nueva(q):
        s = snaps[q.simbolo]
        return Quote(q.simbolo, s['bid'], s['ask'], q.strike, q.tipo, q.oi,
                     s['volumen'], s.get('iv'))

    return Spread(spread.direccion, nueva(spread.larga), nueva(spread.corta),
                  spread.expiracion, spread.subyacente, spread.spot), ''


def elegir(cliente, subyacente, direccion, spot, ahora):
    """Elige el vertical segun §7. Devuelve (Spread|None, razon).

    LONG : compra la call del primer strike <= spot, vende la de +$2.
    SHORT: espejo con puts (compra el primer strike >= spot, vende la de -$2).
    """
    tipo = 'call' if direccion == 'LONG' else 'put'
    hoy = reloj.fecha_et(ahora)
    contratos, err = listar_contratos(cliente, subyacente, tipo, spot, hoy)
    if err:
        return None, 'API_DOWN:%s' % err[:80]
    if not contratos:
        return None, 'SIN_CADENA_DTE_1_2'

    # La expiracion mas cercana con DTE 1-2 (§7.1)
    expiracion = min(c['expiration_date'] for c in contratos)
    delmismo = [c for c in contratos if c['expiration_date'] == expiracion]
    porstrike = {}
    for c in delmismo:
        try:
            porstrike[round(float(c['strike_price']), 2)] = c
        except (KeyError, ValueError):
            continue
    if not porstrike:
        return None, 'SIN_STRIKES'

    if direccion == 'LONG':
        candidatos = [k for k in porstrike if k <= spot]
        if not candidatos:
            return None, 'SIN_STRIKE_BAJO_SPOT'
        k_larga = max(candidatos)
        k_corta = round(k_larga + config.ANCHO_SPREAD, 2)
    else:
        candidatos = [k for k in porstrike if k >= spot]
        if not candidatos:
            return None, 'SIN_STRIKE_SOBRE_SPOT'
        k_larga = min(candidatos)
        k_corta = round(k_larga - config.ANCHO_SPREAD, 2)
    if k_corta not in porstrike:
        return None, 'SIN_ANCHO_2_DOLARES'

    c_larga, c_corta = porstrike[k_larga], porstrike[k_corta]
    snaps, err = snapshots(cliente, [c_larga['symbol'], c_corta['symbol']])
    if err:
        return None, 'API_DOWN:%s' % err[:80]
    if c_larga['symbol'] not in snaps or c_corta['symbol'] not in snaps:
        return None, 'SIN_QUOTE'

    def _q(c):
        s = snaps[c['symbol']]
        oi_crudo = c.get('open_interest')
        oi = (int(float(oi_crudo))
              if oi_crudo not in (None, '') else None)
        return Quote(c['symbol'], s['bid'], s['ask'],
                     round(float(c['strike_price']), 2), tipo,
                     oi, s['volumen'], s.get('iv'))

    return Spread(direccion, _q(c_larga), _q(c_corta), expiracion, subyacente,
                  spot), ''
