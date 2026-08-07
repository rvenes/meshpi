# Utviklingsstatus og vidare plan

Sist oppdatert: 7. august 2026
Gjeldande stabilutgiving: MeshPi 0.8.7
Gjeldande betautgiving: MeshPi 0.8.8b1

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
- All brukarhistorikk blir eigd av lokal node-ID. Kjeldenode, profil og
  transport blir lagra som identitet og sporingsinformasjon innanfor dette
  nodeområdet.
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
- Felles SQLite-fil med strengt skilde nodeområde for public, DM, ulest-status,
  arkivering, nodeliste og observasjonar.
- Same public-melding blir lagra separat når ho blir motteken av ulike lokale
  nodar, men kan dedupliserast mellom profilar til same lokale node.
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

### Blåtann og samtalevising i 0.8.3–0.8.5

- BLE er ein tredje profiltype bak daemonen, ved sida av TCP og seriell.
- Profilformat v3 migrerer eksisterande profilar, bevarer aktiv profil og
  hugsar siste kjende lokale node-ID.
- BLE-identitet blir lagra ugjennomsiktig, og profil-ID-en blir avleidd frå
  identifikatoren i staden for visingsnamnet.
- Eksplisitt, serialisert BLE-oppdaging, nynorske adapterfeil og 30 sekunds
  IPC-frist er implementert med mocka testar.
- Tilkoplingsveljaren viser TCP og USB først, søkjer etter BLE i bakgrunnen,
  støttar F5 for nytt søk og forkastar seine svar etter lukking.
- Tilkoplingsveljaren og manuelle `ble://IDENTIFIKATOR`-mål er implementerte.
- Grunnleggjande oppdaging, profilbyte, reconnect og kontrollert
  tenesteomstart er live-testa på Windows.
- På macOS bruker søk og tilkopling same CoreBluetooth-økt. Det gjer
  tilkoplinga raskare og meir føreseieleg, og brukarrettleiinga forklarer
  systemdialogen for PIN og avgrensinga med samtidige BLE-klientar.
- Nye DM-ar har eksplisitt kanalval, DM-titlar viser kanalruta, og Meshtastic
  si NAK-årsak blir lagra og vist ved mislukka sending.
- Samtalelista slår saman eldre kanalruter til éi DM-oppføring per node,
  gøymer gamle arkiverte public-ruter utan å slette historikken og held
  DM-seksjonen rett plassert når samtalerekkjefølgja endrar seg.
- Kanalar og DM-ar har eigne visuelle seksjonar. F8 skjuler DM-ar, medan F9
  skjuler sekundærkanalar; primærkanalen er alltid synleg øvst.

BLE er framleis merkt eksperimentell. BLE i systemtenester på Linux og macOS,
og BLE i Docker, er ikkje ferdig plattformtesta.

### Betakanal og databaseeksport i 0.8.6–0.8.7

- Stabil- og betakanalen har kvar sitt signerte manifest og blir validerte
  uavhengig av kvarandre.
- `meshpi update --beta` vel uttrykkeleg den interne betakanalen. Vanleg
  `meshpi update` held fram med å bruke berre stabilkanalen.
- Releaseverktøyet støttar PEP 440-førehandsversjonar som `0.9.0b1` utan at
  den stabile manifestpeikaren blir endra.
- `meshpi export [FIL] [--force]` eksporterer heile databasen som UTF-8 JSON
  Lines via daemonen.
- Eksporten omfattar meldingar, nodar, kanalar, telemetri, posisjonar,
  nodehandlingar og arkiveringsstatus, med format-, program- og
  databaseskjemaversjon og kontrollerbare radtal.
- Eksporten bruker eitt konsistent SQLite-øyeblikksbilete medan tenesta
  køyrer, skriv atomisk og nektar å overskrive ei eksisterande fil utan
  `--force`.
- Ein avbroten eller ufullstendig eksport blir ikkje liggjande att som ei
  ferdig fil. Eksportformatet er førebels for trygg oppbevaring og lesing,
  ikkje eit lova importformat.

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
| 0.8.4 | Rett plassering av DM-ar når samtalerekkjefølgja endrar seg |
| 0.8.5 | Raskare og meir føreseieleg BLE-tilkopling på macOS |
| 0.8.6 | Separat, uttrykkeleg betakanal for interne testutgåver |
| 0.8.7 | Konsistent og atomisk databaseeksport via daemonen |
| 0.8.8b1 | Strengt dataskilje per lokal node-ID og nodebundne samtaler |

## Avklaringar som alt er tekne

### Data frå fleire gatewayar

Nodeliste, public, DM, ulest-status, telemetri, posisjon og nodehandlingar blir
viste innanfor éin lokal node-ID. Dette gjeld òg standardkanalen LongFast.
Profilen er berre transportspor og hugsar siste kjende lokale node-ID, slik at
rett historikk kan visast under reconnect. Profilar med same lokale node-ID
deler datasett; ulike lokale nodar blir aldri slått saman automatisk.

### Miljødata

«Environment metrics» er ikkje eit eige vindauge. Dei blir lagra som ein eigen
telemetritype og viste i Telemetri-fana. Ei eiga fane er berre aktuelt dersom
reelle data viser at tabellen blir for tett eller vanskeleg å filtrere.

## Roadmapstatus

Denne delen bevarer dei tekniske detaljane i både gjennomførte og attståande
roadmap-punkt. Nummera viser produktområda frå den opphavlege planen, ikkje
prioriteringa framover. Den faktiske rekkjefølgja står under «Tilrådd neste
arbeid».

