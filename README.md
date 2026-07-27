# MeshPi

MeshPi er ein liten og stabil Meshtastic-klient for terminalen. Han held eitt
TCP-samband til ein Meshtastic-node ope i bakgrunnen, lagrar meldingar i SQLite
og gir eit nynorsk fullskjermsgrensesnitt og vanlege CLI-kommandoar over SSH.

> [!WARNING]
> MeshPi er framleis i ein tidleg utviklingsfase. Funksjonar, brukargrensesnitt,
> konfigurasjon og lagringsformat kan endre seg, og prosjektet bør testast nøye
> før det blir brukt i kritiske eller produksjonsnære miljø.

Sjå [DEVELOPMENT.md](DEVELOPMENT.md) for utviklingshistorikk, arkitekturval og
prioritert roadmap for mellom anna fleirkanalar og Blåtann/BLE.

Første utgåve har ikkje webgrensesnitt. Kjernen og den lokale IPC-protokollen er
likevel skilde frå CLI-en, slik at eit webgrensesnitt kan leggjast til seinare.

## Funksjonar

- mottek og sender tekst på alle aktiverte Meshtastic-kanalar
- mottek og sender direkte meldingar på ei tydeleg kanalrute
- lagrar samtalehistorikk og ulest-status i SQLite
- viser kjende nodar og tilgjengeleg nodeinformasjon
- loggar motteken telemetri og GPS-posisjonar per kjeldenode i SQLite
- samlar nodeinfo, telemetri, posisjonar og traceroute-logg i éi vising
- kan byte mellom lagra, oppdaga eller manuelle TCP- og USB/serielle profilar
- viser RF, MQTT eller «Ukjend» utan å gjette
- viser RSSI, SNR og hoppinformasjon når ho finst
- skil vanleg/implisitt ACK frå ende-til-ende-ACK til DM-mottakaren
- koplar automatisk til på nytt etter sambandsbrot
- har fullskjerms TUI, vanlege kommandoar og enkel interaktiv chat
- kan laste ned og installere ei fullstendig signatur- og hashkontrollert
  oppdatering utan å køyre ein kommando frå manifestet
- kan køyre kontinuerleg som systemd-teneste, LaunchAgent eller
  per-brukar-autostart på Windows

MeshPi endrar aldri konfigurasjonen på Meshtastic-noden og sender aldri
meldingar automatisk.

## Arkitektur

`meshpi daemon` er bakgrunnstenesta. Ho eig Meshtastic-sambandet og SQLite-fila.
På Linux og macOS snakkar CLI-kommandoane med tenesta over ein privat
Unix-socket. På Windows bruker MeshPi ein eksklusivt reservert TCP-socket på
`127.0.0.1:8765`. TCP-socketen kan ikkje bindast til ei ekstern adresse.
IPC-tokenet blir kontrollert for begge transportane.

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

På ein minimal Debian-/Ubuntu-installasjon må du først ha `curl`. Installer òg
den vanlege `venv`-pakken; installatøren kontrollerer den valde
Python-versjonen og viser rett versjonspakke dersom ho manglar:

```bash
sudo apt update
sudo apt install curl python3-venv
curl -fLO https://venes.org/meshpi/install-linux.sh
sudo sh install-linux.sh
```

Installatøren installerer aldri systempakkar automatisk. Dersom til dømes
Python 3.14 manglar `venv`, får du kommandoen
`sudo apt install python3.14-venv` før MeshPi-filer blir lasta ned.

macOS:

```bash
curl -fLO https://venes.org/meshpi/install-macos.sh
sh install-macos.sh
```

Køyr aldri macOS-installatøren med `sudo`; han avviser root for å hindre ei
utilsikta installering under `/var/root`.

Windows PowerShell:

```powershell
Invoke-WebRequest https://venes.org/meshpi/install-windows.ps1 -OutFile install-windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1
```

Vel `session` dersom daemonen berre skal leve medan MeshPi er i bruk. MeshPi
lagrar alle meldingar han faktisk tek imot i session-modus, men kan ikkje
garantere at noden leverer meldingar som kom medan MeshPi var avslutta:

```bash
# Linux
curl -fLO https://venes.org/meshpi/install-linux.sh
sh install-linux.sh --mode=session

# macOS
curl -fLO https://venes.org/meshpi/install-macos.sh
sh install-macos.sh --mode=session
```

På Windows PowerShell:

```powershell
Invoke-WebRequest https://venes.org/meshpi/install-windows.ps1 -OutFile install-windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1 -Mode Session
```

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
sudo apt install curl python3-venv
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

