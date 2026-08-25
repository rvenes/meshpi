# Arbeidsinstruks for MeshPi

Denne fila gjeld heile MeshPi-repositoriet. Følg den aktive globale
instruksjonsstrukturen kumulativt når oppgåva utløyser maskin-, Mac-,
Syncthing- eller venes.org-arbeid. Utførlege releaseprosedyrar står i
[`RELEASING.md`](RELEASING.md).

## Radio- og datatryggleik

- Endra aldri kanal-, radio- eller annan konfigurasjon på ein Meshtastic-node.
- Automatiske testar skal mocke Meshtastic og skal aldri sende på radio.
- Send aldri ei ekte melding på public-kanalen under agentstyrt live-test.
  Public kan bli vidaresendt til MQTT og nå andre delar av mesh-nettet.
- Ekte radiosending skal berre vere ei tydeleg merkt DM etter at brukaren har
  bedt om den aktuelle testen og den fulle mottakar-ID-en er stadfesta.
- Bevar database, profilar, historikk og konfigurasjon ved installasjon,
  oppdatering og test. Sletting av brukardata krev uttrykkeleg godkjenning.
- Ei ny installering skal ikkje ha ein førehandsvald node. IP-adresser,
  serienummer, node-ID-ar og vertsnamn frå utviklingsmiljøet skal aldri bli
  distribuerte standardverdiar.
- Meshtastic TCP på port 4403 er ukryptert. Ikkje framstill det som trygt over
  eit ubeskytta nett.

## Arkitektur og IPC

- Berre daemonen skal eige Meshtastic-sambandet og SQLite-fila.
- MeshPi kan ha fleire lagra tilkoplingsprofilar, men berre éin aktiv gateway
  om gongen.
- CLI, TUI og nye grensesnitt skal bruke den lokale IPC-protokollen; dei skal
  ikkje opne parallelle radio- eller databasesamband.
- IPC skal vere lokal-only: privat Unix-socket på Linux/macOS eller eksklusiv
  loopback-TCP på Windows. TCP skal berre godta `127.0.0.1`, `::1` eller
  `localhost` og skal aldri bindast til ei ekstern adresse.
- IPC-token og private socketrettar skal bevarast. Nye transporttypar skal inn
  bak daemonen og den eksisterande profilmodellen.

## Utviklingskontrakt og kvalitetsportar

- Brukargrensesnitt, feilmeldingar og brukarvend dokumentasjon skal vere på
  nynorsk. Python-identifikatorar og korte tekniske kommentarar kan vere på
  engelsk.
- Støtta Python-versjon er 3.11 eller nyare.
- Hald CLI-kompatibilitet når det er mogleg, og legg testar til nye funksjonar
  og feilrettingar.
- Før levering skal minst desse vere grøne:

  ```text
  python -m pytest -q
  python -m ruff check .
  ```

- Ved tryggleiks- eller utgivingsarbeid skal Bandit køyrast dersom det er
  installert. Ingen medium eller høge funn skal ignorerast.
- Kontroller endra POSIX-skript med `sh -n` og endra PowerShell-skript med ein
  PowerShell-syntakskontroll på Windows.
- Rapporter presist kva som blei testa. Repoet har ingen eigen CI-workflow som
  erstattar dei lokale portane.

## Live- og plattformtest

- Les den gitignorerte `LOCAL_TESTING.md` før lokal live-, maskin- eller
  signeringstest dersom fila finst. Ho skal aldri commitast, kopierast til ei
  utgiving eller publiserast.
- Start med lesande kontrollar som versjon, status, nodeliste, tenestestatus og
  `meshpi doctor --offline`.
- Mac-, Linux- og Windows-testar skal gjerast på den aktuelle plattforma og
  følgje dei aktive globale vertsinstruksjonane. Private vertsdetaljar skal
  berre liggje i lokale eller globale private instruksjonar.
