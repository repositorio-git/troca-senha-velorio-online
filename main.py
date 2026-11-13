"""Troca semanal das senhas do Velório Online (segunda-feira, 04h).

Gera as senhas, grava nos cadastros de Empreendimentos do WordPress, confere se
ficaram salvas, registra no Log_VO e manda o e-mail de cada unidade.
Se alguma unidade falhar, a TI recebe um alerta.

    python main.py                  execução normal
    python main.py --dry-run        só faz login e mostra as senhas atuais (não grava, não envia)
    python main.py --somente "Cristo Rei" --email-teste voce@cortel.com.br
                                    grava de verdade, mas manda o e-mail só para você

Códigos de saída: 0 = ok | 1 = alguma unidade falhou | 2 = não conseguiu nem começar
"""

import argparse
import html
import logging
import os
import secrets
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ================================ CONFIGURAÇÃO ================================

WP_URL = "https://cortel.com.br"
# ID do campo de senha no repeater "Capelas" do ACF. Linha 0 = Capela 1 (ou única), linha 1 = Ecumênica.
CAMPO_SENHA = "acf-field_5f08d80f6fe1b-row-{linha}-field_5f08d8c36fe1e"

SMTP_HOST, SMTP_PORTA = "smtp.office365.com", 587
REMETENTE = "cortex@cortel.com.br"
ASSUNTO = "Velório Online"

LOG_VO = r"\\dcsrv01\Deptos\Informatica\Velorio_Online\Log_VO.txt"
ALERTA_TI = ["ti.logs@cortel.com.br"]
CCO = ["david.batista@cortel.com.br", "ti.logs@cortel.com.br"]

# Uma unidade = um e-mail. "capelas" define quantas senhas: rótulo vazio = capela única.
# No São José, os dois cadastros recebem as mesmas senhas.
UNIDADES = [
    {
        "nome": "São José",
        "cadastros": {5412: "Crematório Metropolitano", 5659: "São José"},
        "capelas": ["Capela 1", "Ecumênica"],
        "para": ["atendimento.saojose@cortel.com.br", "cerimonial.saojose@cortel.com.br",
                 "vanessa.perez@cortel.com.br", "lori.fascio@cortel.com.br", "andreia.pires@cortel.com.br"],
    },
    {
        "nome": "São Vicente",
        "cadastros": {5624: "São Vicente"},
        "capelas": [""],
        "para": ["atendimento.saovicente@cortel.com.br", "cerimonial.saovicente@cortel.com.br",
                 "olavo.hider@cortel.com.br", "caroline.leao@cortel.com.br", "andreia.pires@cortel.com.br"],
    },
    {
        "nome": "Saint Hilaire",
        "cadastros": {5636: "Saint Hilaire"},
        "capelas": [""],
        "para": ["atendimento.sainthilaire@cortel.com.br", "cerimonial.sainthilaire@cortel.com.br",
                 "vanessa.silveira@cortel.com.br", "cristina.neves@cortel.com.br", "andreia.pires@cortel.com.br"],
    },
    {
        "nome": "Memorial da Colina",
        "cadastros": {5643: "Memorial da Colina"},
        "capelas": [""],
        "para": ["atendimento.colina@cortel.com.br", "cerimonial.memorialdacolina@cortel.com.br",
                 "fabiane.atkinson@cortel.com.br", "wagner.molina@cortel.com.br", "andreia.pires@cortel.com.br"],
    },
    {
        "nome": "Cristo Rei",
        "cadastros": {5650: "Cristo Rei"},
        "capelas": [""],
        "para": ["atendimento.cristorei@cortel.com.br", "cerimonial.cristorei@cortel.com.br",
                 "ronaldo.vasconcelos@cortel.com.br", "andreia.pires@cortel.com.br"],
    },
]

# ==============================================================================

RAIZ = Path(__file__).resolve().parent
PASTA_LOGS = RAIZ / "logs"
log = logging.getLogger("velorio")


def mascarar(senha):
    return senha[:2] + "****" + senha[-2:]


def descrever(erro):
    """Primeira linha do erro (as mensagens do Selenium vêm com stacktrace junto)."""
    linhas = (getattr(erro, "msg", None) or str(erro)).strip().splitlines()
    return html.escape(f"{type(erro).__name__}: {linhas[0] if linhas else ''}")


# --------------------------------- WordPress ---------------------------------

def abrir_navegador(visivel=False):
    opcoes = webdriver.ChromeOptions()
    if not visivel:
        opcoes.add_argument("--headless=new")
    opcoes.add_argument("--window-size=1600,1000")
    # Aceita o "Sair do site? As alterações podem não ser salvas" do WordPress.
    opcoes.set_capability("unhandledPromptBehavior", "accept")
    driver = webdriver.Chrome(options=opcoes)
    driver.set_page_load_timeout(60)
    return driver


def esperar(driver, condicao, segundos=30):
    return WebDriverWait(driver, segundos).until(condicao)


