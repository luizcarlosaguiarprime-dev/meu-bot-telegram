
import os
import time
import math
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict

import requests
import telebot
from flask import Flask

# ============================================================
# BOT DE MONITORAMENTO LIVE - FUTEBOL
# ============================================================
# Fonte principal: API-Football / API-Sports
#
# Variáveis no Render:
#   BOT_TOKEN=token do Telegram
#   APIFOOTBALL_KEY=chave da API-Football
#
# Opcional:
#   LIVE_INTERVAL=60
#   HISTORY_MATCHES=15
#   ALERT_COOLDOWN=15
#
# O bot NÃO faz apostas. Ele monitora jogos e envia sinais
# estatísticos para análise manual.
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("APIFOOTBALL_KEY")

LIVE_INTERVAL = max(30, int(os.getenv("LIVE_INTERVAL", "60")))
HISTORY_MATCHES = max(5, min(20, int(os.getenv("HISTORY_MATCHES", "15"))))
ALERT_COOLDOWN = max(5, int(os.getenv("ALERT_COOLDOWN", "15")))

TZ = ZoneInfo("America/Sao_Paulo")
API_BASE = "https://v3.football.api-sports.io"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não configurado.")

if not API_KEY:
    raise RuntimeError(
        "APIFOOTBALL_KEY não configurada. "
        "Crie uma chave na API-Football e adicione no Render."
    )

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

CHAT_ID = None
MONITORANDO = True

# Cache para reduzir chamadas à API
CACHE = {}
CACHE_TTL = 45

# Histórico já calculado
HIST_CACHE = {}

# Último alerta por jogo/tipo
LAST_ALERT = {}

# Evita recalcular o mesmo jogo simultaneamente
PROCESSANDO = set()

# IDs das competições prioritárias.
# A temporada é determinada automaticamente pela configuração abaixo.
LEAGUES = {
    # Campeonatos
    71: "Brasileirão Série A",
    39: "Premier League",
    140: "La Liga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    94: "Primeira Liga",

    # Copas nacionais
    72: "Copa do Brasil",
    45: "FA Cup",
    143: "Copa del Rey",
    81: "DFB Pokal",
    137: "Coppa Italia",
    66: "Coupe de France",
    96: "Taça de Portugal",

    # Europa / América do Sul
    2: "Champions League",
    3: "Europa League",
    848: "Conference League",
    13: "Libertadores",
    11: "Sudamericana",
}

# Estados considerados ao vivo
LIVE_STATUSES = {
    "1H", "HT", "2H", "ET", "BT", "P", "LIVE"
}


# ============================================================
# UTILITÁRIOS
# ============================================================

def agora():
    return datetime.now(TZ)


def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def safe_int(v, default=0):
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def normalizar_nome(nome):
    if not nome:
        return ""
    return (
        nome.lower()
        .replace(".", "")
        .replace("-", " ")
        .replace("_", " ")
        .strip()
    )


def cache_get(key):
    item = CACHE.get(key)
    if not item:
        return None

    timestamp, value = item
    if time.time() - timestamp > CACHE_TTL:
        CACHE.pop(key, None)
        return None

    return value


def cache_set(key, value, ttl=None):
    CACHE[key] = (time.time(), value)


def api_get(endpoint, params=None, ttl=CACHE_TTL):
    key = endpoint + "|" + str(sorted((params or {}).items()))
    cached = cache_get(key)

    if cached is not None:
        return cached

    headers = {
        "x-apisports-key": API_KEY
    }

    url = API_BASE + endpoint

    try:
        r = requests.get(
            url,
            headers=headers,
            params=params or {},
            timeout=20
        )

        if r.status_code == 429:
            print("API-Football: limite de requisições atingido.")
            return None

        if r.status_code != 200:
            print("API-Football erro:", r.status_code, r.text[:300])
            return None

        data = r.json()

        if data.get("errors"):
            print("API-Football errors:", data["errors"])
            return None

        response = data.get("response", [])
        cache_set(key, response)

        return response

    except Exception as e:
        print("Erro API:", e)
        return None


def percentual(valor):
    return f"{valor:.0f}%"


def barra(valor, tamanho=10):
    valor = max(0, min(100, valor))
    cheios = round(valor / 100 * tamanho)
    return "█" * cheios + "░" * (tamanho - cheios)


# ============================================================
# TEMPORADA
# ============================================================

def temporada_atual():
    # API-Football normalmente identifica a temporada pelo ano
    # em que a competição começou.
    # Para ligas europeias em setembro, 2026 = temporada 2026.
    # Para Brasileirão, também usamos 2026.
    return agora().year


# ============================================================
# JOGOS AO VIVO
# ============================================================

