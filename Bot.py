import os
import time
import threading
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import telebot
from flask import Flask

# ============================================================
# BOT DE FUTEBOL - PRE-JOGO + LIVE
# API-FOOTBALL + THE ODDS API
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
APIFOOTBALL_KEY = os.getenv("APIFOOTBALL_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

LIVE_INTERVAL = max(
    45,
    int(os.getenv("LIVE_INTERVAL", "60"))
)

HISTORY_MATCHES = max(
    5,
    min(15, int(os.getenv("HISTORY_MATCHES", "10")))
)

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN não configurado."
    )

if not APIFOOTBALL_KEY:
    raise RuntimeError(
        "APIFOOTBALL_KEY não configurada."
    )

TZ = ZoneInfo("America/Sao_Paulo")

API_FOOTBALL = (
    "https://v3.football.api-sports.io"
)

ODDS_API = (
    "https://api.the-odds-api.com/v4"
)

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML"
)

app = Flask(__name__)

CHAT_ID = None
MONITORANDO = True

CACHE = {}
CACHE_LOCK = threading.Lock()

LAST_ALERT = {}
LAST_PREGAME = {}

# ============================================================
# LIGAS
# ============================================================

LEAGUES = {

    # Campeonatos
    71: "🇧🇷 Brasileirão Série A",
    39: "🏴 Premier League",
    140: "🇪🇸 La Liga",
    78: "🇩🇪 Bundesliga",
    135: "🇮🇹 Serie A",
    61: "🇫🇷 Ligue 1",
    94: "🇵🇹 Primeira Liga",

    # Copas
    72: "🇧🇷 Copa do Brasil",
    45: "🏴 FA Cup",
    143: "🇪🇸 Copa del Rey",
    81: "🇩🇪 DFB Pokal",
    137: "🇮🇹 Coppa Italia",
    66: "🇫🇷 Coupe de France",
    96: "🇵🇹 Taça de Portugal",

    # Continentais
    2: "🏆 Champions League",
    3: "🏆 Europa League",
    848: "🏆 Conference League",
    13: "🏆 Libertadores",
    11: "🏆 Sudamericana",
}

LIVE_STATUSES = {
    "1H",
    "HT",
    "2H",
    "ET",
    "BT",
    "P",
    "LIVE",
    "INT",
}

# ============================================================
# UTILIDADES
# ============================================================

def agora():
    return datetime.now(TZ)


def numero(valor):

    if valor is None:
        return 0.0

    if isinstance(valor, (int, float)):
        return float(valor)

    try:

        texto = str(valor)
        texto = texto.replace("%", "")
        texto = texto.strip()

        if "/" in texto:
            texto = texto.split("/")[0]

        return float(texto)

    except Exception:
        return 0.0


def normalizar(texto):

    texto = str(texto or "").lower()

    texto = unicodedata.normalize(
        "NFD",
        texto
    )

    return "".join(
        c for c in texto
        if unicodedata.category(c) != "Mn"
    ).strip()


def formatar_data(data):

    try:

        dt = datetime.fromisoformat(
            data.replace("Z", "+00:00")
        )

        dt = dt.astimezone(TZ)

        return dt.strftime(
            "%d/%m/%Y %H:%M"
        )

    except Exception:

        return "N/D"


def enviar(chat_id, texto):

    if not chat_id:
        return

    partes = []

    for i in range(
        0,
        len(texto),
        3900
    ):

        partes.append(
            texto[i:i + 3900]
        )

    for parte in partes:

        try:

            bot.send_message(
                chat_id,
                parte
            )

        except Exception as e:

            print(
                "Erro enviando Telegram:",
                e
            )


# ============================================================
# CACHE
# ============================================================

def cache_get(chave):

    with CACHE_LOCK:

        item = CACHE.get(chave)

    if not item:
        return None

    validade, valor = item

    if time.time() >= validade:

        with CACHE_LOCK:
            CACHE.pop(chave, None)

        return None

    return valor


def cache_set(
    chave,
    valor,
    ttl
):

    with CACHE_LOCK:

        CACHE[chave] = (
            time.time() + ttl,
            valor
        )


# ============================================================
# API-FOOTBALL
# ============================================================

def api_football(
    endpoint,
    params=None,
    ttl=45
):

    params = params or {}

    chave = (
        "football|"
        + endpoint
        + "|"
        + str(
            sorted(params.items())
        )
    )

    salvo = cache_get(chave)

    if salvo is not None:
        return salvo

    try:

        resposta = requests.get(

            API_FOOTBALL + endpoint,

            headers={
                "x-apisports-key":
                APIFOOTBALL_KEY
            },

            params=params,

            timeout=20
        )

        if resposta.status_code != 200:

            print(
                "API-Football:",
                resposta.status_code,
                resposta.text[:300]
            )

            return None

        dados = resposta.json()

        cache_set(
            chave,
            dados,
            ttl
        )

        return dados

    except Exception as e:

        print(
            "Erro API-Football:",
            e
        )

        return None


# ============================================================
# THE ODDS API
# ============================================================

