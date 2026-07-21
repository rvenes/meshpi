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
- kan køyre kontinuerleg som systemd-teneste, LaunchAgent eller
  per-brukar-autostart på Windows

MeshPi endrar aldri konfigurasjonen på Meshtastic-noden og sender aldri
meldingar automatisk.

## Arkitektur

`meshpi daemon` er bakgrunnstenesta. Ho eig Meshtastic-sambandet og SQLite-fila.
CLI-kommandoane snakkar med tenesta over ein lokal TCP-socket på
`127.0.0.1:8765`. Socketen kan ikkje bindast til ei ekstern adresse.

Dette gjer at meldingar blir tekne imot sjølv om ingen er SSH-innlogga, og at
berre eitt program om gongen bruker TCP-sambandet til radioen.

## Krav

- Linux med systemd, macOS eller Windows 10/11
- Python 3.11 eller nyare
- ein Meshtastic-node via TCP eller USB/seriell

Ei ny installering har ingen førehandsvald node. Første gong du køyrer
`meshpi`, opnar nodeveljaren automatisk og viser oppdaga TCP- og USB-einingar.
Du kan òg skrive IP, vertsnamn, COM-port eller seriellsti manuelt.

## Installere

Standardvalet er `always`: daemonen startar automatisk og tek imot meldingar
sjølv om TUI-en er lukka.

Linux, inkludert Raspberry Pi OS:

```bash
curl -fLO https://venes.org/meshpi/install-linux.sh
sudo sh install-linux.sh
```

macOS:

```bash
curl -fLO https://venes.org/meshpi/install-macos.sh
sh install-macos.sh
```

Windows PowerShell:

```powershell
Invoke-WebRequest https://venes.org/meshpi/install-windows.ps1 -OutFile install-windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1
```

Vel `session` dersom daemonen berre skal leve medan MeshPi er i bruk:

```bash
# Linux
sh install-linux.sh --mode=session

# macOS
sh install-macos.sh --mode=session
```

På Windows lastar du ned skriptet som vist under og køyrer
`.\install-windows.ps1 -Mode Session`.

Installasjonsskripta lastar ned versjonsmanifest, ei plattformspesifikk låsefil
og MeshPi-pakken over HTTPS. Både låsefila og pakken blir kontrollerte med
SHA-256. Alle Python-avhengigheiter har eksakt versjon og hash og blir
installerte med `pip --require-hashes`. Eksisterande konfigurasjon og data blir
bevarte.

### Transparent installasjon

Direkte piping til `bash`, `sudo bash` eller `iex` betyr at du stoler på
innhaldet webserveren leverer akkurat då. Eit meir transparent alternativ er å
laste ned og lese skriptet først:

`less` er berre ein filvisar. Bla med piltastane og trykk `q` for å lukke han
før du køyrer installasjonskommandoen på neste linje.

```bash
# Linux
curl -fLO https://venes.org/meshpi/install-linux.sh
less install-linux.sh
sudo sh install-linux.sh

# macOS
curl -fLO https://venes.org/meshpi/install-macos.sh
less install-macos.sh
sh install-macos.sh
```

```powershell
# Windows PowerShell
Invoke-WebRequest https://venes.org/meshpi/install-windows.ps1 -OutFile install-windows.ps1
Get-Content .\install-windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1
```

Manifestet er signert med ein separat RSA-3072-utgjevingsnøkkel som er festa i
programmet og installasjonsskripta. Installatøren avviser eit usignert eller
endra manifest før han lastar ned pakken. SHA-256-hashar i det signerte
manifestet bind dessutan MeshPi-pakken, installasjonsskripta og låsefilene til
utgivinga.

Meshtastic sitt vanlege TCP-grensesnitt på port 4403 er ukryptert. Bruk TCP
berre på eit nett du stoler på, eller over VPN/SSH-tunnel. USB/seriell sender
ikkje trafikken over lokalnettet.

Køyr den same kommandoen på nytt for å oppdatere. TUI-en sjekkar
`https://venes.org/meshpi/version.json` ved oppstart. Dersom ein ny versjon
finst, viser chatloggen ei lokal systemmelding med rett kommando. Kommandoen
blir aldri lagd i sendefeltet eller sendt over Meshtastic.