def obter_jogos_ao_vivo():
    """
    Retorna todos os jogos live disponíveis na API.
    Depois filtramos pelas ligas configuradas.
    """
    dados = api_get("/fixtures", {"live": "all"}, ttl=30)

    if not dados:
        return []

    jogos = []

    for item in dados:
        fixture = item.get("fixture", {})
        league = item.get("league", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        status = fixture.get("status", {})

        league_id = league.get("id")

        if league_id not in LEAGUES:
            continue

        status_short = status.get("short")

        if status_short not in LIVE_STATUSES:
            continue

        jogos.append({
            "id": fixture.get("id"),
            "timestamp": fixture.get("timestamp"),
            "minute": safe_int(status.get("elapsed")),
            "status": status_short,
            "league_id": league_id,
            "league": LEAGUES.get(league_id, league.get("name", "")),
            "home_id": teams.get("home", {}).get("id"),
            "away_id": teams.get("away", {}).get("id"),
            "home": teams.get("home", {}).get("name", "Casa"),
            "away": teams.get("away", {}).get("name", "Fora"),
            "home_goals": safe_int(goals.get("home")),
            "away_goals": safe_int(goals.get("away")),
        })

    return jogos


# ============================================================
# ESTATÍSTICAS LIVE
# ============================================================

def obter_estatisticas_live(fixture_id):
    dados = api_get(
        "/fixtures/statistics",
        {"fixture": fixture_id},
        ttl=35
    )

    if not dados:
        return {}

    resultado = {
        "home": {},
        "away": {}
    }

    for team_block in dados:
        team_id = team_block.get("team", {}).get("id")
        stats = {}

        for s in team_block.get("statistics", []):
            tipo = s.get("type")
            valor = s.get("value")
            stats[tipo] = valor

        # A API retorna os dois times; o primeiro bloco não deve ser
        # presumido como casa, por isso identificamos pelo ID depois.
        resultado.setdefault("raw", []).append({
            "team_id": team_id,
            "stats": stats
        })

    return resultado


def parse_stat_number(valor):
    if valor is None:
        return 0.0

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).strip()

    if "%" in texto:
        try:
            return float(texto.replace("%", "").strip())
        except Exception:
            return 0.0

    # Exemplos: "5", "5/8"
    if "/" in texto:
        try:
            return float(texto.split("/")[0])
        except Exception:
            return 0.0

    try:
        return float(texto)
    except Exception:
        return 0.0


def organizar_stats_live(raw, home_id, away_id):
    resultado = {
        "home": defaultdict(float),
        "away": defaultdict(float)
    }

    for bloco in raw.get("raw", []):
        team_id = bloco.get("team_id")
        stats = bloco.get("stats", {})

        lado = None

        if team_id == home_id:
            lado = "home"
        elif team_id == away_id:
            lado = "away"

        if not lado:
            continue

        for chave, valor in stats.items():
            resultado[lado][chave] = parse_stat_number(valor)

    return resultado


def stat(stats, nome, lado):
    return safe_float(stats.get(lado, {}).get(nome), 0)


# ============================================================
# HISTÓRICO DAS EQUIPES
# ============================================================

def obter_historico_time(team_id):
    """
    Busca os últimos jogos da equipe.
    Não fica preso somente ao campeonato atual:
    pega a sequência recente geral, permitindo cruzar
    desempenho de diferentes competições.
    """
    cache_key = f"history:{team_id}:{HISTORY_MATCHES}"

    if cache_key in HIST_CACHE:
        timestamp, value = HIST_CACHE[cache_key]

        # Histórico dura mais tempo que estatísticas live
        if time.time() - timestamp < 900:
            return value

    dados = api_get(
        "/fixtures",
        {
            "team": team_id,
            "last": HISTORY_MATCHES
        },
        ttl=600
    )

    if not dados:
        return []

    historico = []

    for jogo in dados:
        fixture = jogo.get("fixture", {})
        teams = jogo.get("teams", {})
        goals = jogo.get("goals", {})
        league = jogo.get("league", {})

        home = teams.get("home", {})
        away = teams.get("away", {})

        eh_casa = home.get("id") == team_id

        gols_pro = (
            safe_int(goals.get("home"))
            if eh_casa
            else safe_int(goals.get("away"))
        )

        gols_contra = (
            safe_int(goals.get("away"))
            if eh_casa
            else safe_int(goals.get("home"))
        )

        vencedor = jogo.get("teams", {}).get("home", {}).get("winner")

        if vencedor is True:
            resultado = "V" if eh_casa else "D"
        elif vencedor is False:
            resultado = "D" if eh_casa else "V"
        else:
            resultado = "E"

        historico.append({
            "fixture_id": fixture.get("id"),
            "date": fixture.get("date"),
            "league_id": league.get("id"),
            "league": league.get("name", ""),
            "home": home.get("name", ""),
            "away": away.get("name", ""),
            "casa": eh_casa,
            "gols_pro": gols_pro,
            "gols_contra": gols_contra,
            "resultado": resultado,
        })

    HIST_CACHE[cache_key] = (time.time(), historico)
    return historico


