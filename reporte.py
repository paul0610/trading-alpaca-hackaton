# -*- coding: utf-8 -*-
"""Genera el dashboard estatico de Remora Desk desde su diario JSONL.

Solo stdlib. El HTML resultante es autocontenido, apto para GitHub Pages y no
ejecuta JavaScript. Todo texto procedente del diario se escapa antes de entrar
al documento: el journal es dato, nunca instrucciones ni markup confiable.
"""
import argparse
import html
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from motor import config


def leer_jsonl(ruta):
    fuera = []
    ruta = Path(ruta)
    if not ruta.exists():
        return fuera
    for linea in ruta.read_text(encoding='utf-8').splitlines():
        try:
            fuera.append(json.loads(linea))
        except (ValueError, TypeError):
            continue
    return fuera


def leer_json(ruta):
    try:
        return json.loads(Path(ruta).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}


def esc(valor):
    return html.escape(str(valor if valor is not None else ''), quote=True)


def dinero(valor):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        n = 0.0
    signo = '-' if n < 0 else ''
    return '%s$%s' % (signo, format(abs(n), ',.2f'))


def numero(valor, defecto=0.0):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return defecto


def resumen(decisiones, eventos, estado):
    aperturas = [d for d in decisiones if d.get('accion') == 'OPEN']
    sombras = [d for d in decisiones if d.get('accion') == 'OPEN_SOMBRA']
    pases = [d for d in decisiones if d.get('accion') == 'PASS']
    cierres = [e for e in eventos
               if e.get('tipo') in ('POSICION_CERRADA',
                                    'POSICION_CERRADA_PARCIAL')]
    realizados = [numero(e.get('realizado_usd')) for e in cierres]
    slips = [numero(e.get('slippage')) for e in eventos
             if e.get('tipo') in ('FILL_APERTURA', 'CIERRE')
             and e.get('slippage') is not None]
    fallos_gate = {}
    for d in decisiones:
        for nombre, g in (d.get('gates') or {}).items():
            if (g or {}).get('estado') == 'FAIL':
                fallos_gate[nombre] = fallos_gate.get(nombre, 0) + 1
    return {
        'decisiones': len(decisiones), 'aperturas': len(aperturas),
        'sombras': len(sombras), 'pases': len(pases),
        'posiciones': len(estado.get('posiciones') or []),
        'realizado': sum(realizados),
        'ganadoras': sum(1 for x in realizados if x > 0),
        'perdedoras': sum(1 for x in realizados if x < 0),
        'slippage_medio': (sum(slips) / len(slips) if slips else None),
        'fallos_gate': fallos_gate,
        'halt': estado.get('halt'), 'equity': numero(estado.get('equity')),
        'sesion': estado.get('sesion') or '—',
    }


def curva_svg(eventos):
    puntos = [0.0]
    acumulado = 0.0
    for e in eventos:
        if e.get('tipo') in ('POSICION_CERRADA',
                             'POSICION_CERRADA_PARCIAL'):
            acumulado += numero(e.get('realizado_usd'))
            puntos.append(acumulado)
    ancho, alto, margen = 760, 210, 24
    if len(puntos) == 1:
        return ('<div class="empty-chart"><span>No closed trades yet.</span>'
                '<small>The realized P&amp;L curve will appear here.</small></div>')
    minimo, maximo = min(puntos), max(puntos)
    if math.isclose(minimo, maximo):
        minimo -= 1
        maximo += 1
    escala_x = (ancho - 2 * margen) / max(1, len(puntos) - 1)
    escala_y = (alto - 2 * margen) / (maximo - minimo)

    def xy(i, v):
        return (margen + i * escala_x,
                alto - margen - (v - minimo) * escala_y)

    linea = ' '.join('%.1f,%.1f' % xy(i, v) for i, v in enumerate(puntos))
    base_y = xy(0, 0.0)[1] if minimo <= 0 <= maximo else alto - margen
    area = '%s %.1f,%.1f %.1f,%.1f' % (
        linea, ancho - margen, base_y, margen, base_y)
    color = '#36d9a0' if puntos[-1] >= 0 else '#ff6b6b'
    return (
        '<svg class="curve" viewBox="0 0 %d %d" role="img" '
        'aria-label="Cumulative realized P and L: %s">'
        '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="zero"/>'
        '<polygon points="%s" fill="%s" opacity=".10"/>'
        '<polyline points="%s" fill="none" stroke="%s" stroke-width="3" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        '</svg>' % (ancho, alto, esc(dinero(puntos[-1])), margen, base_y,
                    ancho - margen, base_y, area, color, linea, color))


