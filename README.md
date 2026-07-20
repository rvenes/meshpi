# MeshPi

MeshPi er ein liten og stabil Meshtastic-klient for terminalen. Han held eitt
TCP-samband til ein Meshtastic-node ope i bakgrunnen, lagrar meldingar i SQLite
og gir eit nynorsk fullskjermsgrensesnitt og vanlege CLI-kommandoar over SSH.

> [!WARNING]
> MeshPi er framleis i ein tidleg utviklingsfase. Funksjonar, brukargrensesnitt,
> konfigurasjon og lagringsformat kan endre seg, og prosjektet bør testast nøye
> før det blir brukt i kritiske eller produksjonsnære miljø.

Første utgåve har ikkje webgrensesnitt. Kjernen og den lokale IPC-protokollen er
likevel skilde frå CLI-en, slik at eit webgrensesnitt kan leggjast til seinare.

## Funksjonar

- mottek og sender tekst på public kanal 0
- mottek og sender direkte meldingar
- lagrar samtalehistorikk og ulest-status i SQLite
- viser kjende nodar og tilgjengeleg nodeinformasjon
- kan byte mellom lagra, oppdaga eller manuelle TCP- og USB/serielle profilar
- viser RF, MQTT eller «Ukjend» utan å gjette
- viser RSSI, SNR og hoppinformasjon når ho finst
- følgjer ACK/NAK for direkte meldingar når Meshtastic gir sikkert svar
- koplar automatisk til på nytt etter sambandsbrot
- har fullskjerms TUI, vanlege kommandoar og enkel interaktiv chat
- kan køyre kontinuerleg som ein avgrensa systemd-teneste

MeshPi endrar aldri konfigurasjonen på Meshtastic-noden og sender aldri
meldingar automatisk.

## Arkitektur

`meshpi daemon` er bakgrunnstenesta. Ho eig Meshtastic-sambandet og SQLite-fila.
CLI-kommandoane snakkar med tenesta over ein lokal TCP-socket på
`127.0.0.1:8765`. Socketen kan ikkje bindast til ei ekstern adresse.

Dette gjer at meldingar blir tekne imot sjølv om ingen er SSH-innlogga, og at
berre eitt program om gongen bruker TCP-sambandet til radioen.

## Krav

- Python 3.11 eller nyare
- Debian, Ubuntu eller Raspberry Pi OS for anbefalt systemd-drift
- ein Meshtastic-node med TCP aktivert

Standard utviklingsnode er:

```text
10.0.0.152:4403
```

## Lokal utvikling

```bash
git clone https://github.com/rvenes/meshpi.git
cd meshpi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[test,dev]"
cp .env.example .env
pytest
ruff check .
```

Start tenesta i éin terminal:

```bash
source .venv/bin/activate
meshpi daemon
```

Start fullskjermsgrensesnittet i ein annan terminal:

```bash
source .venv/bin/activate
meshpi
```

Du kan òg bruke dei vanlege CLI-kommandoane:

```bash
meshpi status
meshpi nodes
meshpi conversations
```

## Velje Meshtastic-node

Utan argument opnar MeshPi den sist brukte tilkoplinga:

```bash
meshpi
```

Du kan byte TCP-node direkte. Profilen blir lagra og vald før TUI-en opnar:

```bash
meshpi 10.0.0.135
meshpi meshtastic.local
meshpi 10.0.0.135:4403
```

USB/seriell fungerer på same måte. Bruk helst den stabile `by-id`-stien på
Linux:

```bash
meshpi /dev/serial/by-id/usb-Seeed_Studio_XIAO-BOOT_...-if00
```

På Windows kan målet vere til dømes `COM3`. Den eksplisitte forma er òg
tilgjengeleg:

```bash
meshpi connect 10.0.0.135
meshpi connect /dev/ttyACM0 --name "USB-node"
```

Opne den interaktive tilkoplingsveljaren:

```bash
meshpi new
```

Veljaren viser lagra profilar, oppdaga USB-portar og Meshtastic TCP-portar i det
konfigurerte lokalnettet. Skriv for å filtrere eller skrive eit manuelt mål,
bruk `↑`/`↓`, og trykk Enter for å byte og opne TUI-en. Vis profilane utan å
byte:

```bash
meshpi connections
```

Daemonen eig framleis berre eitt radiosamband om gongen. Profilbyte lukkar det
gamle sambandet kontrollert og koplar til det nye utan systemd-omstart.
Meldingshistorikken er felles, medan kvar melding får lagra kva gatewayprofil
ho kom gjennom.

Bluetooth/BLE er ikkje aktivert i denne versjonen. Det blir ei eiga seinare
fase, sidan Linux-tenesta då òg må handtere Bluetooth-oppdaging, paring og
tilgangsrettar på ein føreseieleg måte.

## Fullskjermsgrensesnitt

`meshpi` eller `meshpi tui` opnar samtalelista, den aktive samtalen,
nodedetaljar og ei rullbar nodeliste i same terminalvindauge. Nye meldingar kjem
inn automatisk, og den aktive samtalen rullar ned til den nyaste meldinga. Ein
DM som kjem til ei anna samtale, gir eit synleg varsel. Marker ein node i
høgrepanelet for å vise detaljane, og trykk Enter for å opne DM. «Ny DM» viser
òg heile nodelista og kan filtrerast på namn eller node-ID.

Piltastane flyttar den blå markeringa i ei liste. Trykk Enter for å gjere den
markerte samtalen eller noden aktiv i chatten. Tastane i grensesnittet er:

```text
Tab / Shift+Tab    neste / førre felt: samtalar, chat og nodar
Ctrl+L             flytt markøren til meldingsfeltet
Enter              opne markert samtale/node, eller send tekst i meldingsfeltet
Delete             lukk/arkiver markert DM frå samtalelista
Ctrl+D             søk i nodelista og opne ein ny DM
F2                 flytt markøren til samtalelista
F3                 flytt markøren til nodelista
Ctrl+R             oppdater samtalar og nodar
Ctrl+Q             avslutt
```

Grensesnittet tilpassar seg terminalbreidda. Nodedetaljane blir skjulte først
dersom vindauget er smalt.

Ein lukka DM blir berre skjult frå samtalelista; meldingane blir ikkje sletta.
Opnar du noden frå nodelista, sender ei ny melding, eller får ein ny DM frå
noden, kjem samtalen automatisk tilbake.

I nodeveljaren kan du skrive for å filtrere lista, bruke `↑`/`↓` og trykkje
Enter. Den lokale noden blir ikkje vist som mottakar. Dersom mottakaren ikkje
finst i lista, kan du skrive den fulle node-ID-en og trykkje Enter.

## Kommandoar

### Status og nodar

```bash
meshpi status
meshpi nodes
meshpi nodes --search venes --sort name
meshpi node 710365c8
```

Ei stjerne i nodelista markerer den lokale Meshtastic-noden.

### Historikk

```bash
meshpi conversations
meshpi public
meshpi public --limit 200
meshpi dm 710365c8
```

### Sending

Ingenting blir sendt før ein eksplisitt sendekommando eller Enter i interaktiv
chat:

```bash
meshpi send-public "Test på public kanal 0"
meshpi send-dm 710365c8 "Direkte testmelding"
```

Tekst blir validert som UTF-8 og kan vere maksimalt 237 byte. DM-node-ID må
vere åtte heksadesimale teikn, med eller utan `!`.

### Sanntid og interaktiv chat

Følg alle nye meldingar:

```bash
meshpi watch
```

Følg berre public kanal 0 eller ein DM:

```bash
meshpi watch public
meshpi watch 710365c8
```

Start ein interaktiv samtale:

```bash
meshpi chat public
meshpi chat 710365c8
```

Du kan bruke `!` framfor node-ID, men i Bash må argumentet då stå i enkle
hermeteikn, til dømes `meshpi dm '!710365c8'`. Utan hermeteikn tolkar Bash
utropsteiknet som historikkutviding.

I chatten:

```text
/status   vis sambandsstatus
/nodar    vis nodelista
/hjelp    vis chatkommandoar
/slutt    avslutt
```

### JSON for skript

Legg det globale valet før kommandoen:

```bash
meshpi --json status
meshpi --json nodes
meshpi --json public
meshpi --json watch public
```

