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

@app.route('/')
def index():
    # Obtener el Top picks actual
    top_picks = main.get_all_money_machine_picks()
    
    # Obtener Estadísticas y Ranking de Ligas
    stats = main.get_stats()
    top_leagues = main.get_top_leagues_rank()
    
    # Obtener la Cartelera Diaria para el Sidebar
    sidebar_matches = main.get_daily_leagues_matches()
    
    # Fecha de hoy para comparar
    hoy_str = "2026-02-21" # Alineado con el Salto Temporal
    
    return render_template('index.html', 
                           picks=top_picks, 
                           stats=stats,
                           top_leagues=top_leagues,
                           sidebar_matches=sidebar_matches,
                           hoy=hoy_str,
                           last_sync=main.engine.last_updated)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