def odds_api(
    endpoint,
    params=None,
    ttl=60
):

    if not ODDS_API_KEY:
        return None

    params = dict(
        params or {}
    )

    params["apiKey"] = ODDS_API_KEY

    chave = (
        "odds|"
        + endpoint
        + "|"
        + str(
            sorted(params.items())
        )
    )

    salvo = cache_get(chave)

    if salvo is not None:
        return salvo

    try:

        resposta = requests.get(

            ODDS_API + endpoint,

            params=params,

            timeout=20
        )

        if resposta.status_code != 200:

            print(
                "The Odds API:",
                resposta.status_code,
                resposta.text[:300]
            )

            return None

        dados = resposta.json()

        cache_set(
            chave,
            dados,
            ttl
        )

        return dados

    except Exception as e:

        print(
            "Erro The Odds API:",
            e
        )

        return None


# ============================================================
# DADOS DO JOGO
# ============================================================

def fixture_id(fixture):

    return fixture.get(
        "fixture",
        {}
    ).get(
        "id"
    )


def league_id(fixture):

    return fixture.get(
        "league",
        {}
    ).get(
        "id"
    )


def team_id(
    fixture,
    lado
):

    return fixture.get(
        "teams",
        {}
    ).get(
        lado,
        {}
    ).get(
        "id"
    )


def team_name(
    fixture,
    lado
):

    return fixture.get(
        "teams",
        {}
    ).get(
        lado,
        {}
    ).get(
        "name",
        "N/D"
    )


def fixture_status(fixture):

    return fixture.get(
        "fixture",
        {}
    ).get(
        "status",
        {}
    ).get(
        "short",
        ""
    )


def fixture_minute(fixture):

    return fixture.get(
        "fixture",
        {}
    ).get(
        "status",
        {}
    ).get(
        "elapsed"
    )


def league_name(fixture):

    return LEAGUES.get(
        league_id(fixture),
        fixture.get(
            "league",
            {}
        ).get(
            "name",
            "Liga"
        )
    )


# ============================================================
# JOGOS AO VIVO
# ============================================================

def buscar_jogos_live():

    encontrados = {}

    # --------------------------------------------------------
    # PRIMEIRA BUSCA
    # Todas as partidas ao vivo
    # --------------------------------------------------------

    dados = api_football(
        "/fixtures",
        {
            "live": "all"
        },
        ttl=30
    )

    for fixture in (
        dados or {}
    ).get(
        "response",
        []
    ):

        if (
            league_id(fixture)
            in LEAGUES
            and
            fixture_status(fixture)
            in LIVE_STATUSES
        ):

            encontrados[
                fixture_id(fixture)
            ] = fixture

    # --------------------------------------------------------
    # SEGUNDA BUSCA
    # Cada liga separadamente
    # --------------------------------------------------------

    hoje = agora().strftime(
        "%Y-%m-%d"
    )

    for liga in LEAGUES:

        dados = api_football(

            "/fixtures",

            {
                "league": liga,
                "season": agora().year,
                "date": hoje
            },

            ttl=30
        )

        for fixture in (
            dados or {}
        ).get(
            "response",
            []
        ):

            if (
                fixture_status(fixture)
                in LIVE_STATUSES
            ):

                encontrados[
                    fixture_id(fixture)
                ] = fixture

    jogos = list(
        encontrados.values()
    )

    jogos.sort(
        key=lambda x: (
            league_name(x),
            team_name(x, "home")
        )
    )

    print(
        "Jogos LIVE encontrados:",
        len(jogos)
    )

    return jogos


# ============================================================
# PRÓXIMOS JOGOS
# ============================================================

def buscar_proximos(
    dias=2,
    por_liga=3
):

    inicio = agora().date()

    fim = (
        inicio
        + timedelta(days=dias)
    )

    encontrados = {}

    for liga in LEAGUES:

        dados = api_football(

            "/fixtures",

            {
                "league": liga,
                "season": agora().year,
                "from":
                    inicio.strftime(
                        "%Y-%m-%d"
                    ),
                "to":
                    fim.strftime(
                        "%Y-%m-%d"
                    )
            },

            ttl=120
        )

        contador = 0

        for fixture in (
            dados or {}
        ).get(
            "response",
            []
        ):

            if fixture_status(
                fixture
            ) in {
                "NS",
                "TBD"
            }:

                encontrados[
                    fixture_id(fixture)
                ] = fixture

                contador += 1

                if contador >= por_liga:
                    break

    jogos = list(
        encontrados.values()
    )

    jogos.sort(
        key=lambda x:
            x.get(
                "fixture",
                {}
            ).get(
                "date",
                ""
            )
    )

    return jogos


# ============================================================
# ESTATÍSTICAS DA PARTIDA
# ============================================================

def estatisticas_fixture(
    fid,
    ttl=35
):

    dados = api_football(

        "/fixtures/statistics",

        {
            "fixture": fid
        },

        ttl=ttl
    )

    resultado = {}

    for bloco in (
        dados or {}
    ).get(
        "response",
        []
    ):

        tid = bloco.get(
            "team",
            {}
        ).get(
            "id"
        )

        if not tid:
            continue

        stats = {}

        for item in bloco.get(
            "statistics",
            []
        ):

            stats[
                item.get("type")
            ] = item.get(
                "value"
            )

        resultado[tid] = stats

    return resultado


def stat(
    dados,
    tid,
    *nomes
):

    bloco = dados.get(
        tid,
        {}
    )

    for nome in nomes:

        if nome in bloco:

            return numero(
                bloco[nome]
            )

    return 0.0