def obter_stats_historico_fixture(fixture_id):
    """
    Estatísticas de uma partida histórica.
    É chamada apenas para um conjunto limitado de partidas,
    evitando excesso de requisições.
    """
    cache_key = f"oldstats:{fixture_id}"

    if cache_key in CACHE:
        timestamp, value = CACHE[cache_key]
        if time.time() - timestamp < 3600:
            return value

    dados = api_get(
        "/fixtures/statistics",
        {"fixture": fixture_id},
        ttl=3600
    )

    if not dados:
        return {}

    saida = []

    for bloco in dados:
        team_id = bloco.get("team", {}).get("id")
        stats = {}

        for s in bloco.get("statistics", []):
            stats[s.get("type")] = parse_stat_number(s.get("value"))

        saida.append({
            "team_id": team_id,
            "stats": stats
        })

    cache_set(cache_key, saida)
    return saida


# ============================================================
# MODELO HISTÓRICO
# ============================================================

def media_ponderada(valores):
    if not valores:
        return 0.0

    # Dá mais peso aos jogos mais recentes.
    # valores devem estar do mais recente para o mais antigo.
    pesos = list(range(len(valores), 0, -1))

    numerador = sum(v * p for v, p in zip(valores, pesos))
    denominador = sum(pesos)

    return numerador / denominador


def calcular_forma(team_id):
    historico = obter_historico_time(team_id)

    if not historico:
        return {
            "jogos": 0,
            "vitorias": 0,
            "empates": 0,
            "derrotas": 0,
            "pontos": 0,
            "gols_pro": 0,
            "gols_contra": 0,
            "media_gols_pro": 0,
            "media_gols_contra": 0,
            "casa_media_gols": 0,
            "fora_media_gols": 0,
            "escanteios_pro": 0,
            "escanteios_contra": 0,
            "finalizacoes": 0,
            "finalizacoes_alvo": 0,
            "ataques_perigosos": 0,
            "recuperacoes": 0,
            "amostra_stats": 0,
        }

    # Mantemos todos os jogos para gols/resultados.
    vitorias = sum(x["resultado"] == "V" for x in historico)
    empates = sum(x["resultado"] == "E" for x in historico)
    derrotas = sum(x["resultado"] == "D" for x in historico)

    pontos = vitorias * 3 + empates

    gols_pro = [x["gols_pro"] for x in historico]
    gols_contra = [x["gols_contra"] for x in historico]

    # Estatísticas detalhadas das últimas partidas.
    # Para não explodir o limite da API, usamos no máximo 8.
    esc_pro = []
    esc_contra = []
    finalizacoes = []
    finalizacoes_alvo = []
    ataques_perigosos = []
    recuperacoes = []

    limite_stats = min(8, len(historico))

    for jogo in historico[:limite_stats]:
        dados = obter_stats_historico_fixture(jogo["fixture_id"])

        if not dados:
            continue

        bloco_time = None
        bloco_oponente = None

        for bloco in dados:
            if bloco.get("team_id") == team_id:
                bloco_time = bloco.get("stats", {})
            else:
                bloco_oponente = bloco.get("stats", {})

        if not bloco_time:
            continue

        esc_pro.append(
            safe_float(bloco_time.get("Corner Kicks"))
        )

        if bloco_oponente:
            esc_contra.append(
                safe_float(bloco_oponente.get("Corner Kicks"))
            )

        finalizacoes.append(
            safe_float(bloco_time.get("Total Shots"))
        )

        finalizacoes_alvo.append(
            safe_float(bloco_time.get("Shots on Goal"))
        )

        # Algumas contas/API não fornecem esses campos.
        # Se existirem, aproveitamos.
        ataques_perigosos.append(
            safe_float(
                bloco_time.get("Dangerous Attacks")
            )
        )

        recuperacoes.append(
            safe_float(
                bloco_time.get("Ball Possession")
            )
        )

    casa_gols = [
        x["gols_pro"] for x in historico
        if x["casa"]
    ]

    fora_gols = [
        x["gols_pro"] for x in historico
        if not x["casa"]
    ]

    return {
        "jogos": len(historico),
        "vitorias": vitorias,
        "empates": empates,
        "derrotas": derrotas,
        "pontos": pontos,
        "gols_pro": sum(gols_pro),
        "gols_contra": sum(gols_contra),
        "media_gols_pro": media_ponderada(gols_pro),
        "media_gols_contra": media_ponderada(gols_contra),
        "casa_media_gols": (
            media_ponderada(casa_gols) if casa_gols else 0
        ),
        "fora_media_gols": (
            media_ponderada(fora_gols) if fora_gols else 0
        ),
        "escanteios_pro": (
            media_ponderada(esc_pro) if esc_pro else 0
        ),
        "escanteios_contra": (
            media_ponderada(esc_contra) if esc_contra else 0
        ),
        "finalizacoes": (
            media_ponderada(finalizacoes) if finalizacoes else 0
        ),
        "finalizacoes_alvo": (
            media_ponderada(finalizacoes_alvo)
            if finalizacoes_alvo else 0
        ),
        "ataques_perigosos": (
            media_ponderada(ataques_perigosos)
            if ataques_perigosos else 0
        ),
        "recuperacoes": (
            media_ponderada(recuperacoes)
            if recuperacoes else 0
        ),
        "amostra_stats": len(finalizacoes),
    }