def tarjeta(etiqueta, valor, nota, clase=''):
    return ('<article class="metric %s"><span>%s</span><strong>%s</strong>'
            '<small>%s</small></article>'
            % (esc(clase), esc(etiqueta), esc(valor), esc(nota)))


def filas_gates(fallos, total):
    if not fallos:
        return ('<div class="empty-inline">No hard-gate failures recorded. '
                'NOT_EVALUATED is kept separate from PASS.</div>')
    maximo = max(fallos.values())
    filas = []
    for nombre, cantidad in sorted(fallos.items(),
                                   key=lambda x: (-x[1], x[0])):
        ancho = cantidad / float(maximo) * 100.0
        filas.append(
            '<div class="gate-row"><code>%s</code><div class="bar"><i '
            'style="width:%.1f%%"></i></div><b>%d</b><small>%.1f%%</small></div>'
            % (esc(nombre), ancho, cantidad,
               cantidad / float(max(1, total)) * 100.0))
    return ''.join(filas)


def razon_decision(d):
    llm = d.get('llm') or {}
    return (llm.get('razon') or d.get('detalle') or d.get('razon')
            or d.get('senal', {}).get('motivo') or '—')


def filas_decisiones(decisiones):
    if not decisiones:
        return ('<tr><td colspan="7" class="empty-table">No decisions yet. '
                'Run the desk in shadow mode to create the first evidence.</td></tr>')
    filas = []
    for d in reversed(decisiones[-80:]):
        accion = d.get('accion') or '—'
        clase = ('open' if accion.startswith('OPEN') else 'pass')
        sen = d.get('senal') or {}
        direccion = sen.get('direccion') or '—'
        gates_fail = [k for k, v in (d.get('gates') or {}).items()
                      if (v or {}).get('estado') == 'FAIL']
        gate_txt = ', '.join(gates_fail) if gates_fail else '—'
        confianza = (d.get('llm') or {}).get('confianza')
        confianza = ('%s%%' % confianza if confianza is not None else '—')
        filas.append(
            '<tr><td><time>%s</time></td><td>%s</td><td>%s</td>'
            '<td><span class="pill %s">%s</span></td><td>%s</td>'
            '<td>%s</td><td class="reason">%s</td></tr>'
            % (esc(d.get('ts_et') or d.get('ciclo_et') or '—'),
               esc(d.get('simbolo') or '—'), esc(direccion), clase,
               esc(accion), esc(gate_txt), esc(confianza),
               esc(razon_decision(d))))
    return ''.join(filas)