# ============================================================
# HISTÓRICO
# ============================================================

def historico_time(
    tid
):

    dados = api_football(

        "/fixtures",

        {
            "team": tid,
            "last": HISTORY_MATCHES
        },

        ttl=900
    )

    return (
        dados or {}
    ).get(
        "response",
        []
    )


def media_ponderada(
    valores
):

    valores = [
        numero(x)
        for x in valores
    ]

    valores = [
        x for x in valores
        if x is not None
    ]

    if not valores:
        return 0.0

    pesos = list(
        range(
            len(valores),
            0,
            -1
        )
    )

    return (
        sum(
            v * p
            for v, p
            in zip(
                valores,
                pesos
            )
        )
        /
        sum(pesos)
    )


def calcular_historico(
    tid
):

    jogos = historico_time(
        tid
    )

    gols_pro = []
    gols_contra = []

    resultados = []

    escanteios = []
    escanteios_contra = []

    finalizacoes = []
    no_alvo = []

    posses = []
    ataques = []

    # --------------------------------------------------------
    # RESULTADOS E GOLS
    # --------------------------------------------------------

    for jogo in jogos:

        casa = (
            team_id(
                jogo,
                "home"
            )
            == tid
        )

        gh = jogo.get(
            "goals",
            {}
        ).get(
            "home"
        )

        ga = jogo.get(
            "goals",
            {}
        ).get(
            "away"
        )

        if gh is None or ga is None:
            continue

        if casa:

            pro = numero(gh)
            contra = numero(ga)

        else:

            pro = numero(ga)
            contra = numero(gh)

        gols_pro.append(pro)
        gols_contra.append(contra)

        if pro > contra:
            resultados.append("V")

        elif pro == contra:
            resultados.append("E")

        else:
            resultados.append("D")

    # --------------------------------------------------------
    # ESTATÍSTICAS DOS ÚLTIMOS 6 JOGOS
    # --------------------------------------------------------

    for jogo in jogos[:6]:

        fid = fixture_id(
            jogo
        )

        dados = estatisticas_fixture(
            fid,
            ttl=3600
        )

        meu = dados.get(
            tid,
            {}
        )

        adversario = (
            team_id(
                jogo,
                "away"
            )
            if team_id(
                jogo,
                "home"
            ) == tid
            else
            team_id(
                jogo,
                "home"
            )
        )

        outro = dados.get(
            adversario,
            {}
        )

        if not meu:
            continue

        escanteios.append(
            numero(
                meu.get(
                    "Corner Kicks"
                )
            )
        )

        escanteios_contra.append(
            numero(
                outro.get(
                    "Corner Kicks"
                )
            )
        )

        finalizacoes.append(
            numero(
                meu.get(
                    "Total Shots"
                )
            )
        )

        no_alvo.append(
            numero(
                meu.get(
                    "Shots on Goal"
                )
            )
        )

        posses.append(
            numero(
                meu.get(
                    "Ball Possession"
                )
            )
        )

        ataques.append(
            numero(
                meu.get(
                    "Dangerous Attacks"
                )
            )
        )

    jogos_validos = len(
        gols_pro
    )

    pontos = (
        resultados.count("V") * 3
        +
        resultados.count("E")
    )

    return {

        "jogos":
            jogos_validos,

        "vitorias":
            resultados.count("V"),

        "empates":
            resultados.count("E"),

        "derrotas":
            resultados.count("D"),

        "forma":
            "".join(
                resultados[:5]
            ),

        "pontos":
            pontos,

        "gols_pro":
            media_ponderada(
                gols_pro
            ),

        "gols_contra":
            media_ponderada(
                gols_contra
            ),

        "escanteios":
            media_ponderada(
                escanteios
            ),

        "escanteios_contra":
            media_ponderada(
                escanteios_contra
            ),

        "finalizacoes":
            media_ponderada(
                finalizacoes
            ),

        "no_alvo":
            media_ponderada(
                no_alvo
            ),

        "posse":
            media_ponderada(
                posses
            ),

        "ataques":
            media_ponderada(
                ataques
            ),

        "amostra_stats":
            len(
                finalizacoes
            )
    }


# ============================================================
# ÍNDICE PRÉ-JOGO
# ============================================================

def indice_pre_jogo(
    historico
):

    jogos = max(
        1,
        historico.get(
            "jogos",
            0
        )
    )

    pontos_por_jogo = (
        historico.get(
            "pontos",
            0
        )
        /
        jogos
    )

    score = 50

    score += (
        pontos_por_jogo
        - 1.5
    ) * 12

    score += (
        historico.get(
            "gols_pro",
            0
        )
        -
        historico.get(
            "gols_contra",
            0
        )
    ) * 10

    score += (
        historico.get(
            "finalizacoes",
            0
        )
        - 10
    ) * 1.2

    score += (
        historico.get(
            "no_alvo",
            0
        )
        - 3
    ) * 2

    return max(
        0,
        min(
            100,
            score
        )
    )


# ============================================================
# PRESSÃO LIVE
# ============================================================

