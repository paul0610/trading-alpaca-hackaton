# -*- coding: utf-8 -*-
"""SALIDAS — el Cierre de Paul embotellado (LOGICA §10). Sin IA, jamas.

Precedencia del monitor: R6/R7 -> R5 -> R3 -> R1 -> R2 -> R4.
Todo se mide en % de la prima pagada: `mark_pct = (mark - debito)/debito*100`.

AVISO SOBRE LA ESCALERA R1 (verificado, no ajustado)
----------------------------------------------------
Un vertical de ancho $2 vale como maximo $2.00, asi que el techo de `mark_pct`
es `(ancho - debito)/debito * 100`. Con el debito esperado de §7.3 ($0.70-1.30)
eso da:

    debito $0.70 -> techo +185.7%  (peldanos vivos: +40 +65 +100 +161)
    debito $1.00 -> techo +100.0%  (peldanos vivos: +40 +65 +100, al borde)
    debito $1.30 -> techo  +53.8%  (peldano vivo:   +40)

El peldano **+261% no es alcanzable jamas** dentro del rango de la propia spec,
y con debito alto la escalera se reduce a un solo peldano. §13 esta congelada
(Regla 5: reportar, no ajustar en silencio), asi que la tabla se implementa
LITERAL. Lo que se anade es evidencia: `peldanos_alcanzables()` se journalea en
cada apertura, de modo que el diario muestre exactamente cuanta escalera estaba
viva en ese trade. La correccion propuesta (expresar los peldanos como % del
beneficio maximo `ancho - debito` en vez de % de la prima) esta en
DESVIACIONES.md y necesita acta de Paul.
"""
from motor import config

# Acciones que el monitor puede devolver
MANTENER = 'MANTENER'
CERRAR = 'CERRAR'


class Decision(object):
    __slots__ = ('accion', 'motivo', 'detalle')

    def __init__(self, accion, motivo='', detalle=None):
        self.accion = accion
        self.motivo = motivo
        self.detalle = detalle or {}

    @property
    def cierra(self):
        return self.accion == CERRAR

    def dict(self):
        return {'accion': self.accion, 'motivo': self.motivo,
                'detalle': self.detalle}


_MANTENER = Decision(MANTENER)


def peldanos_alcanzables(debito, ancho=config.ANCHO_SPREAD):
    """Que peldanos de R1 puede siquiera tocar este trade. Evidencia, no logica."""
    if debito <= 0:
        return {'techo_pct': None, 'vivos': [], 'muertos':
                [u for u, _ in config.ESCALERA]}
    techo = (ancho - debito) / debito * 100.0
    vivos = [u for u, _ in config.ESCALERA if u <= techo]
    muertos = [u for u, _ in config.ESCALERA if u > techo]
    return {'techo_pct': round(techo, 1), 'vivos': vivos, 'muertos': muertos}


# ---------------------------------------------------------------------------
# Reglas individuales (puras y testeables una por una)
# ---------------------------------------------------------------------------

def r1_escalera(pos):
    """Candados fib: deja correr cohetes, pero nunca devuelve un pico."""
    candado = None
    umbral = None
    for u, c in config.ESCALERA:
        if pos.peak_pct >= u:
            candado, umbral = c, u
    if candado is None:
        return _MANTENER
    if pos.mark_pct <= candado:
        return Decision(CERRAR, 'ESCALERA',
                        {'peldano': umbral, 'candado': candado,
                         'peak_pct': round(pos.peak_pct, 2),
                         'mark_pct': round(pos.mark_pct, 2)})
    return _MANTENER


