# Arbeidsinstruks for AI-agentar

Denne fila gjeld heile MeshPi-repositoriet. Ho forklarer korleis prosjektet
skal utviklast, testast og publiserast. Følg alltid nyare og meir konkrete
instruksjonar frå brukaren dersom dei kolliderer med denne fila.

## Mål og tryggleiksgrenser

MeshPi er ein terminalklient for Meshtastic. Bakgrunnstenesta eig sambandet til
radioen og lagrar meldingar lokalt; CLI/TUI snakkar med tenesta over IPC på
loopback.

- Endra aldri konfigurasjonen på ein Meshtastic-node.
- Send aldri ei ekte melding på public-kanalen under live-test. Public kan bli
  vidaresendt til MQTT og plukka opp andre stader i mesh-nettet. Ekte
  radiosending skal berre vere DM etter at brukaren uttrykkeleg har bedt om
  testen og oppgitt eller stadfesta mottakar.
- Automatiske testar skal mocke Meshtastic og skal ikkje sende på radio.
- Ver ekstra varsam på maskiner som køyrer andre produksjonstenester. Avgrens
  start/stopp til MeshPi og varsle før ei MeshPi-teneste blir starta på nytt.
- Bevar eksisterande database, profilar og konfigurasjon ved installasjon og
  oppdatering. Sletting av brukardata krev uttrykkeleg godkjenning.
- Ei ny installering skal ikkje ha ein førehandsvald node. IP-adresser,
  serienummer, node-ID-ar og vertsnamn frå utviklingsmiljøet skal aldri
  distribuerast som standardverdiar.
- Ikkje bind IPC til andre adresser enn `127.0.0.1`, `::1` eller `localhost`.
- Meshtastic TCP på port 4403 er ukryptert. Ikkje framstill det som trygt over
  eit ubeskytta nett.

## Språk og kodepraksis

- Brukargrensesnitt, feilmeldingar og brukarvend dokumentasjon skal vere på
  nynorsk.
- Python-identifikatorar og korte tekniske kommentarar kan vere på engelsk.
- Støtta Python-versjon er 3.11 eller nyare.
- Hald CLI-kompatibilitet når det er mogleg, og legg testar til nye funksjonar
  og feilrettingar.
- Ikkje gjer tilfeldige formatteringar eller endringar utanfor oppgåva.
- Arbeid rundt eksisterande lokale endringar; dei tilhøyrer brukaren.

## Prosjektkart

- `meshpi/`: programkode, daemon, IPC, database, CLI og Textual-TUI.
- `tests/`: testar; Meshtastic-sambandet blir mocka.
- `installers/`: installasjon og avinstallasjon for Linux, macOS og Windows.
- `locks/`: plattformspesifikke, hash-låste Python-avhengigheiter.
- `scripts/prepare_release.py`: byggjer wheel, reknar hashar og signerer
  `website/version.json`.
- `website/`: kjeldefilene for `https://venes.org/meshpi/`.
- `build/release-<versjon>/`: genererte utgivingsfiler; skal ikkje commitast.
- `Dockerfile` og `docker-compose.yml`: valfri containerdrift. Systemd er
  førstevalet på Raspberry Pi.

Arkitekturen har eitt viktig invariant: berre daemonen skal eige
Meshtastic-sambandet og SQLite-fila. Nye grensesnitt skal bruke den lokale
IPC-protokollen, ikkje opne eit parallelt radiosamband.

## Vanleg utviklingsflyt

1. Les oppgåva og inspiser status før endringar:

   ```text
   git status --short
   git diff
   ```

2. Opprett eller bruk eit isolert miljø og installer utviklingsavhengigheiter:

   ```text
   python -m venv .venv
   python -m pip install -e ".[test,dev]"
   ```

3. Gjer den minste samanhengande endringa. Oppdater testar og dokumentasjon i
   same endring når åtferd eller kommandoar blir endra.

4. Køyr relevante testar under arbeidet. Før levering skal minst dette vere
   grønt:

   ```text
   python -m pytest -q
   python -m ruff check .
   ```

5. Ved tryggleiks- eller utgivingsarbeid, køyr også dersom Bandit er installert:

   ```text
   python -m bandit -q -r meshpi scripts
   ```

   Vurder låge varsel konkret. Ingen medium eller høge funn skal ignorerast.