CSS = r"""
:root{color-scheme:dark;--bg:#071118;--panel:#0c1a22;--panel2:#10242d;
--line:#203840;--text:#ecf7f4;--muted:#8da6a7;--cyan:#55d6c2;
--green:#36d9a0;--red:#ff6b6b;--amber:#ffbd66;--ink:#061014}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
body:before{content:"";position:fixed;inset:0;pointer-events:none;background:
radial-gradient(circle at 78% -10%,rgba(85,214,194,.13),transparent 34%),
linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),
linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);
background-size:auto,32px 32px,32px 32px}.shell{max-width:1440px;margin:auto;
padding:26px 28px 60px;position:relative}.top{display:flex;align-items:flex-start;
justify-content:space-between;gap:24px;margin-bottom:22px}.brand{display:flex;gap:14px;
align-items:center}.mark{width:46px;height:46px;border:1px solid var(--cyan);
display:grid;place-items:center;border-radius:13px;background:rgba(85,214,194,.08);
font-family:ui-monospace,monospace;font-weight:900;color:var(--cyan);font-size:19px}
h1{font-size:23px;letter-spacing:-.02em;margin:0}h1 span{color:var(--cyan)}
.brand p,.updated{margin:4px 0 0;color:var(--muted);font-size:13px}.status{text-align:right}
.status b{display:inline-flex;gap:7px;align-items:center;border:1px solid var(--line);
border-radius:99px;padding:7px 11px;font-size:12px;letter-spacing:.08em;text-transform:uppercase}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 12px var(--green)}
.dot.halt{background:var(--red);box-shadow:0 0 12px var(--red)}.metrics{display:grid;
grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:12px}.metric,
.panel{background:linear-gradient(150deg,rgba(16,36,45,.96),rgba(10,25,32,.96));
border:1px solid var(--line);border-radius:15px}.metric{padding:17px 18px;min-height:112px}
.metric span,.eyebrow{display:block;color:var(--muted);font-size:11px;font-weight:700;
letter-spacing:.11em;text-transform:uppercase}.metric strong{display:block;font-size:28px;
line-height:1;margin:17px 0 8px;letter-spacing:-.035em}.metric small{color:var(--muted)}
.metric.good strong{color:var(--green)}.metric.bad strong{color:var(--red)}
.limits{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);
border:1px solid var(--line);border-radius:13px;overflow:hidden;margin-bottom:12px}
.limit{background:#0a171e;padding:12px 16px;display:flex;justify-content:space-between;
gap:12px;font-size:12px}.limit span{color:var(--muted)}.limit b{font-family:ui-monospace,monospace}
.grid{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(300px,.8fr);gap:12px;
margin-bottom:12px}.panel{padding:19px}.panel-head{display:flex;justify-content:space-between;
align-items:flex-end;gap:16px;margin-bottom:12px}.panel h2{font-size:16px;margin:4px 0 0}
.panel-head strong{font-size:20px}.curve{width:100%;height:210px;display:block}.zero{stroke:var(--line);
stroke-width:1;stroke-dasharray:4 5}.empty-chart{height:210px;border:1px dashed var(--line);
display:grid;place-content:center;text-align:center;border-radius:10px;color:var(--muted)}
.empty-chart span{color:var(--text);font-weight:700}.empty-chart small{margin-top:5px}
.gate-row{display:grid;grid-template-columns:34px 1fr 28px 44px;gap:9px;align-items:center;
margin:13px 0}.gate-row code{color:var(--cyan);font-weight:800}.gate-row b{text-align:right}
.gate-row small{color:var(--muted);text-align:right}.bar{height:7px;background:#061015;
border-radius:20px;overflow:hidden}.bar i{display:block;height:100%;background:var(--amber);
border-radius:20px}.empty-inline{color:var(--muted);padding:36px 2px;line-height:1.6}
.table-panel{padding:0;overflow:hidden}.table-head{padding:19px 19px 15px}.scroll{overflow:auto}
table{width:100%;border-collapse:collapse;min-width:940px;font-size:12px}th{text-align:left;
color:var(--muted);font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:11px 14px;
border-top:1px solid var(--line);border-bottom:1px solid var(--line)}td{padding:13px 14px;
border-bottom:1px solid rgba(32,56,64,.65);vertical-align:top}tbody tr:hover{background:rgba(85,214,194,.035)}
time{white-space:nowrap;color:var(--muted);font-family:ui-monospace,monospace}.pill{display:inline-block;
padding:4px 7px;border-radius:6px;font-weight:800;font-size:10px;letter-spacing:.06em}
.pill.open{background:rgba(54,217,160,.12);color:var(--green)}.pill.pass{background:rgba(141,166,167,.11);
color:#b8cbca}.reason{max-width:420px;line-height:1.5;color:#c8d9d7}.empty-table{text-align:center;
padding:54px;color:var(--muted)}footer{display:flex;justify-content:space-between;gap:20px;
color:var(--muted);font-size:11px;margin-top:16px}footer b{color:var(--cyan)}
@media(max-width:900px){.metrics{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}
.limits{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.shell{padding:18px 14px 40px}
.top{display:block}.status{text-align:left;margin-top:14px}.metrics{grid-template-columns:1fr 1fr}
.metric{padding:14px;min-height:102px}.metric strong{font-size:23px}.limits{grid-template-columns:1fr}
footer{display:block;line-height:1.8}}
"""