def r2_freno(pos, rsi1m):
    """Freno del flotante: 'cuando hay frenos, van a liquidar'.

    Armado con mark_pct >= +25. Dispara si (a) estamos pegados al pico,
    (b) la velocidad es ~0 (3 lecturas dentro de 4 puntos) y (c) el RSI1m
    confirma extension.

    Sin RSI1m NO dispara: la spec exige la revalidacion (c) y cosechar sin
    ella seria inventarse una condicion que no se comprobo.
    """
    if pos.mark_pct < config.FRENO_ARMADO_PCT:
        return _MANTENER
    if len(pos.lecturas) < config.FRENO_LECTURAS:
        return _MANTENER
    cerca_del_pico = (pos.peak_pct - pos.mark_pct) <= config.FRENO_BANDA_PTS
    ultimas = pos.lecturas[-config.FRENO_LECTURAS:]
    quieto = (max(ultimas) - min(ultimas)) <= config.FRENO_DISPERSION_PTS
    if not (cerca_del_pico and quieto):
        return _MANTENER
    if rsi1m is None:
        return _MANTENER
    extendido = (rsi1m >= config.FRENO_RSI1M_LONG if pos.direccion == 'LONG'
                 else rsi1m <= config.FRENO_RSI1M_SHORT)
    if not extendido:
        return _MANTENER
    return Decision(CERRAR, 'FRENO',
                    {'mark_pct': round(pos.mark_pct, 2),
                     'peak_pct': round(pos.peak_pct, 2),
                     'lecturas': ultimas, 'rsi1m': round(rsi1m, 2)})


def r3_stop(pos):
    if pos.mark_pct <= config.SL_PCT:
        return Decision(CERRAR, 'SL', {'mark_pct': round(pos.mark_pct, 2),
                                       'umbral': config.SL_PCT})
    return _MANTENER


def r4_timestop(pos, ahora):
    """Impulso muerto: el estancamiento oscilante paga theta sin tesis."""
    edad = pos.edad_h(ahora)
    lo, hi = config.TIMESTOP_BANDA
    if edad >= config.TIMESTOP_H and lo < pos.mark_pct < hi:
        return Decision(CERRAR, 'IMPULSO_MUERTO',
                        {'edad_h': round(edad, 2),
                         'mark_pct': round(pos.mark_pct, 2)})
    return _MANTENER


def r5_eod(minutos_et):
    """Flat a las 15:50 SIN excepciones.

    [DESV] Se dispara a las 15:45, no a las 15:50. El cierre de §8 es una
    escalera de 60 s + 60 s + limit al bid: arrancarla *a* las 15:50 hace
    imposible estar plano *a* las 15:50. Ver DESVIACIONES.md.
    """
    if minutos_et >= config.EOD_INICIO_ET:
        return Decision(CERRAR, 'EOD', {'minutos_et': round(minutos_et, 1),
                                        'flat_objetivo': config.EOD_ET})
    return _MANTENER


def r6_watchdog_dia(pnl_dia_pct):
    if pnl_dia_pct <= config.WATCHDOG_DIA_PCT:
        return Decision(CERRAR, 'WATCHDOG_DIA',
                        {'pnl_dia_pct': round(pnl_dia_pct, 3),
                         'umbral': config.WATCHDOG_DIA_PCT})
    return _MANTENER


def r7_circuit_breaker(pnl_total_pct):
    if pnl_total_pct <= config.WATCHDOG_TOTAL_PCT:
        return Decision(CERRAR, 'WATCHDOG_SEMANA',
                        {'pnl_total_pct': round(pnl_total_pct, 3),
                         'umbral': config.WATCHDOG_TOTAL_PCT})
    return _MANTENER


# ---------------------------------------------------------------------------
# Evaluacion compuesta
# ---------------------------------------------------------------------------

def evaluar_flota(pnl_dia_pct, pnl_total_pct):
    """R7 y R6: cierran TODA la flota y activan HALT. Se evaluan primero."""
    d = r7_circuit_breaker(pnl_total_pct)
    if d.cierra:
        return d
    return r6_watchdog_dia(pnl_dia_pct)


def evaluar_posicion(pos, minutos_et, ahora, rsi1m=None):
    """Precedencia R5 -> R3 -> R1 -> R2 -> R4 (R6/R7 ya se evaluaron)."""
    for d in (r5_eod(minutos_et), r3_stop(pos), r1_escalera(pos),
              r2_freno(pos, rsi1m), r4_timestop(pos, ahora)):
        if d.cierra:
            return d
    return _MANTENER