# ============================================================
# ÍNDICES DO JOGO
# ============================================================

def calcular_pressao(stats, lado):
    """
    Índice de pressão live.
    É um índice interno, não uma probabilidade matemática.
    """
    outro = "away" if lado == "home" else "home"

    ataques = stat(stats, "Dangerous Attacks", lado)
    ataques_op = stat(stats, "Dangerous Attacks", outro)

    chutes = stat(stats, "Total Shots", lado)
    chutes_op = stat(stats, "Total Shots", outro)

    alvo = stat(stats, "Shots on Goal", lado)
    alvo_op = stat(stats, "Shots on Goal", outro)

    esc = stat(stats, "Corner Kicks", lado)
    esc_op = stat(stats, "Corner Kicks", outro)

    posse = stat(stats, "Ball Possession", lado)
    posse_op = stat(stats, "Ball Possession", outro)

    # Se algum fornecedor não disponibilizar "Dangerous Attacks",
    # os demais componentes continuam funcionando.
    ataque_diff = max(-20, min(20, ataques - ataques_op)) * 1.2
    chute_diff = max(-10, min(10, chutes - chutes_op)) * 2.0
    alvo_diff = max(-6, min(6, alvo - alvo_op)) * 3.0
    esc_diff = max(-5, min(5, esc - esc_op)) * 2.0
    posse_diff = max(-30, min(30, posse - posse_op)) * 0.20

    score = 50 + ataque_diff + chute_diff + alvo_diff + esc_diff + posse_diff

    return max(0, min(100, score))


def calcular_indice_ofensivo_live(stats, lado):
    """
    Mede produção ofensiva atual.
    """
    outro = "away" if lado == "home" else "away"

    chutes = stat(stats, "Total Shots", lado)
    alvo = stat(stats, "Shots on Goal", lado)
    ataques = stat(stats, "Dangerous Attacks", lado)
    esc = stat(stats, "Corner Kicks", lado)

    base = (
        chutes * 4.0 +
        alvo * 7.0 +
        ataques * 0.35 +
        esc * 3.0
    )

    return max(0, min(100, base))


def calcular_indice_escanteios(stats, forma_home, forma_away):
    """
    Tendência de escanteios combinando:
      - histórico;
      - escanteios live;
      - pressão atual.
    """
    live_h = stat(stats, "Corner Kicks", "home")
    live_a = stat(stats, "Corner Kicks", "away")

    press_h = calcular_pressao(stats, "home")
    press_a = calcular_pressao(stats, "away")

    historico_h = (
        forma_home["escanteios_pro"] +
        forma_away["escanteios_contra"]
    ) / 2

    historico_a = (
        forma_away["escanteios_pro"] +
        forma_home["escanteios_contra"]
    ) / 2

    tendencia_h = (
        historico_h * 0.45 +
        live_h * 0.30 +
        (press_h / 100 * 8) * 0.25
    )

    tendencia_a = (
        historico_a * 0.45 +
        live_a * 0.30 +
        (press_a / 100 * 8) * 0.25
    )

    return tendencia_h, tendencia_a


def calcular_forca_prejogo(forma):
    """
    Índice baseado na forma histórica.
    """
    jogos = max(1, forma["jogos"])

    pontos_jogo = forma["pontos"] / jogos

    ataque = forma["media_gols_pro"]
    defesa = forma["media_gols_contra"]

    score = (
        50
        + (pontos_jogo - 1.5) * 15
        + (ataque - defesa) * 12
    )

    return max(0, min(100, score))