6. Kontroller shell-syntaks for endra POSIX-skript med `sh -n`. Kontroller
   PowerShell-syntaks på Windows når eit `.ps1`-skript er endra.

7. Sjå gjennom `git diff --check`, heile diffen og `git status --short` før
   commit. Testresultat skal rapporterast presist; ikkje sei at noko er testa
   dersom det berre er lese eller simulert.

## Live-testar

Før lokal live-testing skal agenten lese `LOCAL_TESTING.md` dersom fila finst.
Ho inneheld private SSH-alias, lokale adresser og rollene til testmaskinene.
Fila er med vilje ignorert av Git og skal aldri commitast, kopierast til ei
utgiving eller publiserast. Dersom ho manglar, bruk berre opplysningar brukaren
gir i den aktuelle oppgåva; ikkje gjett adresser eller tilgang.

Start med lesande kontrollar:

```text
meshpi --version
meshpi status
meshpi nodes
meshpi doctor --offline
meshpi service status
```

Ved ekte meldingstest skal public-steget i README-en ikkje brukast i dette
miljøet. Test mottak passivt eller bruk ei tydeleg merkt DM til ein avtalt og
stadfesta node-ID, berre etter uttrykkeleg beskjed frå brukaren.

Mac-, Linux- og Windows-testar skal gjerast på den aktuelle plattforma. Private
vertsnamn, SSH-alias og tilgangsdetaljar skal berre liggje i den gitignorerte
`LOCAL_TESTING.md` eller i operatøren sine private instruksjonar utanfor
repoet. Dei skal aldri leggjast i spora filer, commitast eller nemnast i
offentlege loggar. Dersom instruksjonane ikkje er tilgjengelege, stopp før
plattformarbeidet og spør brukaren.

Ikkje start ei heil maskin på nytt berre for å teste MeshPi. Dersom omstart av
maskina faktisk er nødvendig, varsle brukaren først.

## Installasjon og tenestemodellar

Alle plattformer støttar to modusar:

- `always`: daemonen startar automatisk og lever vidare etter at TUI-en blir
  lukka.
- `session`: daemonen blir starta ved behov og kan stoppast når TUI-en blir
  lukka.

Installatørane skal vere idempotente, kontrollere signatur og SHA-256, bruke
`pip --require-hashes`, byggje kvar versjon i ei eiga mappe og kunne rulle
tilbake til førre fungerande versjon.

Plattformdetaljar som ikkje må regresserast:

- Linux: systemd-tenesta og `/opt/meshpi` må ikkje påverke andre tenester.
- macOS: byte av `current`-symlenka skal bruke atomisk erstatting og ikkje BSD
  `mv` mot ei symlenke til ei mappe. Vent til den gamle LaunchAgent-jobben er
  heilt fjerna før same label blir registrert på nytt.
- Windows: autostart og prosessvakta er per brukar; behandl alle stiar som
  bokstavlege stiar og bevar eksisterande konfigurasjon.

## Avhengigheiter og låsefiler

Direkte køyretidsavhengigheiter skal samsvare mellom `pyproject.toml` og
`locks/requirements.in`. `locks/linux.txt`, `locks/macos.txt` og
`locks/windows.txt` er plattformspesifikke og må genererast på den aktuelle
plattformen med `pip-compile --allow-unsafe --generate-hashes --strip-extras`.
Kommandoen som sist blei brukt står i toppen av kvar låsefil.

Når ei avhengigheit blir endra:

1. Oppdater både `pyproject.toml` og `locks/requirements.in`.
2. Regenerer alle tre plattformfilene på rett operativsystem.
3. Køyr full testsuite på nytt.
4. Installer frå låsefila med `--require-hashes` på kvar plattform.
5. Signer manifestet på nytt; ein endra låsefil gjer førre signatur ugyldig.

## Versjonering

MeshPi er i 0.x-serien. Bruk normalt:

- patchversjon for feilrettingar og mindre UI-forbetringar;
- minorversjon for større funksjonar eller merkbare grensesnittendringar.

Ved versjonsauke skal desse stadene kontrollerast:

- `pyproject.toml`
- `meshpi/__init__.py`
- versjonsforventningar i `tests/`
- versjonsnummer, utgåvetekst og wheel-lenkje i `website/index.html`
- utgåvenotat i `website/version.json`

Ikkje handrediger dynamiske hashar, storleikar, publiseringstid eller signatur.
`scripts/prepare_release.py` skal generere dei.

