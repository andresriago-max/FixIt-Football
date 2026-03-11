from flask import Flask, render_template
import main
import urllib.parse
from datetime import datetime

app = Flask(__name__)

# Motor PRO: Inicialización única al cargar el módulo
from main import init_engine
init_engine()

@app.template_filter('urlencode')
def urlencode_filter(s):
    if s is None:
        return ''
    return urllib.parse.quote(str(s))

@app.route('/debug')
def debug():
    import os
    from main import engine
    key = os.getenv("FOOTBALL_API_KEY")
    return {
        "api_key_detected": "SI" if key else "NO",
        "api_key_preview": key[:4] if key and len(key) >= 4 else "????",
        "engine_status": engine.last_updated,
        "picks_count": len(engine.cached_picks),
        "matches_count": len(engine.matches),
        "thread_started": getattr(engine, '_thread_started', False)
    }

@app.route('/sync')
def sync():
    """Fuerza una sincronización inmediata del motor."""
    import threading
    from main import engine
    # Solo lanzar un nuevo hilo si no hay uno activo
    with engine._lock:
        engine.last_updated = "Sincronizando manualmente..."
    threading.Thread(target=engine.fetch_data, daemon=True).start()
    return {"status": "Sincronización iniciada", "message": "Abriendo página principal en 30 segundos..."}

@app.route('/test-api')
def test_api():
    import os, requests
    key = os.getenv("FOOTBALL_API_KEY")
    url = "https://v3.football.api-sports.io/status"
    try:
        r = requests.get(url, headers={'x-apisports-key': key}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

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