def calcular_indice_vitoria(jogo, stats, forma_h, forma_a):
    """
    Não é probabilidade de vitória.
    É um índice de domínio/tendência do momento.
    """
    pre_h = calcular_forca_prejogo(forma_h)
    pre_a = calcular_forca_prejogo(forma_a)

    live_h = calcular_pressao(stats, "home")
    live_a = calcular_pressao(stats, "away")

    ofens_h = calcular_indice_ofensivo_live(stats, "home")
    ofens_a = calcular_indice_ofensivo_live(stats, "away")

    # 35% histórico, 45% pressão live, 20% produção ofensiva
    score_h = pre_h * 0.35 + live_h * 0.45 + ofens_h * 0.20
    score_a = pre_a * 0.35 + live_a * 0.45 + ofens_a * 0.20

    return max(0, min(100, score_h)), max(0, min(100, score_a))


def calcular_sinal_recuperacao(stats):
    """
    API-Football nem sempre fornece recuperações.
    Quando o dado não existe, não inventamos.
    Usamos posse/pressão como complemento.
    """
    # Nem todos os provedores têm "Ball Recoveries".
    h = stat(stats, "Ball Recoveries", "home")
    a = stat(stats, "Ball Recoveries", "away")

    if h == 0 and a == 0:
        return None

    return h, a


# ============================================================
# GERAÇÃO DO ALERTA
# ============================================================

def qualidade_sinal(score):
    if score >= 78:
        return "MUITO FORTE"
    if score >= 68:
        return "FORTE"
    if score >= 58:
        return "MODERADO"
    return "BAIXO"


def construir_analise(jogo):
    fixture_id = jogo["id"]

    live_raw = obter_estatisticas_live(fixture_id)

    if not live_raw:
        return None

    stats = organizar_stats_live(
        live_raw,
        jogo["home_id"],
        jogo["away_id"]
    )

    forma_h = calcular_forma(jogo["home_id"])
    forma_a = calcular_forma(jogo["away_id"])

    press_h = calcular_pressao(stats, "home")
    press_a = calcular_pressao(stats, "away")

    forca_h, forca_a = calcular_indice_vitoria(
        jogo,
        stats,
        forma_h,
        forma_a
    )

    esc_h, esc_a = calcular_indice_escanteios(
        stats,
        forma_h,
        forma_a
    )

    recuperacoes = calcular_sinal_recuperacao(stats)

    resultado = {
        "press_h": press_h,
        "press_a": press_a,
        "forca_h": forca_h,
        "forca_a": forca_a,
        "esc_h": esc_h,
        "esc_a": esc_a,
        "recuperacoes": recuperacoes,
        "forma_h": forma_h,
        "forma_a": forma_a,
        "stats": stats,
    }

    return resultado


