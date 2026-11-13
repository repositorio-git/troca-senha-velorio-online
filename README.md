# Troca de Senha — Velório Online

Substitui o robô VELORIO_ONLINE do Rocketbot. Toda segunda às 04h: gera senhas de 8 dígitos, grava nos
cadastros de Empreendimentos do WordPress, confere, registra no `Log_VO.txt` (senhas mascaradas) e manda o
e-mail "Velório Online" para cada unidade. Se algo falhar, a TI recebe um alerta.

Tudo está no `main.py`. Unidades, destinatários e IDs dos campos ficam no topo do arquivo, e o layout do
e-mail fica no `email_unidade.html`.

## Instalação

```cmd
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
notepad .env
```

Precisa do Google Chrome instalado. O Selenium baixa o chromedriver sozinho.

## Uso

```cmd
.venv\Scripts\python main.py --dry-run --mostrar-navegador
```
Testa o login no SMTP e no WordPress e mostra as senhas atuais. Não grava nada e não envia nada.

```cmd
.venv\Scripts\python main.py --somente "Cristo Rei" --email-teste seu.email@cortel.com.br
```
**Grava de verdade**, mas o e-mail vai só para você. Repasse a senha para a unidade.

```cmd
.venv\Scripts\python main.py
```
Execução normal. Saída: `0` ok, `1` alguma unidade falhou, `2` não conseguiu começar. Os logs e os
screenshots de erro ficam em `logs\` (os screenshots podem mostrar as senhas novas).

## Agendamento

```cmd
schtasks /Create /TN "Velorio Online - Troca de Senhas" /SC WEEKLY /D MON /ST 04:00 ^
  /TR "\"C:\codes\PycharmProjects\troca-senha-velorio-online\.venv\Scripts\python.exe\" \"C:\codes\PycharmProjects\troca-senha-velorio-online\main.py\"" ^
  /RU <USUARIO_DO_SERVIDOR> /RP
```

Use um usuário com acesso ao `\\dcsrv01\Deptos\...`. **Desative o agendamento do Rocketbot antes**, senão as
unidades recebem dois e-mails com senhas diferentes.
