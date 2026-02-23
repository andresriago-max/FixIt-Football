from flask import Flask, render_template
import main
import urllib.parse
from datetime import datetime

app = Flask(__name__)

@app.template_filter('urlencode')
def urlencode_filter(s):
    if s is None:
        return ''
    return urllib.parse.quote(str(s))

@app.route('/test-api')
def test_api():
    """Ruta para forzar sincronización y ver logs directos en el navegador."""
    try:
        from main import engine
        print(">>> TEST-API: Iniciando sincronización forzada...")
        engine.fetch_data()
        return f"OK: Sincronización terminada. Estado final: {engine.last_updated}"
    except Exception as e:
        print(f">>> TEST-API: ERROR: {e}")
        return f"ERROR: {str(e)}"

@app.route('/debug')
def debug():
    from main import engine
    picks = engine.cached_picks
    odds_count = len(engine.fixtures_odds)
    preds_count = len(engine.fixtures_predictions)
    matches_count = len(engine.matches)
    lines = [
        f"<b>Partidos cargados:</b> {matches_count}",
        f"<b>Cuotas disponibles:</b> {odds_count}",
        f"<b>Predicciones nativas:</b> {preds_count}",
        f"<b>Picks en cache:</b> {len(picks)}",
        f"<b>Último sync:</b> {engine.last_updated}",
        "<hr>",
    ]
    for p in picks:
        lines.append(f"✅ {p['teams']} | {p['market']} | {p['prob']}% | cuota: {p['odds']}<br>")
    # Mostrar predicciones disponibles
    lines.append("<hr><b>Predicciones (primeras 20):</b><br>")
    for fid, pct in list(engine.fixtures_predictions.items())[:20]:
        teams = next((f"{m['teams']['home']['name']} vs {m['teams']['away']['name']}"
                     for m in engine.matches if m['fixture']['id'] == fid), str(fid))
        lines.append(f"&nbsp;&nbsp;{teams}: {pct}%<br>")
    return "<br>".join(lines)

@app.route('/health')
def health():
    """Ruta de salud para monitoreo de despliegue."""
    return {"status": "healthy", "engine_status": main.engine.last_updated}, 200

@app.route('/')
def index():
    # Obtener el Top picks actual
    top_picks = main.get_all_money_machine_picks()
    
    # Obtener Estadísticas y Ranking de Ligas
    stats = main.get_stats()
    top_leagues = main.get_top_leagues_rank()
    
    # Obtener la Cartelera Diaria para el Sidebar
    sidebar_matches = main.get_daily_leagues_matches()
    
    # Fecha de hoy dinámica (España CET)
    from datetime import timezone, timedelta
    tz_spain = timezone(timedelta(hours=1))
    hoy_str = datetime.now(tz_spain).strftime("%Y-%m-%d")
    
    return render_template('index.html', 
                           picks=top_picks, 
                           stats=stats,
                           top_leagues=top_leagues,
                           sidebar_matches=sidebar_matches,
                           hoy=hoy_str,
                           last_sync=main.engine.last_updated)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