def gerar_sinais(jogo, analise):
    sinais = []

    stats = analise["stats"]
    ph = analise["press_h"]
    pa = analise["press_a"]
    fh = analise["forca_h"]
    fa = analise["forca_a"]
    eh = analise["esc_h"]
    ea = analise["esc_a"]

    minuto = jogo["minute"]

    # --------------------------------------------------------
    # PRESSÃO
    # --------------------------------------------------------
    diff_press = abs(ph - pa)

    if max(ph, pa) >= 65 and diff_press >= 12:
        lado = "home" if ph > pa else "away"
        nome = jogo["home"] if lado == "home" else jogo["away"]
        score = max(ph, pa)

        sinais.append({
            "tipo": "PRESSAO",
            "lado": lado,
            "nome": nome,
            "score": score,
            "texto": (
                f"🔥 Pressão ofensiva de <b>{nome}</b>\n"
                f"Índice de pressão: {score:.0f}/100"
            )
        })

    # --------------------------------------------------------
    # ESCANTEIOS
    # --------------------------------------------------------
    diff_esc = abs(eh - ea)

    if max(eh, ea) >= 4.0 and diff_esc >= 1.2:
        lado = "home" if eh > ea else "away"
        nome = jogo["home"] if lado == "home" else jogo["away"]
        score = min(100, 50 + diff_esc * 12 + max(ph, pa) * 0.25)

        # Exigimos também algum sinal live para evitar que
        # somente o histórico gere alerta.
        esc_live_h = stat(stats, "Corner Kicks", "home")
        esc_live_a = stat(stats, "Corner Kicks", "away")
        esc_live_diff = abs(esc_live_h - esc_live_a)

        if esc_live_diff >= 1 or max(ph, pa) >= 70:
            sinais.append({
                "tipo": "ESCANTEIOS",
                "lado": lado,
                "nome": nome,
                "score": score,
                "texto": (
                    f"🚩 Tendência de escanteios para "
                    f"<b>{nome}</b>\n"
                    f"Índice: {score:.0f}/100"
                )
            })

    # --------------------------------------------------------
    # DOMÍNIO / TENDÊNCIA DE RESULTADO
    # --------------------------------------------------------
    diff_forca = abs(fh - fa)

    if max(fh, fa) >= 70 and diff_forca >= 12:
        lado = "home" if fh > fa else "away"
        nome = jogo["home"] if lado == "home" else jogo["away"]

        score = max(fh, fa)

        sinais.append({
            "tipo": "DOMINIO",
            "lado": lado,
            "nome": nome,
            "score": score,
            "texto": (
                f"⚽ Domínio/tendência de resultado de "
                f"<b>{nome}</b>\n"
                f"Índice de domínio: {score:.0f}/100"
            )
        })

    # --------------------------------------------------------
    # PRESSÃO + ESCANTEIOS
    # --------------------------------------------------------
    if max(ph, pa) >= 72:
        lado = "home" if ph > pa else "away"
        nome = jogo["home"] if lado == "home" else jogo["away"]

        esc_live = stat(stats, "Corner Kicks", lado)

        if esc_live >= 3:
            sinais.append({
                "tipo": "PRESSAO_ESCANTEIO",
                "lado": lado,
                "nome": nome,
                "score": min(100, max(ph, pa) + 8),
                "texto": (
                    f"🔥🚩 Pressão + escanteios de <b>{nome}</b>\n"
                    f"Pressão: {max(ph, pa):.0f}/100\n"
                    f"Escanteios atuais: {esc_live:.0f}"
                )
            })

    # --------------------------------------------------------
    # GOL / MOMENTO OFENSIVO
    # --------------------------------------------------------
    ch = stat(stats, "Total Shots", "home")
    ca = stat(stats, "Total Shots", "away")

    alh = stat(stats, "Shots on Goal", "home")
    ala = stat(stats, "Shots on Goal", "away")

    if max(ph, pa) >= 72 and (max(alh, ala) >= 3 or max(ch, ca) >= 8):
        lado = "home" if ph > pa else "away"
        nome = jogo["home"] if lado == "home" else jogo["away"]

        sinais.append({
            "tipo": "MOMENTO_GOL",
            "lado": lado,
            "nome": nome,
            "score": min(100, max(ph, pa)),
            "texto": (
                f"🎯 Forte momento ofensivo de <b>{nome}</b>\n"
                f"Pressão: {max(ph, pa):.0f}/100\n"
                f"Finalizações: {max(ch, ca):.0f}\n"
                f"No alvo: {max(alh, ala):.0f}"
            )
        })

    # Ordena pela intensidade, não por EV.
    sinais.sort(key=lambda x: x["score"], reverse=True)

    return sinais


# ============================================================
# RELATÓRIO
# ============================================================

def historico_resumo(forma):
    jogos = forma["jogos"]

    if jogos == 0:
        return "Sem histórico disponível."

    aproveitamento = forma["pontos"] / (jogos * 3) * 100

    return (
        f"Últimos {jogos}: "
        f"{forma['vitorias']}V "
        f"{forma['empates']}E "
        f"{forma['derrotas']}D\n"
        f"⚽ Gols: {forma['media_gols_pro']:.2f} "
        f"marcados / {forma['media_gols_contra']:.2f} sofridos\n"
        f"🚩 Escanteios a favor: "
        f"{forma['escanteios_pro']:.1f}\n"
        f"🎯 Finalizações: "
        f"{forma['finalizacoes']:.1f}\n"
        f"🎯 No alvo: "
        f"{forma['finalizacoes_alvo']:.1f}\n"
        f"📊 Aproveitamento de pontos: "
        f"{aproveitamento:.0f}%"
    )


