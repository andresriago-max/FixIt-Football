import os
import json
import requests
import threading
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
print(f"[{datetime.now()}] >>> CARGANDO MOTOR FIXIT PRO <<<")
API_KEY = os.getenv("FOOTBALL_API_KEY")

if not API_KEY:
    print(f"[{datetime.now()}] ⚠️ WARNING: FOOTBALL_API_KEY no detectada en .env ni variables de entorno.")
else:
    API_KEY = str(API_KEY).strip()
    k_preview = API_KEY[:4] if len(API_KEY) >= 4 else "****"
    print(f"[{datetime.now()}] ✅ API_KEY detectada (Inicio: {k_preview})")

# Configuración Global
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    'x-apisports-key': API_KEY,
    'x-rapidapi-host': 'v3.football.api-sports.io',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
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
    3: 'Europa League',
    179: 'Liga Argentina',
    144: 'Premiership Escocia'
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
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.stats_file = "stats.json"
        self.stats = self.load_stats()
        # Asegurar que stats tiene la estructura correcta
        if not isinstance(self.stats, dict): self.stats = {"ganadas": 0, "perdidas": 0, "ligas": {}}
        if 'ligas' not in self.stats or not isinstance(self.stats['ligas'], dict): self.stats['ligas'] = {}

    def load_stats(self):
        if os.path.exists(self.stats_file):
            with open(self.stats_file, 'r') as f:
                return json.load(f)
        return {"ganadas": 0, "perdidas": 0, "ligas": {}}

    def save_stats(self):
        with open(self.stats_file, 'w') as f:
            json.dump(self.stats, f, indent=4)

    def fetch_data(self):
        """Coordinador: partidos -> cuotas -> predicciones -> picks."""
        try:
            print(f"[{datetime.now()}] Iniciando fetch_data...")
            if not API_KEY:
                with self._lock: self.last_updated = "Error: Falta API KEY"
                return

            tz_spain = timezone(timedelta(hours=1))
            hoy_str = datetime.now(tz_spain).strftime("%Y-%m-%d")
            manana_str = (datetime.now(tz_spain) + timedelta(days=1)).strftime("%Y-%m-%d")

            # Fase 1: Partidos
            with self._lock: self.last_updated = "Paso 1/4: Buscando partidos..."
            all_fixtures = []
            for d in [hoy_str, manana_str]:
                url = f"{BASE_URL}/fixtures?date={d}"
                print(f"[{datetime.now()}] Consultando: {url}")
                try:
                    resp = self.session.get(url, timeout=10)
                    if resp.status_code == 200:
                        all_fixtures.extend(resp.json().get('response', []))
                except Exception as e:
                    print(f"Error cargando partidos para {d}: {e}")

            processed = [f for f in all_fixtures if f['league']['id'] in ENABLED_LEAGUES]
            with self._lock:
                self.matches = processed
            
            if not processed:
                with self._lock: self.last_updated = "No hay partidos hoy/mañana"
                return

            # Fase 2: Cuotas
            with self._lock: self.last_updated = f"Paso 2/4: Cargando cuotas ({len(processed)} partidos)..."
            # Obtenemos cuotas para hoy y mañana
            self.fetch_odds(hoy_str)
            self.fetch_odds(manana_str)

            # Fase 3: Predicciones
            with self._lock: self.last_updated = f"Paso 3/4: Analizando mercados..."
            fixture_ids = [m['fixture']['id'] for m in processed]
            self.fetch_predictions(fixture_ids)

            # Fase 4: Picks Finales
            with self._lock: self.last_updated = "Paso 4/4: Seleccionando mejores picks..."
            self.cached_picks = self.process_top_8()
            self.update_stats_from_results()

            with self._lock:
                self.last_updated = datetime.now(tz_spain).strftime("%H:%M")
            print(f"[{datetime.now()}] >>> FETCH COMPLETADO OK <<<")

        except Exception as e:
            print(f"[{datetime.now()}] ERROR CRÍTICO EN FETCH: {e}")
            with self._lock:
                self.last_updated = f"Err: {str(e)[:15]}"


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
        """Obtiene cuotas para múltiples mercados por partido."""
        try:
            url = f"{BASE_URL}/odds?date={date}"
            response = requests.get(url, headers=HEADERS, timeout=15)
            if response.status_code == 200:
                data = response.json()
                odds_data = data.get('response', [])
                new_odds = {}  # {fixture_id: {market_key: odd_float}}

                # Mercados que nos interesan:
                # 1 = Match Winner, 2 = Double Chance, 5 = Goals Over/Under, 8 = Both Teams Score
                TARGET_BETS = {1, 2, 5, 8}

                for item in odds_data:
                    f_id = item['fixture']['id']
                    bookmakers = item.get('bookmakers', [])
                    if not bookmakers: continue

                    # Preferencia Bet365 (id:8) o el primero disponible
                    bm = next((b for b in bookmakers if b['id'] == 8), bookmakers[0])
                    markets = {}

                    for bet in bm.get('bets', []):
                        bid = bet['id']
                        if bid not in TARGET_BETS: continue
                        for val in bet.get('values', []):
                            odd = float(val['odd'])
                            v = val['value']
                            if bid == 1 and v == 'Home':
                                markets['home_win'] = odd
                            elif bid == 1 and v == 'Away':
                                markets['away_win'] = odd
                            elif bid == 2 and v == '1X':   # Local no pierde
                                markets['double_1x'] = odd
                            elif bid == 5 and v == 'Over 2.5':
                                markets['over25'] = odd
                            elif bid == 8 and v == 'Yes':  # Ambos marcan
                                markets['btts'] = odd

                    if markets:
                        new_odds[f_id] = markets

                with self._lock:
                    self.fixtures_odds = new_odds
        except Exception as e:
            print(f"Error fetching odds: {e}")

    def fetch_predictions(self, fixtures_ids):
        """Obtiene predicciones nativas (Home %) y consejos para los partidos clave."""
        new_preds = {}
        new_advice = {}
        count = 0
        for f_id in fixtures_ids[:60]:
            try:
                count += 1
                if count % 10 == 0: print(f"[{datetime.now()}] Procesando prediccion {count}/60...")
                url = f"{BASE_URL}/predictions?fixture={f_id}"
                response = self.session.get(url, timeout=7)
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
        """Selecciona el mejor mercado por partido con probabilidad >=40%."""
        picks = []
        seen_fixtures = set()  # evitar duplicar el mismo partido
        with self._lock:
            matches_snapshot = list(self.matches)
            odds_snapshot = dict(self.fixtures_odds)
            preds_snapshot = dict(self.fixtures_predictions)
            advice_snapshot = dict(self.fixtures_advice)

        # Configuración de mercados: (market_key, label, icon, color)
        MARKET_META = {
            'home_win':   ('Victoria Local',    'fa-shield-halved', '#10b981'),
            'away_win':   ('Victoria Visitante', 'fa-plane',         '#6366f1'),
            'double_1x':  ('Doble Oportunidad', 'fa-arrows-split-up-and-left', '#f59e0b'),
            'btts':       ('Ambos Marcan',       'fa-futbol',        '#ec4899'),
            'over25':     ('Más de 2.5 Goles',   'fa-fire',          '#ef4444'),
        }

        # España CET
        tz_spain = timezone(timedelta(hours=1))
        now_spain = datetime.now(tz_spain)
        today_str = (now_spain + timedelta(days=1)).strftime("%Y-%m-%d")  # mañana
        tomorrow_str = (now_spain + timedelta(days=2)).strftime("%Y-%m-%d")

        for m in matches_snapshot:
            f_id = m['fixture']['id']

            # Filtro de horario
            utc_time = datetime.strptime(m['fixture']['date'], "%Y-%m-%dT%H:%M:%S%z")
            match_spain = utc_time.astimezone(tz_spain)
            match_date = match_spain.strftime("%Y-%m-%d")
            match_hour = match_spain.hour
            is_valid_time = (match_date == today_str) or (match_date == tomorrow_str and match_hour < 7)
            if not is_valid_time: continue

            advice = advice_snapshot.get(f_id, "Datos Estadísticos Oficiales")
            league = ENABLED_LEAGUES.get(m['league']['id'], m['league']['name'])
            home = m['teams']['home']['name']
            away = m['teams']['away']['name']
            time_str = match_spain.strftime("%H:%M")
            markets_for_fixture = odds_snapshot.get(f_id, {})

            # Evaluar todos los mercados disponibles para este partido
            candidates = []

            for market_key, (label, icon, color) in MARKET_META.items():
                odd = markets_for_fixture.get(market_key)
                native = preds_snapshot.get(f_id) if market_key == 'home_win' else None

                # Necesitamos al menos cuota o predicción nativa
                if not odd and not native: continue

                # Probabilidad: nativa si disponible (home_win), si no 1/cuota
                if market_key == 'home_win' and native:
                    prob = native
                elif odd and odd > 0:
                    prob = int(round(100 / odd))
                else:
                    continue  # sin datos suficientes

                if prob < 40: continue  # Umbral mínimo de confianza

                candidates.append({
                    'id': f_id,
                    'teams': f"{home} vs {away}",
                    'league': league,
                    'market': label,
                    'description': advice,
                    'prob': prob,
                    'odds': odd,
                    'date': match_spain.strftime("%d-%m-%Y"),
                    'time': time_str,
                    'icon': icon,
                    'color': color,
                })

            if not candidates: continue

            # Elegir el mercado con mayor probabilidad para este partido
            best = max(candidates, key=lambda x: x['prob'])

            if f_id not in seen_fixtures:
                picks.append(best)
                seen_fixtures.add(f_id)

            if len(picks) >= 20: break

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
                        self.stats['ganadas'] = self.stats.get('ganadas', 0) + 1
                        
                        ligas = self.stats.get('ligas', {})
                        if not isinstance(ligas, dict): ligas = {}
                        
                        ligas[league_name] = ligas.get(league_name, 0) + 1
                        self.stats['ligas'] = ligas
                        cambios = True
                    else:
                        self.stats['perdidas'] = self.stats.get('perdidas', 0) + 1
                        cambios = True
        
        if cambios:
            self.save_stats()

    def get_top_leagues(self):
        """Retorna las 3 mejores ligas por aciertos."""
        with self._lock:
            sorted_ligas = sorted(self.stats['ligas'].items(), key=lambda x: x[1], reverse=True)
            return sorted_ligas[:3]

engine = FixItPRO()

def init_engine():
    """Inicialización única del motor. Seguro para múltiples workers."""
    with engine._lock:
        if not engine.matches and "Iniciando" in engine.last_updated:
            print(f"[{datetime.now()}] >>> INICIALIZANDO SOLICITUD DE DATOS INICIAL <<<")
            threading.Thread(target=engine.fetch_data, daemon=True).start()
            engine.start_scheduler()

# Iniciar motor al importar el módulo
init_engine()

if __name__ == "__main__":
    print(f"[{datetime.now()}] Motor corriendo en modo manual.")
    time.sleep(5)

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
