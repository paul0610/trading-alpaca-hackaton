# -*- coding: utf-8 -*-
"""SENAL de entrada — "cruce limpio adaptado" (LOGICA §4). Determinista.

S1 cruce dentro de la vela · S2 cierre limpio con cuerpo · S3 tendencia a favor
S4 RSI multiframe alineado · S5 vela-tope · S6 anti-serrucho.

NOTA DE DISENO (S6 y la cadencia de 10 min)
-------------------------------------------
S6 mira "las ultimas 3 velas 5m", pero el ciclo de decision corre cada 10 min
(D3) y por tanto solo *actua* sobre una de cada dos velas 5m. Si la senal se
computara unicamente en el momento de actuar, S6 consultaria un historial con
huecos y el anti-serrucho seria ciego a la mitad de los cruces.

La solucion es hacer el calculo **sin estado**: cada ciclo se recomputa la
serie completa de senales sobre TODAS las velas 5m cerradas de la ventana. S6
ve asi el historial integro. El agente luego *actua* solo sobre la ultima vela
y solo en su franja de 10 min, y nunca arrastra un cruce viejo. No cambia
ningun parametro de §13; cambia donde vive el historial.
"""
from motor import config, indicadores

LONG = 'LONG'
SHORT = 'SHORT'


class Senal(object):
    """Resultado de evaluar una vela 5m cerrada."""

    __slots__ = ('ts', 'direccion', 'go', 'cond', 'datos', 'motivo')

    def __init__(self, ts, direccion=None, go=False, cond=None, datos=None,
                 motivo=''):
        self.ts = ts
        self.direccion = direccion
        self.go = go
        self.cond = cond or {}
        self.datos = datos or {}
        self.motivo = motivo

    @property
    def crudo(self):
        """True si S1..S5 pasaron (hubo cruce), aunque S6 lo vete."""
        return all(self.cond.get(k) for k in ('S1', 'S2', 'S3', 'S4', 'S5'))

    def dict(self):
        return {'ts': self.ts, 'direccion': self.direccion, 'go': self.go,
                'condiciones': dict(self.cond), 'motivo': self.motivo,
                'datos': dict(self.datos)}

    def __repr__(self):
        return ('Senal(%s %s go=%r %s)'
                % (self.ts, self.direccion, self.go, self.motivo))


ANCHO_5M = 5 * 60
ANCHO_15M = 15 * 60
ANCHO_30M = 30 * 60


def _valor_hasta(velas, serie, ts_limite, ancho):
    """Ultimo valor de `serie` cuya vela ya habia CERRADO en `ts_limite`.

    Es el guardian anti-lookahead entre marcos temporales: al evaluar una vela
    5m que cierra a las 10:05, el RSI de 15m que se puede usar es el de la
    vela que cerro a las 10:00, nunca el de la que cierra a las 10:15.

    `ancho` se pasa explicito y NO se infiere de la serie: si faltara el primer
    bucket (el feed IEX es fino y tiene huecos), inferirlo de las dos primeras
    velas daria 1800 donde son 900 y correria el limite media hora.
    """
    fuera = None
    for i, v in enumerate(velas):
        if v.ts + ancho > ts_limite:
            break
        if serie[i] is not None:
            fuera = serie[i]
    return fuera


def serie_senales(v5, v15, v30):
    """Evalua TODAS las velas 5m cerradas. Devuelve list[Senal] alineada a v5.

    Las velas sin indicadores calentados devuelven una Senal NO-GO con motivo
    `CALENTANDO` — nunca un GO por accidente.
    """
    n = len(v5)
    cierres5 = [v.c for v in v5]
    ema_r = indicadores.ema(cierres5, config.EMA_RAPIDA)
    ema_l = indicadores.ema(cierres5, config.EMA_LENTA)
    rsi5 = indicadores.rsi_wilder(cierres5, config.RSI_PERIODO)
    atr5 = indicadores.atr_wilder(v5, config.ATR_PERIODO)
    rsi15 = indicadores.rsi_wilder([v.c for v in v15], config.RSI_PERIODO)
    rsi30 = indicadores.rsi_wilder([v.c for v in v30], config.RSI_PERIODO)

    # -- pasada 1: S1..S5 por vela (sin S6, que necesita el historial) -----
    parciales = []
    for i in range(n):
        parciales.append(_evaluar_barra(i, v5, v15, v30, ema_r, ema_l, rsi5,
                                        atr5, rsi15, rsi30))

    # -- pasada 2: S6 anti-serrucho sobre el historial completo ------------
    return aplicar_s6(parciales)