Etter første installasjon bruker du den innebygde, kontrollerte oppdateraren:

```bash
# Linux, always-modus
sudo meshpi update

# Linux session-modus og macOS
meshpi update
```

```powershell
# Windows
meshpi update
```

Bruk `meshpi update --check` for berre å sjekke. Sjølve installasjonen krev at
du skriv `OPPDATER`, eller at du legg til `--yes` for ei uttrykkeleg
ikkje-interaktiv stadfesting.

TUI-en sjekkar `https://venes.org/meshpi/version.json` ved oppstart og viser
`meshpi update` i ei lokal systemmelding når ein ny versjon finst. Kommandoen
blir aldri lagd i sendefeltet eller sendt over Meshtastic. Oppdateraren
verifiserer manifestsignaturen, lastar ned installatøren, låsefila og
wheel-pakken til ei privat mellombels mappe, kontrollerer signert storleik og
SHA-256 og køyrer berre den lokalt verifiserte installatøren. Kommandoar i
manifestet blir aldri køyrde.

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

USB/seriell fungerer på same måte. MeshPi lagrar USB-identiteten når eininga
oppgir eit serienummer. Dersom macOS eller Windows seinare gir den same fysiske
eininga ein ny seriellsti eller COM-port, flyttar MeshPi profilen automatisk
berre når identiteten gir eitt eintydig treff. Ved tvil må du velje porten på
nytt. Bruk helst den stabile `by-id`-stien på Linux:

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
bruk `↑`/`↓`, og trykk Enter for å byte og opne TUI-en. Ein lagra seriellprofil
blir merkt `IKKJE TILKOPLA` og lagd nedst dersom USB-identiteten ikkje finst.
Innebygde Linux-portar som `/dev/ttyS*` er skjulte som standard; trykk F4 for å
vise dei. Vis profilane utan å byte:

```bash
meshpi connections
```

Daemonen eig framleis berre eitt radiosamband om gongen. Profilbyte lukkar det
gamle sambandet kontrollert og koplar til det nye utan systemd-omstart.
Meldingshistorikken ligg i éin database. Kanalar blir berre samla når ein trygg
Meshtastic-kanal-ID viser at dei er den same logiske kanalen. Kvar melding får
dessutan lagra lokal node, gatewayprofil og kanalrute. Same melding motteken
via fleire gatewayar blir vist éin gong, men alle mottaka blir logga.

MeshPi sender berre på kanalar som er stadfesta i kanaloversikta til den aktive
noden. Ei historisk, ukjend eller mellombels rute er lesbar, men ikkje sendbar,
og fell aldri stille tilbake til kanal 0. Dersom ei melding kjem før
kanaloversikta er klar, blir ho lagra på ei mellombels rute og automatisk bunden
om når noden leverer den verkelege kanalbindinga.

Bluetooth/BLE er ikkje aktivert i denne versjonen. Det blir ei eiga seinare
fase, sidan Linux-tenesta då òg må handtere Bluetooth-oppdaging, paring og
tilgangsrettar på ein føreseieleg måte.

## Fullskjermsgrensesnitt

`meshpi` eller `meshpi tui` opnar samtalelista, den aktive samtalen,
nodedetaljar og ei rullbar nodeliste i same terminalvindauge. Nye meldingar kjem
inn automatisk, og den aktive samtalen rullar ned til den nyaste meldinga. Ein
DM som kjem til ei anna samtale, gir eit synleg varsel. Marker ein node i
høgrepanelet for å vise detaljane, og trykk Enter for å opne DM. «Ny DM» viser
òg heile nodelista og kan filtrerast på namn eller node-ID. Høgreklikk på
ein node, eller marker han og trykk `Shift+F10`, for å opne nodehandlingane.
Her kan du mellom anna sende traceroute. Status og resultat kjem som ei tydeleg
ramme i DM-samtalen med noden, slik at resten av appen kan brukast medan du
ventar. Vel «Nodeinfo og loggar» i den same menyen for ei samla vising med
oversikt, telemetri, posisjonslogg og traceroute-logg. Posisjonane har ei
Google Maps-lenkje som terminalen kan opne lokalt når han støttar lenkjer;
heile URL-en blir òg vist slik at han kan kopierast over SSH.
På posisjonsfana kan du uttrykkeleg velje «Utveksle posisjonsdata». Då sender
MeshPi siste kjende posisjon for den lokale noden dersom ho finst og
posisjonsdeling er slått på for kanalen. Koordinatane bruker den konfigurerte
presisjonen. Dersom deling er slått av eller presisjonen er ukjend, blir berre
førespurnaden send. Varselet fortel om eigen posisjon faktisk blei delt.
Målnoden blir i begge tilfelle beden om å svare med sin posisjon. På
traceroute-fana kan du starte ein ny traceroute direkte. Ingen av handlingane
blir sende automatisk.

