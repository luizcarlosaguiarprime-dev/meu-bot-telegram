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
# CONFIGURAÇÃO
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
APIFOOTBALL_KEY = os.getenv("APIFOOTBALL_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

TZ = ZoneInfo("America/Sao_Paulo")

# Atualização LIVE
LIVE_INTERVAL = max(
    45,
    int(os.getenv("LIVE_INTERVAL", "60"))
)

# Últimos jogos usados no histórico
HISTORY_MATCHES = max(
    5,
    min(
        10,
        int(
            os.getenv(
                "HISTORY_MATCHES",
                "10"
            )
        )
    )
)

# Intervalo entre alertas
ALERT_COOLDOWN = max(
    5,
    int(
        os.getenv(
            "ALERT_COOLDOWN",
            "10"
        )
    )
) * 60


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN não configurado."
    )

if not APIFOOTBALL_KEY:
    raise RuntimeError(
        "APIFOOTBALL_KEY não configurada."
    )


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


# ============================================================
# ESTADO
# ============================================================

CHAT_ID = None

MONITORANDO = True

# Jogos que devem ser acompanhados
JOGOS_MONITORADOS = {}

# Último alerta enviado por jogo/tipo
ULTIMOS_ALERTAS = {}

# Cache
CACHE = {}
CACHE_LOCK = threading.Lock()


# ============================================================
# LIGAS
# ============================================================

LEAGUES = {

    71: "🇧🇷 Brasileirão Série A",

    39: "🏴 Premier League",

    140: "🇪🇸 La Liga",

    78: "🇩🇪 Bundesliga",

    135: "🇮🇹 Serie A",

    61: "🇫🇷 Ligue 1",

    94: "🇵🇹 Primeira Liga",

    72: "🇧🇷 Copa do Brasil",

    45: "🏴 FA Cup",

    143: "🇪🇸 Copa del Rey",

    81: "🇩🇪 DFB Pokal",

    137: "🇮🇹 Coppa Italia",

    66: "🇫🇷 Coupe de France",

    96: "🇵🇹 Taça de Portugal",

    2: "🏆 Champions League",

    3: "🏆 Europa League",

    848: "🏆 Conference League",

    13: "🏆 Libertadores",

    11: "🏆 Sudamericana",
}


# Status considerados LIVE
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


# Status considerados pré-jogo
PRE_STATUSES = {
    "NS",
    "TBD",
}


# ============================================================
# FUNÇÕES BÁSICAS
# ============================================================

def agora():

    return datetime.now(TZ)


def normalizar(texto):

    texto = str(
        texto or ""
    ).lower()

    texto = unicodedata.normalize(
        "NFD",
        texto
    )

    return "".join(
        c
        for c in texto
        if unicodedata.category(c)
        != "Mn"
    ).strip()


def numero(valor):

    if valor is None:
        return 0.0

    if isinstance(
        valor,
        (int, float)
    ):
        return float(valor)

    try:

        texto = str(valor)

        texto = (
            texto
            .replace("%", "")
            .strip()
        )

        return float(texto)

    except Exception:

        return 0.0


def formatar_data(data):

    try:

        dt = datetime.fromisoformat(
            data.replace(
                "Z",
                "+00:00"
            )
        )

        dt = dt.astimezone(TZ)

        return dt.strftime(
            "%d/%m/%Y %H:%M"
        )

    except Exception:

        return "N/D"


def enviar(
    chat_id,
    texto
):

    if not chat_id:
        return

    # Telegram aceita mensagens menores
    # que 4096 caracteres.
    partes = []

    for inicio in range(
        0,
        len(texto),
        3900
    ):

        partes.append(
            texto[
                inicio:inicio + 3900
            ]
        )

    for parte in partes:

        try:

            bot.send_message(
                chat_id,
                parte
            )

        except Exception as e:

            print(
                "Erro Telegram:",
                e
            )


# ============================================================
# CACHE
# ============================================================

