# Utviklingsstatus og vidare plan

Sist oppdatert: 28. juli 2026
Gjeldande utgiving: MeshPi 0.8.3

Denne fila er den varige overleveringa mellom utviklingstrådar. Ho skal
oppdaterast når ei større funksjon blir ferdig, når eit viktig arkitekturval
blir teke, eller når prioriteringa framover endrar seg.

## Start ein ny utviklingstråd

Be den nye tråden om å:

1. lese `AGENTS.md` og `DEVELOPMENT.md`;
2. kontrollere `git status --short` og `git diff` før endringar;
3. stadfeste kva roadmap-punkt oppgåva høyrer til;
4. halde seg til arkitekturen og tryggleiksgrensene nedanfor.

Eit roadmap-punkt er ikkje i seg sjølv løyve til å sende radiotrafikk,
publisere ei utgiving eller endre konfigurasjonen på ein Meshtastic-node.

## Produktretning

MeshPi skal ikkje erstatte ein full Meshtastic-klient. Målet er ein stabil og
lett terminalklient som kan stå tilkopla over tid, lagre nyttig historikk og
gi eit godt driftsbilete over SSH.

Det viktigaste skiljet mot ein vanleg klient er derfor:

- påliteleg mottak og lokal historikk når TUI-en er lukka;
- enkel public-chat og DM i terminalen;
- node-, telemetri-, posisjons- og traceroutehistorikk;
- trygg drift, oppdatering og tilbakeføring på Linux, macOS og Windows;
- eitt tydeleg grensesnitt for operatørarbeid, ikkje alle funksjonane i den
  offisielle klienten.

## Arkitektur som skal bevarast

- Berre daemonen eig Meshtastic-sambandet og SQLite-fila.
- CLI og TUI bruker lokal IPC; dei skal ikkje opne eigne radiosamband.
- IPC skal berre bruke loopback eller ein privat Unix-socket.
- MeshPi kan ha fleire lagra tilkoplingsprofilar, men berre éin aktiv gateway
  om gongen.
- Meldingar og observasjonar blir knytte til kjeldenoden. Det blir i tillegg
  lagra kva profil og lokal gateway som tok imot data.
- MeshPi skal aldri endre kanal-, radio- eller annan nodekonfigurasjon.
- Posisjonsdeling og andre radiosendingar skal vere uttrykkelege
  brukarhandlingar.
- Installasjon og oppdatering skal bevare database, profilar og konfigurasjon.
- Nye transporttypar, som BLE, skal inn bak daemonen og den eksisterande
  profilmodellen.

## Dette er ferdig

### Grunnplattform og drift

- Bakgrunnsdaemon, SQLite-lagring og lokal IPC.
- `always`- og `session`-modus på Linux, macOS og Windows.
- TCP- og USB/serielle profilar, oppdaging, profilbyte og stabil
  USB-identitet.
- Automatisk ny tilkopling etter sambandsbrot.
- Signerte utgivingsmanifest, SHA-256-kontroll, hash-låste avhengigheiter,
  versjonerte installasjonar og tilbakeføring.
- TUI, vanlege CLI-kommandoar og driftskontroll med `status`, `doctor` og
  tenestekommandoar.

### Meldingar og traceroute

- Public-chat på alle aktiverte kanalar og kanalruting for direkte meldingar.
- Felles database med separate logiske kanalar, ulest-status, arkivering av DM
  og duplikatkontroll på tvers av gatewayar.
- Same public-melding kan ha fleire lagra gatewayobservasjonar utan å bli vist
  fleire gonger.
- RF/MQTT/Ukjend, RSSI, SNR og hoppinformasjon når pakken inneheld dette.
- Skilje mellom vanleg ACK, ende-til-ende-levering og NAK.
- Traceroute kan startast frå nodehandlingar og nodeinfo.
- Forsøk, resultat, framrute, returrute, SNR og hopptal blir lagra og viste i
  tabellformat.
- Cooldown hindrar at traceroute blir sendt oftare enn fastvaren tillèt.