Resultatet frå traceroute viser ruta fram, eventuell returrute og SNR per hopp
når nodane rapporterer dette. Traceroute-forsøk og resultat blir lagra i den
lokale loggen og viste igjen både i DM-samtalen og nodeinfo. Fastvaren tillèt
éin traceroute kvart 30. sekund;
nodehandlinga er sperra og viser ei nedteljing til neste forsøk kan sendast.
Meldingar frå tidlegare datoar viser dato som `21.07.26` i svak grå tekst før
klokkeslettet; meldingar frå i dag viser berre klokkeslett.

Piltastane flyttar den blå markeringa i ei liste. Trykk Enter for å gjere den
markerte samtalen eller noden aktiv i chatten. Tastane i grensesnittet er:

```text
F1                 vis eller lukk denne oversikta
Tab / Shift+Tab    flytt mellom samtalar, melding og nodar
Enter              opne vald samtale/node eller send melding
↑ / ↓              naviger i den aktive lista
Ctrl+L             flytt markøren til meldingsfeltet
Ctrl+D             finn ein node og start ein ny DM
F2                 flytt markøren til samtalelista
F3                 flytt markøren til nodelista
Shift+F10          opne handlingar for markert node
Delete             lukk vald DM utan å slette historikken
Ctrl+R             oppdater status, samtalar og nodar
Ctrl+U             kopier oppdateringskommandoen når ein ny versjon finst
Ctrl+Q             avslutt MeshPi og vel kva som skjer med daemonen
Esc                lukk dialogen som er open
```

Grensesnittet tilpassar seg terminalbreidda. Nodedetaljane blir skjulte først
dersom vindauget er smalt.

Sendestatusen på ein DM går frå `[sendt]` til `[ACK]` når Meshtastic gir ein
vanleg eller implisitt ACK. Det viser at pakken er teken vidare i nettet, men er
ikkje eit leveringsbevis. Først ein routing-ACK frå den valde DM-mottakaren gir
`[levert]`. Ein NAK gir `[feila]`. «transport ukjend» tyder at pakken ikkje har
metadata som beviser om transporten var RF eller MQTT; MeshPi gjettar ikkje.

Ein lukka DM blir berre skjult frå samtalelista; meldingane blir ikkje sletta.
Opnar du noden frå nodelista, sender ei ny melding, eller får ein ny DM frå
noden, kjem samtalen automatisk tilbake.

I nodeveljaren kan du skrive for å filtrere lista, bruke `↑`/`↓` og trykkje
Enter. Den lokale noden blir ikkje vist som mottakar. Dersom mottakaren ikkje
finst i lista, kan du skrive den fulle node-ID-en og trykkje Enter.

## Kommandoar

Alle CLI-kommandoane:

| Kommando | Kva han gjer |
|---|---|
| `meshpi` eller `meshpi tui` | Start fullskjermsgrensesnittet. |
| `meshpi new` | Oppdag, vel eller legg til ei tilkopling, og opne TUI-en. |
| `meshpi connect MÅL [--name NAMN]` | Byt til ei TCP- eller serielltilkopling. |
| `meshpi connections` | Vis lagra tilkoplingsprofilar. |
| `meshpi daemon` | Køyr bakgrunnstenesta i framgrunnen; mest for feilsøking og tenesteoppsett. |
| `meshpi doctor [--offline]` | Køyr sjølvtest; `--offline` krev ikkje ein tilgjengeleg node. |
| `meshpi service {status,start,stop,enable,disable}` | Vis eller styr bakgrunnstenesta og autostart. |
| `meshpi update [--check] [--yes]` | Sjekk eller installer ei signert og hashkontrollert oppdatering. |
| `meshpi status` | Vis sambands- og tilkoplingsstatus. |
| `meshpi nodes [--search TEKST] [--sort name\|seen\|id]` | Vis, filtrer og sorter kjende nodar. |
| `meshpi node NODE-ID` | Vis alle lagra detaljar om éin node. |
| `meshpi conversations` | Vis samtalar og talet på uleste meldingar. |
| `meshpi channels` | Vis trygge kanalnamn, indeksar og samtale-ID-ar på aktiv node. |
| `meshpi delete-messages {public,dm,all} [--yes]` | Slett meldingar i valt omfang. |
| `meshpi public [--channel INDEKS\|SAMTALE-ID] [--limit TAL]` | Vis meldingar frå ein public-kanal. |
| `meshpi dm NODE-ID [--channel INDEKS] [--limit TAL]` | Vis DM-historikken med éin node. |
| `meshpi send-public TEKST [--channel INDEKS\|SAMTALE-ID]` | Send til vald public-kanal. |
| `meshpi send-dm NODE-ID TEKST [--channel INDEKS]` | Send ein DM via vald kanal. |
| `meshpi watch [all\|public\|SAMTALE-ID\|NODE-ID]` | Følg nye meldingar i sanntid. |
| `meshpi chat {public\|SAMTALE-ID\|NODE-ID} [--limit TAL]` | Start interaktiv linjebasert chat. |