def montar_mensagem_jogo(jogo, analise, sinais):
    stats = analise["stats"]
    fh = analise["forma_h"]
    fa = analise["forma_a"]

    minuto = jogo["minute"]
    placar = f"{jogo['home_goals']} x {jogo['away_goals']}"

    posse_h = stat(stats, "Ball Possession", "home")
    posse_a = stat(stats, "Ball Possession", "away")

    ch_h = stat(stats, "Total Shots", "home")
    ch_a = stat(stats, "Total Shots", "away")

    alvo_h = stat(stats, "Shots on Goal", "home")
    alvo_a = stat(stats, "Shots on Goal", "away")

    esc_h = stat(stats, "Corner Kicks", "home")
    esc_a = stat(stats, "Corner Kicks", "away")

    ataques_h = stat(stats, "Dangerous Attacks", "home")
    ataques_a = stat(stats, "Dangerous Attacks", "away")

    texto = (
        f"⚽ <b>{jogo['home']} x {jogo['away']}</b>\n"
        f"🏆 {jogo['league']}\n"
        f"⏱️ {minuto}' — <b>{placar}</b>\n\n"

        f"📡 <b>DADOS AO VIVO</b>\n"
        f"🔥 Pressão: {analise['press_h']:.0f} x "
        f"{analise['press_a']:.0f}\n"
        f"🚩 Escanteios: {esc_h:.0f} x {esc_a:.0f}\n"
        f"🎯 Finalizações: {ch_h:.0f} x {ch_a:.0f}\n"
        f"🎯 No alvo: {alvo_h:.0f} x {alvo_a:.0f}\n"
        f"⚡ Ataques perigosos: "
        f"{ataques_h:.0f} x {ataques_a:.0f}\n"
        f"📊 Posse: {posse_h:.0f}% x {posse_a:.0f}%\n\n"

        f"📚 <b>HISTÓRICO RECENTE</b>\n"
        f"🏠 <b>{jogo['home']}</b>\n"
        f"{historico_resumo(fh)}\n\n"
        f"✈️ <b>{jogo['away']}</b>\n"
        f"{historico_resumo(fa)}\n\n"
    )

    if sinais:
        texto += "🚨 <b>SINAIS DETECTADOS</b>\n\n"

        for i, sinal in enumerate(sinais[:4], 1):
            texto += (
                f"{i}. {sinal['texto']}\n"
                f"📊 Intensidade: "
                f"{qualidade_sinal(sinal['score'])}\n"
                f"Índice: {sinal['score']:.0f}/100\n\n"
            )
    else:
        texto += (
            "🟡 <b>Nenhum sinal forte neste momento.</b>\n"
            "O jogo continua sendo monitorado.\n\n"
        )

    texto += (
        "⚠️ <i>Os índices são estimativas estatísticas para "
        "auxiliar a análise ao vivo. Não são garantia de resultado "
        "ou de lucro. Faça qualquer decisão manualmente.</i>"
    )

    return texto


# ============================================================
# CONTROLE DE ALERTAS
# ============================================================

def chave_alerta(fixture_id, sinal):
    return f"{fixture_id}:{sinal['tipo']}:{sinal['lado']}"


def pode_alertar(chave):
    ultimo = LAST_ALERT.get(chave)

    if ultimo is None:
        return True

    return time.time() - ultimo >= ALERT_COOLDOWN * 60


def registrar_alerta(chave):
    LAST_ALERT[chave] = time.time()


# ============================================================
# MONITORAMENTO
# ============================================================

def monitorar():
    global MONITORANDO

    print("Monitor live iniciado.")

    while True:
        try:
            if not MONITORANDO or CHAT_ID is None:
                time.sleep(15)
                continue

            jogos = obter_jogos_ao_vivo()

            if jogos:
                print(f"Jogos live encontrados: {len(jogos)}")

            for jogo in jogos:
                fixture_id = jogo["id"]

                if not fixture_id:
                    continue

                if fixture_id in PROCESSANDO:
                    continue

                PROCESSANDO.add(fixture_id)

                try:
                    analise = construir_analise(jogo)

                    if not analise:
                        continue

                    sinais = gerar_sinais(jogo, analise)

                    # Envia somente sinais relevantes.
                    sinais_fortes = [
                        s for s in sinais
                        if s["score"] >= 68
                    ]

                    for sinal in sinais_fortes:
                        chave = chave_alerta(fixture_id, sinal)

                        if not pode_alertar(chave):
                            continue

                        # Para não bombardear o Telegram:
                        # enviamos uma análise completa.
                        msg = montar_mensagem_jogo(
                            jogo,
                            analise,
                            sinais
                        )

                        try:
                            bot.send_message(
                                CHAT_ID,
                                msg
                            )
                            registrar_alerta(chave)

                        except Exception as e:
                            print("Erro enviando Telegram:", e)

                        # Apenas um alerta por ciclo por partida.
                        break

                finally:
                    PROCESSANDO.discard(fixture_id)

            time.sleep(LIVE_INTERVAL)

        except Exception as e:
            print("Erro no monitor:", e)
            time.sleep(30)


# ============================================================
# TELEGRAM
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):
    global CHAT_ID, MONITORANDO

    CHAT_ID = message.chat.id
    MONITORANDO = True

    bot.send_message(
        message.chat.id,
        "🤖 <b>Monitor Live iniciado!</b>\n\n"
        "Vou acompanhar os jogos das ligas configuradas "
        "e procurar sinais estatísticos de:\n\n"
        "🔥 pressão ofensiva\n"
        "🚩 tendência de escanteios\n"
        "⚽ domínio/tendência de resultado\n"
        "🎯 momento ofensivo\n"
        "📚 histórico recente das duas equipes\n\n"
        f"⏱️ Atualização: a cada {LIVE_INTERVAL} segundos.\n\n"
        "Use /jogos para ver partidas ao vivo.\n"
        "Use /status para verificar o monitor."
    )


