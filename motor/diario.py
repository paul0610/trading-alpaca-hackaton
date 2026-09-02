# -*- coding: utf-8 -*-
"""Diario: la evidencia ES el producto (LOGICA §11).

- `decisiones.jsonl` — un registro por ciclo, incluidos los PASS.
- `eventos.jsonl`    — fills, cierres con razon, slippage, errores.
- `estado.json`      — posiciones, picos, cooldowns, contadores. Escritura
                       atomica (tmp + os.replace + fsync): sobrevive kill -9.
- `intenciones.jsonl`— intencion durable ANTES de cada POST de orden. Es lo
                       que permite reconciliar tras un crash sin duplicar.

Los JSONL se abren, escriben y cierran en cada append con fsync. Es mas lento
que mantener el handle abierto, pero un ciclo son 10 minutos: la durabilidad
vale infinitamente mas que los microsegundos.
"""
import json
import os
import threading
import time
from pathlib import Path

from motor import reloj


def _serializable(obj):
    """Ultimo recurso para que un objeto raro nunca tumbe el journal."""
    try:
        return str(obj)
    except Exception:
        return '<no serializable>'


class Diario(object):
    def __init__(self, carpeta):
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.f_decisiones = self.carpeta / 'decisiones.jsonl'
        self.f_eventos = self.carpeta / 'eventos.jsonl'
        self.f_intenciones = self.carpeta / 'intenciones.jsonl'
        self.f_estado = self.carpeta / 'estado.json'
        self._lock = threading.Lock()

    # -- append JSONL ------------------------------------------------------
    def _append(self, ruta, obj):
        linea = json.dumps(obj, ensure_ascii=False, default=_serializable)
        with self._lock:
            with open(ruta, 'a', encoding='utf-8') as f:
                f.write(linea + '\n')
                f.flush()
                os.fsync(f.fileno())
        return obj

    def _sellar(self, obj, tipo):
        ahora = time.time()
        sello = {'ts_utc': round(ahora, 3),
                 'ts_et': reloj.iso_et(ahora),
                 'sesion': reloj.fecha_et(ahora),
                 'tipo': tipo}
        sello.update(obj)
        return sello

    def decision(self, obj):
        """Un ciclo de decision completo (GO/NO-GO, gates, LLM, accion)."""
        return self._append(self.f_decisiones, self._sellar(obj, 'decision'))

    def evento(self, tipo, **campos):
        """Fill, cierre, error, arranque, HALT... todo lo que no es un ciclo."""
        return self._append(self.f_eventos, self._sellar(campos, tipo))

    def intencion(self, obj):
        """Se escribe ANTES de mandar una orden. Sin esto no hay idempotencia:
        un crash entre el POST y el fsync dejaria una orden huerfana."""
        return self._append(self.f_intenciones, self._sellar(obj, 'intencion'))

    def intenciones_abiertas(self):
        """Intenciones registradas sin resolucion posterior conocida.

        Devuelve la ultima intencion por `client_order_id`; el reconciliador
        pregunta al broker por cada una antes de dejar operar al agente.
        """
        porid = {}
        for reg in self.leer(self.f_intenciones):
            coid = reg.get('client_order_id')
            if coid:
                porid[coid] = reg
        return porid

    # -- lectura -----------------------------------------------------------
    def leer(self, ruta):
        """Lee un JSONL tolerando la ultima linea truncada por un kill -9."""
        ruta = Path(ruta)
        if not ruta.exists():
            return []
        out = []
        for linea in ruta.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                out.append(json.loads(linea))
            except ValueError:
                continue        # linea a medio escribir: se ignora, no se muere
        return out

    def decisiones(self):
        return self.leer(self.f_decisiones)

    def eventos(self):
        return self.leer(self.f_eventos)

    # -- estado atomico ----------------------------------------------------
    def guardar_estado(self, estado):
        """tmp + fsync + os.replace: o esta el estado viejo o el nuevo, jamas
        un archivo a medias (leccion 11-ago: los picos sobreviven reinicios)."""
        tmp = self.f_estado.with_suffix('.json.tmp')
        with self._lock:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(estado, f, ensure_ascii=False, indent=1,
                          default=_serializable)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.f_estado)

    def cargar_estado(self):
        if not self.f_estado.exists():
            return {}
        try:
            return json.loads(self.f_estado.read_text(encoding='utf-8'))
        except ValueError:
            return {}