Globale val skal stå før kommandoen:

| Val | Kva det gjer |
|---|---|
| `-h`, `--help` | Vis hjelp for hovudkommandoen eller ein underkommando. |
| `--version` | Vis installert MeshPi-versjon. |
| `--env-file FIL` | Les konfigurasjon frå ei anna miljøfil enn `.env`. |
| `--json` | Skriv maskinlesbar JSON for ikkje-interaktive kommandoar. |

Til dømes viser `meshpi nodes --help` alle vala for nodelista, medan
`meshpi --json nodes --sort name` skriv den sorterte lista som JSON.

### Start og tilkopling

```bash
meshpi
meshpi tui
meshpi new
meshpi connect 192.0.2.42 --name "Heimenode"
meshpi connect COM4 --name "USB-node"
meshpi connections
```

Du kan òg skrive eit TCP-mål eller ei seriellsti direkte, til dømes
`meshpi 192.0.2.42` eller `meshpi COM4`. Det er ein snarveg til `meshpi
connect MÅL`.

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
meshpi channels
meshpi public
meshpi public --channel 2
meshpi public --limit 200
meshpi dm 710365c8
```

Slett alle public-kanalar, alle DM-ar eller begge delar med éin kommando:

```bash
meshpi delete-messages public
meshpi delete-messages dm
meshpi delete-messages all
```

Kommandoen krev at du skriv `SLETT`. Legg til `--yes` for ei uttrykkeleg
ikkje-interaktiv stadfesting, til dømes `meshpi delete-messages all --yes`.
Traceroute-loggen og nodelista blir ikkje sletta av denne kommandoen.

### Sending

Ingenting blir sendt før ein eksplisitt sendekommando eller Enter i interaktiv
chat:

```bash
meshpi send-public "Test på primærkanalen"
meshpi send-public "Test på kanal 2" --channel 2
meshpi send-dm 710365c8 "Direkte testmelding" --channel 2
```

Tekst blir validert som UTF-8 og kan vere maksimalt 237 byte. DM-node-ID må
vere åtte heksadesimale teikn, med eller utan `!`.

### Sanntid og interaktiv chat

Følg alle nye meldingar:

```bash
meshpi watch
```

Følg primærkanalen, ein kanalspesifikk samtale-ID eller ein DM. `public` tyder
berre den aktive primærkanalen; bruk `all` for alle kanalar og DM-ar:

```bash
meshpi watch public
meshpi watch channel:global:Ops:1234
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

### Teneste og sjølvtest

```bash
meshpi doctor
meshpi doctor --offline
meshpi service status
meshpi service start
meshpi service stop
meshpi service enable
meshpi service disable
```

`meshpi daemon` køyrer daemonen i framgrunnen og er først og fremst nyttig ved
feilsøking eller i eit tenesteoppsett. Vanleg bruk skal gå gjennom den
installerte bakgrunnstenesta eller session-modusen.

## Konfigurasjon

MeshPi les `.env` frå arbeidskatalogen dersom fila finst. Eksisterande
miljøvariablar har prioritet.

```dotenv
MESHTASTIC_HOST=
MESHTASTIC_PORT=4403
DATABASE_PATH=./data/meshtastic.db
CONNECTIONS_PATH=./data/connections.json
DISCOVERY_SUBNET=
IPC_TRANSPORT=auto
IPC_HOST=127.0.0.1
IPC_PORT=8765
IPC_SOCKET_PATH=./data/meshpi.sock
IPC_SOCKET_GID=
IPC_TOKEN=replace-with-64-random-hex-characters
LOG_LEVEL=INFO
OBSERVATION_RETENTION_DAYS=365
UPDATE_URL=https://venes.org/meshpi/version.json
UPDATE_TIMEOUT=3
BACKGROUND_MODE=always
```

