# -*- coding: utf-8 -*-
"""Smoke test de credenciales Alpaca (paper). Solo stdlib, sin dependencias.

Lee .env de esta carpeta, llama GET /v2/account y /v2/clock del entorno
paper y muestra estado de la cuenta. NUNCA imprime las keys.
"""
import io
import json
import sys
import urllib.request
from pathlib import Path

if (getattr(sys.stdout, 'encoding', '') or '').lower().replace('-', '') != 'utf8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = 'https://paper-api.alpaca.markets'
CUENTA_ESPERADA = 'PA3YMXK3ZBP1'


def cargar_env():
    env = {}
    ruta = Path(__file__).with_name('.env')
    if not ruta.exists():
        sys.exit('FALTA .env — copia .env.example como .env y pon tus keys')
    for linea in ruta.read_text(encoding='utf-8').splitlines():
        linea = linea.strip()
        if linea and not linea.startswith('#') and '=' in linea:
            k, _, v = linea.partition('=')
            env[k.strip()] = v.strip()
    return env


def get(path, headers):
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    env = cargar_env()
    key = env.get('ALPACA_API_KEY', '')
    sec = env.get('ALPACA_SECRET_KEY', '')
    if 'TU_KEY' in key or not key or not sec:
        sys.exit('El .env todavia tiene placeholders — pon tus keys reales')
    h = {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': sec}
    try:
        acct = get('/v2/account', h)
    except urllib.error.HTTPError as e:
        sys.exit(f'AUTH FALLO ({e.code}): keys invalidas o de otra cuenta/entorno')
    clock = get('/v2/clock', h)
    print('=== CUENTA PAPER VERIFICADA ===')
    print(f"  account_number : {acct.get('account_number')}")
    print(f"  status         : {acct.get('status')}")
    print(f"  equity         : ${float(acct.get('equity', 0)):,.2f}")
    print(f"  buying_power   : ${float(acct.get('buying_power', 0)):,.2f}")
    print(f"  options level  : {acct.get('options_approved_level')} "
          f"(trading level {acct.get('options_trading_level')})")
    print(f"  mercado abierto: {clock.get('is_open')} | "
          f"proxima apertura {clock.get('next_open')} | "
          f"proximo cierre {clock.get('next_close')}")
    if acct.get('account_number') != CUENTA_ESPERADA:
        sys.exit('\nCUENTA INCORRECTA: se esperaba %s' % CUENTA_ESPERADA)


if __name__ == '__main__':
    main()