## Byggje og signere ei utgiving

Ei privat RSA-utgivingsnøkkel finst berre utanfor repoet. Ho skal aldri
skrivast ut i terminaloutput, kopierast inn i prosjektet eller commitast. Bruk
sti via argument eller miljøvariabelen `MESHPI_SIGNING_KEY`.

Frå prosjektre rota, etter versjonsauke og grøne testar:

```text
python scripts/prepare_release.py --signing-key <privat-nøkkelsti>
```

Skriptet:

1. byggjer `build/release-<versjon>/meshpi-<versjon>-py3-none-any.whl`;
2. reknar storleik og SHA-256 for wheel, tre låsefiler og tre installatørar;
3. oppdaterer metadata i `website/version.json`;
4. signerer det kanoniske manifestet med RSA-PKCS1v1.5/SHA-256;
5. kopierer manifestet til utgivingsmappa.

Køyr full testsuite etter signering. Signaturtestane skal då vere grøne. Kvar
endring i wheel, installatør, låsefil eller manifest etter dette krev ny bygging
og ny signatur.

## Publisere på venes.org

Publisering er ei ekstern endring og skal berre gjerast når brukaren ber om
det. På hovudmaskina blir denne mappa synkronisert automatisk til webhotellet:

```text
H:\Koding\Venes.org\meshpi
```

Publiseringsmappa skal få:

- `website/index.html`, `styles.css`, `script.js`, `.htaccess` og
  `version.json` i rota;
- alle installasjons- og avinstalleringsskript frå `installers/` i rota;
- `LICENSE` i rota;
- `locks/*.txt` under `locks/`;
- den nye wheel-fila under `downloads/`.

Ikkje kopier den private signeringsnøkkelen, `.env`, database, profilar,
loggar, lokale byggjemiljø eller private utviklingsinstruksjonar.

Etter at synkroniseringa har fått tid til å fullføre, last ned det offentlege
`https://venes.org/meshpi/version.json` og kontroller:

1. at `latest_version` er rett;
2. at manifestsignaturen blir godkjend av `meshpi.signing`;
3. at storleik og SHA-256 stemmer for alle sju artefaktar: wheel, tre
   låsefiler og tre installatørar;
4. at nettsida og wheel-lenkja svarar utan HTTP-feil.

Ikkje test ein installatør frå venes.org før den offentlege signaturen og alle
hashane er verifiserte. Installer deretter på dei plattformene endringa gjeld,
og kontroller versjon, `current`-peikar, tenestestatus og
`meshpi doctor --offline`. Ved installatørendringar bør same installatør køyrast
to gonger for å avdekkje idempotens- og tenesterace.

## Git og GitHub

- Ikkje commit genererte `build/`-filer, virtuelle miljø, `.env`, data eller
  loggar.
- Bruk presise commit-meldingar som skildrar den ferdige endringa.
- Ikkje push berre fordi ei lokal kodeendring er ferdig; push når brukaren har
  bedt om publisering eller GitHub-oppdatering.
- Ved ei publisert utgiving skal Git-versjonen, `website/version.json` og dei
  offentlege filene vere identiske. Commit og push først etter at test og
  offentleg verifikasjon er grøne.
- Bruk aldri destruktive Git-kommandoar for å rydde bort endringar du ikkje
  sjølv har laga.

## Sjekkliste før ferdigmelding

- [ ] Endringa er avgrensa til oppgåva og eksisterande brukarendringar er
      bevarte.
- [ ] Ingen utviklar-IP, node-ID, token, passord, privat nøkkel eller privat
      vertsdetalj er lagd til.
- [ ] `pytest`, Ruff og relevant skriptsyntaks er grøne.
- [ ] README/nettside/installasjonskommandoar er oppdaterte dersom åtferda blei
      endra.
- [ ] Versjon og utgåvenotat samsvarar overalt.
- [ ] Manifestet blei signert etter siste artefaktendring.
- [ ] Offentleg signatur og alle artefakthashar er verifiserte etter opplasting.
- [ ] Relevant plattforminstallasjon og offline doctor er testa.
- [ ] Git-status, commit og push er rapporterte korrekt.

MeshPi er GPL-3.0-only. Ved gjenbruk av tredjepartskode må lisensen vere
kompatibel, kjelda må dokumenterast, og nødvendig opphavsrett/attribusjon må
bevarast.