### 1. Stabilisere 0.8-observasjonane

Mål: sikre at langtidslogging fungerer med verkelege nodar og varierande
fastvare før datamodellen blir bygd vidare.

Dette er alt dekt automatisk:

- duplikatkontroll for telemetri og posisjon;
- oppbevaringsopprydding og samandrag per node;
- avvising av posisjon utan gyldige koordinatar;
- fallback til mottakstid for urimelege framtidige tidsstempel;
- indekserte, avgrensa databasespørjingar med støtte for telemetritype og
  sidepeikar.

Dette står att:

- La Windows-installasjonen logge over tid og kontrollere databasevekst,
  duplikatkontroll og sortering.
- Test data frå fleire nodetypar og telemetritypar.
- Utvide testane for tomme, delvise og andre feilaktige tidsstempel frå fleire
  fastvarevariantar.
- Måle spørjetid når ein node har mange tusen observasjonar.
- Leggje tidsfilter, telemetritypefilter og synleg sideinndeling til nodeinfo.
  Databasen og IPC-en har delar av grunnlaget, men TUI-en bruker enno faste
  radgrenser utan «last fleire».
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
- Public-samtalar blir knytte til lokal node og logisk kanal. DM-samtalar blir
  knytte til lokal node, fjernnode og kanal, slik at svar ikkje fell stille
  tilbake til kanal 0.
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

### 3. Fullføre plattformstøtta for Blåtann/BLE

Mål: gjere den eksperimentelle BLE-støtta føreseieleg i dei støtta
tenestemodellane, framleis med berre eitt samband eigd av daemonen.

Arkitekturen, profilformatet, oppdaginga, tilkoplingsveljaren, reconnect og
mocka testar er implementerte. Windows er den første live-testa
BLE-plattforma, og macOS har plattformspesifikk CoreBluetooth-handtering.

Dette står att:

1. Test BLE frå den faktiske Linux-systemtenesta og avklar nødvendige
   BlueZ-/systemd-rettar utan å gi tenesta breiare tilgang enn nødvendig.
2. Test BLE frå den faktiske macOS LaunchAgent-jobben, inkludert
   Bluetooth-løyve og systemdialog for paring.
3. Test sambandsbrot, adapter av/på, node ute av rekkevidd, profilbyte og
   automatisk ny tilkopling på Linux og macOS.
4. Stadfest kva som skal støttast i Docker, eller dokumenter BLE i Docker som
   uttrykkeleg ikkje støtta.
5. Hald paring i operativsystemet; MeshPi skal ikkje lagre PIN eller endre
   Bluetooth-konfigurasjonen.

Ferdigkriterium: ein BLE-profil kan oppdagast, lagrast, koplast til på nytt og
bytast frå/til i dei støtta tenestemodellane utan parallelle radiosamband eller
tap av eksisterande data.

### 4. Fleire samtidige gatewayar

Lokal nodeidentitet og DM-semantikk er no avklart i datamodellen:

- ein DM-samtale er lokal node + fjernnode + logisk kanal;
- historikken viser kvar rute separat;
- ei historisk rute er lesbar, men ikkje sendbar når kanalen ikkje finst på
  den aktive noden;
- profil er transportspor, medan lokal node-ID er gatewayidentiteten.
- all historikk og alle standardvisingar er avgrensa til lokal node-ID.

MeshPi skal framleis ha berre eitt aktivt samband. Ikkje start samtidige
gatewayar før reconnect, hendingar, ressursbruk og tenestelivssyklus for fleire
daemon-eigde samband er spesifiserte og testa.

### 5. Vidare vising og eksport

Aktuelle, men lågare prioriterte forbetringar:

- filtrering og sideinndeling for lange telemetri- og posisjonsloggar;
- nodeavgrensa eksport til CSV eller JSON. Heile databasen kan alt
  eksporterast trygt som JSON Lines med `meshpi export`;
- kartlenkje for eit valt tidsrom eller ei enkel ruteframstilling;
- min/maks for utvalde målingar; siste verdi finst alt i nodeoversikta;
- tydelegare vising av datakvalitet og manglande GPS-fix. Satellittal,
  GPS-presisjon og PDOP blir alt viste, medan lagra fix-type og fix-kvalitet
  ikkje er synlege i tabellen;
- eventuelt eit leseorientert webgrensesnitt over den lokale IPC-en.

## Tilrådd neste arbeid

1. La 0.8.7 samle reelle data over tid og noter databasevekst, felt eller
   nodetypar som blir viste feil.
2. Legg tidsfilter, telemetritypefilter og sideinndeling til nodeinfo, og mål
   spørjetid med mange tusen observasjonar.
3. Gjennomfør BLE-test frå systemtenesta på Linux og LaunchAgent på macOS.
4. Bruk den signerte betakanalen til neste større datamodell- eller
   grensesnittendring, og ta `meshpi export` før ei testutgåve som kan endre
   databaseskjemaet.
5. Vent med fleire samtidige gatewayar til tenestelivssyklusen er spesifisert
   og testa.

## Opne produktval

- Treng Telemetri-fana tidsfilter før ho treng fleire faner?
- Skal posisjonshistorikk berre gi lenkjer, eller seinare kunne eksporterast
  som ei rute?
- Skal BLE i Docker støttast, eller dokumenterast som utanfor støtta omfang?
