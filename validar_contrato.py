# -*- coding: utf-8 -*-
"""Preflight de solo lectura para la cadena y G10; nunca crea ordenes."""
import argparse
import sys

from motor import config, contratos, datos, reloj, rest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--simbolo', default='SPY')
    args = ap.parse_args(argv)
    cfg = config.Config()
    problemas = cfg.validar()
    if problemas:
        print('\n'.join('CONFIG: %s' % p for p in problemas))
        return 2
    cli = rest.Cliente(cfg)
    rc = cli.reloj()
    if not rc.ok:
        print('CLOCK: %s' % rc.error)
        return 1
    ahora = reloj.epoch_de_iso((rc.datos or {}).get('timestamp', ''))
    spots, err = datos.snapshot_indices(cli, [args.simbolo])
    if err or args.simbolo not in spots:
        print('SPOT: %s' % (err or 'sin ultimo trade'))
        return 1
    spot = spots[args.simbolo]
    print('%s spot=%.2f | sesion ET=%s' %
          (args.simbolo, spot, reloj.fecha_et(ahora)))
    fallos = 0
    for direccion in ('LONG', 'SHORT'):
        sp, razon = contratos.elegir(cli, args.simbolo, direccion, spot, ahora)
        if sp is None:
            print('%-5s PASS %s' % (direccion, razon))
            fallos += 1
            continue
        ok, razon, det = sp.liquidez()
        esperada = config.DEBITO_ESPERADO[0] <= sp.mid <= config.DEBITO_ESPERADO[1]
        print('%-5s %s exp=%s %.0f/%.0f debit=%.2f IV=%.3f/%.3f '
              'spread_rel=%.1f%% OI=%s/%s vol=%s/%s debit_esperado=%s'
              % (direccion, 'G10 PASS' if ok else razon,
                 sp.expiracion, sp.larga.strike, sp.corta.strike, sp.mid,
                 sp.larga.iv or 0.0, sp.corta.iv or 0.0,
                 numero(det.get('spread_rel')) * 100.0,
                 sp.larga.oi, sp.corta.oi,
                 sp.larga.volumen, sp.corta.volumen,
                 'si' if esperada else 'no'))
    return 0 if fallos < 2 else 1


def numero(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


if __name__ == '__main__':
    sys.exit(main())
