# =====================================================================
# ROBO DE MONITORAMENTO DE PROMOCOES DE MILHAS, PONTOS E CARTOES
# Versao "nuvem": roda no GitHub Actions e grava os resultados em
# docs/data.json, que a pagina docs/index.html exibe.
# =====================================================================

import os
import re
import json
import unicodedata
from datetime import datetime

import feedparser

# =====================================================================
# 1. CAMINHOS (agora relativos ao repositorio, nao mais ~\Downloads)
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
DATA_JSON_PATH = os.path.join(DOCS_DIR, "data.json")
HISTORICO_JSON_PATH = os.path.join(DOCS_DIR, "historico.json")

# =====================================================================
# 2. FONTES DE PROMOCOES (RSS)
# =====================================================================
FEEDS = [
    ("Pontos pra Voar", "https://pontospravoar.com/feed/"),
    ("Passageiro de Primeira", "https://passageirodeprimeira.com/feed/"),
    ("Melhores Destinos - Promocoes", "https://www.melhoresdestinos.com.br/promocoes/feed"),
    ("Mestre das Milhas", "https://mestredasmilhas.com/feed/"),
]

# =====================================================================
# 3. DICIONARIOS DE PALAVRAS-CHAVE
# =====================================================================
PROGRAMAS = [
    "Livelo", "Esfera", "LATAM Pass", "Smiles", "Azul Fidelidade", "Azul",
    "TAP Miles&Go", "TAP", "Iberia Plus", "Avios", "ALL - Accor Live Limitless",
    "Accor",
]

BANCOS = [
    "Santander", "Bradesco", "Banco do Brasil", "Caixa", "Itau", "Itaú",
    "Nubank", "C6 Bank", "C6", "Inter", "XP", "BTG Pactual", "BTG",
    "Porto Bank", "Porto",
]

PARCEIROS = [
    "Amazon", "Magalu", "Magazine Luiza", "Mercado Livre", "Casas Bahia",
    "Ponto", "Fast Shop", "Sam's Club", "Sams Club", "Carrefour",
]

CLUBES = ["Clube Livelo", "Clube Smiles", "Clube Azul", "Clube Esfera"]

TODAS_EMPRESAS = PROGRAMAS + BANCOS + PARCEIROS

PALAVRAS_TRANSFERENCIA = ["transfer", "bonificad", "bonus de transfer", "bônus de transfer"]
PALAVRAS_COMPRA = ["shopping", "compre e ganhe", "pontos por r$", "pontos por real", "milhas por r$"]
PALAVRAS_CARTAO = ["cartao", "cartão", "anuidade", "bateu, ganhou", "bateu ganhou"]
PALAVRAS_CLUBE = ["clube livelo", "clube smiles", "clube azul", "clube esfera", "assinante", "assinatura"]
PALAVRAS_CADASTRO = ["cadastr", "inscri"]

# =====================================================================
# 4. FUNCOES DE APOIO (mesma logica da versao local)
# =====================================================================

def normalizar(texto):
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return texto.lower().strip()


def contem_alguma(texto_normalizado, lista_palavras):
    return any(normalizar(p) in texto_normalizado for p in lista_palavras)


def encontrar_empresas(texto_normalizado):
    encontrados = []
    for empresa in TODAS_EMPRESAS:
        padrao = r"\b" + re.escape(normalizar(empresa)) + r"\b"
        if re.search(padrao, texto_normalizado) and empresa not in encontrados:
            encontrados.append(empresa)
    return encontrados


def extrair_percentual_bonus(texto_normalizado):
    achados = re.findall(r"(\d{1,3})\s*%", texto_normalizado)
    if not achados:
        return None
    valores = [int(v) for v in achados if 0 < int(v) <= 300]
    return max(valores) if valores else None


def extrair_pontos_por_real(texto_normalizado):
    padrao = r"(\d{1,3})\s*(?:pontos|milhas|pts)\s*(?:por|/)\s*(?:cada\s*)?r?\$?\s*1\b"
    achados = re.findall(padrao, texto_normalizado)
    if not achados:
        return None
    valores = [int(v) for v in achados if 0 < int(v) <= 500]
    return max(valores) if valores else None