def indice_pressao(
    dados,
    meu,
    adversario
):

    ataques = stat(
        dados,
        meu,
        "Dangerous Attacks"
    )

    ataques_op = stat(
        dados,
        adversario,
        "Dangerous Attacks"
    )

    chutes = stat(
        dados,
        meu,
        "Total Shots"
    )

    chutes_op = stat(
        dados,
        adversario,
        "Total Shots"
    )

    alvo = stat(
        dados,
        meu,
        "Shots on Goal"
    )

    alvo_op = stat(
        dados,
        adversario,
        "Shots on Goal"
    )

    esc = stat(
        dados,
        meu,
        "Corner Kicks"
    )

    esc_op = stat(
        dados,
        adversario,
        "Corner Kicks"
    )

    posse = stat(
        dados,
        meu,
        "Ball Possession"
    )

    posse_op = stat(
        dados,
        adversario,
        "Ball Possession"
    )

    score = 50

    score += (
        ataques
        - ataques_op
    ) * 1.15

    score += (
        chutes
        - chutes_op
    ) * 2

    score += (
        alvo
        - alvo_op
    ) * 3

    score += (
        esc
        - esc_op
    ) * 2

    score += (
        posse
        - posse_op
    ) * 0.55

    return max(
        0,
        min(
            100,
            score
        )
    )


# ============================================================
# ÍNDICE OFENSIVO
# ============================================================

def indice_ofensivo(
    dados,
    tid
):

    chutes = stat(
        dados,
        tid,
        "Total Shots"
    )

    alvo = stat(
        dados,
        tid,
        "Shots on Goal"
    )

    ataques = stat(
        dados,
        tid,
        "Dangerous Attacks"
    )

    escanteios = stat(
        dados,
        tid,
        "Corner Kicks"
    )

    score = (

        chutes * 3

        +

        alvo * 6

        +

        ataques * 0.8

        +

        escanteios * 2

    )

    return max(
        0,
        min(
            100,
            score
        )
    )


# ============================================================
# ANÁLISE LIVE
# ============================================================

def analisar_live(
    fixture
):

    home = team_id(
        fixture,
        "home"
    )

    away = team_id(
        fixture,
        "away"
    )

    if not home or not away:
        return None

    dados = estatisticas_fixture(
        fixture_id(fixture)
    )

    if not dados:
        return None

    hist_home = calcular_historico(
        home
    )

    hist_away = calcular_historico(
        away
    )

    press_home = indice_pressao(
        dados,
        home,
        away
    )

    press_away = indice_pressao(
        dados,
        away,
        home
    )

    ofens_home = indice_ofensivo(
        dados,
        home
    )

    ofens_away = indice_ofensivo(
        dados,
        away
    )

    pre_home = indice_pre_jogo(
        hist_home
    )

    pre_away = indice_pre_jogo(
        hist_away
    )

    return {

        "dados":
            dados,

        "hist_home":
            hist_home,

        "hist_away":
            hist_away,

        "press_home":
            press_home,

        "press_away":
            press_away,

        "ofens_home":
            ofens_home,

        "ofens_away":
            ofens_away,

        "pre_home":
            pre_home,

        "pre_away":
            pre_away,

        "esc_home":
            stat(
                dados,
                home,
                "Corner Kicks"
            ),

        "esc_away":
            stat(
                dados,
                away,
                "Corner Kicks"
            ),

        "chutes_home":
            stat(
                dados,
                home,
                "Total Shots"
            ),

        "chutes_away":
            stat(
                dados,
                away,
                "Total Shots"
            ),

        "alvo_home":
            stat(
                dados,
                home,
                "Shots on Goal"
            ),

        "alvo_away":
            stat(
                dados,
                away,
                "Shots on Goal"
            ),

        "posse_home":
            stat(
                dados,
                home,
                "Ball Possession"
            ),

        "posse_away":
            stat(
                dados,
                away,
                "Ball Possession"
            ),

        "ataques_home":
            stat(
                dados,
                home,
                "Dangerous Attacks"
            ),

        "ataques_away":
            stat(
                dados,
                away,
                "Dangerous Attacks"
            )
    }


# ============================================================
# SINAIS LIVE
# ============================================================

def gerar_sinais(
    analise
):

    sinais = []

    ph = analise[
        "press_home"
    ]

    pa = analise[
        "press_away"
    ]

    if (
        max(ph, pa) >= 68
        and
        abs(ph - pa) >= 10
    ):

        lado = (
            "mandante"
            if ph > pa
            else
            "visitante"
        )

        sinais.append(
            (
                "🔥 PRESSÃO",
                78,
                lado
            )
        )

    total_esc = (
        analise[
            "esc_home"
        ]
        +
        analise[
            "esc_away"
        ]
    )

    if total_esc >= 5:

        sinais.append(
            (
                "🚩 ESCANTEIOS",
                70,
                "volume alto"
            )
        )

    total_chutes = (
        analise[
            "chutes_home"
        ]
        +
        analise[
            "chutes_away"
        ]
    )

    if total_chutes >= 8:

        sinais.append(
            (
                "🎯 FINALIZAÇÕES",
                70,
                "volume ofensivo"
            )
        )

    total_alvo = (
        analise[
            "alvo_home"
        ]
        +
        analise[
            "alvo_away"
        ]
    )

    if total_alvo >= 4:

        sinais.append(
            (
                "🎯 CHUTES NO ALVO",
                72,
                "produção ofensiva"
            )
        )

    ataques_h = analise[
        "ataques_home"
    ]

    ataques_a = analise[
        "ataques_away"
    ]

    if (
        max(
            ataques_h,
            ataques_a
        ) >= 25
        and
        abs(
            ataques_h
            -
            ataques_a
        ) >= 8
    ):

        lado = (
            "mandante"
            if ataques_h > ataques_a
            else
            "visitante"
        )

        sinais.append(
            (
                "🔥 ATAQUES PERIGOSOS",
                72,
                lado
            )
        )

    return sorted(
        sinais,
        key=lambda x: x[1],
        reverse=True
    )


