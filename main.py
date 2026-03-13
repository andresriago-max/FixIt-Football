import os
import json
import math
import requests
import threading
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
LOG_BUFFER = []
def log(msg):
    s = f"[{datetime.now()}] {msg}"
    print(s, flush=True)
    LOG_BUFFER.append(s)
    if len(LOG_BUFFER) > 200:
        LOG_BUFFER.pop(0)

log(">>> CARGANDO MOTOR FIXIT PRO v5 (Diag Mode) <<<")

API_KEY = os.getenv("FOOTBALLDATA_API_KEY") or os.getenv("FOOTBALL_API_KEY")

if not API_KEY:
    print(f"[{datetime.now()}] WARNING: No se detectó FOOTBALLDATA_API_KEY en .env")
else:
    API_KEY = str(API_KEY).strip()
    print(f"[{datetime.now()}] OK: API_KEY detectada (Inicio: {API_KEY[:4]})")

BASE_URL = "https://api.football-data.org/v4"
HEADERS = {
    "X-Auth-Token": API_KEY or "",
    "User-Agent": "FixItFootball/2.0"
}

# Competiciones habilitadas en Football-Data.org plan gratuito
# Código -> nombre display
ENABLED_COMPETITIONS = {
    "PL":  "Premier League",
    "PD":  "La Liga",
    "BL1": "Bundesliga",
    "SA":  "Serie A",
    "FL1": "Ligue 1",
    "PPL": "Liga Portugal",
    "DED": "Eredivisie",
    "CL":  "Champions League",
    "EL":  "Europa League",
    "ELC": "Championship",
    "CLI": "Copa Libertadores",
    "BSA": "Serie A Brasil",
}

# Meta visual por tipo de pick (market_key → label, icon, color)
MARKET_META = {
    "home_win":  ("Victoria Local",     "fa-shield-halved",            "#10b981"),
    "draw":      ("Empate",             "fa-equals",                   "#f59e0b"),
    "away_win":  ("Victoria Visitante", "fa-plane",                    "#6366f1"),
}

# ─────────────────────────────────────────────────────────
#  MOTOR MATEMÁTICO: DISTRIBUCIÓN DE POISSON
# ─────────────────────────────────────────────────────────