### Nodeinfo og observasjonar i 0.8.0

- Ei samla nodeinfovising med fanene Oversikt, Telemetri, Posisjon og
  Traceroute.
- Telemetri og posisjonar blir logga per kjeldenode i SQLite.
- Standard oppbevaringstid er 365 dagar og kan konfigurerast.
- Duplikat blir fjerna sjølv om same observasjon kjem via fleire gatewayar.
- Gatewayprofil, lokal gateway, transport og tilgjengeleg RF-metadata blir
  lagra saman med observasjonen.
- Desse telemetritypane blir kjende att:
  - einingsdata;
  - miljødata;
  - luftkvalitet;
  - straumdata;
  - lokalstatistikk;
  - helse-, verts- og trafikkdata.
- Miljødata ligg førebels i den same Telemetri-fana som dei andre
  telemetritypane. Det er medvite valt for å unngå mange små vindauge.
- Posisjonsloggen viser koordinatar, høgd og andre GPS-felt som faktisk finst.
- Google Maps-lenkjer er klikkbare, og heile URL-en kan kopierast over SSH.
- Oversikta kan vise siste kjende plassering for både vald node og lokal node.
- «Utveksle posisjonsdata» ber målnoden om posisjon. Lokal posisjon blir berre
  delt når ho finst, kanalen tillèt deling, og presisjonen er kjend. Delte
  koordinatar blir maskerte til den konfigurerte presisjonen.
- Nodeinfo kan lukkast med mus, `Esc` eller den vesle knappen i botnlinja.
- Handlingsknappane er samla i botnlinja, og `Ctrl+Q` verkar sjølv om ein dialog
  er open.
- Statuslinja viser vertsnamnet på PC-en, slik at fleire SSH-økter er lettare å
  skilje.

### Milepålar

| Versjon | Viktigaste endring |
| --- | --- |
| 0.5.x | Tryggleik, traceroute, leveringsstatus og betre livssyklus |
| 0.6.x | USB-identitet, profilbyte og tenesterettingar på fleire plattformer |
| 0.7.0 | Sikre oppdateringar og herda IPC |
| 0.7.1 | Rett kronologi mellom meldingar, ACK og traceroute |
| 0.8.0 | Nodeinfo, telemetri-, posisjons- og traceroutehistorikk |
| 0.8.2 | Fleirkanalsmeldingar, kanalbundne DM-ruter og gatewayobservasjonar |
| 0.8.3 | Eksperimentell BLE, betre DM-ruter og delt samtale-/kanalvising |

## Avklaringar som alt er tekne

### Data frå fleire gatewayar

Telemetri og posisjon gjeld kjeldenoden, ikkje profilen som tilfeldigvis var
aktiv då data kom inn. Same data skal derfor visast samla for noden. Profil og
lokal gateway blir likevel lagra som sporingsinformasjon.

Public-meldingar blir dedupliserte på tvers av gatewayar når ein trygg logisk
kanal-ID stadfestar at kanalen er den same. Gatewaymottaka blir likevel logga
kvar for seg. DM-samtalar blir skilde per lokal node, fjernnode og logisk
kanal, slik at profilbyte ikkje blandar avsendaridentitet eller kanalrute.

### Miljødata

«Environment metrics» er ikkje eit eige vindauge. Dei blir lagra som ein eigen
telemetritype og viste i Telemetri-fana. Ei eiga fane er berre aktuelt dersom
reelle data viser at tabellen blir for tett eller vanskeleg å filtrere.

## Vidare roadmap

Rekkjefølgja under er den tilrådde rekkjefølgja, ikkje ein lovnad om bestemte
versjonsnummer.

### 1. Stabilisere 0.8-observasjonane

Mål: sikre at langtidslogging fungerer med verkelege nodar og varierande
fastvare før datamodellen blir bygd vidare.

- La Windows-installasjonen logge over tid og kontrollere databasevekst,
  duplikatkontroll og sortering.