# ============================================================
# TEXTO LIVE
# ============================================================

def texto_live(
    fixture,
    analise=None
):

    if analise is None:

        analise = analisar_live(
            fixture
        )

    if not analise:

        return (
            "❌ Dados LIVE insuficientes "
            "neste momento."
        )

    home = team_name(
        fixture,
        "home"
    )

    away = team_name(
        fixture,
        "away"
    )

    gols_home = (
        fixture.get(
            "goals",
            {}
        ).get(
            "home"
        )
        or 0
    )

    gols_away = (
        fixture.get(
            "goals",
            {}
        ).get(
            "away"
        )
        or 0
    )

    minuto = (
        fixture_minute(
            fixture
        )
        or
        fixture_status(
            fixture
        )
    )

    if (
        analise["press_home"]
        >=
        analise["press_away"]
        + 8
    ):

        dominio = (
            f"🔥 Pressão maior do "
            f"<b>{home}</b>"
        )

    elif (
        analise["press_away"]
        >=
        analise["press_home"]
        + 8
    ):

        dominio = (
            f"🔥 Pressão maior do "
            f"<b>{away}</b>"
        )

    else:

        dominio = (
            "⚖️ Pressão equilibrada"
        )

    texto = f"""

🔴 <b>ANÁLISE LIVE</b>

⚽ <b>{home} {gols_home} x {gols_away} {away}</b>

🏆 {league_name(fixture)}

⏱️ {minuto}'

━━━━━━━━━━━━━━━━━━

{dominio}

━━━━━━━━━━━━━━━━━━

📊 <b>ESTATÍSTICAS LIVE</b>

🎯 Finalizações:
{home}: {analise['chutes_home']:.0f}
{away}: {analise['chutes_away']:.0f}

🎯 No alvo:
{home}: {analise['alvo_home']:.0f}
{away}: {analise['alvo_away']:.0f}

🚩 Escanteios:
{home}: {analise['esc_home']:.0f}
{away}: {analise['esc_away']:.0f}

📊 Posse:
{home}: {analise['posse_home']:.0f}%
{away}: {analise['posse_away']:.0f}%

🔥 Ataques perigosos:
{home}: {analise['ataques_home']:.0f}
{away}: {analise['ataques_away']:.0f}

━━━━━━━━━━━━━━━━━━

📈 <b>ÍNDICES</b>

Pré-jogo:
{home}: {analise['pre_home']:.0f}/100
{away}: {analise['pre_away']:.0f}/100

🔥 Pressão LIVE:
{home}: {analise['press_home']:.0f}/100
{away}: {analise['press_away']:.0f}/100

🎯 Produção ofensiva:
{home}: {analise['ofens_home']:.0f}/100
{away}: {analise['ofens_away']:.0f}/100

━━━━━━━━━━━━━━━━━━

"""

    sinais = gerar_sinais(
        analise
    )

    if sinais:

        texto += (
            "🚨 <b>INDICADORES ATIVOS</b>\n\n"
        )

        for nome, score, detalhe in sinais:

            texto += (
                f"• {nome}\n"
                f"  {detalhe}\n"
                f"  Intensidade: {score}/100\n\n"
            )

    else:

        texto += (
            "🟡 <b>NENHUM INDICADOR FORTE AGORA</b>\n\n"
        )

    texto += (
        "⚠️ Os índices são estatísticos e "
        "não garantem resultado."
    )

    return texto


# ============================================================
# PRÉ-JOGO
# ============================================================