def poisson_prob(lam: float, k: int) -> float:
    """P(X = k) para una distribución de Poisson con media lam."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k * math.exp(-lam)) / math.factorial(k)


def calculate_1x2_poisson(lam_home: float, lam_away: float, max_goals: int = 8):
    """
    Calcula las probabilidades de 1 (local), X (empate), 2 (visitante)
    usando distribución de Poisson bivariante independiente.
    Retorna (prob_home, prob_draw, prob_away) como floats 0-1.
    """
    prob_home = prob_draw = prob_away = 0.0
    for i in range(max_goals + 1):
        p_i = poisson_prob(lam_home, i)
        for j in range(max_goals + 1):
            p_j = poisson_prob(lam_away, j)
            joint = p_i * p_j
            if i > j:
                prob_home += joint
            elif i == j:
                prob_draw += joint
            else:
                prob_away += joint
    # Normalizar por si las probabilidades no suman exactamente 1
    total = prob_home + prob_draw + prob_away
    if total > 0:
        prob_home /= total
        prob_draw  /= total
        prob_away  /= total
    return prob_home, prob_draw, prob_away


def build_lambda(team_history: list, is_home_in_fixture: bool) -> tuple:
    """
    Calcula λ_ataque y λ_defensa de un equipo a partir de sus últimos N partidos.
    Retorna (avg_goles_marcados, avg_goles_recibidos).
    """
    if not team_history:
        return 1.2, 1.2  # valores neutros por defecto

    scored_list  = []
    conceded_list = []

    for match in team_history:
        home_team_id = match.get("homeTeam", {}).get("id")
        home_score   = match.get("score", {}).get("fullTime", {}).get("home")
        away_score   = match.get("score", {}).get("fullTime", {}).get("away")

        if home_score is None or away_score is None:
            continue  # partido sin resultado

        if match.get("homeTeam", {}).get("id") == home_team_id:
            # El equipo jugó como local
            scored_list.append(home_score)
            conceded_list.append(away_score)
        else:
            scored_list.append(away_score)
            conceded_list.append(home_score)

    if not scored_list:
        return 1.2, 1.2

    avg_scored   = sum(scored_list)   / len(scored_list)
    avg_conceded = sum(conceded_list) / len(conceded_list)
    return avg_scored, avg_conceded


def compute_lambdas(home_history: list, away_history: list,
                    league_avg_home: float = 1.45,
                    league_avg_away: float = 1.05) -> tuple:
    """
    Calcula los parámetros de Poisson (λ_home, λ_away) según el modelo de
    fuerza de ataque/defensa relativa (simplificado Dixon-Coles).
    """
    home_avg_scored,  home_avg_conceded = build_lambda(home_history, True)
    away_avg_scored,  away_avg_conceded = build_lambda(away_history, False)

    # Fuerza de ataque = promedio marcado del equipo / media de la liga
    home_attack  = home_avg_scored   / league_avg_home if league_avg_home > 0 else 1.0
    away_attack  = away_avg_scored   / league_avg_away if league_avg_away > 0 else 1.0

    # Fuerza defensiva = media de la liga / promedio recibido (si recibe poco → mejor defensa)
    home_defense = league_avg_away   / home_avg_conceded if home_avg_conceded > 0 else 1.0
    away_defense = league_avg_home   / away_avg_conceded if away_avg_conceded > 0 else 1.0

    # λ = ataque propio * defensa rival * media liga (como escala)
    lam_home = home_attack * away_defense * league_avg_home
    lam_away = away_attack * home_defense * league_avg_away

    # Clamp razonable
    lam_home = max(0.3, min(lam_home, 5.0))
    lam_away = max(0.3, min(lam_away, 5.0))
    return lam_home, lam_away


def apply_fatigue(prob: float, last_match_date_str: str, today: datetime) -> float:
    """
    Resta un 5% de probabilidad si el equipo jugó hace menos de 4 días.
    last_match_date_str formato: 'YYYY-MM-DD' o ISO.
    """
    if not last_match_date_str:
        return prob
    try:
        last = datetime.fromisoformat(last_match_date_str[:10])
        days_rest = (today.date() - last.date()).days
        if days_rest < 4:
            prob *= 0.95
    except Exception:
        pass
    return prob


def last_match_date(history: list) -> str:
    """Devuelve la fecha del partido más reciente en el historial."""
    dates = []
    for m in history:
        d = m.get("utcDate", "")
        if d:
            dates.append(d[:10])
    if not dates:
        return ""
    return sorted(dates, reverse=True)[0]


# ─────────────────────────────────────────────────────────
#  CLASE PRINCIPAL
# ─────────────────────────────────────────────────────────
class FixItPRO:
    def __init__(self):
        log("FixItPRO: Iniciando constructor...")
        self.matches: list          = []
        self.cached_picks: list     = []
        self.team_history_cache: dict = {}  # {team_id: [matches]}
        self.last_updated: str      = "Sincronizando Motor PRO..."
        self.is_fetching: bool      = False
        self._lock                  = threading.RLock()
        self.session                = requests.Session()
        self.session.headers.update(HEADERS)
        self.stats_file             = "stats.json"
        self.stats                  = self.load_stats()
        self.cached_picks           = self.stats.get("cached_picks", [])
        # Sanidad de stats
        if not isinstance(self.stats, dict):
            self.stats = {"ganadas": 0, "perdidas": 0, "ligas": {}, "processed_fixtures": [], "historial": [], "cached_picks": [], "team_histories": {}}
        for key in ("ligas", "processed_fixtures", "historial", "cached_picks", "team_histories"):
            if key not in self.stats:
                self.stats[key] = {} if key in ("ligas", "team_histories") else []

    # ── Persistencia ──────────────────────────────────────
    def load_stats(self) -> dict:
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, "r") as f:
                    content = f.read().strip()
                    if content:
                        return json.loads(content)
        except Exception as e:
            print(f"[{datetime.now()}] Warning: No se pudo cargar stats.json ({e})")
        return {"ganadas": 0, "perdidas": 0, "ligas": {}, "processed_fixtures": [], "historial": [], "cached_picks": [], "team_histories": {}}

    def save_stats(self):
        with open(self.stats_file, "w") as f:
            json.dump(self.stats, f, indent=4)

    # ── API: Partidos del día ──────────────────────────────
    def fetch_matches_for_dates(self, date_from: str, date_to: str) -> list:
        """Consulta /v4/matches para un rango de fechas."""
        url = f"{BASE_URL}/matches?dateFrom={date_from}&dateTo={date_to}"
        try:
            resp = self.session.get(url, timeout=20)
            print(f"[{datetime.now()}] /matches → HTTP {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("matches", [])
            else:
                print(f"[{datetime.now()}] Error /matches: {resp.text[:200]}")
        except Exception as e:
            print(f"[{datetime.now()}] Excepción /matches: {e}")
        return []

    # ── API: Historial de un equipo ────────────────────────
    def fetch_team_history(self, team_id: int, limit: int = 5) -> list:
        """Obtiene últimos N resultados de un equipo (con cache en stats.json)."""
        # 1. Verificar Cache
        cache_key = str(team_id)
        if cache_key in self.stats.get("team_histories", {}):
            cached_data = self.stats["team_histories"][cache_key]
            # Validar que tenga suficientes partidos y sea reciente (opcional para simplicidad)
            if len(cached_data) >= limit:
                return cached_data[:limit]

        # 2. Si no hay cache, consultar API
        url = f"{BASE_URL}/teams/{team_id}/matches?status=FINISHED&limit={limit}"
        try:
            print(f"[{datetime.now()}] API: Consultando historial equipo {team_id}...")
            # Respetar rate limit (10 req/min -> 1 cada 6.1s)
            time.sleep(6.1)
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json().get("matches", [])
                # Guardar en cache persistentemente
                with self._lock:
                    if "team_histories" not in self.stats:
                        self.stats["team_histories"] = {}
                    self.stats["team_histories"][cache_key] = data
                return data
            else:
                print(f"[{datetime.now()}] API Error {resp.status_code} en historial {team_id}")
        except Exception as e:
            print(f"[{datetime.now()}] Excepción fetch_team_history: {e}")
        return []

    # ── Coordinador principal ──────────────────────────────
    def fetch_data(self):
        """Coordinador: partidos → historiales → Poisson → picks con valor."""
        log("fetch_data: Intentando entrar...")
        with self._lock:
            if self.is_fetching:
                log("fetch_data: Ya hay un fetch en curso, abortando.")
                return
            self.is_fetching = True
        log("fetch_data: Lock adquirido y bandera is_fetching marcada.")

        try:
            log("fetch_data: Iniciando try block...")
            print(f"[{datetime.now()}] >>> INICIANDO FETCH_DATA v5 <<<", flush=True)
            if not API_KEY:
                with self._lock:
                    self.last_updated = "Error: Falta API_KEY"
                return

            log("fetch_data: Preparando fechas...")
            import pytz
            tz_spain   = pytz.timezone("Europe/Madrid")
            now_spain  = datetime.now(tz_spain)
            hoy_str    = now_spain.strftime("%Y-%m-%d")
            manana_str = (now_spain + timedelta(days=1)).strftime("%Y-%m-%d")
            log(f"fetch_data: Ventana {hoy_str} a {manana_str}")

            with self._lock:
                self.last_updated = "Paso 1/3: Obteniendo partidos..."
            log("fetch_data: Estado actualizado a Paso 1/3")

            # ── Fase 1: Partidos ──────────────────────────
            all_matches = self.fetch_matches_for_dates(hoy_str, manana_str)
            log(f"fetch_data: Recibidos {len(all_matches)} partidos")
            print(f"[{datetime.now()}] Partidos recibidos del API: {len(all_matches)}")
            enabled_comp_codes = set(ENABLED_COMPETITIONS.keys())
            filtered = [
                m for m in all_matches
                if m.get("competition", {}).get("code") in enabled_comp_codes
                and m.get("status") in ("SCHEDULED", "TIMED")
            ]
            log(f"fetch_data: Filtrados {len(filtered)} partidos")
            print(f"[{datetime.now()}] Partidos filtrados por liga/estado: {len(filtered)}")

            with self._lock:
                self.matches = filtered  # Solo los que analizamos para mayor agilidad
                self.last_updated = f"Paso 2/3: Analizando {len(filtered)} partidos..."
            log(f"fetch_data: matches actualizado, estado -> Paso 2/3")

            if not filtered:
                with self._lock:
                    self.last_updated = "Sin partidos PRO programados"
                log("fetch_data: No hay partidos, terminando.")
                return

            # ── Fase 2: Análisis Poisson ──────────────────
            log("fetch_data: Iniciando análisis Poisson...")
            self.cached_picks = [] # Limpìar para nueva carga progresiva
            picks = self._build_poisson_picks(filtered, now_spain)
            
            # ── Fase 3: Resultados ayer ───────────────────
            with self._lock:
                self.cached_picks = picks
                self.stats["cached_picks"] = picks
                self.last_updated = now_spain.strftime("%H:%M")
            log(f"fetch_data: Paso 3/3 terminado, {len(picks)} picks.")

            self.update_stats_from_results()
            self.save_stats()
            log("fetch_data: Stats guardadas, fetch completo.")
            print(f"[{datetime.now()}] >>> FETCH OK: {len(picks)} value-picks <<<")

        except Exception as e:
            log(f"fetch_data: CRITICAL ERROR: {e}")
            with self._lock:
                self.last_updated = f"Error Motor: {str(e)[:20]}"
        finally:
            with self._lock:
                self.is_fetching = False
            log("fetch_data: Salida (is_fetching = False)")

    # ── Motor Poisson + Value Betting ──────────────────────
    def _build_poisson_picks(self, matches: list, now_spain: datetime) -> list:
        """
        Para cada partido:
          1. Obtiene historial de ambos equipos (últimos 10)
          2. Calcula λ con modelo de fuerza relativa
          3. Aplica ajuste de fatiga
          4. Calcula probabilidades 1X2 con Poisson
          5. Filtra por valor: (prob × cuota) - 1 > 0.10
        """
        picks_found = []
        today      = now_spain
        tomorrow   = (now_spain + timedelta(days=1))
        valid_dates = {
            today.strftime("%Y-%m-%d"),
            tomorrow.strftime("%Y-%m-%d"),
        }

        import pytz
        tz_spain = pytz.timezone("Europe/Madrid")

        for m in matches:
            try:
                # ── Datos del partido ──
                fixture_id  = m.get("id")
                home_id     = m.get("homeTeam", {}).get("id")
                away_id     = m.get("awayTeam", {}).get("id")
                home_name   = m.get("homeTeam", {}).get("shortName") or m.get("homeTeam", {}).get("name", "?")
                away_name   = m.get("awayTeam", {}).get("shortName") or m.get("awayTeam", {}).get("name", "?")
                comp_code   = m.get("competition", {}).get("code", "")
                league_name = ENABLED_COMPETITIONS.get(comp_code, m.get("competition", {}).get("name", comp_code))
                utc_date_str = m.get("utcDate", "")

                # ── Fecha / hora en España ──
                utc_dt   = datetime.fromisoformat(utc_date_str.replace("Z", "+00:00"))
                spain_dt = utc_dt.astimezone(tz_spain)
                match_date_str = spain_dt.strftime("%Y-%m-%d")
                time_str = spain_dt.strftime("%H:%M")

                if match_date_str not in valid_dates:
                    continue

                # ── Odds del partido (opcionales: plan gratuito no las incluye) ──
                odds_block = m.get("odds", {})
                # La API gratuita devuelve {"message": "...premium..."} en lugar de cuotas reales
                if isinstance(odds_block, dict) and "message" in odds_block:
                    odd_home = odd_draw = odd_away = None
                else:
                    odd_home = odds_block.get("homeWin")
                    odd_draw = odds_block.get("draw")
                    odd_away = odds_block.get("awayWin")

                has_odds = any(isinstance(o, (int, float)) for o in [odd_home, odd_draw, odd_away])

                # ── Historial de equipos ──
                with self._lock:
                    self.last_updated = f"Analizando: {home_name[:12]} vs {away_name[:12]}"

                home_hist = self.fetch_team_history(home_id, limit=5)
                away_hist = self.fetch_team_history(away_id, limit=5)

                # ── Lambdas Poisson ──
                lam_home, lam_away = compute_lambdas(home_hist, away_hist)

                # ── Ajuste de fatiga ──
                last_home = last_match_date(home_hist)
                last_away = last_match_date(away_hist)
                lam_home = apply_fatigue(lam_home, last_home, today)
                lam_away = apply_fatigue(lam_away, last_away, today)

                # ── Probabilidades 1X2 ──
                prob_h, prob_d, prob_a = calculate_1x2_poisson(lam_home, lam_away)

                # ── Value Betting por mercado ──
                markets_data = [
                    ("home_win", prob_h, odd_home),
                    ("draw",     prob_d, odd_draw),
                    ("away_win", prob_a, odd_away),
                ]

                for market_key, prob, odd in markets_data:
                    market_name, icon, color = MARKET_META[market_key]

                    if has_odds and isinstance(odd, (int, float)) and odd > 1.0:
                        # ── Modo VALUE BETTING: cuota disponible ──
                        value = (prob * float(odd)) - 1.0
                        if value <= 0.10:
                            continue
                        desc = f"λ Local={lam_home:.2f} | λ Visit={lam_away:.2f} | Valor={value:.3f}"
                        odds_val = float(odd)
                    else:
                        # ── Modo POISSON PURO: sin cuota (plan gratuito) ──
                        # Bajamos umbral a 35% para que la app siempre tenga picks relevantes
                        if prob <= 0.35:
                            continue
                        value = prob - 0.35
                        desc  = f"λ Local={lam_home:.2f} | λ Visit={lam_away:.2f} | Confianza Poisson={int(prob*100)}%"
                        odds_val = "PRO"

                    p = {
                        "id":          fixture_id,
                        "teams":       f"{home_name} vs {away_name}",
                        "league":      league_name,
                        "market":      market_name,
                        "description": desc,
                        "prob":        int(prob * 100),
                        "odds":        odds_val,
                        "value":       value,
                        "date":        spain_dt.strftime("%d-%m-%Y"),
                        "time":        time_str,
                        "icon":        icon,
                        "color":       color
                    }
                    picks_found.append(p)
                    # Actualización progresiva para que el usuario vea picks mientras se calculan
                    with self._lock:
                        self.cached_picks.append(p)
                    print(f"[{datetime.now()}] Pick Generado: {home_name} vs {away_name} -> {market_name} ({int(prob*100)}%)")

            except Exception as e:
                import traceback
                print(f"[{datetime.now()}] Error procesando partido {fixture_id}: {e}\n{traceback.format_exc()}")
                continue

        # Ordenar por valor descendente (los mejores primero)
        picks_found.sort(key=lambda x: x.get("value", 0), reverse=True)
        print(f"[{datetime.now()}] Análisis terminado. {len(picks_found)} picks generados.")
        return picks_found

    # ── Scheduler ─────────────────────────────────────────
    def start_scheduler(self):
        """Hilo en segundo plano: actualiza a las 02:00 y 12:00 (hora local)."""
        def run_loop():
            print("Scheduler PRO v2: Iniciado")
            while True:
                now = datetime.now()
                if (now.hour == 2 and now.minute == 0) or (now.hour == 12 and now.minute == 0):
                    self.fetch_data()
                    time.sleep(61)
                time.sleep(30)

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()

    # ── Stats: actualizar desde resultados ────────────────
    def update_stats_from_results(self):
        """Analiza partidos FINISHED para actualizar W/L y el historial."""
        cambios = False
        with self._lock:
            if "processed_fixtures" not in self.stats:
                self.stats["processed_fixtures"] = []
            if "historial" not in self.stats:
                self.stats["historial"] = []

            processed_ids = set(self.stats["processed_fixtures"])

            for m in self.matches:
                f_id   = m.get("id")
                status = m.get("status", "")
                if status != "FINISHED" or f_id in processed_ids:
                    continue

                home_goals = m.get("score", {}).get("fullTime", {}).get("home")
                away_goals = m.get("score", {}).get("fullTime", {}).get("away")
                if home_goals is None or away_goals is None:
                    continue

                comp_code   = m.get("competition", {}).get("code", "")
                league_name = ENABLED_COMPETITIONS.get(comp_code, comp_code)
                home_name   = m.get("homeTeam", {}).get("name", "?")
                away_name   = m.get("awayTeam", {}).get("name", "?")
                teams_str   = f"{home_name} vs {away_name}"
                date_str    = m.get("utcDate", "")[:10]

                if home_goals > away_goals:
                    self.stats["ganadas"] = self.stats.get("ganadas", 0) + 1
                    ligas = self.stats.get("ligas", {})
                    ligas[league_name] = ligas.get(league_name, 0) + 1
                    self.stats["ligas"] = ligas
                    self.stats["historial"].insert(0, {
                        "fecha":     date_str,
                        "equipos":   teams_str,
                        "liga":      league_name,
                        "resultado": f"{home_goals}-{away_goals}",
                        "timestamp": time.time(),
                    })
                    self.stats["historial"] = self.stats["historial"][:50]
                else:
                    self.stats["perdidas"] = self.stats.get("perdidas", 0) + 1

                self.stats["processed_fixtures"].append(f_id)
                processed_ids.add(f_id)
                if len(self.stats["processed_fixtures"]) > 500:
                    self.stats["processed_fixtures"] = self.stats["processed_fixtures"][-500:]
                cambios = True

        if cambios:
            self.save_stats()

    # ── Helpers de consulta ───────────────────────────────
    def get_top_leagues(self) -> list:
        """Retorna las 3 mejores ligas por aciertos."""
        with self._lock:
            sorted_ligas = sorted(self.stats.get("ligas", {}).items(), key=lambda x: x[1], reverse=True)
            return sorted_ligas[:3]


# ─────────────────────────────────────────────────────────
#  INSTANCIA GLOBAL + INICIALIZACIÓN
# ─────────────────────────────────────────────────────────

engine = FixItPRO()


def init_engine():
    """Inicialización única por worker de Gunicorn."""
    print(f"[{datetime.now()}] Intento init_engine...", flush=True)
    with engine._lock:
        if getattr(engine, "_thread_started", False):
            print(f"[{datetime.now()}] init_engine: Ya iniciado en este proceso.", flush=True)
            return
        engine._thread_started = True

        if not engine.matches and "Sincronizando" in engine.last_updated:
            print(f"[{datetime.now()}] >>> LANZANDO MOTOR DE FONDO <<<", flush=True)
            t = threading.Thread(target=engine.fetch_data, daemon=True)
            t.start()
            engine.start_scheduler()
            print(f"[{datetime.now()}] init_engine: Hilo secundario lanzado.", flush=True)


# ─────────────────────────────────────────────────────────
#  API PÚBLICA (usada por app.py sin cambios)
# ─────────────────────────────────────────────────────────

def get_stats() -> dict:
    return engine.stats


def get_top_leagues_rank() -> list:
    return engine.get_top_leagues()


def get_all_money_machine_picks() -> list:
    """Devuelve inmediatamente la caché de picks calculados."""
    return engine.cached_picks


def get_daily_leagues_matches() -> dict:
    """
    Retorna solo los partidos que ya tienen pronósticos PRO para el sidebar.
    Esto hace el proceso mucho más ágil.
    """
    output     = {}
    with engine._lock:
        picks = list(engine.cached_picks)

    if not picks:
        return {}

    for p in picks:
        league = p.get("league", "Otros")
        if league not in output:
            output[league] = []
        
        output[league].append({
            "id":     p.get("id"),
            "teams":  p.get("teams"),
            "time":   p.get("time"),
            "status": "PND", 
            "score":  "-"
        })
    return output


# ─────────────────────────────────────────────────────────
#  MODO MANUAL (python main.py)
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[{datetime.now()}] Motor corriendo en modo manual.")
    engine.fetch_data()
    picks = engine.cached_picks
    print(f"\n=== {len(picks)} VALUE-PICKS GENERADOS ===")
    for p in picks:
        print(f"  [{p['league']}] {p['teams']} → {p['market']} | Cuota: {p['odds']} | Prob: {p['prob']}% | Valor: {p.get('value', 0):.3f}")