`IPC_TRANSPORT=auto` vel Unix-socket på Linux/macOS og loopback-TCP på
Windows. `IPC_TRANSPORT=tcp` kan brukast uttrykkeleg på alle plattformer;
`IPC_HOST` godtek då berre `127.0.0.1`, `::1` eller `localhost`.
`IPC_SOCKET_PATH` vel Unix-socketen. Installatørane set ein privat
plattformtilpassa sti. `IPC_SOCKET_GID` er valfri POSIX-gruppetilgang og bør
berre setjast av installatøren eller ein administrator. `IPC_TOKEN` må vere
minst 32 teikn og blir kontrollert før ein IPC-kommando blir utført.

`OBSERVATION_RETENTION_DAYS` styrer kor lenge motteken telemetri og
posisjonsdata blir tekne vare på, frå 1 til 3650 dagar. Standardverdien er 365.
Data blir knytte til kjeldenoden, ikkje til den aktive tilkoplingsprofilen.
MeshPi lagrar samstundes kva profil og lokal gateway som tok imot pakken, og
fjernar duplikat dersom same pakke kjem inn via fleire gatewayar.

Når `DISCOVERY_SUBNET` er tom, finn MeshPi det lokale IPv4-nettet automatisk
og søkjer der. Set til dømes `DISCOVERY_SUBNET=192.168.1.0/24` for å avgrense
TCP-søket manuelt. Nettet kan maksimalt vere `/22`. Seriell oppdaging brukar
systemet si portliste og føretrekkjer stabile stiar under `/dev/serial/by-id`.
To elles like USB-einingar utan serienummer eller annan stabil maskinvare-ID
kan byte portnamn etter fråkopling eller omstart. Gi slike profilar tydelege
namn og kontroller porten før bruk.

Set `UPDATE_URL` til tom verdi dersom automatisk oppdateringssjekk skal vere
av. Nettverksfeil under sjekken blir ignorerte og hindrar aldri oppstart.

`BACKGROUND_MODE=always` held daemonen i gang uavhengig av TUI-en. Dersom du
vel å stoppe tenesta når du avsluttar, startar neste `meshpi` henne på nytt.
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
`sudo`. `meshpi service stop` stoppar daemonen kontrollert og lastar ved behov
ut den plattformstyrte tenesta slik at ho ikkje startar daemonen opp att.

### Linux / Raspberry Pi OS

- programversjonar: `/opt/meshpi/releases/`
- aktiv og førre versjon: `/opt/meshpi/current` og `/opt/meshpi/previous`
- konfigurasjon: `/etc/meshpi.env` (installasjonsbrukaren:`meshpi`, `0640`)
- database og profilar: `/var/lib/meshpi` (`meshpi:meshpi`, `0750`)
- IPC-socket: `/run/meshpi/meshpi.sock` (privat, med tilgang for
  installasjonsbrukaren)
- logg: `journalctl -u meshpi -f`
- teneste: `sudo systemctl status|start|stop|restart meshpi`

Always-installasjonen bruker installasjonsbrukaren si private primærgruppe for
sockettilgang utan å krevje ny innlogging. Installatøren avviser ei primærgruppe
som er delt av fleire kontoar. På system med felles `users`- eller
`staff`-gruppe kan du bruke session-modus eller få administratoren til å gi
kontoen ei privat primærgruppe før always-installasjon.

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

Testane dekkjer mellom anna fleire kanalar og gatewayar, DM-ruter, node-ID,
RF/MQTT, duplikatkontroll, SQLite-migrering, sending, ACK/NAK, reconnect og
inputvalidering.

## Utgjevingsnøklar

Manifestet oppgir ein `key_id`. Appen og alle tre installatørane har eit
allowlista nøkkelregister og ei eiga tilbakekallingsliste. Ein ny nøkkel skal
først leggjast inn i ei utgiving som framleis er signert med ein allereie
tiltrudd nøkkel. Først etter at denne overgangsutgivinga er tilgjengeleg, kan
seinare manifest signerast med den nye nøkkelen. Ein kompromittert nøkkel blir
lagd i tilbakekallingslista i neste trygt distribuerte utgiving. Sjå
[`SECURITY.md`](SECURITY.md) for prosedyre og avgrensingar.

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