def texto_pre_jogo(
    fixture
):

    home_id = team_id(
        fixture,
        "home"
    )

    away_id = team_id(
        fixture,
        "away"
    )

    home = team_name(
        fixture,
        "home"
    )

    away = team_name(
        fixture,
        "away"
    )

    hist_home = calcular_historico(
        home_id
    )

    hist_away = calcular_historico(
        away_id
    )

    index_home = indice_pre_jogo(
        hist_home
    )

    index_away = indice_pre_jogo(
        hist_away
    )

    if (
        index_home
        >=
        index_away + 8
    ):

        leitura = (
            f"Os indicadores recentes "
            f"apresentam maior volume "
            f"estatístico do <b>{home}</b>."
        )

    elif (
        index_away
        >=
        index_home + 8
    ):

        leitura = (
            f"Os indicadores recentes "
            f"apresentam maior volume "
            f"estatístico do <b>{away}</b>."
        )

    else:

        leitura = (
            "Os indicadores pré-jogo "
            "estão relativamente equilibrados."
        )

    texto = f"""

🟢 <b>ANÁLISE PRÉ-JOGO</b>

⚽ <b>{home} x {away}</b>

🏆 {league_name(fixture)}

🕐 {formatar_data(
    fixture['fixture']['date']
)}

━━━━━━━━━━━━━━━━━━

🏠 <b>{home}</b>

Forma:
<code>{hist_home.get('forma', 'N/D')}</code>

V/E/D:
{hist_home.get('vitorias', 0)}
/
{hist_home.get('empates', 0)}
/
{hist_home.get('derrotas', 0)}

⚽ Gols marcados:
{hist_home.get('gols_pro', 0):.2f}

⚽ Gols sofridos:
{hist_home.get('gols_contra', 0):.2f}

🚩 Escanteios:
{hist_home.get('escanteios', 0):.2f}

🎯 Finalizações:
{hist_home.get('finalizacoes', 0):.2f}

🎯 No alvo:
{hist_home.get('no_alvo', 0):.2f}

📊 Posse:
{hist_home.get('posse', 0):.1f}%

🔥 Ataques perigosos:
{hist_home.get('ataques', 0):.1f}

━━━━━━━━━━━━━━━━━━

✈️ <b>{away}</b>

Forma:
<code>{hist_away.get('forma', 'N/D')}</code>

V/E/D:
{hist_away.get('vitorias', 0)}
/
{hist_away.get('empates', 0)}
/
{hist_away.get('derrotas', 0)}

⚽ Gols marcados:
{hist_away.get('gols_pro', 0):.2f}

⚽ Gols sofridos:
{hist_away.get('gols_contra', 0):.2f}

🚩 Escanteios:
{hist_away.get('escanteios', 0):.2f}

🎯 Finalizações:
{hist_away.get('finalizacoes', 0):.2f}

🎯 No alvo:
{hist_away.get('no_alvo', 0):.2f}

📊 Posse:
{hist_away.get('posse', 0):.1f}%

🔥 Ataques perigosos:
{hist_away.get('ataques', 0):.1f}

━━━━━━━━━━━━━━━━━━

🔎 <b>O QUE ESPERAR</b>

{leitura}

"""

    total_escanteios = (
        hist_home.get(
            "escanteios",
            0
        )
        +
        hist_away.get(
            "escanteios",
            0
        )
    )

    total_finalizacoes = (
        hist_home.get(
            "finalizacoes",
            0
        )
        +
        hist_away.get(
            "finalizacoes",
            0
        )
    )

    if total_escanteios >= 8:

        texto += (
            "🚩 Tendência histórica de "
            "volume de escanteios.\n"
        )

    if total_finalizacoes >= 20:

        texto += (
            "🎯 Tendência histórica de "
            "muitas finalizações.\n"
        )

    if (
        hist_home.get(
            "gols_pro",
            0
        )
        +
        hist_away.get(
            "gols_pro",
            0
        )
        >= 2.5
    ):

        texto += (
            "⚽ Bom volume histórico "
            "de gols marcados.\n"
        )

    texto += f"""

📈 <b>ÍNDICES PRÉ-JOGO</b>

{home}: {index_home:.0f}/100

{away}: {index_away:.0f}/100

⚠️ Estes índices não são probabilidades
e não garantem resultado.

"""

    odds = buscar_odds(
        home,
        away
    )

    if odds:

        texto += (
            "\n"
            +
            formatar_odds(
                odds
            )
        )

    return texto


# ============================================================
# THE ODDS API
# ============================================================

ODDS_SPORTS = [

    "soccer_epl",

    "soccer_spain_la_liga",

    "soccer_germany_bundesliga",

    "soccer_italy_serie_a",

    "soccer_france_ligue_one",

    "soccer_portugal_primeira_liga",

    "soccer_brazil_campeonato",

    "soccer_uefa_champs_league",

    "soccer_uefa_europa_league",

    "soccer_uefa_europa_conference_league",

    "soccer_conmebol_libertadores",

    "soccer_conmebol_sudamericana",
]


def buscar_odds(
    home,
    away
):

    if not ODDS_API_KEY:

        return []

    encontrados = []

    home_norm = normalizar(
        home
    )

    away_norm = normalizar(
        away
    )

    for sport in ODDS_SPORTS:

        dados = odds_api(

            f"/sports/{sport}/odds",

            {
                "regions":
                    "eu",

                "markets":
                    "h2h,totals,btts",

                "oddsFormat":
                    "decimal"
            },

            ttl=60
        )

        for jogo in dados or []:

            h = normalizar(
                jogo.get(
                    "home_team"
                )
            )

            a = normalizar(
                jogo.get(
                    "away_team"
                )
            )

            if (

                (
                    home_norm in h
                    or h in home_norm
                )

                and

                (
                    away_norm in a
                    or a in away_norm
                )

            ):

                encontrados.append(
                    jogo
                )

    return encontrados


def formatar_odds(
    jogos
):

    if not jogos:

        return (
            "📊 <b>ODDS:</b> "
            "não encontradas."
        )

    linhas = [
        "💰 <b>ODDS DISPONÍVEIS</b>"
    ]

    for jogo in jogos[:1]:

        for casa in jogo.get(
            "bookmakers",
            []
        ):

            nome_casa = casa.get(
                "title",
                "Casa"
            )

            for mercado in casa.get(
                "markets",
                []
            ):

                if (
                    mercado.get(
                        "key"
                    )
                    != "h2h"
                ):

                    continue

                for outcome in mercado.get(
                    "outcomes",
                    []
                ):

                    linhas.append(

                        f"• {nome_casa}: "
                        f"{outcome.get('name')} "
                        f"@ "
                        f"{outcome.get('price')}"

                    )

    return "\n".join(
        linhas[:18]
    )