@bot.message_handler(commands=["status"])
def status(message):
    bot.send_message(
        message.chat.id,
        "🟢 <b>Monitor operacional</b>\n\n"
        f"⏱️ Intervalo: {LIVE_INTERVAL}s\n"
        f"📚 Histórico: últimos {HISTORY_MATCHES} jogos\n"
        f"🏆 Competições configuradas: {len(LEAGUES)}\n"
        f"📡 Monitoramento: {'ATIVO' if MONITORANDO else 'PAUSADO'}"
    )


@bot.message_handler(commands=["parar"])
def parar(message):
    global MONITORANDO
    MONITORANDO = False

    bot.send_message(
        message.chat.id,
        "⏸️ Monitoramento pausado.\n"
        "Use /continuar para ativar novamente."
    )


@bot.message_handler(commands=["continuar"])
def continuar(message):
    global CHAT_ID, MONITORANDO

    CHAT_ID = message.chat.id
    MONITORANDO = True

    bot.send_message(
        message.chat.id,
        "▶️ Monitoramento ao vivo ativado novamente."
    )


@bot.message_handler(commands=["jogos"])
def jogos(message):
    try:
        lista = obter_jogos_ao_vivo()

        if not lista:
            bot.send_message(
                message.chat.id,
                "🟡 Nenhum jogo ao vivo das ligas configuradas "
                "foi encontrado agora."
            )
            return

        texto = "📡 <b>JOGOS AO VIVO</b>\n\n"

        for jogo in lista[:30]:
            texto += (
                f"⚽ {jogo['home']} x {jogo['away']}\n"
                f"🏆 {jogo['league']}\n"
                f"⏱️ {jogo['minute']}' "
                f"| {jogo['home_goals']} x {jogo['away_goals']}\n\n"
            )

        bot.send_message(message.chat.id, texto)

    except Exception as e:
        bot.send_message(
            message.chat.id,
            "❌ Não foi possível consultar os jogos agora."
        )
        print("Erro /jogos:", e)


@bot.message_handler(commands=["analisar"])
def analisar(message):
    """
    /analisar NOME
    Mostra jogos live procurando o nome informado.
    """
    partes = message.text.split(maxsplit=1)

    if len(partes) < 2:
        bot.send_message(
            message.chat.id,
            "Use assim:\n"
            "<code>/analisar Palmeiras</code>"
        )
        return

    busca = normalizar_nome(partes[1])

    jogos_live = obter_jogos_ao_vivo()

    encontrados = [
        j for j in jogos_live
        if busca in normalizar_nome(j["home"])
        or busca in normalizar_nome(j["away"])
    ]

    if not encontrados:
        bot.send_message(
            message.chat.id,
            "🟡 Não encontrei esse time entre os jogos "
            "ao vivo monitorados."
        )
        return

    for jogo in encontrados:
        analise = construir_analise(jogo)

        if not analise:
            continue

        sinais = gerar_sinais(jogo, analise)

        bot.send_message(
            message.chat.id,
            montar_mensagem_jogo(jogo, analise, sinais)
        )


@bot.message_handler(commands=["ligas"])
def ligas(message):
    texto = "🏆 <b>LIGAS MONITORADAS</b>\n\n"

    for nome in LEAGUES.values():
        texto += f"• {nome}\n"

    bot.send_message(message.chat.id, texto)


# ============================================================
# HEALTH CHECK PARA RENDER
# ============================================================

@app.route("/")
def home():
    return "Monitor Live Futebol OK"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "monitorando": MONITORANDO,
        "chat_configurado": CHAT_ID is not None,
        "timestamp": agora().isoformat()
    }


# ============================================================
# INICIALIZAÇÃO
# ============================================================

def iniciar_monitor():
    thread = threading.Thread(
        target=monitorar,
        daemon=True
    )
    thread.start()


def iniciar_telegram():
    try:
        print("Telegram polling iniciado.")
        bot.infinity_polling(
            timeout=30,
            long_polling_timeout=30,
            skip_pending=True
        )
    except Exception as e:
        print("Erro no Telegram polling:", e)


if __name__ == "__main__":
    iniciar_monitor()

    telegram_thread = threading.Thread(
        target=iniciar_telegram,
        daemon=True
    )
    telegram_thread.start()

    # Render fornece PORT.
    port = int(os.environ.get("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port
    )
