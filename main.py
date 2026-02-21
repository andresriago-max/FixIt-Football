import os
import json
import requests
import threading
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
print(f"[{datetime.now()}] >>> CARGANDO MAIN.PY (Versión Diagnóstico 10:35) <<<")
API_KEY = os.getenv("FOOTBALL_API_KEY")
print(f"[{datetime.now()}] DEBUG: API_KEY detectada? {'SÍ' if API_KEY else 'NO'}")
if API_KEY:
    API_KEY = API_KEY.strip()
    print(f"[{datetime.now()}] DEBUG: API_KEY (primeros 4): {API_KEY[:4]}")

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    'x-apisports-key': API_KEY
}

# Ligas PRO habilitadas (Mapping API-Sports ID)
# 140: La Liga, 39: Premier League, 135: Serie A, 78: Bundesliga, 61: Ligue 1, 94: Primeira Liga, 88: Eredivisie
ENABLED_LEAGUES = {
    140: 'La Liga',
    39: 'Premier League',
    135: 'Serie A',
    78: 'Bundesliga',
    61: 'Ligue 1',
    94: 'Liga Portugal',
    88: 'Eredivisie',
    40: 'Championship', # Inglaterra 2
    141: 'La Liga 2',      # España 2
    136: 'Serie B',        # Italia 2
    79: '2. Bundesliga',  # Alemania 2
    62: 'Ligue 2',        # Francia 2
    253: 'MLS',           # USA
    262: 'Liga MX',        # México
    179: 'Liga Argentina',
    71: 'Serie A Brasil',
    239: 'Liga Colombia',
    2: 'Champions League',
    3: 'Europa League'
}