def extrair_gasto_minimo(texto_normalizado):
    padrao = r"r\$\s*([\d\.]{3,8})"
    achados = re.findall(padrao, texto_normalizado)
    if not achados:
        return None
    valores = []
    for a in achados:
        limpo = a.replace(".", "")
        if limpo.isdigit():
            valores.append(int(limpo))
    return max(valores) if valores else None


def classificar_promocao(bonus_pct, pontos_por_real):
    """
    Nota 0-100: bonus %, ou pontos por R$, o que for maior.
    >=80 EXCELENTE | 60-79 MUITO BOA | 40-59 BOA | 20-39 NORMAL | <20 NAO VALE A PENA
    """
    candidatos = [v for v in [bonus_pct, pontos_por_real] if v is not None]
    if not candidatos:
        return 0, "⚪ NORMAL", 4

    nota = min(max(candidatos), 100)
    if nota >= 80:
        return nota, "🔥 EXCELENTE", 1
    elif nota >= 60:
        return nota, "🟢 MUITO BOA", 2
    elif nota >= 40:
        return nota, "🟡 BOA", 3
    elif nota >= 20:
        return nota, "⚪ NORMAL", 4
    else:
        return nota, "🔴 NAO VALE A PENA", 5


def identificar_tipo(texto_normalizado):
    if contem_alguma(texto_normalizado, PALAVRAS_TRANSFERENCIA):
        return "Transferencia bonificada"
    if contem_alguma(texto_normalizado, PALAVRAS_CARTAO):
        return "Cartao de credito"
    if contem_alguma(texto_normalizado, PALAVRAS_COMPRA):
        return "Compra bonificada"
    if contem_alguma(texto_normalizado, PALAVRAS_CLUBE):
        return "Clube / assinatura"
    return "Necessita verificacao"


def chave_unica(fonte, titulo):
    return f"{normalizar(fonte)}::{normalizar(titulo)}"


# =====================================================================
# 5. COLETA DOS FEEDS
# =====================================================================

def coletar_promocoes():
    registros = []
    momento_coleta = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for fonte, url in FEEDS:
        try:
            d = feedparser.parse(url)
            if getattr(d, "bozo", False) and not d.entries:
                print(f"Aviso: feed '{fonte}' nao retornou itens (link pode ter mudado).")
                continue

            for entry in d.entries:
                titulo = entry.get("title", "").strip()
                link = entry.get("link", "")
                resumo = entry.get("summary", entry.get("description", ""))
                publicado = entry.get("published", entry.get("updated", ""))

                texto_completo = f"{titulo} {resumo}"
                texto_norm = normalizar(texto_completo)

                bonus_pct = extrair_percentual_bonus(texto_norm)
                pontos_real = extrair_pontos_por_real(texto_norm)
                gasto_min = extrair_gasto_minimo(texto_norm)
                empresas = encontrar_empresas(texto_norm)
                tipo = identificar_tipo(texto_norm)

                nota, classificacao, prioridade = classificar_promocao(bonus_pct, pontos_real)

                registros.append({
                    "chave": chave_unica(fonte, titulo),
                    "data_coleta": momento_coleta,
                    "empresa": ", ".join(empresas) if empresas else "Necessita verificacao",
                    "programa": empresas[0] if empresas else "Necessita verificacao",
                    "tipo": tipo,
                    "descricao": titulo,
                    "bonus_pct": bonus_pct,
                    "pontos_por_real": pontos_real,
                    "gasto_minimo": gasto_min,
                    "nota": nota,
                    "necessita_clube": True if contem_alguma(texto_norm, PALAVRAS_CLUBE) else None,
                    "necessita_cartao": True if contem_alguma(texto_norm, PALAVRAS_CARTAO) else None,
                    "necessita_cadastro": True if contem_alguma(texto_norm, PALAVRAS_CADASTRO) else None,
                    "data_publicacao": publicado if publicado else None,
                    "classificacao": classificacao,
                    "prioridade": prioridade,
                    "link": link,
                    "fonte": fonte,
                    "status": "",
                })
        except Exception as e:
            print(f"Erro ao ler {fonte} ({url}): {e}")

    return registros


