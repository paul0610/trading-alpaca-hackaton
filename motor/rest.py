# -*- coding: utf-8 -*-
"""Cliente REST minimo sobre urllib (D6/D8: cero dependencias).

Regla 6 de ARQUITECTURA §6: un reintento con backoff de 2 s. Si persiste, el
que llama decide — el ciclo de ENTRADA se pierde y journalea `API_DOWN`
(jamas abrir a ciegas), pero las SALIDAS insisten (proteger > abrir).

Nunca lanza excepciones hacia arriba: devuelve `Respuesta`, que es explicita
sobre el fallo. Un motor de trading no puede morir por un 502.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from motor import config


class Respuesta(object):
    __slots__ = ('ok', 'datos', 'codigo', 'error')

    def __init__(self, ok, datos=None, codigo=0, error=''):
        self.ok = ok
        self.datos = datos
        self.codigo = codigo
        self.error = error

    def __repr__(self):
        return ('Respuesta(ok=%r, codigo=%r, error=%r)'
                % (self.ok, self.codigo, self.error))


def _una_vez(metodo, url, headers, cuerpo, timeout):
    datos = None
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode('utf-8')
    req = urllib.request.Request(url, data=datos, headers=headers,
                                 method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            codigo = getattr(r, 'status', None) or r.getcode()
            crudo = r.read().decode('utf-8')
            if not crudo:
                return Respuesta(True, {}, codigo)
            return Respuesta(True, json.loads(crudo), codigo)
    except urllib.error.HTTPError as e:
        try:
            detalle = e.read().decode('utf-8')[:400]
        except Exception:
            detalle = ''
        return Respuesta(False, None, e.code, 'HTTP %s %s' % (e.code, detalle))
    except urllib.error.URLError as e:
        return Respuesta(False, None, 0, 'RED %s' % (e.reason,))
    except json.JSONDecodeError as e:
        return Respuesta(False, None, 0, 'JSON invalido: %s' % (e,))
    except Exception as e:                              # timeout, socket, etc.
        return Respuesta(False, None, 0, '%s: %s' % (type(e).__name__, e))


def _reintentable(r):
    """429 y 5xx merecen reintento; un 4xx de logica (400/403/422) no."""
    return r.codigo == 0 or r.codigo == 429 or r.codigo >= 500


class Cliente(object):
    """Cliente REST de Alpaca (trading + market data)."""

    def __init__(self, cfg, dormir=time.sleep):
        self.cfg = cfg
        self._dormir = dormir            # inyectable para tests deterministas

    def pedir(self, metodo, url, params=None, cuerpo=None,
              reintentos=config.REST_REINTENTOS,
              timeout=config.REST_TIMEOUT_S):
        if params:
            limpios = {k: v for k, v in params.items() if v is not None}
            if limpios:
                url = url + '?' + urllib.parse.urlencode(limpios)
        headers = self.cfg.headers()
        r = _una_vez(metodo, url, headers, cuerpo, timeout)
        intento = 0
        while (not r.ok) and intento < reintentos and _reintentable(r):
            intento += 1
            self._dormir(config.REST_BACKOFF_S * intento)
            r = _una_vez(metodo, url, headers, cuerpo, timeout)
        return r

    # -- atajos ------------------------------------------------------------
    def trading(self, metodo, ruta, params=None, cuerpo=None, **kw):
        return self.pedir(metodo, config.URL_TRADING + ruta, params, cuerpo,
                          **kw)

    def datos(self, metodo, ruta, params=None, **kw):
        return self.pedir(metodo, config.URL_DATOS + ruta, params, None, **kw)

    # -- cuenta / reloj ----------------------------------------------------
    def cuenta(self):
        return self.trading('GET', '/v2/account')

    def reloj(self):
        return self.trading('GET', '/v2/clock')

    def calendario(self, desde, hasta):
        return self.trading('GET', '/v2/calendar',
                            {'start': desde, 'end': hasta})
