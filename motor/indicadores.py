# -*- coding: utf-8 -*-
"""Indicadores: EMA, RSI de Wilder, ATR de Wilder. Funciones puras.

Todas devuelven una lista **alineada** con la serie de entrada, con `None` en
el periodo de calentamiento. Alinear en vez de recortar evita el bug clasico
de indexar el indicador con el indice de la vela equivocada.
"""


def ema(valores, periodo):
    """EMA con semilla SMA (convencion estandar)."""
    n = len(valores)
    out = [None] * n
    if n < periodo or periodo < 1:
        return out
    k = 2.0 / (periodo + 1.0)
    prev = sum(valores[:periodo]) / float(periodo)
    out[periodo - 1] = prev
    for i in range(periodo, n):
        prev = (valores[i] - prev) * k + prev
        out[i] = prev
    return out


def rsi_wilder(cierres, periodo=14):
    """RSI de Wilder (suavizado de Wilder, no SMA de Cutler).

    El primer valor sale en el indice `periodo` (necesita `periodo` cambios).
    """
    n = len(cierres)
    out = [None] * n
    if n <= periodo or periodo < 1:
        return out
    ganancias = 0.0
    perdidas = 0.0
    for i in range(1, periodo + 1):
        d = cierres[i] - cierres[i - 1]
        if d >= 0:
            ganancias += d
        else:
            perdidas -= d
    avg_g = ganancias / periodo
    avg_p = perdidas / periodo
    out[periodo] = _rsi(avg_g, avg_p)
    for i in range(periodo + 1, n):
        d = cierres[i] - cierres[i - 1]
        subida = d if d > 0 else 0.0
        bajada = -d if d < 0 else 0.0
        avg_g = (avg_g * (periodo - 1) + subida) / periodo
        avg_p = (avg_p * (periodo - 1) + bajada) / periodo
        out[i] = _rsi(avg_g, avg_p)
    return out


def _rsi(avg_g, avg_p):
    if avg_p == 0:
        return 100.0 if avg_g > 0 else 50.0   # serie plana => neutral, no 100
    rs = avg_g / avg_p
    return 100.0 - (100.0 / (1.0 + rs))


def rango_verdadero(velas):
    """TR alineado; la primera vela no tiene cierre previo (None)."""
    out = [None] * len(velas)
    for i in range(1, len(velas)):
        v, prev = velas[i], velas[i - 1]
        out[i] = max(v.h - v.l, abs(v.h - prev.c), abs(v.l - prev.c))
    return out


def atr_wilder(velas, periodo=20):
    """ATR de Wilder sobre objetos con .h .l .c"""
    n = len(velas)
    out = [None] * n
    if n <= periodo or periodo < 1:
        return out
    tr = rango_verdadero(velas)
    prev = sum(tr[1:periodo + 1]) / float(periodo)
    out[periodo] = prev
    for i in range(periodo + 1, n):
        prev = (prev * (periodo - 1) + tr[i]) / periodo
        out[i] = prev
    return out