def login(driver):
    driver.get(f"{WP_URL}/wp-login.php")
    usuario = esperar(driver, EC.presence_of_element_located((By.ID, "user_login")))
    usuario.clear()
    usuario.send_keys(os.environ["WP_USUARIO"])
    driver.find_element(By.ID, "user_pass").send_keys(os.environ["WP_SENHA"])
    driver.find_element(By.ID, "wp-submit").click()

    esperar(driver, EC.any_of(
        EC.presence_of_element_located((By.ID, "wpadminbar")),
        EC.presence_of_element_located((By.ID, "login_error")),
    ))
    erro = driver.find_elements(By.ID, "login_error")
    if erro:
        raise RuntimeError(f"WordPress recusou o login: {erro[0].text.strip()}")
    log.info("Login no WordPress ok.")


def abrir_cadastro(driver, post_id):
    driver.get(f"{WP_URL}/wp-admin/post.php?post={post_id}&action=edit")
    return esperar(driver, EC.element_to_be_clickable((By.ID, "publish")))


def ler_senhas(driver, quantidade):
    return [driver.find_element(By.ID, CAMPO_SENHA.format(linha=i)).get_attribute("value").strip()
            for i in range(quantidade)]


def gravar_senhas(driver, post_id, senhas):
    """Abre o cadastro, troca as senhas, clica em Atualizar e confere o que ficou gravado."""
    botao = abrir_cadastro(driver, post_id)
    for linha, senha in enumerate(senhas):
        campo = driver.find_element(By.ID, CAMPO_SENHA.format(linha=linha))
        campo.clear()
        campo.send_keys(senha)

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", botao)
    # Clique normal de propósito: se o cadastro estiver travado por outra pessoa, o aviso
    # do WordPress fica na frente do botão e o clique falha em vez de salvar por cima.
    botao.click()
    esperar(driver, EC.staleness_of(botao))  # página recarregou
    esperar(driver, EC.presence_of_element_located((By.CSS_SELECTOR, "#message.updated, #message.notice-success")))

    if ler_senhas(driver, len(senhas)) != senhas:
        raise RuntimeError(f"Post {post_id}: a senha não ficou gravada depois de salvar.")


def screenshot(driver, nome):
    try:
        PASTA_LOGS.mkdir(exist_ok=True)
        driver.save_screenshot(str(PASTA_LOGS / f"{datetime.now():%Y%m%d_%H%M%S}_{nome}.png"))
    except Exception:
        pass


# ---------------------------------- E-mail -----------------------------------

def conectar_smtp():
    smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORTA, timeout=60)
    smtp.starttls()
    smtp.login(REMETENTE, os.environ["EMAIL_SENHA"])
    return smtp


def enviar_email(para, assunto, html, cco=()):
    msg = EmailMessage()
    msg["From"] = REMETENTE
    msg["To"] = ", ".join(para)
    msg["Subject"] = assunto
    msg["Date"] = formatdate(localtime=True)
    msg.set_content("Este e-mail precisa de um leitor com suporte a HTML.")
    msg.add_alternative(html, subtype="html")
    with conectar_smtp() as smtp:
        # A cópia oculta vai só no envelope, nunca no cabeçalho.
        smtp.send_message(msg, to_addrs=[*para, *cco])


CARD = """        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:18px; border-collapse:collapse;">
          <tr><td style="border:1px solid #e5e7eb; padding:22px; background:#f9fafb;">{rotulo}
            <p style="margin:0; font-size:30px; font-weight:700; color:#042554; letter-spacing:3px; text-align:center; font-family:'Ubuntu', Arial, sans-serif;">{senha}</p>
          </td></tr>
        </table>
"""
ROTULO = """
            <p style="margin:0 0 8px 0; font-size:13px; color:#6b7280; text-transform:uppercase; letter-spacing:0.8px; font-family:'Ubuntu', Arial, sans-serif;">{texto}</p>"""


def montar_email(capelas, senhas):
    cards = "".join(
        CARD.format(rotulo=ROTULO.format(texto=rotulo) if rotulo else "", senha=senha)
        for rotulo, senha in zip(capelas, senhas)
    )
    template = (RAIZ / "email_unidade.html").read_text(encoding="utf-8")
    return template.replace("$cards", cards)


# ---------------------------------- Processo ---------------------------------

