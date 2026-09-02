# -*- coding: utf-8 -*-
"""DoD del incremento I1: imprime las senales de los ultimos dias habiles de
SPY con velas REALES, para revision ocular.

Ademas responde la pregunta que decide si el proyecto tiene demo:
**¿esta senal dispara alguna vez?** S1..S6 encadenadas son muy restrictivas;
un agente que nunca opera es un demo catastrofico. El resumen final cuenta
cuantos cruces hubo y que condicion los mato, para poder discutir con datos
(no con intuicion) si algun parametro de §13 necesita acta de Paul.

Uso:
    python validar_senal.py                # 5 sesiones, SPY
    python validar_senal.py --dias 10 --simbolo SPY --detalle
"""
import argparse
import io
import sys
from collections import Counter

from motor import config, datos, reloj, rest, senal

if (getattr(sys.stdout, 'encoding', '') or '').lower().replace('-', '') != 'utf8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                  errors='replace')

COND = ('S1', 'S2', 'S3', 'S4', 'S5', 'S6')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dias', type=int, default=5,
                    help='sesiones hacia atras a revisar')
    ap.add_argument('--simbolo', default='SPY')
    ap.add_argument('--detalle', action='store_true',
                    help='imprime tambien las velas casi-senal')
    args = ap.parse_args(argv)

    cfg = config.Config()
    problemas = cfg.validar()
    if problemas:
        print('\n'.join('CONFIG: %s' % p for p in problemas))
        return 2

    cli = rest.Cliente(cfg)
    r = cli.reloj()
    if not r.ok:
        print('No se pudo leer /v2/clock: %s' % r.error)
        return 1
    ahora = reloj.epoch_de_iso((r.datos or {}).get('timestamp', ''))

    # Se piden dias de calendario extra: fines de semana, feriados y el
    # calentamiento de EMA50/RSI30m consumen sesiones.
    inicio = ahora - (args.dias + 6) * 86400
    print('Descargando velas 1m de %s ...' % args.simbolo)
    v1, err = datos.velas_1m(cli, args.simbolo, inicio, ahora)
    if err:
        print('ERROR de datos: %s' % err)
        return 1
    if not v1:
        print('Sin velas. ¿Feed correcto? (FEED=%s)' % cfg.feed)
        return 1

    v5 = datos.agregar(v1, 5, ahora)
    v15 = datos.agregar(v1, 15, ahora)
    v30 = datos.agregar(v1, 30, ahora)
    print('velas: 1m=%d  5m=%d  15m=%d  30m=%d' %
          (len(v1), len(v5), len(v15), len(v30)))

    serie = senal.serie_senales(v5, v15, v30)
    sesiones = sorted({reloj.fecha_et(s.ts) for s in serie})[-args.dias:]
    serie = [s for s in serie if reloj.fecha_et(s.ts) in sesiones]

    gos = [s for s in serie if s.go]
    crudos = [s for s in serie if s.crudo]
    # El motor calcula S6 sobre TODAS las 5m, pero solo actua cada 10 min en
    # :x5+15 s. Para una vela, eso equivale a ts ET terminado en :00/:10/...;
    # ademas se aplica la ventana real G2 al instante de cierre.
    def accionable(s):
        cierre = s.ts + 5 * 60
        minuto_inicio = reloj.et(s.ts).minute
        m_cierre = reloj.minutos_et(cierre)
        return (s.go and minuto_inicio % config.CICLO_MIN == 0
                and config.ENTRADA_INI <= m_cierre <= config.ENTRADA_FIN)

    accionables = [s for s in serie if accionable(s)]
    print('\n=== SESIONES %s -> %s ===' % (sesiones[0], sesiones[-1]))

    for fecha in sesiones:
        deldia = [s for s in serie if reloj.fecha_et(s.ts) == fecha]
        gd = [s for s in deldia if s.go]
        ad = [s for s in deldia if accionable(s)]
        cd = [s for s in deldia if s.crudo]
        print('\n%s  velas5m=%3d  cruces(S1-S5)=%d  GO=%d  accionables=%d'
              % (fecha, len(deldia), len(cd), len(gd), len(ad)))
        for s in gd:
            d = s.datos
            etiqueta = 'GO*' if accionable(s) else 'GO '
            print('   %s %s  %-5s close=%.2f nivel=%.2f rsi5=%.1f/15=%.1f/'
                  '30=%.1f'
                  % (etiqueta, reloj.et(s.ts).strftime('%H:%M'), s.direccion,
                     d['close'],
                     d['nivel_alto'] if s.direccion == 'LONG'
                     else d['nivel_bajo'],
                     d['rsi5m'], d['rsi15m'], d['rsi30m']))
        for s in cd:
            if not s.go:
                print('   VETADO(S6 serrucho) %s %s'
                      % (reloj.et(s.ts).strftime('%H:%M'), s.direccion))
        if args.detalle:
            for s in deldia:
                if s.direccion is None and s.motivo.startswith('NO_CRUCE'):
                    fallan = s.motivo.split(':', 1)[1].split(',')
                    if len(fallan) == 1:      # a UNA condicion de disparar
                        print('   casi  %s falta %s'
                              % (reloj.et(s.ts).strftime('%H:%M'), fallan[0]))

    # -- por que no disparan: el cuello de botella real --------------------
    bloqueos = Counter()
    casi = 0
    for s in serie:
        if s.motivo.startswith('NO_CRUCE'):
            fallan = [c for c in s.motivo.split(':', 1)[1].split(',') if c]
            for c in fallan:
                bloqueos[c] += 1
            if len(fallan) == 1:
                casi += 1

    print('\n=== RESUMEN (%d sesiones) ===' % len(sesiones))
    print('  velas 5m evaluadas : %d' % len(serie))
    print('  cruces S1-S5       : %d' % len(crudos))
    print('  GO (con S6)        : %d' % len(gos))
    print('  GO accionables (*) : %d' % len(accionables))
    print('  vetados por S6     : %d' % (len(crudos) - len(gos)))
    print('  a UNA condicion    : %d' % casi)
    print('  condicion que mas bloquea (cuenta de fallos por vela):')
    for c in COND:
        if bloqueos.get(c):
            print('     %s: %d' % (c, bloqueos[c]))
    if not gos:
        print('\n  ATENCION: CERO senales en %d sesiones. Un agente que nunca'
              % len(sesiones))
        print('  opera no tiene demo. Esto hay que decidirlo con Paul antes de')
        print('  lanzar en vivo (§13 esta congelada: reportar, no ajustar).')
    else:
        print('\n  Frecuencia bruta: %.2f GO por sesion'
              % (len(gos) / float(len(sesiones))))
        print('  Frecuencia accionable antes de G1-G10: %.2f por sesion'
              % (len(accionables) / float(len(sesiones))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