- Test data frå fleire nodetypar og telemetritypar.
- Kontrollere tomme, delvise, feilaktige og framtidige tidsstempel.
- Måle spørjetid når ein node har mange tusen observasjonar.
- Vurdere filtrering på tidsrom og telemetritype i nodeinfo.
- Køyre relevant installasjonstest på Linux og macOS før neste utgiving.

Ferdigkriterium: ingen datatap eller feil kjeldenode, stabil minnebruk, raske
tabellar og føreseieleg opprydding etter oppbevaringstida.

### 2. Fleirkanalsstøtte – implementert i 0.8.2

Mål: støtte tekstmeldingar på fleire eksisterande Meshtastic-kanalar utan å
lese ut eller vise hemmelege kanalnøklar og utan å endre nodekonfigurasjonen.

Gjennomført arkitektur:

- Kanalindeksen blir behandla som lokal for kvar Meshtastic-node.
- `settings.name` og den ikkje-hemmelege `settings.id` gir ein logisk
  kanalidentitet når ID-en finst. Utan ID blir kanalen medvite lokal til node,
  indeks og trygt namn.
- TCP-, seriell- og framtidige BLE-profilar er berre transportspor. Lokal
  node-ID er gatewayidentiteten.
- Public-samtalar blir knytte til logisk kanal. DM-samtalar blir knytte til
  lokal node, fjernnode og kanal, slik at svar ikkje fell stille tilbake til
  kanal 0.
- `message_observations` lagrar kvart gatewaymottak separat frå den logiske
  meldinga.
- Ei versjonert, transaksjonell SQLite-migrering bevarer eksisterande
  meldingar som bakoverkompatible legacy-samtalar.
- Kanaloversikta les berre indeks, rolle, trygt namn og offentleg kanal-ID.
  PSK og PSK-avleidde hashar blir aldri lesne, lagra eller sende over IPC.
- CLI har `channels` og `--channel`; gamle kommandoar utan kanalval bruker
  framleis primærkanalen.
- Ei eksplisitt samtalerute blir validert mot aktiv lokal node, mottakar og
  kanalbinding. Ukjende og historiske ruter er ikkje sendbare og fell aldri
  tilbake til kanal 0.
- Meldingar som kjem før kanaloversikta er klar, får ei mellombels,
  ikkje-sendbar rute. Når oversikta kjem, blir ruta bunden om transaksjonelt og
  eventuelle duplikat blir slått saman utan å miste gatewayobservasjonar.

Ferdigkriterium: alle gyldige kanalindeksar blir tekne imot, kanalane blir
ikkje blanda utan trygg identitet, rett kanal blir brukt ved sending, eldre
historikk blir bevart, og ingen kanalnøklar blir eksponerte.

### 3. Blåtann/BLE

Mål: BLE som ein tredje profiltype ved sida av TCP og seriell, framleis med
berre eitt samband eigd av daemonen.

Implementert i 0.8.3:

- Transportopprettinga for TCP, seriell og BLE er skild frå daemonen.
- Profilformat v2 migrerer eksisterande profilar og bevarer aktiv profil.
- BLE-identitet blir lagra ugjennomsiktig, og profil-ID-en blir avleidd frå
  identifikatoren i staden for visingsnamnet.
- Eksplisitt, serialisert BLE-oppdaging, nynorske adapterfeil og 30 sekunds
  IPC-frist er implementert med mocka testar. Veljaren opnar før BLE-søket,
  viser USB/TCP-resultat først, viser søkestatus og støttar F5 for nytt søk.
- Lukking av veljaren forkastar seine svar utan å starte parallelle søk.
- Tilkoplingsveljaren og manuelle `ble://IDENTIFIKATOR`-mål er implementerte.
- Grunnleggjande oppdaging, profilbyte og tilkopling er stadfesta på Windows.
- Trygg migrering bind eldre DM-historikk til den observerte primærkanalen når
  lokal node og motpart kan stadfestast. Tvitydig historikk er framleis låst.