# ============================================================
# COMANDOS TELEGRAM
# ============================================================

@bot.message_handler(
    commands=["start"]
)
def start(message):

    global CHAT_ID
    global MONITORANDO

    CHAT_ID = message.chat.id

    MONITORANDO = True

    bot.send_message(

        message.chat.id,

        """
🤖 <b>BOT DE FUTEBOL ONLINE</b>

🟢 Pré-jogo + retrospecto
🔴 Análise LIVE
📚 Últimos jogos
⚽ Gols
🚩 Escanteios
🎯 Finalizações
📊 Posse
🔥 Ataques perigosos
📈 Pressão
💰 Odds disponíveis

<b>Comandos:</b>

/jogos
/prejogo
/analisar TIME
/status
/ligas
/parar
/continuar

⚠️ O bot é informativo e não realiza apostas.
"""
    )


@bot.message_handler(
    commands=["status"]
)
def status(message):

    bot.send_message(

        message.chat.id,

        f"""
🟢 <b>STATUS DO BOT</b>

Monitoramento:
{'ATIVO' if MONITORANDO else 'PAUSADO'}

⏱️ Atualização:
{LIVE_INTERVAL} segundos

📚 Histórico:
últimos {HISTORY_MATCHES} jogos

🏆 Ligas:
{len(LEAGUES)}

📡 API-Football:
OK

💰 The Odds API:
{'OK' if ODDS_API_KEY else 'não configurada'}
"""
    )


@bot.message_handler(
    commands=["jogos"]
)
def jogos(message):

    bot.send_message(
        message.chat.id,
        "🔎 Procurando jogos ao vivo..."
    )

    try:

        lista = buscar_jogos_live()

        if not lista:

            bot.send_message(

                message.chat.id,

                "🟡 Nenhum jogo ao vivo "
                "das ligas configuradas "
                "foi encontrado agora."
            )

            return

        texto = (
            "🔴 <b>JOGOS AO VIVO</b>\n\n"
        )

        for jogo in lista[:30]:

            gh = (
                jogo.get(
                    "goals",
                    {}
                ).get(
                    "home"
                )
                or 0
            )

            ga = (
                jogo.get(
                    "goals",
                    {}
                ).get(
                    "away"
                )
                or 0
            )

            texto += (

                f"⚽ <b>"
                f"{team_name(jogo, 'home')} "
                f"{gh} x {ga} "
                f"{team_name(jogo, 'away')}"
                f"</b>\n"

                f"🏆 {league_name(jogo)}\n"

                f"⏱️ "
                f"{fixture_minute(jogo) or fixture_status(jogo)}"
                "\n\n"
            )

        enviar(
            message.chat.id,
            texto
        )

    except Exception as e:

        print(
            "Erro /jogos:",
            e
        )

        bot.send_message(
            message.chat.id,
            "❌ Erro ao consultar os jogos."
        )


@bot.message_handler(
    commands=["prejogo"]
)
def prejogo(message):

    bot.send_message(
        message.chat.id,
        "🔎 Procurando próximos jogos..."
    )

    try:

        lista = buscar_proximos(
            dias=2,
            por_liga=2
        )

        if not lista:

            bot.send_message(
                message.chat.id,
                "🟡 Nenhum próximo jogo encontrado."
            )

            return

        # Mostra até 5 análises para não
        # inundar o Telegram.
        for jogo in lista[:5]:

            enviar(
                message.chat.id,
                texto_pre_jogo(jogo)
            )

    except Exception as e:

        print(
            "Erro /prejogo:",
            e
        )

        bot.send_message(
            message.chat.id,
            "❌ Erro ao gerar pré-jogo."
        )


@bot.message_handler(
    commands=["analisar"]
)
def analisar(message):

    partes = message.text.split(
        maxsplit=1
    )

    if len(partes) < 2:

        bot.send_message(

            message.chat.id,

            "Use assim:\n\n"
            "<code>/analisar Botafogo</code>"
        )

        return

    busca = normalizar(
        partes[1]
    )

    bot.send_message(
        message.chat.id,
        "🔎 Procurando esse time..."
    )

    # Primeiro procura LIVE.
    lista_live = buscar_jogos_live()

    for jogo in lista_live:

        if (

            busca in normalizar(
                team_name(
                    jogo,
                    "home"
                )
            )

            or

            busca in normalizar(
                team_name(
                    jogo,
                    "away"
                )
            )

        ):

            enviar(
                message.chat.id,
                texto_live(jogo)
            )

            return

    # Depois procura próximos jogos.
    proximos = buscar_proximos(
        dias=3,
        por_liga=10
    )

    for jogo in proximos:

        if (

            busca in normalizar(
                team_name(
                    jogo,
                    "home"
                )
            )

            or

            busca in normalizar(
                team_name(
                    jogo,
                    "away"
                )
            )

        ):

            enviar(
                message.chat.id,
                texto_pre_jogo(jogo)
            )

            return

    bot.send_message(

        message.chat.id,

        "🟡 Não encontrei esse time "
        "nas ligas configuradas."
    )


