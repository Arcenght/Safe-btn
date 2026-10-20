# SafeBtn backend

## Subir
1. `pip install -r requirements.txt`
2. Copie `config.example.json` para `config.json` e edite:
   - `device_token`: segredo longo (so o botao/app deve saber)
   - `contacts`: nome + topico ntfy de cada contato (nomes longos e aleatorios)
   - `owner_topic`: seu proprio topico, para receber "fulano confirmou"
   - `public_url`: endereco pelo qual os CONTATOS acessam o servidor (pagina de confirmacao)
3. `python server.py`

## Cada contato
Instala o app **ntfy** e assina o topico dele. Android: desativar a otimizacao de
bateria do app.

## Testar sem o botao
    python simular.py toque     # inicia o escalonamento
    python simular.py longo     # escalada grave: avisa todos

## Importante
- Para os contatos abrirem o botao "Confirmar", `public_url` precisa ser acessivel
  de fora (VPS, ou um tunel como Cloudflare Tunnel / ngrok para teste).
- Se o servidor reiniciar durante um alerta, os timers em andamento se perdem.
- Os eventos do botao NAO trazem localizacao; ela vem do app (`lat`/`lon` no evento
  ou `POST /localizacao`).
