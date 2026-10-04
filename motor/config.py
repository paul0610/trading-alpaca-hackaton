# -*- coding: utf-8 -*-
"""Configuracion y PARAMETROS CONGELADOS.

Unica fuente de parametros del motor. Los valores de la tabla §13 de
LOGICA_TRADING.md son **congelados**: prohibido cambiarlos sin acta de Paul
(ARQUITECTURA §6, Regla 5). Donde la implementacion tuvo que anadir un
parametro que la spec no nombraba, se marca con `# [DESV]` y esta explicado en
DESVIACIONES.md — nada se ajusto en silencio.
"""
import os
from pathlib import Path

from motor.reloj import hhmm

RAIZ = Path(__file__).resolve().parent.parent
DIR_DIARIO = RAIZ / 'diario'
DIR_DOCS = RAIZ / 'docs'


# --------------------------------------------------------------------------
# .env  (NUNCA se escribe, NUNCA se imprime — Regla 2)
# --------------------------------------------------------------------------

def cargar_env(ruta=None):
    """Lee el .env como dict. No toca os.environ ni imprime nada."""
    ruta = Path(ruta) if ruta else (RAIZ / '.env')
    env = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if linea and not linea.startswith('#') and '=' in linea:
                k, _, v = linea.partition('=')
                env[k.strip()] = v.strip()
    # Las variables reales de entorno ganan sobre el archivo (util para tests
    # y para lanzar en modo replay sin editar el .env).
    for k in ('ALPACA_API_KEY', 'ALPACA_SECRET_KEY', 'ALPACA_PAPER_TRADE',
              'FEATHERLESS_API_KEY', 'FEATHERLESS_MODEL', 'FEATHERLESS_BASE',
              'MODO', 'SIMBOLOS', 'FEED'):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


MODOS_VALIDOS = ('sombra', 'real')


class Config(object):
    """Config viva de una corrida. Los secretos viven aqui y no se serializan."""

    def __init__(self, env=None):
        env = env if env is not None else cargar_env()
        self.api_key = env.get('ALPACA_API_KEY', '')
        self.api_secret = env.get('ALPACA_SECRET_KEY', '')
        self.paper = env.get('ALPACA_PAPER_TRADE', 'true').lower() != 'false'
        self.featherless_key = env.get('FEATHERLESS_API_KEY', '')
        self.featherless_modelo = env.get(
            'FEATHERLESS_MODEL', 'deepseek-ai/DeepSeek-V3-0324')
        self.featherless_base = env.get(
            'FEATHERLESS_BASE', 'https://api.featherless.ai/v1')
        self.modo = env.get('MODO', 'sombra').strip().lower()
        self.simbolos = [s.strip().upper()
                         for s in env.get('SIMBOLOS', 'SPY').split(',')
                         if s.strip()]
        self.feed = env.get('FEED', 'iex').strip().lower()

    # -- invariantes de seguridad -----------------------------------------
    def validar(self):
        """Devuelve lista de problemas fatales. Vacia = configuracion sana."""
        p = []
        if not self.api_key or not self.api_secret:
            p.append('faltan ALPACA_API_KEY / ALPACA_SECRET_KEY en .env')
        if 'TU_KEY' in self.api_key or 'TU_SECRET' in self.api_secret:
            p.append('el .env todavia tiene placeholders')
        if self.modo not in MODOS_VALIDOS:
            p.append('MODO invalido: %r (usa %s)'
                     % (self.modo, '/'.join(MODOS_VALIDOS)))
        if not self.paper:
            p.append('ALPACA_PAPER_TRADE=false — este proyecto es SOLO paper')
        if not self.simbolos:
            p.append('SIMBOLOS vacio')
        return p

    @property
    def opera_de_verdad(self):
        """True solo en MODO=real. sombra y replay jamas mandan una orden."""
        return self.modo == 'real'

    def headers(self):
        return {'APCA-API-KEY-ID': self.api_key,
                'APCA-API-SECRET-KEY': self.api_secret,
                'Content-Type': 'application/json'}


# --------------------------------------------------------------------------
# §13 PARAMETROS CONGELADOS (unica fuente de verdad)
# --------------------------------------------------------------------------

URL_TRADING = 'https://paper-api.alpaca.markets'
URL_DATOS = 'https://data.alpaca.markets'
CUENTA_ESPERADA = 'PA3YMXK3ZBP1'

