# Privat webgrensesnitt

MeshPi-web køyrer på same maskin som MeshPi-daemonen. Klientane treng berre
nettlesar og tilgang lokalt eller gjennom Tailscale. Frå 0.9.7 viser
nettlesarfana servernamnet, slik at du kan skilje fleire MeshPi-installasjonar. All radio- og
SQLite-tilgang går gjennom den lokale IPC-en; terminalklienten kan brukast
samstundes. Berre éin gateway er aktiv om gongen.

Webserveren følgjer med **MeshPi 0.9.8** og er valfri ved installasjon.
Utgåva blir distribuert gjennom den ordinære kanalen for praktisk utprøving
fram mot 1.0.0; det blir ikkje oppretta ein ny betakanal. HTTP-delen bruker
Python sitt standardbibliotek, med avgrensa klienttal og tidsfristar. Han er
avgrensa til localhost og privat tilgang gjennom Tailscale, ikkje offentleg
hosting.

## Val ved installasjon

Ein fersk installasjon med interaktiv terminal spør om webgrensesnittet skal
aktiverast, før installasjonen endrar MeshPi-oppsettet. Standard er nei.
Utan interaktiv terminal er web av som standard. Ei oppdatering bevarer det
lagra valet, porten, adressa, innloggingsnøklane og annan brukardata.
Web som var avslått, er framleis avslått etter oppdateringa.

For ein fersk 64-bits Raspberry Pi OS-installasjon med Python 3.11 eller nyare:

```sh
sudo apt update
sudo apt install curl python3-venv
curl -fLO https://venes.org/meshpi/install-linux.sh
sudo sh install-linux.sh --web
meshpi web-info
meshpi web-code
```

På macOS:

```sh
curl -fLO https://venes.org/meshpi/install-macos.sh
sh install-macos.sh --web
```

På Windows PowerShell:

```powershell
Invoke-WebRequest https://venes.org/meshpi/install-windows.ps1 -OutFile install-windows.ps1
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1 -Web Enabled
```

Linux/macOS har `--no-web` for å slå av. Utelat begge webvala for å bevare
oppsettet. Windows har `-Web Disabled` og `-Web Keep` (standard).
For sesjonsmodus bruker du `sh install-linux.sh --mode=session --web` utan
`sudo`, tilsvarande `--mode=session --web` på macOS, eller
`.\install-windows.ps1 -Mode Session -Web Enabled` på Windows.

Installatøren lagrar `WEB_ENABLED`, `WEB_PORT` (standard `8780`),
`WEB_PUBLIC_URL` (standard tom) og `WEB_BASE_PATH` (standard `/meshpi`) i den
vanlege konfigurasjonsfila. Han endrar ikkje brannmur eller Tailscale-oppsett.
I `always`-modus blir web ein eigen prosess: `meshpi-web.service` på Linux,
`org.venes.meshpi.web` på macOS og eit eige barn av prosessvakta på Windows.
Deaktivering og vanleg avinstallering bevarer innloggingsdata.

## Lokal oppstart og innlogging

Finn adressa og få rettleiing i terminalen:

```sh
meshpi web-info
```

Kommandoen viser lagra innloggingsadresse, lokal webhelse og maskina si
Tailscale-adresse dersom Tailscale er tilgjengeleg. Han forklarer eingongskode,
registrering av fleire passkeys og gjenoppretting ved nøkkeltap. Frå 0.9.7
viser han òg kopierbare aktiveringssteg når web er avslått, og Serve-kommandoar
med den lagra porten og stien. Køyr stega sjølv for å endre oppsettet. Bruk til dømes
`meshpi --env-file /etc/meshpi.env web-info` for ei bestemt konfigurasjonsfil.
`meshpi --json web-info` gir adressene og status som JSON.
Hjelpa viser ein kopierbar kodekommando med den Python-installasjonen og
konfigurasjonsfila som faktisk er i bruk. Har du ein separat webtest mot ein
eldre daemon, bruk `web-info --local` for å vise kodekommandoen med `--local`.

