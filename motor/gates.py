# -*- coding: utf-8 -*-
"""GATES del oficial de riesgo (LOGICA §5). Deterministas, sin IA.

REORDENAMIENTO RESPECTO DE ARQUITECTURA §3
------------------------------------------
La spec listaba "evaluar G1..G10 -> consultar LLM -> elegir contrato", pero
G5 (exposicion) necesita la prima nueva, G10 (liquidez) es *del contrato*, y
el input del LLM (§6) incluye "IV y quote del contrato candidato". Los tres
dependen de un contrato que todavia no existe en ese punto: el orden era
circular. Se parte la evaluacion en dos etapas, sin cambiar ningun gate:

  1. `evaluar_previos`  -> G1 G2 G3 G4 G6 G7 G8   (no necesitan contrato)
  2. elegir contrato + calcular tamano
  3. `evaluar_contrato` -> G5 G10                 (ya con prima y quotes)
  4. LLM                -> G9 es el *resultado* de esa etapa, no un gate previo
  5. revalidacion y orden

Ver DESVIACIONES.md. Todos los gates son funciones puras `(ctx) -> Veredicto`:
un test unitario por gate, incluidos los bordes (knife-edges).
"""
import csv
import math
from pathlib import Path

from motor import config, reloj

# Estado de un gate en el diario. Un gate que no se llego a evaluar NO es un
# gate que paso: el schema de §11 exige poder distinguirlos.
PASA = 'PASS'
FALLA = 'FAIL'
NO_EVALUADO = 'NOT_EVALUATED'


class Veredicto(object):
    __slots__ = ('gate', 'estado', 'razon', 'detalle')

    def __init__(self, gate, estado, razon='', detalle=None):
        self.gate = gate
        self.estado = estado
        self.razon = razon
        self.detalle = detalle or {}

    @property
    def ok(self):
        return self.estado == PASA

    def dict(self):
        return {'gate': self.gate, 'estado': self.estado,
                'razon': self.razon, 'detalle': self.detalle}

    def __repr__(self):
        return 'Veredicto(%s %s %s)' % (self.gate, self.estado, self.razon)


def _v(gate, ok, razon_fallo, **detalle):
    return Veredicto(gate, PASA if ok else FALLA, '' if ok else razon_fallo,
                     detalle)


# ---------------------------------------------------------------------------
# G7 — noticias
# ---------------------------------------------------------------------------

def cargar_noticias(ruta):
    """Lee `noticias.csv` -> [(fecha_et, minutos_et, evento)].

    Esquema: `fecha,hora_et,impacto,evento` (cabecera obligatoria).
    Solo se consideran las filas con impacto `high`.
    Devuelve (eventos, problema). Si hay problema, el que llama sigue usando
    las ventanas fallback: **nunca se relaja el veto por un archivo roto**.
    """
    ruta = Path(ruta)
    if not ruta.exists():
        return [], 'SIN_ARCHIVO'
    try:
        filas = list(csv.DictReader(
            ruta.read_text(encoding='utf-8').splitlines()))
    except Exception as e:
        return [], 'ILEGIBLE:%s' % (type(e).__name__,)
    requeridas = {'fecha', 'hora_et', 'impacto', 'evento'}
    if not filas or not requeridas.issubset(set(filas[0].keys() or [])):
        return [], 'ESQUEMA_INVALIDO'
    fuera = []
    for f in filas:
        if (f.get('impacto') or '').strip().lower() != 'high':
            continue
        try:
            fuera.append((f['fecha'].strip(), reloj.hhmm(f['hora_et'].strip()),
                          (f.get('evento') or '').strip()))
        except (ValueError, AttributeError):
            return [], 'FILA_INVALIDA'
    return fuera, ''


