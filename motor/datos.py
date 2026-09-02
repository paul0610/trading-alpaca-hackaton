# -*- coding: utf-8 -*-
"""Market Data: velas 1m de SPY y agregacion local a 5/15/30m.

Fuente unica (LOGICA §3): velas 1m del feed configurado (IEX en paper). Los
marcos superiores se agregan **localmente** para tener una sola verdad.

ANTI-LOOKAHEAD (Regla 4): un bucket agregado solo se emite cuando su ventana
ya cerro en el reloj (`ahora >= fin`). Ningun indicador ve jamas la vela en
formacion. Esta es la regla que hace que el replay historico y la sesion viva
produzcan exactamente los mismos numeros.
"""
from motor import config, reloj


class Vela(object):
    __slots__ = ('ts', 'o', 'h', 'l', 'c', 'v')

    def __init__(self, ts, o, h, l, c, v=0):
        self.ts = ts          # epoch UTC del INICIO de la vela
        self.o = o
        self.h = h
        self.l = l
        self.c = c
        self.v = v

    @property
    def rango(self):
        return self.h - self.l

    @property
    def alcista(self):
        return self.c > self.o

    def __repr__(self):
        return ('Vela(%s o=%.2f h=%.2f l=%.2f c=%.2f)'
                % (reloj.iso_et(self.ts), self.o, self.h, self.l, self.c))

    def dict(self):
        return {'ts': self.ts, 'et': reloj.iso_et(self.ts), 'o': self.o,
                'h': self.h, 'l': self.l, 'c': self.c, 'v': self.v}


def _de_json(b):
    return Vela(reloj.epoch_de_iso(b['t']), float(b['o']), float(b['h']),
                float(b['l']), float(b['c']), float(b.get('v', 0)))


def en_sesion_regular(ts):
    """True si el minuto pertenece a la sesion regular 9:30-16:00 ET.

    [DESV] La spec no lo dice explicitamente, pero agregar velas de pre/post
    market (finisimas en IEX) al canal de 30 min y a EMA/RSI/ATR distorsiona
    los indicadores y rompe la comparabilidad con la lectura de Paul, que es
    de sesion regular. Ver DESVIACIONES.md.
    """
    m = reloj.minutos_et(ts)
    return config.APERTURA_ET <= m < config.CIERRE_ET


def velas_1m(cliente, simbolo, inicio, fin, feed=None, solo_regular=True):
    """Descarga velas 1m entre dos epochs. Devuelve (velas, error)."""
    feed = feed or cliente.cfg.feed
    fuera = []
    token = None
    while True:
        r = cliente.datos('GET', '/v2/stocks/%s/bars' % simbolo, {
            'timeframe': '1Min',
            'start': reloj.iso_utc(inicio),
            'end': reloj.iso_utc(fin),
            'limit': 10000,
            'adjustment': 'raw',
            'feed': feed,
            'sort': 'asc',
            'page_token': token,
        })
        if not r.ok:
            return [], r.error
        cuerpo = r.datos or {}
        for b in (cuerpo.get('bars') or []):
            try:
                fuera.append(_de_json(b))
            except (KeyError, ValueError):
                continue
        token = cuerpo.get('next_page_token')
        if not token:
            break
    if solo_regular:
        fuera = [v for v in fuera if en_sesion_regular(v.ts)]
    # El endpoint puede devolver la barra del minuto actualmente en formacion.
    # Ningun indicador (incluido RSI1m del monitor) debe verla antes de cerrar.
    fuera = [v for v in fuera if v.ts + 60 <= fin]
    fuera.sort(key=lambda v: v.ts)
    return fuera, ''


def agregar(velas1m, minutos, ahora=None):
    """Agrega velas 1m a marcos de `minutos`. Solo buckets YA CERRADOS.

    Los buckets se alinean al reloj absoluto (multiplos de `minutos` desde la
    hora en punto). Como el offset de ET son horas enteras y 9:30 ET es
    multiplo de 5, 15 y 30 minutos, la alineacion por epoch coincide
    exactamente con las velas de 9:30, 9:35, 10:00... de cualquier plataforma.
    """
    if not velas1m:
        return []
    ancho = minutos * 60
    baldes = {}
    orden = []
    for v in velas1m:
        clave = (int(v.ts) // ancho) * ancho
        b = baldes.get(clave)
        if b is None:
            baldes[clave] = Vela(clave, v.o, v.h, v.l, v.c, v.v)
            orden.append(clave)
        else:
            b.h = max(b.h, v.h)
            b.l = min(b.l, v.l)
            b.c = v.c
            b.v += v.v
    orden.sort()
    fuera = []
    for clave in orden:
        if ahora is not None and (clave + ancho) > ahora:
            continue          # bucket en formacion: NO se emite (anti-lookahead)
        fuera.append(baldes[clave])
    return fuera


def frescura_min(velas1m, ahora):
    """Antiguedad en minutos del cierre de la ultima vela 1m (G8).

    Se mide contra el CIERRE del bucket (ts + 60 s), no contra su apertura:
    una vela que abrio hace 2:59 pero cerro hace 1:59 tiene 1:59 de antiguedad.
    """
    if not velas1m:
        return float('inf')
    return (ahora - (velas1m[-1].ts + 60)) / 60.0


def snapshot_indices(cliente, simbolos):
    """Ultimo trade de los subyacentes (para spot en la seleccion de contrato)."""
    r = cliente.datos('GET', '/v2/stocks/trades/latest',
                      {'symbols': ','.join(simbolos), 'feed': cliente.cfg.feed})
    if not r.ok:
        return {}, r.error
    trades = (r.datos or {}).get('trades') or {}
    return {k: float(v['p']) for k, v in trades.items() if 'p' in v}, ''


def titulares(cliente, simbolo, limite=5):
    """Ultimos titulares de Alpaca News para el snapshot del LLM (§6).

    Noticias es contexto blando: un fallo se entrega como `error` al LLM pero
    no suplanta el gate macro G7 ni abre/cierra por si mismo.
    """
    r = cliente.datos('GET', '/v1beta1/news', {
        'symbols': simbolo, 'limit': limite, 'sort': 'desc',
        'include_content': 'false', 'exclude_contentless': 'true',
    })
    if not r.ok:
        return [], r.error
    fuera = []
    for n in ((r.datos or {}).get('news') or [])[:limite]:
        fuera.append({'headline': str(n.get('headline') or '')[:300],
                      'source': str(n.get('source') or '')[:80],
                      'created_at': n.get('created_at'),
                      'symbols': list(n.get('symbols') or [])[:10]})
    return fuera, ''