Oppslaget endrar ikkje konfigurasjon, startar ingen daemon og lagar ingen kode.
Ei oppdaga Tailscale-adresse stadfestar ikkje at HTTPS eller Serve-ruta er sett
opp. Den lagra innloggingsadressa blir aldri automatisk bytt ut. Sjå
[Tailscale CLI](https://tailscale.com/kb/1080/cli) for det lesande
`tailscale status --json`-oppslaget som blir brukt til adresseoppdaging.

For berre denne maskina bruker du `http://localhost:8780/meshpi/`.
Webserveren lyttar berre på `127.0.0.1`; direkte LAN-binding og
`--lan-address` er fjerna. Den lokale IP-adressa er ikkje eit innloggingsfripass.

Med `always`-installasjon startar tenestene i bakgrunnen. Ved manuell køyring
bruker du same konfigurasjon som daemonen:

```sh
meshpi --env-file /etc/meshpi.env web
```

I `session`-modus kan `meshpi web` starte ein manglande daemon. Når
webkommandoen blir avslutta, stoppar han berre ein daemon han sjølv starta;
ein eksisterande daemon blir bevart. `WEB_ENABLED=false` hindrar automatisk
oppstart, men hindrar ikkje ein uttrykkeleg manuell `meshpi web`.
`web --managed` er eit internt installatør-/tenesteval.

Websida kan opnast utan ein konfigurert node og medan daemonen er fråkopla.
Etter innlogging kan eigaren velje eller opprette tilkoplingsprofil.
`meshpi web-check` kontrollerer at den lokale webserveren svarar, ikkje at
radioen er tilkopla.

Første innlogging skjer med ein eingongskode frå terminalen på MeshPi-maskina:

```sh
meshpi web-code
```

Bruk `--env-file` før kommandoen dersom installasjonen ikkje har ein launcher
som vel rett konfigurasjon. Daemonen må køyre, og den lokale brukaren må ha
IPC-tilgang. Koden varer i fem minutt, kan brukast éin gong og blir ikkje
skrive til nettlesarlenkjer eller generert automatisk av installatøren.
**Ein ny kode avsluttar alle eksisterande websesjonar.** Bruk han til første
oppsett eller gjenoppretting, og handsam han som ein løyndom.

Dersom daemonen ikkje er tilgjengeleg eller enno er for gammal til å tilby
kode gjennom IPC, kan du bruke `meshpi web-code --local`. Dette er uttrykkeleg
lokal gjenoppretting og må køyrast som same OS-konto som webprosessen. På Linux
systeminstallasjon er det servicekontoen:

```sh
sudo -u meshpi /opt/meshpi/current/.venv/bin/meshpi \
  --env-file /etc/meshpi.env web-code --local
```

På macOS, Windows og vanleg sesjonsinstallasjon bruker du den same innlogga
brukaren som køyrer web. Ikkje endre eigarskap eller utvid filrettar for å
omgå denne kontrollen. Lokal kode har same femminuttsgrense og avsluttar
sesjonar på same måte som IPC-koden.

MeshPi har éin eigar og fleire namngjevne passkeys, utan separate brukarkontoar
eller roller. På localhost bruker denne utgåva eingongskode; passkeys er berre
aktiverte på den valde HTTPS-adressa. Alle private data, API-handlingar,
eksport og sanntidsstraum krev innlogging. Den offentlege helsekontrollen
inneheld ikkje nodeinformasjon. IPC-tokenet blir aldri sendt til nettlesaren.

## Nettlesar på ein annan PC gjennom SSH

Ein Pi utan skjerm kan framleis bruke localhost-innlogging. Køyr `meshpi web-info`
og `meshpi web-code` i SSH-terminalen på Pi-en. På PC-en opnar du ein
annan terminal og held tunnelen open:

```sh
ssh -N -L 8780:127.0.0.1:8780 BRUKAR@PI-VERT
```

Byt plasshaldarane med din eigen brukar og Pi-vert. Opne så
`http://localhost:8780/meshpi/` i nettlesaren på PC-en og lim inn koden frå
Pi-en. Port 8780 må vere ledig lokalt. Dei to portane skal vere like og svare
til den lagra innloggingsadressa; ved annan webport må begge endrast.
Oppskrifta gjeld localhost-oppsett. Har du valt ei Tailscale-adresse, bruker
du den adressa for innlogging i staden.

## Fast privat HTTPS-adresse gjennom Tailscale

Vel adressa før du registrerer passkeys. Dømet under bruker eit plasshaldarnamn;
byt det ut med den faktiske HTTPS-rotadressa til MeshPi-maskina. På Linux
systeminstallasjon krev endring av `/etc/meshpi.env` administratorrettar:

```sh
sudo meshpi web-configure --enable --port 8780 \
  --public-url https://MASKIN.TAILNET.ts.net --base-path /meshpi
sudo meshpi service enable
sudo meshpi service stop
sudo meshpi service start
tailscale serve status
sudo tailscale serve --bg --set-path /meshpi http://127.0.0.1:8780
```

På macOS/Windows bruker du `web-configure`, `service enable` og
`service stop`/`service start` utan `sudo`. `service enable` er nødvendig for
å aktivere webautostart når installasjonen opphavleg hadde web avslått. I sesjonsmodus avsluttar du den manuelle webprosessen og startar
`meshpi web` igjen etter konfigurasjonsendringa. Tenestestopp kan bryte
sambandet for andre opne MeshPi-klientar kortvarig.

Kontroller at `/meshpi` er ledig i eksisterande Serve-oppsett før du legg til
ruta. Bevar andre ruter; ikkje bruk Serve reset eller Funnel. Serve gir
privat HTTPS under tailnettet sine tilgangsreglar. `--bg` bevarer Serve i
bakgrunnen når terminalen blir lukka, og lèt ruta kome tilbake etter omstart.
Dersom HTTPS ikkje er aktivert, kan Serve vise ei aktiveringslenkje. Opne
lenkja og følg Tailscale-rettleiinga, og køyr kommandoen på nytt dersom det
blir bede om det. Kontroller til slutt med `tailscale serve status`. Sjå
[Tailscale si kommandorettleiing](https://tailscale.com/docs/reference/tailscale-cli/serve).

Opne `https://MASKIN.TAILNET.ts.net/meshpi/`, også frå nettlesaren på
servermaskina. Bruk ein ny `meshpi web-code` for oppsettet på denne adressa,
logg inn med koden og vel **Innlogging og nøklar → Legg til passkey**.
Registrer gjerne to nøklar med attkjennelege namn. Nettlesaren vel tilgjengeleg
nøkkellager, til dømes ein kompatibel passordhandterar, Apple Passord eller
Windows Hello. Ein lokal Hello-nøkkel og ein synkronisert passkey har ulik
flytting mellom einingar; MeshPi krev ingen bestemt leverandør. Faktiske
nettlesar-/leverandørkombinasjonar må testast på dei aktuelle einingane.

Du kan leggje til og fjerne nøklar i same dialog. Nøkkelendringar krev at du
har logga inn dei siste fem minutta. Har du mista nøklane, lagar du ein ny
lokal eingongskode og registrerer ein erstatning. Sesjonar varer i høgst åtte
timar og må opprettast på nytt etter omstart av webprosessen.

Den lagra adressa er fast og blir ikkje bytt automatisk når Tailscale kjem
eller går. Passkeys frå eitt vertsnamn fungerer ikkje på eit anna vertsnamn
eller på localhost. Ved adressebyte må du bruke lokal kode på den nye adressa
og registrere nøklar der. For lokal tilgang igjen:

```sh
meshpi web-configure --public-url ""
```

Konfigurasjonsendringa krev same rettar og omstart som ovanfor. Private
innloggingsdata ligg i `web-auth` ved sida av databasen og skal ikkje leggjast
i Git, ei utgiving eller offentleg eksport.

For å slå av automatisk webdrift lagrar du `meshpi web-configure --disable`
og stoppar/startar tenestene. På Linux systeminstallasjon bruker du `sudo`
for både konfigurasjonsendringa og tenestestyringa. For å slå på igjen bruker
du `--enable`, køyrer `meshpi service enable` og stoppar/startar tenestene. `web-configure` åleine endrar ikkje
ein prosess som allereie køyrer.

Både `/meshpi` og `/meshpi/` fungerer. Ressursar og API-kall bruker den
konfigurerte stien, også når proxyen fjernar prefikset. Proxyen må bevare
nettlesaren sin Origin-header. Tailscale-tilgang og passkey-innlogging er to
separate krav; tilgjengeleg tailnet-adresse gir ikkje automatisk innlogging.

## Installer som app på mobil og datamaskin

Opne den private HTTPS-adressa på eininga og trykk **Installer app** i
topplinja. Nettlesarar som tilbyr direkte installasjon, viser knappen
**Installer MeshPi**. Elles viser dialogen rettleiing:

- **Android, Windows og Linux:** bruk installasjonsvalet i Chrome eller Edge.
- **iPhone og iPad:** opne sida i Safari, bruk delingsknappen og vel
  **Legg til på heimskjermen**. Vel å opne som webapp dersom valet blir vist.
- **Mac:** bruk **Arkiv → Legg til i Dock** i Safari, eller installer frå
  Chrome eller Edge.

Appen får eige ikon og opnar i eit eige vindauge. Nettlesaren avgjer om og
når installasjonsvalet blir tilgjengeleg. HTTPS er nødvendig på andre
einingar; lokal bruk på den lagra `localhost`-adressa fungerer òg.
Appidentitet, startadresse, ikon og service worker følgjer den konfigurerte
webstien, også ved proxyar som fjernar prefikset.

Den installerte appen treng framleis tilgang til nettadressa han blei
installert frå. Ein app installert frå Tailscale-adressa treng derfor
Tailscale. Det finst ingen separat LAN-adresse i denne utgåva.
Ved opning utan samband blir ei fråkopla-side vist med **Prøv
igjen**. Service workeren lagrar ikkje sider, meldingar, nodeinformasjon,
sesjonsverdiar eller eksportar i ein offlinebuffer. Han handsamar berre
navigasjon til appstarten og legg aldri sendingar eller andre handlingar i
kø for seinare utføring. Eksisterande innstilling for tema blir framleis
lagra lokalt, og utkast blir berre haldne i minnet medan sida er open.

## Historikkinnstillingar

**Innstillingar** opnar lagringstid og manuell opprydding for gatewayen som er
vald. Terminalen har same val under **F10 → App → Historikk og lagring**.
Nye gatewayar får 30 dagar; eksisterande migrert historikk får inga
aldersgrense før brukaren stadfestar eit val. Vel 7/30/eigendefinerte dagar
eller 0 for inga aldersgrense. Grensa på 50 000 meldingar gjeld framleis,
med unntak for uavklarte utgåande meldingar.

Rydd berre meldingar eller også telemetri, posisjonar og handlingslogg, eldre
enn vald periode eller alt (0). Førehandsvisinga viser gateway og konkrete
tal. Stadfestinga varer i fem minutt og kan berre brukast éin gong. Nyleg
innkomne postar som ikkje var med i førehandsvisinga, blir ikkje sletta.
Uavklarte utgåande meldingar, pågåande nodehandlingar, nodar, profilar,
innloggingsnøklar og radioinnstillingar blir bevarte. Sletting kan ikkje angrast.

Kvar lokal radio sin node-ID har si eiga fil:
`<dbstem>-gateways/<8hex>.sqlite3`. USB/TCP/BLE-profilar for same radio deler
fila. Berre daemonen opnar databasar. Den opphavlege databasefila og kopiane
`legacy.sqlite3`/`legacy-upgraded.sqlite3` i gateway-mappa blir bevarte ved
migrering og blir ikkje sletta ved historikkopprydding. Dei kan framleis
innehalde eldre private data. Endringar i den opphavlege databasen etter
rollback stoppar ny oppstart til historikkane kan avklarast/flettast.

Webklienten har ingen eksportknapp. `meshpi export` i terminalen eksporterer
berre den valde gatewayen, med gateway-ID og lagringsinnstillingar. Ufordelt
legacy-historikk ligg att i dei bevarte kopiane.

## Funksjonar og grenser

- Kanalar, nye og eksisterande DM-ar, historikk og leveringsstatus.
- Ulest-status er felles med terminalklienten. Ei synleg samtale blir merkt lesen.
- Arkivering av DM-ar; historikken blir bevart.
- Utkast følgjer samtalen medan sida er open. Etter arkivering eller byte av
  gateway må du velje ei samtale før du sender. Sending er sperra til
  historikken for den valde samtalen er lasta.
- Skrivefeltet viser UTF-8-byte og stoppar meldingar over 237 byte før sending.
  Nye meldingar bevarer rulleposisjonen når du les eldre historikk; bruk
  **Nye meldingar ↓** for å hoppe til botnen.
- Nodeliste, nodeinfo, telemetri, posisjons- og traceroutehistorikk.
- Nodevindauget held same plass og storleik ved fanebyte. Tittel, faner og
  handlingsknappar står fast medan innhaldet kan rullast.
- Oversikt grupperer identitet, samband, siste telemetri og historikktal.
  Telemetri viser tabellar per måletype med tidspunkt, transport, einingar og
  nyaste måling først. Tekniske detaljar kan opnast ved behov; nullverdiar og
  manglande målingar blir viste kvar for seg.
- Posisjonshistorikken viser éi rad per rapport med tidspunkt, koordinatar,
  høgd over havet, satellittar, transport og lenkje til Google Maps i ei ny
  fane. Nyaste posisjonstid står først. Ekstra felt kan opnast med «Vis» under
  «Detaljar». Tabellen har faste kolonneoverskrifter og eige rullefelt, også
  sidelengs på smale skjermar. Dei siste 100 mottekne rapportane blir viste.
- Nodelista har søk, transportfilter (alle, berre RF, berre MQTT eller begge)
  og sortering etter sist høyrd, alfabetisk namn eller node-ID. Ved sortering
  etter sist høyrd står lokal gateway først. «Sist» bruker same tidsformat som
  terminalklienten: relativ tid under seks timar, deretter klokkeslett og etter
  eitt døgn dato og klokkeslett. Same liste og val blir brukte på smal skjerm.
- Traceroute viser eigne fram- og returruter med kjende nodenamn, full node-ID,
  SNR per rapportert hopp, tidsbruk, hopptal og lågaste oppgitte SNR. Manglande
  målingar blir merkte uttrykkeleg; null blir ikkje erstatta med ein estimert verdi.
  Tekniske identifikatorar kan opnast separat. Klikk utanfor nodevindauget
  eller trykk Esc for å gå tilbake til chatten.
- Brukarstyrt traceroute og posisjonsutveksling, med daemonen sine sperrer.
- Lagra profilar, manuelle mål og uttrykkeleg TCP-, USB- og BLE-oppdaging på
  vertsmaskina. Éin gateway er aktiv om gongen.
- Historikkinnstillingar og stadfesta opprydding gjennom daemonen.
- Éin delt IPC-watch for nettlesarane, med ny tilkopling og periodisk oppfrisking.
- Tilpassa smal og brei skjerm; webgrensesnittet i 0.9.8 er på nynorsk.
- Sol-/måneknappen i topplinja byter mellom lys og mørk modus. Valet blir lagra
  lokalt i nettlesaren når lokal lagring er tilgjengeleg; før første val
  følgjer temaet systemet. Valet påverkar ikkje daemonen eller andre klientar.
- Varsel og feilmeldingar kan lukkast med ×.

Webgrensesnittet har enno ikkje full funksjonslikskap med terminalklienten:
språkbyte, gjenopning av arkiv, administrasjon av noderegister,
systemtenestestyring og installasjon av oppdateringar står att. Lange loggar
viser dei siste 100 observasjonane og chat dei siste 300 meldingane.

Sendeknappen er sperra medan innsendinga pågår, og nettlesaren sender ikkje
meldingar på nytt automatisk. Ved tapt svar blir utkast og operasjons-ID
bevarte i minnet, og ny sending i samtalen blir sperra. **Kontroller
sendestatus** hentar berre resultatet av den opphavlege handlinga; knappen
utfører aldri ei ny sending. Eit stadfesta resultat viser at sendinga er
registrert; faktisk levering må framleis kontrollerast i historikken.

Like førespurnadar med same operasjons-ID blir avstemte i webprosessen i
opptil éin time, avgrensa til dei siste 1024 handlingane. Etter omstart eller
utløpt resultat kan sendestatus vere ukjend. Kontroller då historikken og
bruk **Eg har kontrollert historikken** for å frigje skrivefeltet utan å
sende noko. Knappen krev først ein statuskontroll og kontrollerer på nytt
før frigjeving; ei handling som framleis står i kø eller blir handsama,
held skrivefeltet sperra. Dette er ikkje varig deduplisering gjennom omstart eller omlasting
av sida. Utkast og uavklarte sendingar blir ikkje lagra etter at sida blir lukka.

Etter omstart av webprosessen må opne klientar logge inn igjen før dei kan
hente private data. Nettlesaren fornyar den separate CSRF-verdien ved behov. Ei sending som blei avvist før handsaming, må startast på nytt
av brukaren. Ei sending med usikkert resultat blir aldri repetert automatisk.
Sesjons- og JSON-kall har tidsavbrot etter 45 sekund i nettlesarar med
AbortController. Tidsavbrot under sending gir usikker status og aldri
automatisk ny sending.

## Kontrollar før vidare utrulling

Køyr full pytest og Ruff. Automatiske testar skal mocke radioen. Kontroller
første innlogging, avvist kode/gjenteke passkey-svar, utlogging, nøkkeltap,
kanonisk adresse, sesjonsbrot, fleire klientar og fråkopla daemon. Kontroller
lokal binding, privat HTTPS, at Funnel er av og at andre Serve-ruter er bevarte.
Ekte radiosending krev særskild autorisasjon etter `AGENTS.md`.

Installatørane har valfri webdrift og tilbakeføring. Ved vidare endringar skal
nyinstallasjon, gjenteken installasjon, oppdatering, omstart og tilbakeføring
kontrollerast på dei faktiske plattformene etter `RELEASING.md`. Test òg
passkeys med dei nettlesarane og nøkkellagra brukarane faktisk har.

WebAuthn bruker `fido2`. Plattformlåsene og dei signerte installasjonsfilene
høyrer til same MeshPi-versjon. Kjeldekoden for denne utgåva ligg i
[kjeldearkivet for 0.9.8](https://venes.org/meshpi/downloads/meshpi-0.9.8.tar.gz).
Meld reproducerbare feil og testresultat i
[GitHub Issues](https://github.com/rvenes/meshpi/issues), utan private data
eller innloggingsløyndomar.

Full funksjonslikskap med terminalen, breiare klienttesting og val av endeleg
HTTP-køyretid står att på vegen mot 1.0.0.