def aplicar_s6(parciales):
    """S6 anti-serrucho: veta un cruce si YA hubo uno del mismo lado en las
    ultimas `ANTI_SERRUCHO_VELAS` velas 5m, se haya tomado o vetado.

    Cuenta cruces *crudos* (S1-S5), no operaciones: "sin senal previa (tomada
    o vetada)". Si contara solo las tomadas, un cruce vetado por S6 dejaria
    hueco para el siguiente y el serrucho volveria por la puerta de atras.
    """
    for i, s in enumerate(parciales):
        if s.direccion is None:
            continue
        ventana = parciales[max(0, i - config.ANTI_SERRUCHO_VELAS):i]
        repetido = any(p.crudo and p.direccion == s.direccion for p in ventana)
        s.cond['S6'] = not repetido
        s.go = s.crudo and s.cond['S6']
        if s.crudo and repetido:
            s.motivo = 'SERRUCHO'
        elif s.go:
            s.motivo = 'GO'
    return parciales


def _evaluar_barra(i, v5, v15, v30, ema_r, ema_l, rsi5, atr5, rsi15, rsi30):
    v = v5[i]
    base = {'S1': False, 'S2': False, 'S3': False, 'S4': False, 'S5': False,
            'S6': None}

    # calentamiento: sin indicadores no hay opinion posible
    atr_prev = atr5[i - 1] if i >= 1 else None
    cierre_5m = v.ts + ANCHO_5M
    r15 = _valor_hasta(v15, rsi15, cierre_5m, ANCHO_15M)
    r30 = _valor_hasta(v30, rsi30, cierre_5m, ANCHO_30M)
    if (i < config.CANAL_VELAS_5M or ema_r[i] is None or ema_l[i] is None
            or rsi5[i] is None or atr_prev is None or r15 is None
            or r30 is None):
        return Senal(v.ts, None, False, base, motivo='CALENTANDO')

    previas = v5[i - config.CANAL_VELAS_5M:i]
    nivel_alto = max(p.h for p in previas)
    nivel_bajo = min(p.l for p in previas)

    datos = {'nivel_alto': round(nivel_alto, 4),
             'nivel_bajo': round(nivel_bajo, 4),
             'open': v.o, 'close': v.c, 'high': v.h, 'low': v.l,
             'ema20': round(ema_r[i], 4), 'ema50': round(ema_l[i], 4),
             'rsi5m': round(rsi5[i], 2), 'rsi15m': round(r15, 2),
             'rsi30m': round(r30, 2),
             'atr20_prev': round(atr_prev, 4), 'rango': round(v.rango, 4)}

    # ---- LONG ----
    largo = dict(base)
    largo['S1'] = v.o <= nivel_alto + config.EPS and v.c > nivel_alto
    largo['S2'] = ((v.c - nivel_alto) >= config.BUFFER_CRUCE - config.EPS
                   and v.c > v.o)
    largo['S3'] = ema_r[i] > ema_l[i]
    largo['S4'] = (config.RSI5M_LONG[0] <= rsi5[i] <= config.RSI5M_LONG[1]
                   and r15 >= config.RSI15M_LONG_MIN
                   and r30 >= config.RSI30M_LONG_MIN)
    largo['S5'] = v.rango <= config.VELA_TOPE_X_ATR * atr_prev
    if all(largo[k] for k in ('S1', 'S2', 'S3', 'S4', 'S5')):
        return Senal(v.ts, LONG, False, largo, datos)

    # ---- SHORT (espejo exacto) ----
    corto = dict(base)
    corto['S1'] = v.o >= nivel_bajo - config.EPS and v.c < nivel_bajo
    corto['S2'] = ((nivel_bajo - v.c) >= config.BUFFER_CRUCE - config.EPS
                   and v.c < v.o)
    corto['S3'] = ema_r[i] < ema_l[i]
    corto['S4'] = (config.RSI5M_SHORT[0] <= rsi5[i] <= config.RSI5M_SHORT[1]
                   and r15 <= config.RSI15M_SHORT_MAX
                   and r30 <= config.RSI30M_SHORT_MAX)
    corto['S5'] = v.rango <= config.VELA_TOPE_X_ATR * atr_prev
    if all(corto[k] for k in ('S1', 'S2', 'S3', 'S4', 'S5')):
        return Senal(v.ts, SHORT, False, corto, datos)

    # Sin cruce completo: se reporta el lado que mas avanzo, para que el
    # diario muestre *por que* no hubo senal (evidencia de las no-decisiones).
    puntos_l = sum(1 for k in ('S1', 'S2', 'S3', 'S4', 'S5') if largo[k])
    puntos_c = sum(1 for k in ('S1', 'S2', 'S3', 'S4', 'S5') if corto[k])
    cond = largo if puntos_l >= puntos_c else corto
    fallidas = [k for k in ('S1', 'S2', 'S3', 'S4', 'S5') if not cond[k]]
    return Senal(v.ts, None, False, cond, datos,
                 motivo='NO_CRUCE:' + ','.join(fallidas))


def ultima(v5, v15, v30):
    """Senal de la ultima vela 5m cerrada (la que el ciclo puede accionar)."""
    serie = serie_senales(v5, v15, v30)
    return serie[-1] if serie else Senal(0, motivo='SIN_DATOS')
