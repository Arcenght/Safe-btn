# SafeBtn: botão de segurança pessoal

O SafeBtn é um **botão de emergência** que avisa pessoas de confiança quando você aperta. Ele foi pensado para ser discreto: um botãozinho no bolso ou na bolsa, sem tela e sem som.

Este documento explica, para qualquer pessoa, **como o conjunto funciona**, **o que é preciso para usar**, **como configurar** e **quais são as limitações atuais**.

> ⚠️ **Aviso importante:** o SafeBtn é um projeto pessoal e **não substitui os serviços oficiais de emergência** (190 Polícia, 192 SAMU, 193 Bombeiros). Ele depende de bateria, Bluetooth, celular e internet funcionando ao mesmo tempo. Leia a seção [Limitações atuais](#9-limitações-atuais) antes de confiar nele.

---

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Como funciona, passo a passo](#2-como-funciona-passo-a-passo)
3. [Os dois tipos de alerta](#3-os-dois-tipos-de-alerta)
4. [Requisitos](#4-requisitos)
5. [Instalação e configuração](#5-instalação-e-configuração)
6. [Como usar no dia a dia](#6-como-usar-no-dia-a-dia)
7. [Para os contatos de confiança](#7-para-os-contatos-de-confiança)
8. [Como testar](#8-como-testar)
9. [Limitações atuais](#9-limitações-atuais)
10. [Problemas comuns](#10-problemas-comuns)
11. [Alternativas ao app no celular](#11-alternativas-ao-app-no-celular)
12. [Estrutura do projeto](#12-estrutura-do-projeto)
13. [Glossário](#13-glossário)

---

## 1. Visão geral

O sistema tem **quatro peças**:

| Peça | O que é | Para que serve |
|---|---|---|
| **Botão físico** | Pequeno aparelho com Bluetooth (firmware em Zephyr/nRF Connect SDK) | Você aperta quando precisa de ajuda |
| **App no celular** (Flutter) | Aplicativo SafeBtn | Recebe o aperto do botão por Bluetooth e repassa à internet, junto com a sua localização |
| **Servidor** (Python/FastAPI) | Programa que roda em um computador ou servidor na internet | Decide quem avisar, em que ordem, e controla as confirmações |
| **ntfy + contatos** | Serviço de notificações e as pessoas que você escolheu | Os contatos recebem o alerta no celular deles e confirmam que vão ajudar |

Resumo em uma frase: **o botão fala com o celular, o celular fala com o servidor, o servidor avisa seus contatos.**

---

## 2. Como funciona, passo a passo

```
  ┌──────────┐  Bluetooth   ┌──────────┐   Internet   ┌──────────┐   ntfy    ┌───────────┐
  │  BOTÃO   │ ───────────▶ │  APP no  │ ───────────▶ │ SERVIDOR │ ────────▶ │ CONTATO 1 │
  │  físico  │ ◀─ confirma ─│ celular  │  + sua       │ (backend)│           │ CONTATO 2 │
  └──────────┘              └──────────┘  localização └──────────┘ ◀─ "Vou   │ CONTATO 3 │
                                                                     ajudar" └───────────┘
```

**1. Você aperta o botão.**
O botão filtra ruídos do contato (*debounce* de 30 ms) e cria um evento com três informações: o **tipo** (toque ou longo), um **número de sequência** e o **tempo desde que ligou**.

**2. O botão envia o evento por Bluetooth (BLE) ao celular.**
Ele usa um tipo de mensagem chamado *indication*, que **exige confirmação do celular**. O evento fica guardado numa fila de até 8 itens (na memória) e só é apagado depois que o celular confirma. Se o celular não estiver por perto, o evento espera. Se a confirmação se perder, o botão reenvia, e por isso podem existir duplicados (o app e o servidor descartam).

**3. O app recebe, confirma ao usuário e envia ao servidor.**
- O celular **vibra uma vez** para confirmar que recebeu o aperto (o botão não tem LED, som ou vibração).
- O app envia `POST /evento` ao servidor **imediatamente**, com a **última localização conhecida** (se houver), sem esperar o GPS.
- Em seguida busca uma **posição GPS nova** (até 20 s) e a envia por `POST /localizacao`.
- Resultado da vibração: **dois toques curtos** = enviado ao servidor; **uma vibração longa** = falhou ou faltou configuração.

**4. O servidor abre um "incidente" e começa a avisar.**
Veja a próxima seção para o escalonamento.

**5. Os contatos recebem uma notificação no celular (via ntfy)** com a hora, o link do mapa e o botão **Confirmar**.

**6. Quando alguém confirma, o escalonamento para**, e você (dono) recebe um aviso "fulano confirmou que vai ajudar".

### Como o servidor escala os avisos

**Toque (alerta normal):**

1. Avisa o **contato 1**.
2. Espera a confirmação por até **120 s** (configurável).
3. Se não confirmou, avisa o **contato 2**, e assim por diante.
4. Se **ninguém** confirmar, o servidor passa para o **alerta grave** e avisa **todos** os contatos.
5. Se o aviso a um contato falhar (ex.: ntfy fora do ar), o servidor passa ao próximo sem esperar.

**Longo (alerta grave):** avisa **todos os contatos de uma vez**, sem esperar ninguém.

### Regras úteis do servidor

- **Incidente em andamento:** durante **15 minutos** (configurável) após o primeiro toque, novos toques são **ignorados** ("já em andamento"). Passado esse tempo, um novo toque abre um novo incidente.
- **Longo durante um incidente:** o incidente vira grave e todos são avisados, mesmo que o escalonamento esteja no meio.
- **Duplicados:** o servidor descarta eventos repetidos (mesmo tipo, sequência e tempo) por 10 minutos.
- **Localização:** a posição enviada pelo app é anexada ao incidente. O servidor também reaproveita uma posição recebida há menos de 5 minutos. A notificação já enviada mostra a localização que existia **naquele momento**; quem abrir a página de confirmação depois vê a posição **mais recente**.
- **Registro:** tudo é gravado em `incidentes.jsonl`.

---

## 3. Os dois tipos de alerta

| Tipo | Como disparar | O que acontece |
|---|---|---|
| **TOQUE** (tipo 1) | Apertar o botão (físico ou na tela do app) | Escalonamento: contato 1, depois 2, depois 3... e, se ninguém confirmar, todos |
| **LONGO** (tipo 2) | **Continuar segurando por 5 segundos** | Alerta **grave**: todos são avisados imediatamente |

Observações:
- O toque sai **assim que você aperta** (para ser rápido). Se você continuar segurando, ao completar 5 s sai também o LONGO. Ou seja, **segurar 5 s gera dois eventos**: TOQUE e depois LONGO.
- No botão da tela do app, aos **3 s** o celular vibra um aviso ("falta pouco") e aos **5 s** vibra mais forte e dispara o LONGO. Um anel de progresso mostra o tempo.
- A mensagem do alerta grave diz que **"a pessoa SEGUROU o botão: situação grave"** e orienta ligar para o 190 se não conseguirem contato.

---

## 4. Requisitos

### 4.1 Hardware

- **Botão físico:** placa com Bluetooth Low Energy compatível com **Zephyr / nRF Connect SDK** (a família Nordic nRF é a indicada) e uma chave (switch) ligada a um pino configurado como `sw0` no devicetree do projeto. Alimentação por bateria.
- **Celular** com Bluetooth LE e GPS:
  - **Android** (recomendado para uso real, ver [limitações](#9-limitações-atuais)), ou
  - **iPhone** (exige um Mac para instalar, ver [seção 5.3](#53-app-no-iphone)).
- **Servidor:** qualquer computador ou VPS que fique **ligado o tempo todo** (um servidor barato na nuvem, um Raspberry Pi em casa etc.).

### 4.2 Software e contas

| Item | Para quê |
|---|---|
| Python **3.10 ou superior** | Rodar o servidor |
| Pacotes `fastapi`, `uvicorn`, `httpx` | Dependências do servidor (`requirements.txt`) |
| **Flutter SDK** compatível com o `pubspec.yaml` (`sdk: ^3.13.5`) | Compilar o app |
| **nRF Connect SDK** (Zephyr) | Compilar e gravar o firmware do botão |
| **App ntfy** nos celulares dos contatos (e no seu, para receber confirmações) | Receber as notificações |
| **Xcode + Mac** | Somente para instalar no iPhone |

### 4.3 Rede

- O **celular do dono** precisa de internet (dados móveis ou Wi-Fi) para falar com o servidor.
- Os **contatos** precisam abrir a página de confirmação. Por isso o endereço `public_url` do servidor precisa ser **acessível pela internet** (VPS, ou um túnel como Cloudflare Tunnel ou ngrok para testes).
- Recomenda-se **HTTPS** (veja as limitações).

### 4.4 Permissões do app

O app pede, e **precisa** dessas permissões para funcionar:

- **Bluetooth** (procurar e conectar ao botão).
- **Localização** (enviar onde você está). No Android, a busca por Bluetooth também depende da permissão de localização/dispositivos próximos.
- **Vibração** (feedback).
- Para funcionar com a **tela bloqueada**, são necessárias permissões e serviços de segundo plano que **ainda não estão implementados** (veja as limitações).

---

## 5. Instalação e configuração

### 5.1 Servidor

```bash
cd backend
pip install -r requirements.txt
cp config.example.json config.json
nano config.json          # edite os campos abaixo
python server.py
```

O `config.json` é lido **da pasta onde você executa o comando** (ou do caminho na variável `SAFEBTN_CONFIG`). Ele é lido **só na inicialização**: reinicie o servidor depois de editar.

| Campo | O que colocar |
|---|---|
| `public_url` | Endereço pelo qual os **contatos** acessam o servidor (ex.: `https://safebtn.exemplo.com`). Em teste local, `http://localhost:8000` |
| `device_token` | Segredo **longo e aleatório**. O mesmo valor vai no app, no campo "Token do botão" |
| `owner_name` | Seu nome, que aparece nos alertas |
| `owner_topic` | Seu tópico ntfy, para você receber "fulano confirmou" |
| `ntfy_server` | Padrão `https://ntfy.sh` (ou seu próprio servidor ntfy) |
| `ntfy_token` | Opcional, para servidores ntfy com autenticação |
| `contacts` | Lista de `{ "name": "...", "topic": "..." }`. **A ordem define quem é avisado primeiro** |
| `ack_timeout_s` | Segundos de espera por confirmação antes de passar ao próximo contato (padrão 120) |
| `incident_window_s` | Duração de um incidente, em segundos (padrão 900 = 15 min) |
| `host` / `port` | Onde o servidor escuta (padrão `0.0.0.0:8000`) |
| `log_file` | Arquivo de registro (padrão `incidentes.jsonl`) |

**Cuidados com o `config.json`:**
- Contém segredos (token e tópicos). **Não coloque no git** e restrinja o acesso (`chmod 600 config.json`).
- Os **tópicos ntfy funcionam como senhas**: quem souber o nome pode ler e publicar nele. Use nomes **longos e aleatórios** (o exemplo já mostra o formato).

Para manter o servidor ligado sempre, rode-o como serviço (por exemplo, com **systemd**, usando `WorkingDirectory=` e `Environment=SAFEBTN_CONFIG=...`).

### 5.2 App no Android

```bash
cd safebtn_app
flutter pub get
flutter run --release
```

Ou gere um APK para instalar: `flutter build apk --release`. No Android o app **não expira**.

Depois de instalar, abra o app, toque na **engrenagem** e preencha:

- **Endereço**: o endereço do servidor como o **celular** o enxerga (ex.: `https://safebtn.exemplo.com`, ou `http://192.168.0.10:8000` na rede local).
- **Token do botão**: o mesmo `device_token` do servidor.

Aceite as permissões de **localização** e **Bluetooth** quando o app pedir.

### 5.3 App no iPhone

1. Precisa de **Mac com Xcode**, cabo USB e Apple ID.
2. Em `ios/Runner/Info.plist`, adicione as chaves de uso de Bluetooth e localização (sem elas o app fecha ao usar esses recursos) e, se o servidor usar `http://`, a exceção de rede:
   `NSBluetoothAlwaysUsageDescription`, `NSLocationWhenInUseUsageDescription`, `NSLocalNetworkUsageDescription`, `NSAppTransportSecurity` e, para tela bloqueada, `UIBackgroundModes` com `bluetooth-central`.
3. Instalação:
   ```bash
   cd safebtn_app
   flutter pub get
   cd ios && pod install && cd ..
   open ios/Runner.xcworkspace   # configure o Team e um Bundle Identifier único
   flutter run --release -d <id-do-iphone>
   ```
4. No iPhone: ative o **Modo de Desenvolvedor** e, na primeira vez, confie no desenvolvedor em Ajustes → Geral → VPN e Gerenciamento de Dispositivo.

**⚠️ Com Apple ID gratuito o app expira em 7 dias** e precisa ser reinstalado (veja as [limitações](#9-limitações-atuais)). A conta paga (US$ 99/ano) dura 1 ano.

### 5.4 Firmware do botão

1. Abra o projeto `safebtn` no **nRF Connect SDK** e compile para a sua placa.
2. Grave na placa.
3. O pino do botão é o alias `sw0` do devicetree (normalmente ajustado no `app.overlay`).
4. Parâmetros no topo do `main.c`:

| Parâmetro | Valor atual | Significado |
|---|---|---|
| `DEBOUNCE_MS` | 30 | Filtro contra ruído do contato |
| `LONG_PRESS_MS` | 5000 | Tempo para virar alerta longo |
| `QUEUE_LEN` | 8 | Eventos guardados na memória enquanto o celular não confirma |
| `SHORT_ON_PRESS` | 1 | O toque sai ao apertar (e não ao soltar) |
| `REQUIRE_ENCRYPTION` | 0 | Sem pareamento/criptografia (veja as limitações) |

O botão aparece no Bluetooth com o nome **`SafeBtn`** e usa o serviço `5a1b0001-7c3d-4e8f-9a2b-1c4d5e6f7a80`, característica `5a1b0002-...` (tipo *indicate*). Esses valores **precisam ser iguais** no firmware, no app (`ble_link.dart`) e no gateway (`safebtn_monitor.py`).

> O valor de `LONG_PRESS_MS` (5 s) existe no firmware **e** no app (`kLongPressMs`). Se mudar em um, mude no outro.

---

## 6. Como usar no dia a dia

### Primeira vez

1. Servidor ligado e configurado.
2. Abra o app, configure **Endereço** e **Token** (engrenagem).
3. Ligue o interruptor **Bluetooth** da tela inicial. O status passa por "procurando o SafeBtn...", "conectando..." e termina em **"conectado (indications ativas)"**.
4. O app **memoriza o botão** encontrado. Nas próximas vezes ele conecta sozinho e reconecta se o sinal cair.
5. Teste (veja a [seção 8](#8-como-testar)).

### Rotina

- Deixe o **Bluetooth do celular ligado**, o **app aberto** (veja as limitações) e o interruptor Bluetooth do app **ligado**.
- **Aperte** o botão para um alerta normal.
- **Segure 5 segundos** para um alerta grave.
- Confirme pela vibração do celular: **1 pulso curto** = o celular recebeu o aperto; **2 toques curtos** = o servidor recebeu; **vibração longa** = falhou.

### Tela do app

- **Endereço do servidor** no topo (ou aviso de que não está configurado).
- **Status do botão** e o interruptor liga/desliga do Bluetooth.
- **Ícone de atualizar**: esquece o botão salvo e procura de novo (útil se você trocar o botão).
- **Botão vermelho "TOQUE"**: faz o mesmo que o botão físico (toque; segure 5 s para o longo).
- **Registro** dos últimos 40 acontecimentos (hora e mensagem), útil para entender o que deu certo ou errado.

---

## 7. Para os contatos de confiança

Cada contato precisa:

1. Instalar o app **ntfy** (Android ou iPhone).
2. **Assinar o tópico** que foi definido para ele no `config.json`.
3. No **Android**, desativar a **otimização de bateria** do ntfy, para as notificações chegarem na hora.
4. Permitir notificações de alta prioridade/som (para que o alerta não passe despercebido).

Quando um alerta chegar:

- A notificação mostra o **nome**, a **hora**, a **localização** (link do Google Maps) e o botão **Confirmar**.
- Ao tocar, abre uma página com o mapa e o botão **"Confirmar: eu vou ajudar"**.
- Ao confirmar, **os avisos param** e o dono é informado. Em alerta grave, os outros contatos também são avisados de quem confirmou.
- **Em situação grave, ligue para o 190** se não conseguir contato com a pessoa.

---

## 8. Como testar

**Teste de ponta a ponta (recomendado fazer toda semana):**

1. Aperte o botão físico.
2. Confirme que o celular **vibra** e o registro do app mostra `BOTÃO: TOQUE`.
3. Confirme que o contato 1 recebeu a notificação e que o dono recebe a confirmação.

**Teste só do servidor, sem botão e sem app:**

```bash
cd backend
python simular.py toque     # inicia o escalonamento
python simular.py longo     # alerta grave: avisa todos
python simular.py toque --lat -23.55 --lon -46.63
```

Avise seus contatos antes de testar, para não gerar susto.

**Ver incidentes recentes** (depuração): `GET /status` com o header `X-Token`.

**Para identificar o botão num scanner Bluetooth** (por exemplo, o app nRF Connect): filtre pelo nome `SafeBtn` ou procure o UUID de serviço acima. Se o botão estiver conectado ao app ou a um gateway, ele **para de aparecer** no scanner (só aceita uma conexão por vez).

---

## 9. Limitações atuais

Leia com atenção: estas são as lacunas reais do projeto hoje.

### Confiabilidade do caminho do alerta

- **O app é um ponto único de falha.** O botão só fala Bluetooth. Sem o app funcionando, conectado e com internet, **o aperto não chega a ninguém**.
- **Sem segundo plano garantido.** O app não possui serviço em segundo plano (no Android, um *foreground service*; no iOS, o tratamento completo de *background modes*). Com a tela bloqueada ou o app fechado, o botão físico **provavelmente não será ouvido**. É o maior risco funcional.
- **Sem reenvio do app ao servidor.** Se o envio falhar (sem internet, servidor fora do ar), o app apenas vibra e registra no log. **Não há fila nem nova tentativa.**
- **O botão não avisa se falhou.** Não tem LED, som nem vibração. Só o celular dá feedback. Sem o app, você aperta e não sente nada.
- **Alertas antigos podem chegar atrasados.** A fila do botão (8 eventos, na memória) é reenviada quando o celular volta. Um aperto de horas atrás pode virar um **falso alarme tardio**. Se o botão reiniciar ou ficar sem bateria, a fila se perde.
- **Alcance:** Bluetooth funciona a poucos metros (cerca de 10 m ou menos com obstáculos). O celular precisa estar com você.

### iPhone

- **Instalação só por Mac/Xcode.**
- **Conta gratuita: o app expira em 7 dias** e para de funcionar sem aviso. Conta paga: 1 ano. Alternativas como SideStore/AltStore renovam sozinhas, mas podem falhar em silêncio.
- **O iOS pode encerrar o app em segundo plano.**
- O iOS não permite o app ligar o Bluetooth sozinho: você liga manualmente.

### Servidor

- **Tudo fica em memória.** Se o servidor reiniciar durante um alerta, os **temporizadores de escalonamento se perdem** e os links de confirmação anteriores deixam de funcionar.
- **Sem HTTPS por padrão.** O exemplo usa `http://`: token e localização trafegam sem criptografia fora da rede local. Em uso real, coloque HTTPS (por exemplo, atrás de um proxy ou túnel).
- **O ntfy público (`ntfy.sh`) é compartilhado.** A segurança dos avisos depende de os **tópicos serem secretos**. Considere um ntfy próprio com autenticação.
- **Uma pessoa, um botão:** o servidor foi pensado para um único dono e um único token de dispositivo.
- **O servidor precisa estar sempre ligado e acessível.** Se ele cair, nada funciona e ninguém é avisado.
- Os avisos dependem da **entrega do ntfy**: se o celular do contato estiver sem internet, sem bateria ou com notificações bloqueadas, ele não será avisado. O servidor só sabe que o **ntfy aceitou** a mensagem, não que ela foi lida.

### Segurança

- **Bluetooth sem pareamento nem criptografia** (`REQUIRE_ENCRYPTION 0`): enquanto desconectado, qualquer celular próximo pode se conectar ao botão. Para uso real, ative a criptografia no firmware e faça o pareamento.
- **Token único** compartilhado entre app e servidor: se vazar, qualquer pessoa pode disparar alertas.
- O `config.json` guarda segredos em texto simples.

### Localização

- O **botão não sabe onde você está**. A posição vem do GPS do celular.
- O primeiro aviso usa a **última posição conhecida**, que pode estar velha ou ausente ("Local: ainda indisponível"). A posição atual chega depois e aparece na página de confirmação.
- Se a permissão de localização for negada ou o GPS estiver desligado, o alerta sai **sem localização**.

### Outras

- O botão não tem indicador de bateria nem aviso de bateria fraca.
- O app mostra o log em português e as mensagens são fixas (sem multilíngue).
- O projeto ainda tem nome e descrição padrão do Flutter no `pubspec.yaml` e README do app.
- Não há integração com serviços oficiais de emergência: **o sistema avisa apenas os contatos cadastrados**.

---

## 10. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Status "Bluetooth desligado" | Bluetooth do celular está desligado | Ligue o Bluetooth; o app espera até 30 s |
| Fica em "procurando o SafeBtn..." | Botão fora de alcance, sem bateria, ou já conectado a outro aparelho | Aproxime; desligue o app/gateway que estiver conectado; use "procurar de novo" |
| "serviço do SafeBtn não encontrado" | Firmware diferente do esperado | Confira os UUIDs no firmware e no app |
| "configure o endereço do backend" + vibração longa | Endereço não preenchido | Engrenagem → preencher Endereço e Token |
| `FALHOU (HTTP 401)` | Token do app diferente do `device_token` | Corrija o token |
| `FALHOU (TimeoutException)` ou erro de conexão | Servidor fora do ar, endereço errado, sem internet | Verifique o servidor e o endereço; no iPhone com `http://`, confira a exceção de rede |
| Status "duplicado" no log do servidor | Normal: evento reenviado pelo botão | Nenhuma ação |
| Contato não recebe notificação | Tópico errado, ntfy sem permissão/bateria otimizada | Confira o tópico e as configurações do ntfy no celular dele |
| Página "Link inválido ou expirado" | O servidor reiniciou ou o link é antigo | Peça um novo alerta |
| Alerta chegou sem localização | GPS desligado, permissão negada ou sem posição prévia | Ligue o GPS e conceda a permissão |
| App no iPhone abre e fecha na hora | Assinatura de 7 dias expirou | Reinstale pelo Xcode |
| "tipo desconhecido" no app | Firmware enviou um tipo diferente de 1 ou 2 | Confira a versão do firmware |

---

## 11. Alternativas ao app no celular

Se o celular não for confiável para você (por exemplo, o iPhone com a expiração de 7 dias), dá para trocar o elo entre o botão e o servidor:

- **Gateway fixo (PC/Raspberry Pi):** o script `safebtn_monitor.py` conecta ao botão, decodifica o evento e envia ao servidor (`--webhook`). Funciona sem expirar, mas **só dentro do alcance do Bluetooth do gateway** (em casa). Antes de usar, é preciso: adicionar o header `X-Token` ao envio, definir uma localização fixa (o gateway não tem GPS) e tratar reenvio e eventos antigos. **No estado atual, o monitor não envia o token e o servidor o rejeitaria.**
- **Android dedicado** com o app atual: não expira e funciona na rua, mas continua dependendo do celular.
- **Botão com conexão própria** (celular LTE-M/NB-IoT, ou Wi-Fi com ESP32): independe de celular, mas exige novo hardware e reescrever o firmware.
- **Web app (PWA):** **não substitui** o app, pois o Safari do iPhone não tem Web Bluetooth; só serviria para o botão virtual na tela.

---

## 12. Estrutura do projeto

```
safebtn_app/               App Flutter
├── pubspec.yaml           Dependências (flutter_blue_plus, geolocator, http, ...)
└── lib/
    ├── main.dart          Interface, botão virtual, envio ao servidor (classe Api)
    └── ble_link.dart      Conexão Bluetooth com o botão (busca, reconexão, decodificação)

safebtn/                   Firmware do botão (Zephyr / nRF Connect SDK)
├── CMakeLists.txt
├── prj.conf               Configuração do Zephyr/Bluetooth
├── app.overlay            Mapeamento de pinos
└── src/main.c             Lógica do botão, fila de eventos, BLE

backend/                   Servidor
├── server.py              API FastAPI: escalonamento, ntfy, confirmação
├── simular.py             Simula o botão para testar sem hardware
├── config.example.json    Modelo de configuração (copiar para config.json)
└── requirements.txt

safebtn_monitor.py         Gateway BLE para PC/Raspberry (alternativa ao app)
```

### API do servidor

| Método e rota | Autenticação | Função |
|---|---|---|
| `POST /evento` | header `X-Token` | Recebe `{tipo, seq, uptime_s, lat?, lon?}` e inicia/escala o incidente |
| `POST /localizacao` | header `X-Token` | Atualiza a posição do incidente em andamento |
| `GET /ack/{token}` | link único do contato | Página com mapa e botão Confirmar |
| `POST /ack/{token}` | link único do contato | Confirma e interrompe o escalonamento |
| `GET /status` | header `X-Token` | Lista incidentes recentes (depuração) |

Formato do pacote Bluetooth do botão (6 bytes, little-endian): `tipo` (1 byte) · `seq` (1 byte) · `uptime_s` (4 bytes).

---

## 13. Glossário

- **BLE (Bluetooth Low Energy):** Bluetooth de baixo consumo, usado em acessórios pequenos com bateria.
- **Indication:** mensagem Bluetooth que exige confirmação de recebimento. Por isso o botão sabe se o celular recebeu.
- **Firmware:** programa que roda dentro do botão físico.
- **Backend / servidor:** programa que roda na internet e coordena os avisos.
- **ntfy:** serviço gratuito de notificações por "tópicos". Quem assina um tópico recebe as mensagens publicadas nele.
- **Tópico:** o "nome do canal" no ntfy. Funciona como uma senha: quem o conhece, acessa.
- **Incidente:** um alerta em andamento, do primeiro toque até acabar a janela de tempo.
- **Escalonamento:** avisar um contato, esperar, e passar ao próximo se ninguém responder.
- **Debounce:** filtro que ignora os "falsos apertos" causados por ruído elétrico no botão.
- **Token:** senha usada pelo app para provar ao servidor que o alerta é legítimo.
- **Gateway:** aparelho fixo (como um Raspberry Pi) que faz a ponte entre o botão e o servidor, no lugar do celular.