- Nye DM-ar i TUI-en har eksplisitt kanalval, DM-titlar viser kanalruta, og
  Meshtastic si NAK-årsak blir lagra og vist ved mislukka sending.
- Samtalelista slår saman eldre kanalruter til éi DM-oppføring per node og
  gøymer gamle arkiverte public-ruter utan å slette historikken.
- Kanalar og DM-ar har eigne visuelle seksjonar. F8 skjuler DM-ar, medan F9
  skjuler sekundærkanalar; primærkanalen er alltid synleg øvst.
- Reconnect og kontrollert tenesteomstart er live-testa med BLE på Windows og
  TCP på Raspberry Pi. BLE i systemtenester på Linux og macOS står att å
  plattformteste før støtta kan reknast som ferdig.

Dette bør starte som ei teknisk undersøking, fordi oppdaging, paring,
tilgangsrettar og stabil identitet er ulike på Linux, macOS og Windows.

Tilrådde utviklingssteg:

1. Stadfest støtta og avgrensingane i den aktuelle Meshtastic Python-versjonen
   på kvar plattform.
2. Skil transportoppretting frå resten av daemonen, slik at TCP, seriell og BLE
   bruker same livssyklus og reconnect-logikk.
3. Utvid profilformatet med ein stabil BLE-identitet utan
   utviklingsspesifikke standardverdiar.
4. Lag lesande BLE-oppdaging med tydelege feilmeldingar for manglande adapter,
   paring og tilgang.
5. Handter Linux/BlueZ og systemd-rettar utan å gi tenesta breiare tilgang enn
   nødvendig.
6. Avklar om paring skal skje utanfor MeshPi eller gjennom ei uttrykkeleg
   brukarhandling.
7. Test sambandsbrot, adapter av/på, node ute av rekkevidd, profilbyte og
   automatisk ny tilkopling.
8. Lag mocka automatiske testar og eigne live-testar per plattform.

Ferdigkriterium: ein BLE-profil kan oppdagast, lagrast, koplast til på nytt og
bytast frå/til utan parallelle radiosamband eller tap av eksisterande data.

### 4. Fleire samtidige gatewayar

Lokal nodeidentitet og DM-semantikk er no avklart i datamodellen:

- ein DM-samtale er lokal node + fjernnode + logisk kanal;
- historikken viser kvar rute separat;
- ei historisk rute er lesbar, men ikkje sendbar når kanalen ikkje finst på
  den aktive noden;
- profil er transportspor, medan lokal node-ID er gatewayidentiteten.

MeshPi skal framleis ha berre eitt aktivt samband. Ikkje start samtidige
gatewayar før reconnect, hendingar, ressursbruk og tenestelivssyklus for fleire
daemon-eigde samband er spesifiserte og testa.

### 5. Vidare vising og eksport

Aktuelle, men lågare prioriterte forbetringar:

- filtrering og sideinndeling for lange telemetri- og posisjonsloggar;
- eksport av nodehistorikk til CSV eller JSON;
- kartlenkje for eit valt tidsrom eller ei enkel ruteframstilling;
- min/maks/siste verdi for utvalde målingar;
- tydelegare vising av datakvalitet og manglande GPS-fix;
- eventuelt eit leseorientert webgrensesnitt over den lokale IPC-en.

## Tilrådd neste arbeid

1. La 0.8.3 samle reelle data ei stund og noter felt eller nodetypar som blir
   viste feil.
2. Stabiliser fleirkanalslogging med verkelege, ulike kanaloppsett utan å sende
   public-testtrafikk.
3. Stabiliser BLE på Windows og gjennomfør plattformtest på Linux og macOS.
4. Vent med fleire samtidige gatewayar til tenestelivssyklusen er spesifisert
   og testa.

## Opne produktval

- Kva plattform skal vere første fullverdige BLE-mål?
- Treng Telemetri-fana tidsfilter før ho treng fleire faner?
- Skal posisjonshistorikk berre gi lenkjer, eller seinare kunne eksporterast
  som ei rute?