def renderizar(decisiones, eventos, estado):
    r = resumen(decisiones, eventos, estado)
    total_cierres = r['ganadoras'] + r['perdedoras']
    acierto = (r['ganadoras'] / float(total_cierres) * 100.0
               if total_cierres else None)
    estado_txt = 'HALTED · %s' % r['halt'] if r['halt'] else 'RISK ENGINE READY'
    dot = 'dot halt' if r['halt'] else 'dot'
    modo = ((decisiones[-1].get('modo') if decisiones else None) or 'no runs').upper()
    ahora = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    cards = ''.join([
        tarjeta('Account equity', dinero(r['equity']) if r['equity'] else '—',
                'Paper account · session %s' % r['sesion']),
        tarjeta('Realized P&L', dinero(r['realizado']),
                '%d closed fill%s' % (total_cierres, '' if total_cierres == 1 else 's'),
                'good' if r['realizado'] > 0 else ('bad' if r['realizado'] < 0 else '')),
        tarjeta('Decisions', str(r['decisiones']),
                '%d PASS · %d shadow OPEN' % (r['pases'], r['sombras'])),
        tarjeta('Win rate', ('%.0f%%' % acierto if acierto is not None else '—'),
                '%d wins · %d losses' % (r['ganadoras'], r['perdedoras'])),
        tarjeta('Avg. slippage',
                ('$%.3f' % r['slippage_medio']
                 if r['slippage_medio'] is not None else '—'),
                'Entry + exit fills'),
    ])
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name="description" content="Remora Desk decision and risk journal dashboard">
<title>Remora Desk · Decision Journal</title><style>%s</style></head>
<body><main class="shell"><header class="top"><div class="brand"><div class="mark">RD</div><div>
<h1>Remora <span>Desk</span></h1><p>Decision journal · every trade, every PASS, every hard gate</p></div></div>
<div class="status"><b><i class="%s"></i>%s</b><div class="updated">%s · %s</div></div></header>
<section class="metrics">%s</section>
<section class="limits" aria-label="Hard risk limits"><div class="limit"><span>Premium / trade</span><b>≤ $500</b></div>
<div class="limit"><span>Aggregate premium</span><b>≤ $1,500</b></div><div class="limit"><span>Open positions</span><b>%d / 3</b></div>
<div class="limit"><span>Forced flat</span><b>15:50 ET</b></div></section>
<section class="grid"><article class="panel"><div class="panel-head"><div><span class="eyebrow">Realized outcome</span>
<h2>Cumulative P&amp;L</h2></div><strong>%s</strong></div>%s</article>
<article class="panel"><div class="panel-head"><div><span class="eyebrow">Deterministic officer</span>
<h2>Hard-gate failures</h2></div><strong>%d</strong></div>%s</article></section>
<section class="panel table-panel"><div class="table-head"><span class="eyebrow">Audit trail</span><h2>Recent decisions</h2></div>
<div class="scroll"><table><thead><tr><th>ET timestamp</th><th>Symbol</th><th>Signal</th><th>Action</th>
<th>Failed gates</th><th>LLM conf.</th><th>Recorded reasoning</th></tr></thead><tbody>%s</tbody></table></div></section>
<footer><span><b>Signal → risk → AI → revalidation → limit order.</b> AI may narrow; it never overrides risk.</span>
<span>Generated from local JSONL · no synthetic trades · %s</span></footer></main></body></html>""" % (
        CSS, dot, esc(estado_txt), esc(modo), esc(ahora), cards,
        r['posiciones'], esc(dinero(r['realizado'])), curva_svg(eventos),
        sum(r['fallos_gate'].values()),
        filas_gates(r['fallos_gate'], r['decisiones']),
        filas_decisiones(decisiones), esc(ahora))


def generar(carpeta=None, salida=None):
    carpeta = Path(carpeta or config.DIR_DIARIO)
    salida = Path(salida or (config.DIR_DOCS / 'index.html'))
    decisiones = leer_jsonl(carpeta / 'decisiones.jsonl')
    eventos = leer_jsonl(carpeta / 'eventos.jsonl')
    estado = leer_json(carpeta / 'estado.json')
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(renderizar(decisiones, eventos, estado), encoding='utf-8')
    return salida, len(decisiones), len(eventos)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Genera docs/index.html desde el diario')
    ap.add_argument('--diario', default=str(config.DIR_DIARIO))
    ap.add_argument('--salida', default=str(config.DIR_DOCS / 'index.html'))
    args = ap.parse_args(argv)
    salida, nd, ne = generar(args.diario, args.salida)
    print('Dashboard generado: %s (%d decisiones, %d eventos)'
          % (salida, nd, ne))
    return 0


if __name__ == '__main__':
    sys.exit(main())