# -- senal (§4) --
CANAL_VELAS_5M = 6
BUFFER_CRUCE = 0.05
# Tolerancia para comparar precios. Los precios son centavos exactos en la
# intencion, pero no en binario: `500.05 - 500.00` puede dar 0.049999999999972
# y hacer que S2 falle en su propio knife-edge. Comparar con EPS hace que el
# borde se comporte como dice la spec ("cierre limpio >= $0.05" incluye 0.05).
EPS = 1e-9
RSI5M_LONG = (55.0, 75.0)
RSI15M_LONG_MIN = 50.0
RSI30M_LONG_MIN = 45.0
RSI5M_SHORT = (25.0, 45.0)
RSI15M_SHORT_MAX = 50.0
RSI30M_SHORT_MAX = 55.0
VELA_TOPE_X_ATR = 2.2
ANTI_SERRUCHO_VELAS = 3
EMA_RAPIDA = 20            # [DESV] §13 no la tabula; §3 la fija en EMA20
EMA_LENTA = 50             # [DESV] §13 no la tabula; §3 la fija en EMA50
RSI_PERIODO = 14           # [DESV] §13 no lo tabula; §3 lo fija en RSI14
ATR_PERIODO = 20           # [DESV] §13 no lo tabula; §3 lo fija en ATR20

# -- sesion (§2) --
# [ACTA PAUL 2026-09-02] 10 -> 5. La validacion de 5 sesiones midio 18 GO
# pero solo 8 accionables: la rejilla de 10 min tiraba el 55% de las senales
# validas porque nacen al cerrar una vela 5m y solo la mitad de esos cierres
# caen en un borde de 10 min. No relaja ninguna condicion S ni ningun gate:
# solo cambia cada cuanto el motor se asoma. El sobre de riesgo lo siguen
# fijando G3 (8 trades/dia), G4 (3 posiciones) y G5 ($1500 de prima viva).
CICLO_MIN = 5
MONITOR_S = 60
ENTRADA_INI = hhmm('9:45')
ENTRADA_FIN = hhmm('15:00')
EOD_ET = hhmm('15:50')
# [DESV] La spec exige estar *flat* a las 15:50, pero §8 define un cierre
# escalonado de 60 s + 60 s. Disparar a las 15:50 llega tarde por construccion.
# Se arranca el EOD a las 15:45 para poder estar plano a las 15:50.
EOD_INICIO_ET = hhmm('15:45')
APERTURA_ET = hhmm('9:30')
CIERRE_ET = hhmm('16:00')

# -- gates (§5) --
MAX_TRADES_DIA = 8
MAX_POSICIONES = 3
PRIMA_MAX_VIVA = 1500.0
PRIMA_MAX_TRADE_ABS = 500.0
PRIMA_MAX_TRADE_PCT_EQUITY = 0.005
COOLDOWN_ROJO_MIN = 30
NOTICIAS_VETO_MIN = 15
FRESCURA_DATOS_MIN = 3     # [DESV] §13 no lo tabula; §5 G8 lo fija en 3 min
# Fallback cuando no hay noticias.csv valido (§5 G7). Se anade 9:55-10:15 ET:
# la franja de 10:00 (ISM, JOLTS, Confianza) es la segunda mas cargada del dia
# y la spec la dejaba descubierta. Ver DESVIACIONES.md.
VENTANAS_VETO_FALLBACK = (
    (hhmm('8:25'), hhmm('8:50')),
    (hhmm('9:55'), hhmm('10:15')),   # [DESV] anadida
    (hhmm('13:55'), hhmm('14:35')),
)

# -- contrato (§7) --
DTE_MIN = 1
DTE_MAX = 2
ANCHO_SPREAD = 2.0
SPREAD_REL_MAX = 0.12
OI_MIN = 100
VOLUMEN_MIN_ALTERNO = 50   # [DESV] §13 no lo tabula; §7.4 lo fija en 50
DEBITO_ESPERADO = (0.70, 1.30)

# -- ordenes (§8) --
OFFSET_APERTURA_1 = 0.02
OFFSET_APERTURA_2 = 0.04
OFFSET_CIERRE_1 = 0.03
OFFSET_CIERRE_2 = 0.06
ESPERA_APERTURA_S = 90
ESPERA_CIERRE_S = 60

# -- salidas (§10) --
ESCALERA = ((40.0, 15.0), (65.0, 40.0), (100.0, 65.0),
            (161.0, 100.0), (261.0, 161.0))
FRENO_ARMADO_PCT = 25.0
FRENO_BANDA_PTS = 10.0
FRENO_LECTURAS = 3
FRENO_DISPERSION_PTS = 4.0
FRENO_RSI1M_LONG = 68.0
FRENO_RSI1M_SHORT = 32.0
SL_PCT = -50.0
TIMESTOP_H = 3.5
TIMESTOP_BANDA = (-15.0, 15.0)
WATCHDOG_DIA_PCT = -1.5
WATCHDOG_TOTAL_PCT = -4.0

# -- LLM (§6) --
LLM_TIMEOUT_S = 20
LLM_REINTENTOS = 1
LLM_RAZON_MAX = 400

# -- REST (Regla 6) --
REST_TIMEOUT_S = 15
REST_REINTENTOS = 1
REST_BACKOFF_S = 2.0