# =====================================================================
# 6. COMPARACAO COM A COLETA ANTERIOR (le o data.json publicado antes)
# =====================================================================

def carregar_json(caminho):
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Aviso: nao consegui ler {caminho} ({e}).")
    return None


def comparar_com_anterior(registros_atuais):
    dados_anteriores = carregar_json(DATA_JSON_PATH)
    anteriores_por_chave = {}
    if dados_anteriores and "promocoes" in dados_anteriores:
        anteriores_por_chave = {p["chave"]: p for p in dados_anteriores["promocoes"]}

    chaves_atuais = {r["chave"] for r in registros_atuais}

    for r in registros_atuais:
        anterior = anteriores_por_chave.get(r["chave"])
        if anterior is None:
            r["status"] = "🆕 NOVA PROMOCAO"
        elif (
            isinstance(r.get("bonus_pct"), (int, float))
            and isinstance(anterior.get("bonus_pct"), (int, float))
            and r["bonus_pct"] > anterior["bonus_pct"]
        ):
            r["status"] = "⬆️ BONUS AUMENTOU"
        else:
            r["status"] = ""

    expiradas = [p for chave, p in anteriores_por_chave.items() if chave not in chaves_atuais]
    for p in expiradas:
        p["status"] = "❌ EXPIRADA"

    return registros_atuais, expiradas


def atualizar_historico(expiradas_desta_rodada):
    historico_antigo = carregar_json(HISTORICO_JSON_PATH) or []
    historico = historico_antigo + expiradas_desta_rodada

    vistos = {}
    for item in historico:
        vistos[item["chave"]] = item  # mantem so a versao mais recente de cada chave
    historico_final = list(vistos.values())[-500:]  # mantem no maximo os ultimos 500

    return historico_final


# =====================================================================
# 7. MONTAR RESUMO (dashboard)
# =====================================================================

def montar_resumo(registros):
    total = len(registros)
    novas = sum(1 for r in registros if r["status"] == "🆕 NOVA PROMOCAO")
    aumentou = sum(1 for r in registros if r["status"] == "⬆️ BONUS AUMENTOU")
    excelentes = sum(1 for r in registros if r["classificacao"] == "🔥 EXCELENTE")
    cartoes = sum(1 for r in registros if r["tipo"] == "Cartao de credito")
    compras = sum(1 for r in registros if r["tipo"] == "Compra bonificada")
    transferencias = sum(1 for r in registros if r["tipo"] == "Transferencia bonificada")

    return {
        "total": total,
        "novas": novas,
        "aumentou": aumentou,
        "excelentes": excelentes,
        "cartoes": cartoes,
        "compras": compras,
        "transferencias": transferencias,
    }


# =====================================================================
# 8. PROGRAMA PRINCIPAL
# =====================================================================

def main():
    os.makedirs(DOCS_DIR, exist_ok=True)

    registros = coletar_promocoes()

    # remove duplicadas da propria coleta atual
    vistos = {}
    for r in registros:
        vistos[r["chave"]] = r
    registros = list(vistos.values())

    registros, expiradas = comparar_com_anterior(registros)

    # ordena: melhores primeiro
    registros.sort(key=lambda r: (r["prioridade"], -r["nota"]))

    historico = atualizar_historico(expiradas)
    resumo = montar_resumo(registros)

    saida = {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "resumo": resumo,
        "promocoes": registros,
    }

    with open(DATA_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)

    with open(HISTORICO_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=2)

    print(f"OK! Promocoes coletadas nesta rodada: {len(registros)}")
    print(f" - Novas: {resumo['novas']} | Bonus aumentou: {resumo['aumentou']} | Excelentes: {resumo['excelentes']}")
    print(f"Arquivo gravado em: {DATA_JSON_PATH}")


if __name__ == "__main__":
    main()