def registrar_log_vo(cadastro, capelas, senhas):
    linhas = [f"Empreendimento: {cadastro}", f"Data: {datetime.now():%d/%m/%Y - %H:%M:%S}"]
    for rotulo, senha in zip(capelas, senhas):
        linhas.append(f"Senha {rotulo}: {mascarar(senha)}" if rotulo else f"Senha: {mascarar(senha)}")
    linhas.append("-" * 57)
    try:
        with open(LOG_VO, "a", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")
    except OSError as e:
        log.warning("Não consegui gravar no Log_VO (%s): %s", cadastro, e)


def gerar_senhas(quantidade):
    """Senhas numéricas de 8 dígitos, distintas entre si."""
    senhas = []
    while len(senhas) < quantidade:
        s = str(10_000_000 + secrets.randbelow(90_000_000))
        if s not in senhas:
            senhas.append(s)
    return senhas


def main():
    parser = argparse.ArgumentParser(description="Troca semanal de senhas do Velório Online")
    parser.add_argument("--dry-run", action="store_true", help="Só lê as senhas atuais. Não grava nem envia nada.")
    parser.add_argument("--somente", nargs="+", metavar="UNIDADE", help='Nomes das unidades, ex.: "Cristo Rei"')
    parser.add_argument("--email-teste", metavar="EMAIL", help="Manda todos os e-mails só para este endereço.")
    parser.add_argument("--mostrar-navegador", action="store_true")
    args = parser.parse_args()

    load_dotenv(RAIZ / ".env")
    PASTA_LOGS.mkdir(exist_ok=True)
    arquivo_log = PASTA_LOGS / f"execucao_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(arquivo_log, encoding="utf-8")],
    )

    unidades = UNIDADES
    if args.somente:
        unidades = [u for u in UNIDADES if u["nome"] in args.somente]
        if len(unidades) != len(args.somente):
            log.error("Unidade desconhecida. Opções: %s", ", ".join(u["nome"] for u in UNIDADES))
            return 2

    def email_unidade(unidade, senhas):
        html = montar_email(unidade["capelas"], senhas)
        if args.email_teste:
            enviar_email([args.email_teste], f"[TESTE] {ASSUNTO} - {unidade['nome']}", html)
        else:
            enviar_email(unidade["para"], ASSUNTO, html, cco=CCO)
        log.info("%s: e-mail enviado.", unidade["nome"])

    # Testa o e-mail ANTES de mexer no WordPress: se o SMTP estiver fora, nenhuma senha
    # é trocada (senão as unidades ficariam com senha nova sem saber qual é).
    driver = None
    try:
        conectar_smtp().quit()
        driver = abrir_navegador(args.mostrar_navegador)
        login(driver)
    except Exception as e:
        log.exception("Não foi possível iniciar.")
        if driver:
            screenshot(driver, "erro_inicio")
            driver.quit()
        try:
            enviar_email(ALERTA_TI, "[FALHA] Velório Online - troca semanal de senhas",
                         f"<p>A troca de senhas não começou: {descrever(e)}</p><p>Nenhuma senha foi alterada.</p>"
                         f"<p>Log: {arquivo_log}</p>")
        except Exception:
            log.exception("Também não foi possível avisar a TI por e-mail.")
        return 2

    falhas = []
    try:
        for unidade in unidades:
            nome, capelas = unidade["nome"], unidade["capelas"]

            if args.dry_run:
                for post_id, cadastro in unidade["cadastros"].items():
                    abrir_cadastro(driver, post_id)
                    atuais = ler_senhas(driver, len(capelas))
                    log.info("[DRY-RUN] %s: senhas atuais %s", cadastro, [mascarar(s) for s in atuais])
                continue

            # As senhas são geradas uma vez: se precisar tentar de novo, usa as mesmas
            # e pula os cadastros já salvos (o São José nunca fica com senhas diferentes).
            senhas = gerar_senhas(len(capelas))
            salvos = set()
            for tentativa in (1, 2):
                try:
                    if driver is None:
                        driver = abrir_navegador(args.mostrar_navegador)
                        login(driver)
                    for post_id, cadastro in unidade["cadastros"].items():
                        if post_id not in salvos:
                            gravar_senhas(driver, post_id, senhas)
                            salvos.add(post_id)
                            log.info("%s: senhas gravadas e conferidas.", cadastro)
                            registrar_log_vo(cadastro, capelas, senhas)
                    email_unidade(unidade, senhas)
                    break
                except Exception as e:
                    log.exception("%s: falhou na tentativa %d.", nome, tentativa)
                    if driver:
                        screenshot(driver, f"erro_{nome}")
                        driver.quit()
                        driver = None
                    erro = e
            else:
                ja_gravados = [c for p, c in unidade["cadastros"].items() if p in salvos]
                falhas.append((nome, erro, ja_gravados))
    finally:
        if driver:
            driver.quit()

    if falhas:
        itens = "".join(
            f"<li><b>{nome}</b>: {descrever(erro)}"
            + (f"<br>Senha nova JÁ GRAVADA em: {', '.join(gravados)} (consulte no WordPress), "
               "mas a unidade NÃO recebeu o e-mail." if gravados else "")
            + "</li>"
            for nome, erro, gravados in falhas
        )
        try:
            enviar_email(ALERTA_TI, "[FALHA] Velório Online - troca semanal de senhas",
                         f"<p>Unidades com falha:</p><ul>{itens}</ul>"
                         f"<p>Siga a execução manual (Documentação de Negócio, seção 5.2).</p>"
                         f"<p>Log: {arquivo_log}</p>")
        except Exception:
            log.exception("Não foi possível enviar o alerta para a TI.")
        return 1

    log.info("Fim: tudo certo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