def cache_get(chave):

    with CACHE_LOCK:

        item = CACHE.get(
            chave
        )

    if not item:
        return None

    validade, valor = item

    if time.time() >= validade:

        with CACHE_LOCK:

            CACHE.pop(
                chave,
                None
            )

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
    ttl=60
):

    params = params or {}

    chave = (
        endpoint
        + "|"
        + str(
            sorted(
                params.items()
            )
        )
    )

    salvo = cache_get(
        chave
    )

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

            timeout=25
        )

        if resposta.status_code != 200:

            print(
                "API-Football erro:",
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

    params[
        "apiKey"
    ] = ODDS_API_KEY

    chave = (
        "ODDS|"
        + endpoint
        + "|"
        + str(
            sorted(
                params.items()
            )
        )
    )

    salvo = cache_get(
        chave
    )

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
                "Odds API erro:",
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
# INFORMAÇÕES DO JOGO
# ============================================================

def fixture_id(
    jogo
):

    return jogo.get(
        "fixture",
        {}
    ).get(
        "id"
    )


def league_id(
    jogo
):

    return jogo.get(
        "league",
        {}
    ).get(
        "id"
    )


def status_jogo(
    jogo
):

    return jogo.get(
        "fixture",
        {}
    ).get(
        "status",
        {}
    ).get(
        "short",
        ""
    )


def minuto_jogo(
    jogo
):

    return jogo.get(
        "fixture",
        {}
    ).get(
        "status",
        {}
    ).get(
        "elapsed"
    )


def nome_time(
    jogo,
    lado
):

    return jogo.get(
        "teams",
        {}
    ).get(
        lado,
        {}
    ).get(
        "name",
        "N/D"
    )


def id_time(
    jogo,
    lado
):

    return jogo.get(
        "teams",
        {}
    ).get(
        lado,
        {}
    ).get(
        "id"
    )


def nome_liga(
    jogo
):

    lid = league_id(
        jogo
    )

    return LEAGUES.get(
        lid,
        jogo.get(
            "league",
            {}
        ).get(
            "name",
            "Liga"
        )
    )


def data_jogo(
    jogo
):

    return jogo.get(
        "fixture",
        {}
    ).get(
        "date"
    )


# ============================================================
# BUSCAR JOGOS DAS PRÓXIMAS 24 HORAS
# ============================================================

def buscar_proximas_24h():

    agora_local = agora()

    limite = (
        agora_local
        + timedelta(
            hours=24
        )
    )

    encontrados = {}

    # --------------------------------------------------------
    # Buscamos HOJE e AMANHÃ diretamente pela DATA.
    # Isso evita o problema anterior da temporada.
    # --------------------------------------------------------

    datas = {
        agora_local.date(),
        limite.date()
    }

    for data in sorted(
        datas
    ):

        data_str = data.strftime(
            "%Y-%m-%d"
        )

        print(
            "Buscando jogos:",
            data_str
        )

        resposta = api_football(

            "/fixtures",

            {
                "date":
                    data_str
            },

            ttl=120
        )

        for jogo in (
            resposta or {}
        ).get(
            "response",
            []
        ):

            if (
                league_id(jogo)
                not in LEAGUES
            ):
                continue

            status = status_jogo(
                jogo
            )

            # Jogos já terminados
            # não entram.
            if status not in PRE_STATUSES:
                continue

            data_str_jogo = data_jogo(
                jogo
            )

            if not data_str_jogo:
                continue

            try:

                inicio = datetime.fromisoformat(
                    data_str_jogo.replace(
                        "Z",
                        "+00:00"
                    )
                ).astimezone(
                    TZ
                )

            except Exception:

                continue

            # Janela exata de 24 horas
            if (
                inicio >= agora_local
                and
                inicio <= limite
            ):

                encontrados[
                    fixture_id(jogo)
                ] = jogo

    jogos = list(
        encontrados.values()
    )

    jogos.sort(
        key=lambda x:
        data_jogo(x) or ""
    )

    print(
        "Próximos 24h:",
        len(jogos)
    )

    return jogos


# ============================================================
# BUSCAR JOGOS LIVE
# ============================================================

def buscar_live():

    encontrados = {}

    # --------------------------------------------------------
    # Primeira tentativa:
    # todos os jogos LIVE
    # --------------------------------------------------------

    resposta = api_football(

        "/fixtures",

        {
            "live": "all"
        },

        ttl=30
    )

    for jogo in (
        resposta or {}
    ).get(
        "response",
        []
    ):

        if (
            league_id(jogo)
            in LEAGUES
            and
            status_jogo(jogo)
            in LIVE_STATUSES
        ):

            encontrados[
                fixture_id(jogo)
            ] = jogo

    # --------------------------------------------------------
    # Segunda tentativa:
    # datas de hoje e amanhã
    # --------------------------------------------------------

    hoje = agora().date()

    for data in [
        hoje,
        hoje + timedelta(days=1)
    ]:

        resposta = api_football(

            "/fixtures",

            {
                "date":
                    data.strftime(
                        "%Y-%m-%d"
                    )
            },

            ttl=30
        )

        for jogo in (
            resposta or {}
        ).get(
            "response",
            []
        ):

            if (
                league_id(jogo)
                in LEAGUES
                and
                status_jogo(jogo)
                in LIVE_STATUSES
            ):

                encontrados[
                    fixture_id(jogo)
                ] = jogo

    jogos = list(
        encontrados.values()
    )

    return jogos


# ============================================================
# REGISTRAR JOGOS PARA MONITORAMENTO
# ============================================================

def atualizar_jogos_monitorados():

    proximos = (
        buscar_proximas_24h()
    )

    lives = buscar_live()

    for jogo in proximos:

        JOGOS_MONITORADOS[
            fixture_id(jogo)
        ] = jogo

    for jogo in lives:

        JOGOS_MONITORADOS[
            fixture_id(jogo)
        ] = jogo

    # Remove jogos encerrados
    remover = []

    for fid, jogo in (
        JOGOS_MONITORADOS.items()
    ):

        if status_jogo(jogo) in {
            "FT",
            "AET",
            "PEN",
            "CANC",
            "PST",
            "ABD",
            "AWD",
            "WO"
        }:

            remover.append(fid)

    for fid in remover:

        JOGOS_MONITORADOS.pop(
            fid,
            None
        )

    print(
        "Jogos monitorados:",
        len(
            JOGOS_MONITORADOS
        )
    )


# ============================================================
# ESTATÍSTICAS DE UMA PARTIDA
# ============================================================

def buscar_estatisticas(
    fid,
    ttl=35
):

    resposta = api_football(

        "/fixtures/statistics",

        {
            "fixture":
                fid
        },

        ttl=ttl
    )

    resultado = {}

    for bloco in (
        resposta or {}
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

            tipo = item.get(
                "type"
            )

            valor = item.get(
                "value"
            )

            stats[
                tipo
            ] = valor

        resultado[
            tid
        ] = stats

    return resultado


def stat(
    dados,
    tid,
    *nomes
):

    equipe = dados.get(
        tid,
        {}
    )

    for nome in nomes:

        if nome in equipe:

            return numero(
                equipe[nome]
            )

    return 0.0


# ============================================================
# HISTÓRICO DOS TIMES
# ============================================================

def buscar_historico(
    tid
):

    resposta = api_football(

        "/fixtures",

        {
            "team":
                tid,

            "last":
                HISTORY_MATCHES
        },

        ttl=900
    )

    return (
        resposta or {}
    ).get(
        "response",
        []
    )


# ============================================================
# ANÁLISE HISTÓRICA
# ============================================================

def analisar_historico(
    tid
):

    jogos = buscar_historico(
        tid
    )

    gols_pro = []
    gols_contra = []

    resultados = []

    escanteios = []
    escanteios_contra = []

    finalizacoes = []
    chutes_alvo = []

    posses = []
    ataques_perigosos = []

    # --------------------------------------------------------
    # GOLS E RESULTADOS
    # --------------------------------------------------------

    for jogo in jogos:

        casa = (
            id_time(
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

        gols_pro.append(
            pro
        )

        gols_contra.append(
            contra
        )

        if pro > contra:

            resultados.append(
                "V"
            )

        elif pro == contra:

            resultados.append(
                "E"
            )

        else:

            resultados.append(
                "D"
            )

    # --------------------------------------------------------
    # ESTATÍSTICAS
    #
    # Para não consumir a API inteira de uma vez,
    # pegamos estatísticas dos jogos mais recentes.
    # --------------------------------------------------------

    for jogo in jogos[:6]:

        fid = fixture_id(
            jogo
        )

        dados = buscar_estatisticas(
            fid,
            ttl=3600
        )

        if not dados:
            continue

        meu = dados.get(
            tid,
            {}
        )

        adversario = (
            id_time(
                jogo,
                "away"
            )
            if id_time(
                jogo,
                "home"
            ) == tid
            else
            id_time(
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

        chutes_alvo.append(
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

        ataques_perigosos.append(
            numero(
                meu.get(
                    "Dangerous Attacks"
                )
            )
        )

    return {

        "jogos":
            len(
                gols_pro
            ),

        "vitorias":
            resultados.count(
                "V"
            ),

        "empates":
            resultados.count(
                "E"
            ),

        "derrotas":
            resultados.count(
                "D"
            ),

        "forma":
            "".join(
                resultados[:5]
            ),

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
                chutes_alvo
            ),

        "posse":
            media_ponderada(
                posses
            ),

        "ataques":
            media_ponderada(
                ataques_perigosos
            )
    }


# ============================================================
# MÉDIA PONDERADA
# ============================================================

def media_ponderada(
    valores
):

    valores = [
        numero(v)
        for v in valores
    ]

    valores = [
        v for v in valores
        if v >= 0
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

    total = sum(
        pesos
    )

    return sum(
        v * p
        for v, p in zip(
            valores,
            pesos
        )
    ) / total


# ============================================================
# ÍNDICE PRÉ-JOGO
# ============================================================

def indice_pre_jogo(
    hist
):

    score = 50.0

    jogos = max(
        hist.get(
            "jogos",
            0
        ),
        1
    )

    pontos = (
        hist.get(
            "vitorias",
            0
        ) * 3
        +
        hist.get(
            "empates",
            0
        )
    )

    pontos_jogo = (
        pontos / jogos
    )

    score += (
        pontos_jogo
        - 1.5
    ) * 10

    saldo = (
        hist.get(
            "gols_pro",
            0
        )
        -
        hist.get(
            "gols_contra",
            0
        )
    )

    score += (
        saldo * 8
    )

    score += (
        hist.get(
            "finalizacoes",
            0
        )
        - 10
    ) * 1.3

    score += (
        hist.get(
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

def calcular_pressao(
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
        -
        ataques_op
    ) * 1.10

    score += (
        chutes
        -
        chutes_op
    ) * 2.0

    score += (
        alvo
        -
        alvo_op
    ) * 3.0

    score += (
        esc
        -
        esc_op
    ) * 1.8

    score += (
        posse
        -
        posse_op
    ) * 0.5

    return max(
        0,
        min(
            100,
            score
        )
    )


# ============================================================
# PRODUÇÃO OFENSIVA
# ============================================================

def calcular_ofensivo(
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
        chutes * 2.5
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
# ANÁLISE COMPLETA LIVE
# ============================================================

def analisar_live(
    jogo
):

    fid = fixture_id(
        jogo
    )

    home = id_time(
        jogo,
        "home"
    )

    away = id_time(
        jogo,
        "away"
    )

    if not home or not away:
        return None

    dados = buscar_estatisticas(
        fid,
        ttl=35
    )

    if not dados:
        return None

    hist_home = analisar_historico(
        home
    )

    hist_away = analisar_historico(
        away
    )

    press_home = calcular_pressao(
        dados,
        home,
        away
    )

    press_away = calcular_pressao(
        dados,
        away,
        home
    )

    ofens_home = calcular_ofensivo(
        dados,
        home
    )

    ofens_away = calcular_ofensivo(
        dados,
        away
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
# DETECÇÃO DE INDICADORES
# ============================================================

def gerar_indicadores(
    analise
):

    indicadores = []

    ph = analise[
        "press_home"
    ]

    pa = analise[
        "press_away"
    ]

    # --------------------------------------------------------
    # PRESSÃO
    # --------------------------------------------------------

    if (
        max(ph, pa) >= 72
        and
        abs(ph - pa) >= 12
    ):

        lado = (
            "mandante"
            if ph > pa
            else
            "visitante"
        )

        indicadores.append({

            "tipo":
                "PRESSAO",

            "nome":
                "🔥 PRESSÃO FORTE",

            "lado":
                lado,

            "score":
                max(
                    ph,
                    pa
                )
        })

    # --------------------------------------------------------
    # ESCANTEIOS
    # --------------------------------------------------------

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

        indicadores.append({

            "tipo":
                "ESCANTEIOS",

            "nome":
                "🚩 VOLUME DE ESCANTEIOS",

            "lado":
                "jogo",

            "score":
                min(
                    100,
                    55 + total_esc * 4
                )
        })

    # --------------------------------------------------------
    # FINALIZAÇÕES
    # --------------------------------------------------------

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

        indicadores.append({

            "tipo":
                "FINALIZACOES",

            "nome":
                "🎯 ALTO VOLUME DE FINALIZAÇÕES",

            "lado":
                "jogo",

            "score":
                min(
                    100,
                    55 + total_chutes * 2
                )
        })

    # --------------------------------------------------------
    # CHUTES NO ALVO
    # --------------------------------------------------------

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

        indicadores.append({

            "tipo":
                "NO_ALVO",

            "nome":
                "🎯 CHUTES NO ALVO",

            "lado":
                "jogo",

            "score":
                min(
                    100,
                    55 + total_alvo * 5
                )
        })

    # --------------------------------------------------------
    # ATAQUES PERIGOSOS
    # --------------------------------------------------------

    ah = analise[
        "ataques_home"
    ]

    aa = analise[
        "ataques_away"
    ]

    if (
        max(ah, aa) >= 30
        and
        abs(
            ah - aa
        ) >= 10
    ):

        lado = (
            "mandante"
            if ah > aa
            else
            "visitante"
        )

        indicadores.append({

            "tipo":
                "ATAQUES",

            "nome":
                "🔥 ATAQUES PERIGOSOS",

            "lado":
                lado,

            "score":
                75
        })

    return indicadores


# ============================================================
# TEXTO PRÉ-JOGO
# ============================================================

def texto_pre_jogo(
    jogo
):

    home = nome_time(
        jogo,
        "home"
    )

    away = nome_time(
        jogo,
        "away"
    )

    home_id = id_time(
        jogo,
        "home"
    )

    away_id = id_time(
        jogo,
        "away"
    )

    hist_home = analisar_historico(
        home_id
    )

    hist_away = analisar_historico(
        away_id
    )

    indice_home = indice_pre_jogo(
        hist_home
    )

    indice_away = indice_pre_jogo(
        hist_away
    )

    total_esc = (
        hist_home[
            "escanteios"
        ]
        +
        hist_away[
            "escanteios"
        ]
    )

    total_finalizacoes = (
        hist_home[
            "finalizacoes"
        ]
        +
        hist_away[
            "finalizacoes"
        ]
    )

    texto = f"""
🟢 <b>PRÉ-JOGO</b>

⚽ <b>{home} x {away}</b>

🏆 {nome_liga(jogo)}

🕐 {formatar_data(data_jogo(jogo))}

━━━━━━━━━━━━━━━━━━

🏠 <b>{home}</b>

Forma:
<code>{hist_home['forma'] or 'N/D'}</code>

V/E/D:
{hist_home['vitorias']} / {hist_home['empates']} / {hist_home['derrotas']}

⚽ Gols marcados:
{hist_home['gols_pro']:.2f}

⚽ Gols sofridos:
{hist_home['gols_contra']:.2f}

🚩 Escanteios:
{hist_home['escanteios']:.2f}

🎯 Finalizações:
{hist_home['finalizacoes']:.2f}

🎯 No alvo:
{hist_home['no_alvo']:.2f}

📊 Posse:
{hist_home['posse']:.1f}%

🔥 Ataques perigosos:
{hist_home['ataques']:.1f}

━━━━━━━━━━━━━━━━━━

✈️ <b>{away}</b>

Forma:
<code>{hist_away['forma'] or 'N/D'}</code>

V/E/D:
{hist_away['vitorias']} / {hist_away['empates']} / {hist_away['derrotas']}

⚽ Gols marcados:
{hist_away['gols_pro']:.2f}

⚽ Gols sofridos:
{hist_away['gols_contra']:.2f}

🚩 Escanteios:
{hist_away['escanteios']:.2f}

🎯 Finalizações:
{hist_away['finalizacoes']:.2f}

🎯 No alvo:
{hist_away['no_alvo']:.2f}

📊 Posse:
{hist_away['posse']:.1f}%

🔥 Ataques perigosos:
{hist_away['ataques']:.1f}

━━━━━━━━━━━━━━━━━━

📈 <b>ÍNDICE PRÉ-JOGO</b>

{home}: {indice_home:.0f}/100

{away}: {indice_away:.0f}/100

━━━━━━━━━━━━━━━━━━

🔎 <b>LEITURA ESTATÍSTICA</b>
"""

    if (
        indice_home
        >=
        indice_away + 8
    ):

        texto += (
            f"\nOs indicadores históricos "
            f"favorecem estatisticamente "
            f"o volume do <b>{home}</b>."
        )

    elif (
        indice_away
        >=
        indice_home + 8
    ):

        texto += (
            f"\nOs indicadores históricos "
            f"favorecem estatisticamente "
            f"o volume do <b>{away}</b>."
        )

    else:

        texto += (
            "\nOs indicadores históricos "
            "estão relativamente equilibrados."
        )

    if total_esc >= 8:

        texto += (
            "\n\n🚩 O histórico apresenta "
            "volume elevado de escanteios."
        )

    if total_finalizacoes >= 20:

        texto += (
            "\n\n🎯 O histórico apresenta "
            "volume elevado de finalizações."
        )

    texto += """

━━━━━━━━━━━━━━━━━━

⚠️ Análise estatística. Não representa
garantia de resultado.
"""

    return texto


# ============================================================
# TEXTO LIVE
# ============================================================

def texto_live(
    jogo,
    analise
):

    home = nome_time(
        jogo,
        "home"
    )

    away = nome_time(
        jogo,
        "away"
    )

    gols_home = (
        jogo.get(
            "goals",
            {}
        ).get(
            "home"
        )
        or 0
    )

    gols_away = (
        jogo.get(
            "goals",
            {}
        ).get(
            "away"
        )
        or 0
    )

    minuto = (
        minuto_jogo(jogo)
        or
        status_jogo(jogo)
    )

    texto = f"""
🔴 <b>ANÁLISE LIVE</b>

⚽ <b>{home} {gols_home} x {gols_away} {away}</b>

🏆 {nome_liga(jogo)}

⏱️ {minuto}'

━━━━━━━━━━━━━━━━━━

📊 <b>ESTATÍSTICAS LIVE</b>

🎯 Finalizações
{home}: {analise['chutes_home']:.0f}
{away}: {analise['chutes_away']:.0f}

🎯 Chutes no alvo
{home}: {analise['alvo_home']:.0f}
{away}: {analise['alvo_away']:.0f}

🚩 Escanteios
{home}: {analise['esc_home']:.0f}
{away}: {analise['esc_away']:.0f}

📊 Posse
{home}: {analise['posse_home']:.0f}%
{away}: {analise['posse_away']:.0f}%

🔥 Ataques perigosos
{home}: {analise['ataques_home']:.0f}
{away}: {analise['ataques_away']:.0f}

━━━━━━━━━━━━━━━━━━

🔥 <b>PRESSÃO</b>

{home}: {analise['press_home']:.0f}/100

{away}: {analise['press_away']:.0f}/100

━━━━━━━━━━━━━━━━━━

🎯 <b>PRODUÇÃO OFENSIVA</b>

{home}: {analise['ofens_home']:.0f}/100

{away}: {analise['ofens_away']:.0f}/100

━━━━━━━━━━━━━━━━━━
"""

    indicadores = gerar_indicadores(
        analise
    )

    if indicadores:

        texto += (
            "\n🚨 <b>INDICADORES ATIVOS</b>\n\n"
        )

        for item in indicadores:

            texto += (
                f"{item['nome']}\n"
                f"Intensidade: "
                f"{item['score']:.0f}/100\n"
                f"Referência: "
                f"{item['lado']}\n\n"
            )

    else:

        texto += (
            "\n🟡 Nenhum indicador forte "
            "detectado neste momento.\n"
        )

    texto += """

⚠️ Indicadores estatísticos.
Não são garantia de resultado.
"""

    return texto


# ============================================================
# ALERTA INTELIGENTE
# ============================================================

def deve_alertar(
    jogo,
    indicadores
):

    if not indicadores:
        return False

    fid = fixture_id(
        jogo
    )

    # Escolhe o indicador mais forte
    principal = max(
        indicadores,
        key=lambda x:
        x["score"]
    )

    tipo = principal[
        "tipo"
    ]

    chave = (
        f"{fid}:"
        f"{tipo}:"
        f"{principal['lado']}"
    )

    agora_ts = time.time()

    ultimo = ULTIMOS_ALERTAS.get(
        chave,
        0
    )

    if (
        agora_ts
        -
        ultimo
        <
        ALERT_COOLDOWN
    ):

        return False

    ULTIMOS_ALERTAS[
        chave
    ] = agora_ts

    return True


# ============================================================
# ODDS OPCIONAIS
# ============================================================

ODDS_SPORTS = {

    39:
        "soccer_epl",

    140:
        "soccer_spain_la_liga",

    78:
        "soccer_germany_bundesliga",

    135:
        "soccer_italy_serie_a",

    61:
        "soccer_france_ligue_one",

    94:
        "soccer_portugal_primeira_liga",

    71:
        "soccer_brazil_campeonato",

    2:
        "soccer_uefa_champs_league",

    3:
        "soccer_uefa_europa_league",

    848:
        "soccer_uefa_europa_conference_league",

    13:
        "soccer_conmebol_libertadores",

    11:
        "soccer_conmebol_sudamericana",
}


def buscar_odds(
    jogo
):

    if not ODDS_API_KEY:
        return []

    lid = league_id(
        jogo
    )

    sport = ODDS_SPORTS.get(
        lid
    )

    if not sport:
        return []

    home = normalizar(
        nome_time(
            jogo,
            "home"
        )
    )

    away = normalizar(
        nome_time(
            jogo,
            "away"
        )
    )

    resposta = odds_api(

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

    encontrados = []

    for partida in (
        resposta or []
    ):

        h = normalizar(
            partida.get(
                "home_team"
            )
        )

        a = normalizar(
            partida.get(
                "away_team"
            )
        )

        if (
            (
                home in h
                or h in home
            )
            and
            (
                away in a
                or a in away
            )
        ):

            encontrados.append(
                partida
            )

    return encontrados


def texto_odds(
    jogos
):

    if not jogos:
        return ""

    linhas = [
        "💰 <b>ODDS DISPONÍVEIS</b>"
    ]

    partida = jogos[0]

    for bookmaker in partida.get(
        "bookmakers",
        []
    ):

        casa = bookmaker.get(
            "title",
            "Casa"
        )

        for mercado in bookmaker.get(
            "markets",
            []
        ):

            if mercado.get(
                "key"
            ) != "h2h":

                continue

            resultados = []

            for outcome in mercado.get(
                "outcomes",
                []
            ):

                resultados.append(
                    f"{outcome.get('name')} "
                    f"@{outcome.get('price')}"
                )

            if resultados:

                linhas.append(
                    f"• <b>{casa}</b>: "
                    + ", ".join(
                        resultados
                    )
                )

    return "\n".join(
        linhas[:15]
    )


# ============================================================
# COMANDO /START
# ============================================================

@bot.message_handler(
    commands=["start"]
)
def start(
    message
):

    global CHAT_ID
    global MONITORANDO

    CHAT_ID = message.chat.id

    MONITORANDO = True

    # Já começa a montar a janela de 24h
    atualizar_jogos_monitorados()

    bot.send_message(

        message.chat.id,

        """
🤖 <b>BOT DE FUTEBOL ONLINE</b>

🟢 Pré-jogo
🔴 Análise LIVE
📚 Últimos jogos
⚽ Gols
🚩 Escanteios
🎯 Finalizações
📊 Posse
🔥 Ataques perigosos
📈 Pressão
💰 Odds

<b>Janela de monitoramento:</b>
próximas 24 horas + jogos LIVE.

<b>Comandos:</b>

/jogos
/prejogo
/analisar TIME
/status
/ligas
/parar
/continuar

⚠️ Sistema informativo e estatístico.
"""
    )


# ============================================================
# /JOGOS
# ============================================================

@bot.message_handler(
    commands=["jogos"]
)
def jogos(
    message
):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,
        "🔎 Procurando jogos LIVE e jogos das próximas 24 horas..."
    )

    try:

        atualizar_jogos_monitorados()

        lives = buscar_live()

        proximos = (
            buscar_proximas_24h()
        )

        for jogo in lives:

            JOGOS_MONITORADOS[
                fixture_id(jogo)
            ] = jogo

        for jogo in proximos:

            JOGOS_MONITORADOS[
                fixture_id(jogo)
            ] = jogo

        if not lives and not proximos:

            bot.send_message(

                message.chat.id,

                "🟡 Nenhum jogo encontrado "
                "nas próximas 24 horas "
                "ou LIVE nas ligas configuradas."
            )

            return

        texto = (
            "⚽ <b>JOGOS ENCONTRADOS</b>\n\n"
        )

        if lives:

            texto += (
                "🔴 <b>AO VIVO</b>\n\n"
            )

            for jogo in lives:

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
                    f"🔴 "
                    f"<b>{nome_time(jogo, 'home')} "
                    f"{gh} x {ga} "
                    f"{nome_time(jogo, 'away')}</b>\n"
                    f"🏆 {nome_liga(jogo)}\n"
                    f"⏱️ "
                    f"{minuto_jogo(jogo) or status_jogo(jogo)}'\n\n"
                )

        if proximos:

            texto += (
                "🟢 <b>PRÓXIMAS 24 HORAS</b>\n\n"
            )

            for jogo in proximos:

                texto += (
                    f"🟢 "
                    f"<b>{nome_time(jogo, 'home')} "
                    f"x "
                    f"{nome_time(jogo, 'away')}</b>\n"
                    f"🏆 {nome_liga(jogo)}\n"
                    f"🕐 "
                    f"{formatar_data(data_jogo(jogo))}\n\n"
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
            "❌ Erro ao buscar jogos."
        )


# ============================================================
# /PREJOGO
# ============================================================

@bot.message_handler(
    commands=["prejogo"]
)
def prejogo(
    message
):

    global CHAT_ID

    CHAT_ID = message.chat.id

    bot.send_message(
        message.chat.id,
        "🔎 Buscando jogos das próximas 24 horas..."
    )

    try:

        jogos_24h = (
            buscar_proximas_24h()
        )

        if not jogos_24h:

            bot.send_message(

                message.chat.id,

                "🟡 Não encontrei partidas "
                "nas próximas 24 horas "
                "nas ligas configuradas."
            )

            return

        # Registra para monitoramento
        for jogo in jogos_24h:

            JOGOS_MONITORADOS[
                fixture_id(jogo)
            ] = jogo

        # Para não mandar dezenas de mensagens
        # de uma vez, mostramos os primeiros 10.
        for jogo in jogos_24h[:10]:

            try:

                texto = texto_pre_jogo(
                    jogo
                )

                odds = buscar_odds(
                    jogo
                )

                if odds:

                    texto += (
                        "\n\n"
                        +
                        texto_odds(
                            odds
                        )
                    )

                enviar(
                    message.chat.id,
                    texto
                )

            except Exception as e:

                print(
                    "Erro pré-jogo:",
                    e
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


# ============================================================
# /ANALISAR
# ============================================================

@bot.message_handler(
    commands=["analisar"]
)
def analisar(
    message
):

    global CHAT_ID

    CHAT_ID = message.chat.id

    partes = message.text.split(
        maxsplit=1
    )

    if len(partes) < 2:

        bot.send_message(

            message.chat.id,

            "Use assim:\n\n"
            "<code>/analisar Flamengo</code>"
        )

        return

    busca = normalizar(
        partes[1]
    )

    bot.send_message(
        message.chat.id,
        "🔎 Procurando o time..."
    )

    try:

        # Primeiro LIVE
        lives = buscar_live()

        for jogo in lives:

            if (
                busca in normalizar(
                    nome_time(
                        jogo,
                        "home"
                    )
                )
                or
                busca in normalizar(
                    nome_time(
                        jogo,
                        "away"
                    )
                )
            ):

                analise = analisar_live(
                    jogo
                )

                if analise:

                    enviar(
                        message.chat.id,
                        texto_live(
                            jogo,
                            analise
                        )
                    )

                return

        # Depois próximas 24h
        proximos = (
            buscar_proximas_24h()
        )

        for jogo in proximos:

            if (
                busca in normalizar(
                    nome_time(
                        jogo,
                        "home"
                    )
                )
                or
                busca in normalizar(
                    nome_time(
                        jogo,
                        "away"
                    )
                )
            ):

                enviar(
                    message.chat.id,
                    texto_pre_jogo(
                        jogo
                    )
                )

                return

        bot.send_message(

            message.chat.id,

            "🟡 Não encontrei esse time "
            "nas próximas 24 horas "
            "ou LIVE."
        )

    except Exception as e:

        print(
            "Erro /analisar:",
            e
        )

        bot.send_message(
            message.chat.id,
            "❌ Erro ao analisar."
        )


# ============================================================
# /STATUS
# ============================================================

@bot.message_handler(
    commands=["status"]
)
def status(
    message
):

    bot.send_message(

        message.chat.id,

        f"""
🟢 <b>STATUS</b>

Monitoramento:
{'ATIVO' if MONITORANDO else 'PAUSADO'}

⏱️ Atualização LIVE:
{LIVE_INTERVAL} segundos

📚 Histórico:
últimos {HISTORY_MATCHES} jogos

🏆 Ligas:
{len(LEAGUES)}

⚽ Jogos monitorados:
{len(JOGOS_MONITORADOS)}

📡 API-Football:
CONFIGURADA

💰 The Odds API:
{'CONFIGURADA' if ODDS_API_KEY else 'NÃO CONFIGURADA'}
"""
    )


# ============================================================
# /LIGAS
# ============================================================

@bot.message_handler(
    commands=["ligas"]
)
def ligas(
    message
):

    texto = (
        "🏆 <b>LIGAS MONITORADAS</b>\n\n"
    )

    for codigo, nome in LEAGUES.items():

        texto += (
            f"{nome}\n"
        )

    enviar(
        message.chat.id,
        texto
    )


# ============================================================
# /PARAR
# ============================================================

@bot.message_handler(
    commands=["parar"]
)
def parar(
    message
):

    global MONITORANDO

    MONITORANDO = False

    bot.send_message(

        message.chat.id,

        "⏸️ <b>Monitoramento pausado.</b>\n\n"
        "Use /continuar para voltar."
    )


# ============================================================
# /CONTINUAR
# ============================================================

@bot.message_handler(
    commands=["continuar"]
)
def continuar(
    message
):

    global CHAT_ID
    global MONITORANDO

    CHAT_ID = message.chat.id

    MONITORANDO = True

    atualizar_jogos_monitorados()

    bot.send_message(

        message.chat.id,

        "▶️ <b>Monitoramento ativado.</b>\n\n"
        "Estou acompanhando jogos LIVE "
        "e partidas das próximas 24 horas."
    )


# ============================================================
# QUALQUER OUTRA MENSAGEM
# ============================================================

@bot.message_handler(
    func=lambda message: True
)
def qualquer_mensagem(
    message
):

    bot.send_message(

        message.chat.id,

        """
🤖 Comandos disponíveis:

/jogos
→ LIVE + próximas 24 horas

/prejogo
→ análise dos próximos jogos

/analisar TIME
→ análise específica

/status
→ status do monitor

/ligas
→ ligas monitoradas

/parar
→ pausar alertas

/continuar
→ continuar monitoramento
"""
    )


# ============================================================
# MONITOR AUTOMÁTICO
# ============================================================

def monitorar():

    print(
        "🔴 Monitor LIVE iniciado."
    )

    while True:

        try:

            # Atualiza a lista periodicamente
            atualizar_jogos_monitorados()

            if (
                MONITORANDO
                and
                CHAT_ID
            ):

                # ------------------------------------------------
                # Analisa todos os jogos registrados
                # ------------------------------------------------

                remover = []

                for fid, jogo in list(
                    JOGOS_MONITORADOS.items()
                ):

                    try:

                        # ------------------------------------------------
                        # Busca a versão mais atual do jogo
                        # ------------------------------------------------

                        resposta = api_football(

                            "/fixtures",

                            {
                                "id":
                                    fid
                            },

                            ttl=30
                        )

                        atualizados = (
                            resposta or {}
                        ).get(
                            "response",
                            []
                        )

                        if atualizados:

                            jogo = atualizados[0]

                            JOGOS_MONITORADOS[
                                fid
                            ] = jogo

                        estado = status_jogo(
                            jogo
                        )

                        # ------------------------------------------------
                        # Se começou, entra LIVE
                        # ------------------------------------------------

                        if estado in LIVE_STATUSES:

                            analise = analisar_live(
                                jogo
                            )

                            if not analise:
                                continue

                            indicadores = (
                                gerar_indicadores(
                                    analise
                                )
                            )

                            # Somente indicadores relevantes
                            indicadores = [
                                item
                                for item
                                in indicadores
                                if item[
                                    "score"
                                ] >= 70
                            ]

                            if indicadores:

                                if deve_alertar(
                                    jogo,
                                    indicadores
                                ):

                                    enviar(

                                        CHAT_ID,

                                        "🚨 <b>ALERTA LIVE</b>\n\n"
                                        +
                                        texto_live(
                                            jogo,
                                            analise
                                        )

                                    )

                        # ------------------------------------------------
                        # Jogo terminado
                        # ------------------------------------------------

                        elif estado in {
                            "FT",
                            "AET",
                            "PEN",
                            "CANC",
                            "PST",
                            "ABD",
                            "AWD",
                            "WO"
                        }:

                            remover.append(
                                fid
                            )

                    except Exception as e:

                        print(
                            f"Erro jogo {fid}:",
                            e
                        )

                for fid in remover:

                    JOGOS_MONITORADOS.pop(
                        fid,
                        None
                    )

        except Exception as e:

            print(
                "Erro monitor:",
                e
            )

        time.sleep(
            LIVE_INTERVAL
        )


# ============================================================
# FLASK / RENDER
# ============================================================

@app.route("/")
def home():

    return (
        "Bot de Futebol LIVE funcionando."
    )


@app.route("/health")
def health():

    return {

        "status":
            "ok",

        "monitorando":
            MONITORANDO,

        "jogos_monitorados":
            len(
                JOGOS_MONITORADOS
            ),

        "chat_configurado":
            CHAT_ID is not None,

        "hora":
            agora().isoformat()
    }


# ============================================================
# TELEGRAM
# ============================================================

def iniciar_telegram():

    print(
        "Removendo webhook antigo..."
    )

    while True:

        try:

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

    # --------------------------------------------------------
    # Monitor
    # --------------------------------------------------------

    thread_monitor = threading.Thread(
        target=monitorar,
        daemon=True
    )

    thread_monitor.start()

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    thread_telegram = threading.Thread(
        target=iniciar_telegram,
        daemon=True
    )

    thread_telegram.start()

    # --------------------------------------------------------
    # Flask / Render
    # --------------------------------------------------------

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