JSON-lesing markerer ikkje meldingar som lesne.

## Konfigurasjon

MeshPi les `.env` frå arbeidskatalogen dersom fila finst. Eksisterande
miljøvariablar har prioritet.

```dotenv
MESHTASTIC_HOST=10.0.0.152
MESHTASTIC_PORT=4403
DATABASE_PATH=./data/meshtastic.db
CONNECTIONS_PATH=./data/connections.json
DISCOVERY_SUBNET=10.0.0.0/24
IPC_HOST=127.0.0.1
IPC_PORT=8765
LOG_LEVEL=INFO
```

`IPC_HOST` godtek berre `127.0.0.1`, `::1` eller `localhost`. IPC-tenesta har
ikkje eiga innlogging og skal derfor berre vere tilgjengeleg for lokale,
betrodde brukarar.

`DISCOVERY_SUBNET` avgrensar TCP-søket i `meshpi new`. Nettet kan maksimalt
vere `/22`. Seriell oppdaging brukar systemet si portliste og føretrekkjer
stabile stiar under `/dev/serial/by-id`.

## Anbefalt installasjon med systemd

Desse kommandoane installerer koden under `/opt/meshpi`, databasen under
`/var/lib/meshpi` og konfigurasjonen i `/etc/meshpi.env`. Dei startar ikkje
andre tenester på nytt og krev ikkje omstart av Pi-en.

```bash
sudo useradd --system --home-dir /opt/meshpi --shell /usr/sbin/nologin meshpi
sudo git clone https://github.com/rvenes/meshpi.git /opt/meshpi
sudo python3 -m venv /opt/meshpi/.venv
sudo /opt/meshpi/.venv/bin/pip install /opt/meshpi

sudo install -m 0644 /opt/meshpi/.env.example /etc/meshpi.env
sudo sed -i 's#DATABASE_PATH=.*#DATABASE_PATH=/var/lib/meshpi/meshtastic.db#' \
    /etc/meshpi.env
sudo install -m 0644 /opt/meshpi/meshpi.service /etc/systemd/system/meshpi.service

sudo systemctl daemon-reload
sudo systemctl enable --now meshpi
sudo systemctl status meshpi
```

Gjer CLI-en tilgjengeleg utan å aktivere virtualenv:

```bash
sudo ln -s /opt/meshpi/.venv/bin/meshpi /usr/local/bin/meshpi
```

Følg loggen:

```bash
journalctl -u meshpi -f
```

Stopp eller start berre MeshPi:

```bash
sudo systemctl stop meshpi
sudo systemctl start meshpi
sudo systemctl restart meshpi
```

Systemd-tenesta køyrer som den separate brukaren `meshpi`, har skrivevern på
systemet og får berre skrive til `/var/lib/meshpi`.

## Docker, valfritt

Systemd er anbefalt på Raspberry Pi. Docker-varianten eksponerer ingen port på
verten:

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec meshpi meshpi status
docker compose exec meshpi meshpi chat public
```

## Testar

Alle automatiske testar mockar Meshtastic-sambandet og sender ingenting på
radio:

```bash
pytest
pytest --cov=meshpi --cov-report=term-missing
ruff check .
```

Testane dekkjer mellom anna kanal 0, DM, node-ID, RF/MQTT, duplikatkontroll,
SQLite, sending, ACK/NAK, reconnect og inputvalidering.

## Trygg live-test

Bruk denne rekkjefølgja:

1. Kontroller `meshpi status` og `meshpi nodes` utan å sende.
2. La tenesta ta imot ei manuelt send melding på kanal 0.
3. Send éi tydeleg merkt testmelding med `meshpi send-public`.
4. Send éi tydeleg merkt DM til ein på førehand avtalt node-ID.
5. Kontroller historikk, transportmetadata og eventuell ACK.

Ikkje bruk Meshtastic sine konfigurasjonskommandoar gjennom same TCP-node medan
MeshPi køyrer.

## Lisens

MeshPi er fri programvare distribuert under GNU General Public License,
versjon 3 (`GPL-3.0-only`). Sjå [LICENSE](LICENSE) for dei fullstendige
lisensvilkåra.
