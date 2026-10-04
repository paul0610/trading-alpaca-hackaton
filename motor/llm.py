# -*- coding: utf-8 -*-
"""Capa de decision IA — Featherless, API OpenAI-compatible (LOGICA §6).

AUTORIDAD (esto es lo que hay que poder demostrarle a un juez):
  - Solo se le consulta cuando la senal YA es GO y los gates YA pasaron.
  - Solo puede convertir un GO en OPEN o PASS. No abre sin senal, no toca
    salidas, no modifica tamanos, no elige strikes.
  - Cualquier fallo (timeout, JSON malformado, red) => PASS. Nunca OPEN.

El campo `razon` va integro al diario: es el alma del write-up y del video.
"""
import json
import urllib.error
import urllib.request

from motor import config

SISTEMA = (
    "You are the decision layer of a disciplined options trading desk. A "
    "deterministic signal engine has ALREADY produced a valid entry signal and "
    "a deterministic risk officer has ALREADY cleared every hard gate. Your "
    "only job is to decide whether to take this specific trade or skip it, and "
    "to explain why in one short paragraph.\n"
    "You cannot change the direction, the strikes, the size, or any exit rule. "
    "You may only answer OPEN or PASS.\n"
    "Answer with a single JSON object and nothing else:\n"
    '{"decision":"OPEN"|"PASS","confianza":<0-100 integer>,'
    '"razon":"<=400 chars, English"}\n'
    "Prefer PASS when the facts are contradictory, when the quoted spread "
    "looks poor relative to the expected move, or when recent journal entries "
    "suggest the same setup just failed. A skipped trade costs nothing; a bad "
    "one costs the whole premium."
)


class Decision(object):
    __slots__ = ('accion', 'confianza', 'razon', 'error', 'modelo', 'ms')

    def __init__(self, accion, confianza=0, razon='', error='', modelo='',
                 ms=0):
        self.accion = accion
        self.confianza = confianza
        self.razon = razon
        self.error = error
        self.modelo = modelo
        self.ms = ms

    @property
    def abre(self):
        return self.accion == 'OPEN'

    def dict(self):
        return {'decision': self.accion, 'confianza': self.confianza,
                'razon': self.razon, 'error': self.error,
                'modelo': self.modelo, 'latencia_ms': self.ms}


USER_AGENT = 'RemoraDesk/1.0 (+https://github.com/paul0610/trading-alpaca-hackaton)'


def _pass(razon, error):
    return Decision('PASS', 0, razon, error)


def construir_prompt(ctx):
    """`ctx` es el snapshot de hechos (§6). Se envia como JSON, sin adornos."""
    return json.dumps(ctx, ensure_ascii=False, indent=1, default=str)


def _extraer_json(texto):
    """Saca el objeto JSON aunque el modelo lo envuelva en ```json ... ```."""
    t = (texto or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[-1]
        if '```' in t:
            t = t.rsplit('```', 1)[0]
    ini, fin = t.find('{'), t.rfind('}')
    if ini < 0 or fin <= ini:
        raise ValueError('sin objeto JSON en la respuesta')
    return json.loads(t[ini:fin + 1])


def _validar(obj, modelo, ms):
    if not isinstance(obj, dict):
        raise ValueError('la respuesta no es un objeto')
    faltan = [k for k in ('decision', 'confianza', 'razon') if k not in obj]
    if faltan:
        raise ValueError('faltan campos: %s' % ','.join(faltan))
    accion = str(obj.get('decision', '')).strip().upper()
    if accion not in ('OPEN', 'PASS'):
        raise ValueError('decision invalida: %r' % (accion,))
    try:
        cruda = obj['confianza']
        if isinstance(cruda, bool):
            raise ValueError('bool no es confianza')
        numerica = float(cruda)
        conf = int(numerica)
        if numerica != conf or not 0 <= conf <= 100:
            raise ValueError('fuera de rango o no entera')
    except (TypeError, ValueError):
        raise ValueError('confianza invalida: %r' % (obj.get('confianza'),))
    if not isinstance(obj['razon'], str):
        raise ValueError('razon no es texto')
    razon = obj['razon'].strip()
    if not razon:
        raise ValueError('razon vacia')
    if len(razon) > config.LLM_RAZON_MAX:
        raise ValueError('razon excede %d caracteres' % config.LLM_RAZON_MAX)
    return Decision(accion, conf, razon, '', modelo, ms)


def decidir(cfg, ctx, reloj_ms=None):
    """Consulta al modelo. NUNCA lanza: el peor caso es PASS con razon.

    `reloj_ms` es inyectable para tests deterministas (el motor no puede usar
    time.time() dentro de una funcion que quiere ser reproducible en replay).
    """
    if not cfg.featherless_key:
        return _pass('No LLM key configured; risk officer defaults to PASS.',
                     'LLM_SIN_KEY')
    cuerpo = {
        'model': cfg.featherless_modelo,
        'messages': [{'role': 'system', 'content': SISTEMA},
                     {'role': 'user', 'content': construir_prompt(ctx)}],
        'temperature': 0.2,
        'max_tokens': 300,
        'response_format': {'type': 'json_object'},
    }
    datos = json.dumps(cuerpo).encode('utf-8')
    # User-Agent explicito: Cloudflare, delante de Featherless, rechaza el UA
    # por defecto de urllib ("Python-urllib/x.y") con error 1010 antes de que
    # la peticion llegue a la API. Cualquier UA propio pasa. Sin esto la capa
    # IA devuelve 403 y el oficial de riesgo degrada a PASS en cada GO, que se
    # ve identico a "el modelo decidio no operar". Verificado 2026-09-02.
    cabeceras = {'Authorization': 'Bearer %s' % cfg.featherless_key,
                 'Content-Type': 'application/json',
                 'User-Agent': USER_AGENT}
    url = cfg.featherless_base.rstrip('/') + '/chat/completions'

    ultimo = ''
    for intento in range(config.LLM_REINTENTOS + 1):
        t0 = reloj_ms() if reloj_ms else 0
        try:
            req = urllib.request.Request(url, data=datos, headers=cabeceras,
                                         method='POST')
            with urllib.request.urlopen(
                    req, timeout=config.LLM_TIMEOUT_S) as r:
                crudo = json.loads(r.read().decode('utf-8'))
            ms = int((reloj_ms() - t0)) if reloj_ms else 0
            texto = crudo['choices'][0]['message']['content']
            return _validar(_extraer_json(texto), cfg.featherless_modelo, ms)
        except urllib.error.HTTPError as e:
            ultimo = 'LLM_HTTP_%s' % e.code
        except urllib.error.URLError as e:
            ultimo = 'LLM_RED:%s' % (e.reason,)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            ultimo = 'LLM_MALFORMED:%s' % (e,)
        except Exception as e:
            ultimo = 'LLM_DOWN:%s' % (type(e).__name__,)
        if intento < config.LLM_REINTENTOS:
            continue
    return _pass('LLM unavailable or malformed (%s); the desk defaults to PASS '
                 'rather than trade blind.' % ultimo[:80], ultimo)
