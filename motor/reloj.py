# -*- coding: utf-8 -*-
"""Reloj de mercado en ET. Solo stdlib, sin `zoneinfo` ni `tzdata`.

Windows no trae la base de datos IANA del sistema y `tzdata` es un paquete de
PyPI: usarlo violaria la Regla 1 de ARQUITECTURA §6 ("cero pip installs").
Aqui se implementan las reglas de DST de US Eastern directamente, que son
estables desde 2007 (Energy Policy Act de 2005):

  - EDT (UTC-4) desde el 2do domingo de marzo, 02:00 hora local estandar
    == 07:00 UTC
  - EST (UTC-5) desde el 1er domingo de noviembre, 02:00 hora local de verano
    == 06:00 UTC

CONVENCION CANONICA (Regla 7): toda hora interna es **epoch UTC en segundos**
(un entero o float). ET es siempre una *vista* derivada de ese epoch, jamas el
almacenamiento. Esto hace que el replay historico (I5) sea determinista y que
el cruce de DST no pueda corromper el estado.
"""
import calendar
from datetime import date, datetime, timedelta

SEG_HORA = 3600
OFFSET_EST = -5 * SEG_HORA
OFFSET_EDT = -4 * SEG_HORA


def _domingo_n(anio, mes, n):
    """Devuelve el date del n-esimo domingo (n>=1) del mes dado."""
    d = date(anio, mes, 1)
    # weekday(): lunes=0 ... domingo=6
    adelanto = (6 - d.weekday()) % 7
    return d + timedelta(days=adelanto + 7 * (n - 1))


def _epoch_utc(anio, mes, dia, hora=0, minuto=0, segundo=0):
    return calendar.timegm((anio, mes, dia, hora, minuto, segundo, 0, 0, 0))


def _ventana_dst(anio):
    """(inicio, fin) del horario de verano de US Eastern, en epoch UTC."""
    ini = _domingo_n(anio, 3, 2)
    fin = _domingo_n(anio, 11, 1)
    return (_epoch_utc(ini.year, ini.month, ini.day, 7),
            _epoch_utc(fin.year, fin.month, fin.day, 6))


def offset_et(epoch):
    """Offset de ET respecto a UTC (en segundos, negativo) para ese instante."""
    anio = datetime.utcfromtimestamp(epoch).year
    ini, fin = _ventana_dst(anio)
    return OFFSET_EDT if ini <= epoch < fin else OFFSET_EST


def es_horario_verano(epoch):
    return offset_et(epoch) == OFFSET_EDT


def et(epoch):
    """Vista ET (datetime *naive*) del epoch UTC dado."""
    return datetime.utcfromtimestamp(epoch + offset_et(epoch))


def epoch_de_et(anio, mes, dia, hora=0, minuto=0, segundo=0):
    """Inverso de `et`: epoch UTC de una hora de pared ET.

    En el salto de primavera hay horas ET que no existen y en el de otono hay
    horas ambiguas; se resuelve de forma estable (se prefiere el offset que
    resulta consistente al reevaluar). El mercado no opera en esas ventanas.
    """
    base = _epoch_utc(anio, mes, dia, hora, minuto, segundo)
    for candidato in (OFFSET_EDT, OFFSET_EST):
        epoch = base - candidato
        if offset_et(epoch) == candidato:
            return epoch
    return base - OFFSET_EST


def minutos_et(epoch):
    """Minutos transcurridos desde medianoche ET (float, con segundos)."""
    t = et(epoch)
    return t.hour * 60 + t.minute + t.second / 60.0


def hhmm(texto):
    """'15:50' -> 950 (minutos desde medianoche). Para leer constantes."""
    h, _, m = texto.partition(':')
    return int(h) * 60 + int(m)


def fecha_et(epoch):
    """'YYYY-MM-DD' de la sesion ET a la que pertenece el epoch."""
    return et(epoch).strftime('%Y-%m-%d')


def iso_et(epoch):
    """'YYYY-MM-DD HH:MM:SS ET' — para diario y logs legibles."""
    return et(epoch).strftime('%Y-%m-%d %H:%M:%S') + ' ET'


def iso_utc(epoch):
    """RFC3339 en UTC — formato que espera la API de Alpaca."""
    return datetime.utcfromtimestamp(epoch).strftime('%Y-%m-%dT%H:%M:%SZ')


def epoch_de_iso(texto):
    """Parsea un timestamp RFC3339 de Alpaca a epoch UTC.

    Acepta 'Z', offsets '+00:00' y fracciones de segundo de cualquier largo.
    """
    t = texto.strip()
    offset = 0
    if t.endswith('Z'):
        t = t[:-1]
    elif len(t) > 6 and t[-6] in '+-' and t[-3] == ':':
        signo = 1 if t[-6] == '+' else -1
        offset = signo * (int(t[-5:-3]) * 3600 + int(t[-2:]) * 60)
        t = t[:-6]
    if '.' in t:
        t = t.split('.', 1)[0]
    d = datetime.strptime(t, '%Y-%m-%dT%H:%M:%S')
    return _epoch_utc(d.year, d.month, d.day, d.hour, d.minute, d.second) - offset


def es_dia_de_semana(epoch):
    """True si la fecha ET cae lun-vie (no considera feriados: eso lo dice
    /v2/calendar, que consulta `datos.py`)."""
    return et(epoch).weekday() < 5