@bot.message_handler(
    commands=["ligas"]
)
def ligas(message):

    texto = (
        "🏆 <b>LIGAS MONITORADAS</b>\n\n"
    )

    for codigo, nome in LEAGUES.items():

        texto += (
            f"{codigo} — {nome}\n"
        )

    enviar(
        message.chat.id,
        texto
    )


@bot.message_handler(
    commands=["parar"]
)
def parar(message):

    global MONITORANDO

    MONITORANDO = False

    bot.send_message(

        message.chat.id,

        "⏸️ Monitoramento pausado.\n\n"
        "Use /continuar para ativar."
    )


@bot.message_handler(
    commands=["continuar"]
)
def continuar(message):

    global CHAT_ID
    global MONITORANDO

    CHAT_ID = message.chat.id

    MONITORANDO = True

    bot.send_message(

        message.chat.id,

        "▶️ Monitoramento LIVE ativado."
    )


@bot.message_handler(
    func=lambda message: True
)
def qualquer_mensagem(message):

    bot.send_message(

        message.chat.id,

        "🤖 Use:\n\n"
        "/start\n"
        "/jogos\n"
        "/prejogo\n"
        "/analisar TIME\n"
        "/status\n"
        "/ligas"
    )


# ============================================================
# MONITOR AUTOMÁTICO
# ============================================================

def monitorar():

    global MONITORANDO

    print(
        "Monitor LIVE iniciado."
    )

    while True:

        try:

            if (
                MONITORANDO
                and
                CHAT_ID
            ):

                # --------------------------------------------
                # JOGOS AO VIVO
                # --------------------------------------------

                jogos = buscar_jogos_live()

                for jogo in jogos:

                    try:

                        analise = analisar_live(
                            jogo
                        )

                        if not analise:
                            continue

                        sinais = [
                            s
                            for s in gerar_sinais(
                                analise
                            )
                            if s[1] >= 70
                        ]

                        if not sinais:
                            continue

                        chave = (
                            f"{fixture_id(jogo)}:"
                            f"{sinais[0][0]}"
                        )

                        ultimo = LAST_ALERT.get(
                            chave,
                            0
                        )

                        # 10 minutos entre
                        # alertas do mesmo tipo.
                        if (
                            time.time()
                            -
                            ultimo
                            < 600
                        ):
                            continue

                        LAST_ALERT[
                            chave
                        ] = time.time()

                        enviar(
                            CHAT_ID,
                            texto_live(
                                jogo,
                                analise
                            )
                        )

                    except Exception as e:

                        print(
                            "Erro análise LIVE:",
                            e
                        )

                # --------------------------------------------
                # PRÉ-JOGO AUTOMÁTICO
                # Até 6 horas antes
                # --------------------------------------------

                proximos = buscar_proximos(
                    dias=1,
                    por_liga=1
                )

                for jogo in proximos:

                    try:

                        data = datetime.fromisoformat(

                            jogo[
                                "fixture"
                            ][
                                "date"
                            ].replace(
                                "Z",
                                "+00:00"
                            )

                        ).astimezone(
                            TZ
                        )

                        minutos = (
                            data - agora()
                        ).total_seconds() / 60

                        fid = fixture_id(
                            jogo
                        )

                        if (
                            0
                            <= minutos
                            <= 360
                        ):

                            ultimo = (
                                LAST_PREGAME.get(
                                    fid,
                                    0
                                )
                            )

                            if (
                                time.time()
                                -
                                ultimo
                                >=
                                21600
                            ):

                                LAST_PREGAME[
                                    fid
                                ] = time.time()

                                enviar(

                                    CHAT_ID,

                                    texto_pre_jogo(
                                        jogo
                                    )
                                )

                    except Exception as e:

                        print(
                            "Erro pré-jogo:",
                            e
                        )

        except Exception as e:

            print(
                "Erro no monitor:",
                e
            )

        time.sleep(
            INTERVAL
        )


# ============================================================
# RENDER
# ============================================================

@app.route("/")
def home():

    return (
        "Monitor Live Futebol OK"
    )


@app.route("/health")
def health():

    return {

        "status": "ok",

        "monitorando":
            MONITORANDO,

        "chat_configurado":
            CHAT_ID is not None,

        "hora":
            agora().isoformat()
    }


# ============================================================
# TELEGRAM POLLING
# ============================================================

def iniciar_telegram():

    while True:

        try:

            print(
                "Removendo webhook antigo do Telegram..."
            )

            bot.remove_webhook()

            time.sleep(2)

            print(
                "Webhook removido."
            )

            print(
                "Telegram polling iniciado."
            )

            bot.infinity_polling(

                timeout=30,

                long_polling_timeout=30,

                # Importante:
                # não ignorar mensagens pendentes
                skip_pending=False,

                allowed_updates=[
                    "message"
                ]
            )

        except Exception as e:

            print(
                "Erro Telegram polling:",
                e
            )

            time.sleep(10)


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    monitor_thread = threading.Thread(
        target=monitorar,
        daemon=True
    )

    monitor_thread.start()

    telegram_thread = threading.Thread(
        target=iniciar_telegram,
        daemon=True
    )

    telegram_thread.start()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    print(
        f"Servidor Flask na porta {port}"
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