class FixItPRO:
    def __init__(self):
        self.matches = []
        self.fixtures_odds = {} # Almacén para cuotas reales
        self.fixtures_predictions = {} # Almacén para coeficientes nativos
        self.fixtures_advice = {} # Almacén para justificación de la API
        self.cached_picks = []
        self.last_updated: str = "Iniciando Motor PRO..."
        self._lock = threading.RLock()
        self.stats_file = "stats.json"
        self.stats = self.load_stats()

    def load_stats(self):
        if os.path.exists(self.stats_file):
            with open(self.stats_file, 'r') as f:
                return json.load(f)
        return {"ganadas": 0, "perdidas": 0, "ligas": {}}

    def save_stats(self):
        with open(self.stats_file, 'w') as f:
            json.dump(self.stats, f, indent=4)

    def fetch_data(self):
        """Obtiene partidos de la API-sports forzando la fecha del SÁBADO (2026-02-21)."""
        try:
            print(f"[{datetime.now()}] Iniciando fetch_data...")
            if not API_KEY:
                print("CRITICAL: FOOTBALL_API_KEY no encontrada en variables de entorno.")
                with self._lock:
                    self.last_updated = "Error: Falta API KEY"
                return

            # Fecha Sábado 21 de Febrero 2026
            date_today = "2026-02-21"
            url = f"{BASE_URL}/fixtures?date={date_today}"

            print(f"[{datetime.now()}] Iniciando peticion a API-Sports...")
            print(f"URL: {url}")
            print(f"Headers (Key length): {len(HEADERS.get('x-apisports-key', ''))}")
            
            # Timeout (Connect, Read) - Más granular
            try:
                response = requests.get(url, headers=HEADERS, timeout=(10, 30))
                print(f"[{datetime.now()}] Respuesta recibida! Status: {response.status_code}")
            except Exception as e:
                print(f"[{datetime.now()}] ERROR CRITICO en requests.get: {e}")
                with self._lock:
                    self.last_updated = f"Err: Conexión API"
                return
            
            if response.status_code == 200:
                data = response.json()
                # Verificar si la API devolvió un mensaje de error en el JSON
                if data.get('errors'):
                    print(f"[{datetime.now()}] API devolvió errores: {data['errors']}")
                    with self._lock:
                        err_msg = str(next(iter(data['errors'].values()), 'Unknown'))
                        self.last_updated = f"API ERR: {err_msg[:15]}"
                    return

                fixtures = data.get('response', [])
                print(f"[{datetime.now()}] Partidos recibidos: {len(fixtures)}")
                
                # Filtrar solo ligas habilitadas
                processed_matches = []
                for f in fixtures:
                    league_id = f['league']['id']
                    if league_id in ENABLED_LEAGUES:
                        processed_matches.append(f)
                
                print(f"Partidos filtrados (Ligas PRO): {len(processed_matches)}")
                
                with self._lock:
                    self.matches = processed_matches
                    self.last_updated = datetime.now().strftime("%H:%M")
                
                if processed_matches:
                    print(f"Buscando cuotas para {len(processed_matches)} partidos...")
                    self.fetch_odds(date_today)
                    
                    # Obtener IDs de los partidos para pedir predicciones
                    fixture_ids = [m['fixture']['id'] for m in processed_matches]
                    print(f"Consultando predicciones oficiales para {len(fixture_ids)} partidos...")
                    self.fetch_predictions(fixture_ids)
                else:
                    print("Sin partidos para las ligas habilitadas hoy.")
                
                with self._lock:
                    self.cached_picks = self.process_top_8()
                    self.update_stats_from_results()
                    self.last_updated = datetime.now().strftime("%H:%M") # Asegurar actualización final
                    print(f"[{datetime.now()}] API Sync Finalizada. Partidos: {len(self.matches)}, Cuotas: {len(self.fixtures_odds)}, Picks PRO: {len(self.cached_picks)}")
            else:
                with self._lock:
                    self.last_updated = f"API ERR: {response.status_code}"
                print(f"API Error {response.status_code}: {response.text}")
        except Exception as e:
            with self._lock:
                self.last_updated = f"Error: {str(e)[:20]}"
            print(f"Connection Error: {e}")

    def start_scheduler(self):
        """Hilo en segundo plano para actualizaciones a las 02:00 y 12:00."""
        def run_loop():
            print("Scheduler PRO (API-Sports): Iniciado")
            while True:
                now = datetime.now()
                if (now.hour == 2 and now.minute == 0) or (now.hour == 12 and now.minute == 0):
                    self.fetch_data()
                    time.sleep(61)
                time.sleep(30)

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()

    def fetch_odds(self, date):
        """Obtiene cuotas reales para los partidos del día."""
        try:
            url = f"{BASE_URL}/odds?date={date}"
            response = requests.get(url, headers=HEADERS, timeout=15)
            if response.status_code == 200:
                data = response.json()
                odds_data = data.get('response', [])
                new_odds = {}
                for item in odds_data:
                    f_id = item['fixture']['id']
                    # Buscamos cuotas de Match Winner (id: 1) en Bet365 (id: 8) o el primero disponible
                    bookmakers = item.get('bookmakers', [])
                    if not bookmakers: continue
                    
                    # Preferencia Bet365 o el primero que tenga cuotas
                    bm = next((b for b in bookmakers if b['id'] == 8), bookmakers[0])
                    for bet in bm.get('bets', []):
                        if bet['id'] == 1: # Match Winner
                            for val in bet.get('values', []):
                                if val['value'] == 'Home':
                                    new_odds[f_id] = float(val['odd'])
                
                with self._lock:
                    self.fixtures_odds = new_odds
        except Exception as e:
            print(f"Error fetching odds: {e}")

    def fetch_predictions(self, fixtures_ids):
        """Obtiene predicciones nativas (Home %) y consejos para los partidos clave."""
        new_preds = {}
        new_advice = {}
        for f_id in fixtures_ids[:40]:
            try:
                url = f"{BASE_URL}/predictions?fixture={f_id}"
                response = requests.get(url, headers=HEADERS, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    res = data.get('response', [])
                    if res:
                        # 1. Porcentaje de victoria local
                        home_pct = res[0].get('predictions', {}).get('percent', {}).get('home')
                        if home_pct:
                            new_preds[f_id] = int(home_pct.replace('%', ''))
                        
                        # 2. Consejo/Justificación de la API
                        advice = res[0].get('predictions', {}).get('advice')
                        if advice:
                            new_advice[f_id] = advice
            except Exception as e:
                print(f"Error prediction {f_id}: {e}")
        
        with self._lock:
            self.fixtures_predictions = new_preds
            self.fixtures_advice = new_advice

    def process_top_8(self):
        """Selecciona partidos con probabilidad real (Nativa o Matemática) >=40%."""
        picks = []
        with self._lock:
            matches_snapshot = list(self.matches)
            odds_snapshot = dict(self.fixtures_odds)
            preds_snapshot = dict(self.fixtures_predictions)
            advice_snapshot = dict(self.fixtures_advice)
        
        # España CET
        tz_spain = timezone(timedelta(hours=1))
        # Salto Temporal: Simular que "hoy" es Sábado 21
        today_str = "2026-02-21"
        tomorrow_str = "2026-02-22"

        for m in matches_snapshot:
            f_id = m['fixture']['id']
            
            # 1. Prioridad: Predicción Nativa de la API
            native_prob = preds_snapshot.get(f_id)
            
            # 2. Respaldo: Cálculo matemático 1/cuota
            real_odd = odds_snapshot.get(f_id)
            calc_prob = int(100 / real_odd) if real_odd else 0
            
            # 3. Justificación
            advice = advice_snapshot.get(f_id, "Datos Estadísticos Oficiales")

            # La probabilidad final es la nativa si existe, si no la calculada
            prob = native_prob if native_prob else calc_prob
            
            # Si no hay ni cuota ni predicción, saltamos
            if prob == 0: continue
            
            # Filtro Final PRO: Mínimo 40%
            if prob < 40: continue

            # Filtro de horario: Hoy España o Madrugada de Mañana (hasta las 07:00 AM)
            # Esto captura partidos de Colombia que ocurren en la madrugada de España
            utc_time = datetime.strptime(m['fixture']['date'], "%Y-%m-%dT%H:%M:%S%z")
            match_spain = utc_time.astimezone(tz_spain)
            match_date = match_spain.strftime("%Y-%m-%d")
            match_hour = match_spain.hour

            is_valid_time = (match_date == today_str) or (match_date == tomorrow_str and match_hour < 7)
            if not is_valid_time: continue

            league = ENABLED_LEAGUES.get(m['league']['id'], m['league']['name'])
            home = m['teams']['home']['name']
            away = m['teams']['away']['name']
            time_str = match_spain.strftime("%H:%M")

            picks.append({
                'id': f_id,
                'teams': f"{home} vs {away}",
                'league': league,
                'market': "Victoria Local",
                'description': advice,
                'prob': prob,
                'odds': real_odd,
                'date': "21-02-2026",
                'time': time_str,
                'icon': 'fa-shield-halved',
                'color': '#10b981'
            })
            if len(picks) >= 20: break
            
        # Ordenar picks por probabilidad descendente (el más seguro arriba) y limitar a 20
        picks = sorted(picks, key=lambda x: x['prob'], reverse=True)
        return picks[:20]

    def update_stats_from_results(self):
        """Analiza partidos finalizados para actualizar el contador de éxitos."""
        cambios = False
        with self._lock:
            for m in self.matches:
                if m['fixture']['status']['short'] == 'FT':
                    home_goals = m['goals']['home']
                    away_goals = m['goals']['away']
                    league_name = ENABLED_LEAGUES.get(m['league']['id'], m['league']['name'])
                    
                    # Nuestra predicción PRO por defecto es Victoria Local
                    if home_goals > away_goals:
                        self.stats['ganadas'] += 1
                        self.stats['ligas'][league_name] = self.stats['ligas'].get(league_name, 0) + 1
                        cambios = True
                    else:
                        self.stats['perdidas'] += 1
                        cambios = True
        
        if cambios:
            self.save_stats()

    def get_top_leagues(self):
        """Retorna las 3 mejores ligas por aciertos."""
        with self._lock:
            sorted_ligas = sorted(self.stats['ligas'].items(), key=lambda x: x[1], reverse=True)
            return sorted_ligas[:3]

engine = FixItPRO()
engine.stats = engine.load_stats()
# Hilo de carga inicial
import threading
threading.Thread(target=engine.fetch_data, daemon=True).start()
engine.start_scheduler()

def get_stats():
    return engine.stats

def get_top_leagues_rank():
    return engine.get_top_leagues()

def get_all_money_machine_picks():
    # Retorna lo que haya en cache inmediatamente, sin esperar a la API
    return engine.cached_picks

def get_daily_leagues_matches():
    """Retorna los partidos para el sidebar siguiendo la jerarquía solicitada."""
    output = {}
    priority_ids = {140, 39, 135, 78, 2, 3} # La Liga, PL, Serie A, Bundes, UCL, UEL
    
    with engine._lock:
        matches_snapshot = list(engine.matches)
        # Extraer IDs de los picks PRO actuales para asegurar su presencia en el sidebar
        top_20_ids = {p.get('id') for p in engine.cached_picks if p.get('id')}
    
    if not matches_snapshot: return {}
        
    for m in matches_snapshot:
        f_id = m['fixture']['id']
        l_id = m['league']['id']
        
        # Filtro de Jerarquía:
        # 1. Si es liga prioritaria (La Liga, PL, etc.)
        # 2. SI está en el Top 20 picks (independientemente de la liga)
        if l_id not in priority_ids and f_id not in top_20_ids:
            continue

        league = ENABLED_LEAGUES.get(l_id, m['league']['name'])
        if league not in output: output[league] = []
        
        status = 'PND'
        short = m['fixture']['status']['short']
        if short in ['1H', '2H', 'HT', 'ET', 'P']: status = 'LIVE'
        elif short in ['FT', 'AET', 'PEN']: status = 'FT'
        
        score = "-"
        if m['goals']['home'] is not None:
            score = f"{m['goals']['home']} - {m['goals']['away']}"

        utc_time = datetime.strptime(m['fixture']['date'], "%Y-%m-%dT%H:%M:%S%z")
        time_str = utc_time.strftime("%H:%M")

        output[league].append({
            'id': f_id,
            'teams': f"{m['teams']['home']['name']} vs {m['teams']['away']['name']}",
            'time': time_str,
            'status': status,
            'score': score
        })
    return output

if __name__ == "__main__":
    engine.fetch_data()
    print(f"Engine PRO (Real): {len(engine.matches)} partidos actuales.")