Oppdateringa blir bygd i ei ny versjonsmappe og testa offline før daemonen blir
stoppa. Etter eit atomisk byte blir den nye daemonen helsesjekka. Dersom
helsesjekken feilar, blir førre fungerande versjon sett tilbake automatisk.

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
F1                 vis eller lukk oversikta over alle tastatursnarvegane
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
MESHTASTIC_HOST=
MESHTASTIC_PORT=4403
DATABASE_PATH=./data/meshtastic.db
CONNECTIONS_PATH=./data/connections.json
DISCOVERY_SUBNET=
IPC_HOST=127.0.0.1
IPC_PORT=8765
LOG_LEVEL=INFO
UPDATE_URL=https://venes.org/meshpi/version.json
UPDATE_TIMEOUT=3
BACKGROUND_MODE=always
```

`IPC_HOST` godtek berre `127.0.0.1`, `::1` eller `localhost`. IPC-tenesta har
ikkje eiga innlogging og skal derfor berre vere tilgjengeleg for lokale,
betrodde brukarar.

Når `DISCOVERY_SUBNET` er tom, finn MeshPi det lokale IPv4-nettet automatisk
og søkjer der. Set til dømes `DISCOVERY_SUBNET=192.168.1.0/24` for å avgrense
TCP-søket manuelt. Nettet kan maksimalt vere `/22`. Seriell oppdaging brukar
systemet si portliste og føretrekkjer stabile stiar under `/dev/serial/by-id`.

Set `UPDATE_URL` til tom verdi dersom automatisk oppdateringssjekk skal vere
av. Nettverksfeil under sjekken blir ignorerte og hindrar aldri oppstart.

`BACKGROUND_MODE=always` held daemonen i gang uavhengig av TUI-en.
`BACKGROUND_MODE=session` startar han ved behov og gir val om å stoppe han når
du avsluttar TUI-en med `Ctrl+Q`.

## Drift, filer og avinstallering

Felles kommandoar:

```text
meshpi service status
meshpi service start
meshpi service stop
meshpi doctor --offline
```

På Linux kan `enable`, `disable` og start av ei stoppa systemteneste krevje
`sudo`. `meshpi service stop` prøver først ei kontrollert avslutting over lokal
IPC.

### Linux / Raspberry Pi OS

- programversjonar: `/opt/meshpi/releases/`
- aktiv og førre versjon: `/opt/meshpi/current` og `/opt/meshpi/previous`
- konfigurasjon: `/etc/meshpi.env` (`root:meshpi`, `0640`)
- database og profilar: `/var/lib/meshpi` (`meshpi:meshpi`, `0750`)
- logg: `journalctl -u meshpi -f`
- teneste: `sudo systemctl status|start|stop|restart meshpi`

Avinstaller og bevar data:

```bash
curl -fLO https://venes.org/meshpi/uninstall-linux.sh
sudo sh uninstall-linux.sh
```

Legg til `--purge-data` for å slette konfigurasjon, database og profilar.
For ein session-installasjon bruker du òg `--mode=session`.

### macOS

- program, konfigurasjon, data og loggar:
  `~/Library/Application Support/MeshPi/`
- autostart:
  `~/Library/LaunchAgents/org.venes.meshpi.plist`
- loggar: `meshpi.log` og `meshpi-error.log` i datamappa

```bash
curl -fLO https://venes.org/meshpi/uninstall-macos.sh
sh uninstall-macos.sh
```

Legg til `--purge-data` for å slette personlege data. Avinstalleraren fjernar
berre PATH-linja dersom MeshPi-installatøren sjølv la henne til.

### Windows

- programversjonar: `%LOCALAPPDATA%\MeshPi\releases`
- database og loggar: `%LOCALAPPDATA%\MeshPi\data`
- konfigurasjon: `%APPDATA%\MeshPi\meshpi.env`
- autostart: snarveg i brukaren si Startup-mappe
- prosessvakt: `meshpi-supervisor.ps1`, som startar daemonen på nytt etter krasj

```powershell
Invoke-WebRequest https://venes.org/meshpi/uninstall-windows.ps1 -OutFile uninstall-windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\uninstall-windows.ps1
```

Bruk `-PurgeData` berre når konfigurasjon, database, profilar og loggar også
skal slettast. Skripta toler å bli køyrde fleire gonger.

## Docker, valfritt

Systemd er anbefalt på Raspberry Pi. Docker-varianten eksponerer ingen port på
verten:

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
# Lim resultatet inn som IPC_TOKEN i .env.
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