def en_veto_noticias(fecha_et, minutos, eventos, problema):
    """(bloqueado, razon). Fail-closed: el fallback SIEMPRE aplica.

    Las ventanas fallback no se desactivan cuando hay `noticias.csv` valido;
    el archivo solo puede anadir vetos, nunca quitarlos. Asi un CSV incompleto
    (el fallo mas probable, porque lo llena un humano con prisa) degrada a la
    proteccion generica en vez de abrir un hueco silencioso.
    """
    for ini, fin in config.VENTANAS_VETO_FALLBACK:
        if ini <= minutos <= fin:
            return True, 'VENTANA_MACRO_%02d:%02d' % (ini // 60, ini % 60)
    if problema:
        return False, ''
    for fecha, m, nombre in eventos:
        if fecha == fecha_et and abs(minutos - m) <= config.NOTICIAS_VETO_MIN:
            return True, 'NOTICIA:%s' % (nombre[:40] or 'high-impact')
    return False, ''


# ---------------------------------------------------------------------------
# Etapa 1 — gates que NO necesitan contrato
# ---------------------------------------------------------------------------

def g1_halt(ctx):
    return _v('G1', not ctx.halt, 'HALT_ACTIVO:%s' % (ctx.halt or '',),
              halt=ctx.halt)


def g2_horario(ctx):
    dentro = config.ENTRADA_INI <= ctx.minutos_et <= config.ENTRADA_FIN
    return _v('G2', dentro, 'FUERA_DE_VENTANA',
              minutos_et=round(ctx.minutos_et, 2),
              ventana=[config.ENTRADA_INI, config.ENTRADA_FIN])


def g3_presupuesto_dia(ctx):
    return _v('G3', ctx.trades_hoy < config.MAX_TRADES_DIA,
              'MAX_TRADES_DIA', trades_hoy=ctx.trades_hoy,
              tope=config.MAX_TRADES_DIA)


def g4_flota(ctx):
    return _v('G4', ctx.posiciones_vivas < config.MAX_POSICIONES,
              'FLOTA_LLENA', vivas=ctx.posiciones_vivas,
              tope=config.MAX_POSICIONES)


def g6_cooldown(ctx):
    if ctx.ultimo_cierre_rojo is None:
        return _v('G6', True, '')
    minutos = (ctx.ahora - ctx.ultimo_cierre_rojo) / 60.0
    return _v('G6', minutos >= config.COOLDOWN_ROJO_MIN, 'COOLDOWN_REVANCHA',
              minutos_desde_rojo=round(minutos, 1),
              requiere=config.COOLDOWN_ROJO_MIN)


def g7_noticias(ctx):
    bloqueado, razon = en_veto_noticias(ctx.fecha_et, ctx.minutos_et,
                                        ctx.noticias, ctx.noticias_problema)
    return _v('G7', not bloqueado, razon or 'VETO_NOTICIAS',
              fuente=ctx.noticias_problema or 'noticias.csv')


def g8_datos_frescos(ctx):
    return _v('G8', ctx.frescura_min < config.FRESCURA_DATOS_MIN,
              'DATOS_RANCIOS',
              frescura_min=(round(ctx.frescura_min, 2)
                            if ctx.frescura_min != float('inf') else None),
              tope=config.FRESCURA_DATOS_MIN)


PREVIOS = (g1_halt, g2_horario, g3_presupuesto_dia, g4_flota, g6_cooldown,
           g7_noticias, g8_datos_frescos)


def evaluar_previos(ctx):
    """Evalua los 7 gates que no dependen del contrato.

    Los evalua TODOS aunque uno falle: el diario debe mostrar el cuadro
    completo, no solo el primer 'no'. Devuelve (todos_ok, [Veredicto]).
    """
    veredictos = [g(ctx) for g in PREVIOS]
    return all(v.ok for v in veredictos), veredictos


# ---------------------------------------------------------------------------
# Etapa 2 — gates que SI necesitan contrato y tamano
# ---------------------------------------------------------------------------

def g5_exposicion(prima_nueva, prima_viva, prima_pendiente):
    total = prima_viva + prima_pendiente + prima_nueva
    return _v('G5', total <= config.PRIMA_MAX_VIVA + 1e-9,
              'EXPOSICION_AGREGADA',
              prima_viva=round(prima_viva, 2),
              prima_pendiente=round(prima_pendiente, 2),
              prima_nueva=round(prima_nueva, 2),
              total=round(total, 2), tope=config.PRIMA_MAX_VIVA)


def g10_liquidez(spread):
    """Liquidez del contrato (LOGICA §7.4). `spread` es un contratos.Spread."""
    ok, razon, detalle = spread.liquidez()
    return _v('G10', ok, razon or 'SIN_LIQUIDEZ', **detalle)


def gate_no_evaluado(nombre, porque='no se llego a esta etapa'):
    return Veredicto(nombre, NO_EVALUADO, porque)


ORDEN_GATES = ('G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8', 'G9', 'G10')


def cuadro(veredictos):
    """dict G1..G10 completo para el diario. Lo no evaluado se marca como tal
    (LOGICA §11 exige G1-G10 cada ciclo; un NO-GO termina antes de los gates,
    asi que sin NOT_EVALUATED el schema seria imposible de cumplir)."""
    porgate = {v.gate: v.dict() for v in veredictos}
    return {g: porgate.get(g, gate_no_evaluado(g).dict())
            for g in ORDEN_GATES}


# ---------------------------------------------------------------------------
# Tamano (LOGICA §9)
# ---------------------------------------------------------------------------

def tamano(equity, debito_mid, prima_viva=0.0, prima_pendiente=0.0):
    """Contratos a operar. Devuelve (qty, prima_peor_caso, detalle).

    Correcciones respecto de la formula literal de §9
    `contratos = max(1, floor(500 / (debit*100)))`:

    a) El `500` estaba hardcodeado donde la propia linea dice que el techo es
       `min(0.5% equity, $500)`. Con equity < $100k el literal excede el techo.
    b) `max(1, ...)` fuerza abrir 1 contrato aunque el presupuesto no alcance:
       convierte un techo de riesgo en un piso. Aqui `qty == 0` significa PASS.
    c) Se dimensiona contra el **peor precio al que la orden puede llenar**
       (`mid + 0.04`, el retry de §8), no contra el mid. Dimensionar al mid y
       llenar al retry rompe el techo: 5 x $0.04 x 100 = $520 sobre un tope de
       $500.
    d) El techo agregado de G5 descuenta tambien la prima **pendiente** (ordenes
       mandadas y no llenadas), que si no reserva exposicion se puede duplicar.
    """
    peor = debito_mid + config.OFFSET_APERTURA_2
    techo_trade = min(config.PRIMA_MAX_TRADE_PCT_EQUITY * equity,
                      config.PRIMA_MAX_TRADE_ABS)
    techo_agregado = config.PRIMA_MAX_VIVA - prima_viva - prima_pendiente
    presupuesto = min(techo_trade, techo_agregado)
    detalle = {'equity': round(equity, 2),
               'debito_mid': round(debito_mid, 4),
               'peor_precio': round(peor, 4),
               'techo_trade': round(techo_trade, 2),
               'techo_agregado': round(techo_agregado, 2),
               'presupuesto': round(presupuesto, 2)}
    if peor <= 0 or presupuesto <= 0:
        detalle['razon'] = 'SIN_PRESUPUESTO'
        return 0, 0.0, detalle
    qty = int(math.floor(presupuesto / (peor * 100.0)))
    if qty < 1:
        detalle['razon'] = 'PRESUPUESTO_INSUFICIENTE_PARA_1_CONTRATO'
        return 0, 0.0, detalle
    detalle['qty'] = qty
    return qty, peor * 100.0 * qty, detalle


# ---------------------------------------------------------------------------
# Contexto
# ---------------------------------------------------------------------------

class Contexto(object):
    """Todo lo que los gates previos necesitan saber. Plano y serializable."""

    def __init__(self, ahora, halt=None, trades_hoy=0, posiciones_vivas=0,
                 ultimo_cierre_rojo=None, frescura_min=0.0, noticias=(),
                 noticias_problema='', equity=0.0, prima_viva=0.0,
                 prima_pendiente=0.0):
        self.ahora = ahora
        self.halt = halt
        self.trades_hoy = trades_hoy
        self.posiciones_vivas = posiciones_vivas
        self.ultimo_cierre_rojo = ultimo_cierre_rojo
        self.frescura_min = frescura_min
        self.noticias = list(noticias)
        self.noticias_problema = noticias_problema
        self.equity = equity
        self.prima_viva = prima_viva
        self.prima_pendiente = prima_pendiente
        self.minutos_et = reloj.minutos_et(ahora)
        self.fecha_et = reloj.fecha_et(ahora)