- På maskiner med andre produksjonstenester skal start/stopp avgrensast til
  MeshPi. Varsle før ei MeshPi-teneste blir starta på nytt, og ikkje start ei
  heil maskin på nytt berre for å teste MeshPi.
- Dersom nødvendig plattform- eller tilgangsinformasjon manglar, stopp den
  delen av testen i staden for å gjette.

## Installatørar og låsefiler

- Alle plattformer støttar `always`- og `session`-modus.
- Installatørane skal vere idempotente, kontrollere manifest/signatur og
  SHA-256, bruke `pip --require-hashes`, byggje kvar versjon i eiga mappe,
  bevare eksisterande data og kunne rulle tilbake til førre fungerande
  versjon.
- Linux: systemd-tenesta og `/opt/meshpi` må ikkje påverke andre tenester.
  Systemd er førstevalet på Raspberry Pi; Docker er valfritt og skal ikkje
  eksponere IPC-porten på verten.
- macOS: byte av `current`-symlenka skal vere atomisk og må ikkje bruke BSD
  `mv` mot ei symlenke til ei mappe. Vent til den gamle LaunchAgent-jobben er
  heilt fjerna før same label blir registrert på nytt.
- Windows: autostart og prosessvakt er per brukar. Behandle alle stiar som
  bokstavlege stiar og bevar eksisterande konfigurasjon.
- Direkte køyretidsavhengigheiter skal samsvare mellom `pyproject.toml` og
  `locks/requirements.in`.
- `locks/linux.txt`, `locks/macos.txt` og `locks/windows.txt` skal genererast
  på rett operativsystem med hashane aktiverte. Kommandoforma står i kvar
  låsefil og i `RELEASING.md`.
- Etter ei avhengigheitsendring skal alle tre låser regenererast, full
  testsuite køyrast, kvar lås installerast med `--require-hashes` på rett
  plattform og manifestet signerast på nytt.

## Releaseintegritet

- Følg heile `RELEASING.md` ved versjons-, byggje-, signerings-, staging- og
  releasearbeid.
- Stabilkanalen skal berre peike på endelege versjonar. Betakanalen skal bruke
  PEP 440-førehandsversjon og må aldri endre stabilmanifestet.
- `scripts/prepare_release.py` skal generere dynamiske hashar, storleikar,
  publiseringstid og signatur. Ikkje handrediger desse felta.
- Den private signeringsnøkkelen skal vere utanfor repo, bygg og staging.
  Bruk godkjend sti via argument eller `MESHPI_SIGNING_KEY`; ikkje søk breitt
  etter nøklar eller skriv nøkkelsti/-innhald i loggar.
- Manifestet skal binde nøyaktig wheel, tre plattformlåser og tre
  installatørar. Ei byteendring i desse eller manifestet etter signering krev
  ny bygging av metadata og ny signatur.
- Versjon, kanal, utgåvetekst og artefaktlenkjer skal samsvare mellom kode,
  testar, nettside, manifest, bygg og staging før publisering.

## Staging, publisering og lisens

- Lokal staging for den offentlege MeshPi-sida er
  `H:\Koding\Venes.org\meshpi`. Staging publiserer ingenting automatisk.
- Stabil- og betatreet, offentlege følgjefiler og kontrollane før/etter staging
  er definerte i `RELEASING.md`.
- Dersom brukaren bestiller publisering, skal den aktive globale
  `venesorgupload.md`-flyten eigne preview, opplasting og offentleg
  verifikasjon. Ikkje bruk konkurrerande FTP-, SFTP-, WinSCP- eller
  synkroniseringslogikk.
- Ikkje legg privat nøkkel, `.env`, database, profil, logg, eksport,
  byggjemiljø eller private instruksjonar i staging eller utgiving.
- MeshPi er GPL-3.0-only. Tredjepartskode må ha kompatibel lisens; kjelde,
  opphavsrett og nødvendig attribusjon skal bevarast.
