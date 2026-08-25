# Utgjevingshandbok for MeshPi

Denne fila er den varige operatørprosedyren for å byggje, signere, stage og
verifisere ei MeshPi-utgiving. `AGENTS.md` eig dei permanente tryggleiks- og
arkitekturinvariantane; denne fila eig dei utførlege release-stega.

Ei releasebestilling gir ikkje i seg sjølv løyve til å committe, pushe, tagge,
opprette ein GitHub Release, endre staging, publisere til venes.org, endre
produksjonsmaskiner eller sende radiotrafikk. Følg alltid den aktive
operatørflyten for miljøet og den konkrete bestillinga.

## Releasegrenser

- Stabilkanalen bruker ein endeleg PEP 440-versjon og
  `website/version.json`.
- Betakanalen bruker ei PEP 440-førehandsutgåve som `X.Y.ZbN` og
  `website/beta/version.json`.
- Ei betautgiving skal aldri endre eller erstatte stabilmanifestet.
- Vanleg `meshpi update` bruker berre stabilkanalen. Beta må veljast
  uttrykkeleg med `meshpi update --beta`.
- Manifestet bind sju artefaktar: wheel, tre plattformlåser og tre
  installatørar. Nettsidefiler, avinstallatørar og lisens er offentlege
  følgjefiler, men er ikkje del av desse sju manifest-hashane.
- Kvar byteendring i wheel, installatør, låsefil eller manifest etter signering
  krev ny bygging av dei aktuelle metadataa og ny signatur.

MeshPi er framleis i 0.x-serien. Bruk normalt patchversjon for feilrettingar og
mindre UI-forbetringar, og minorversjon for større funksjonar eller merkbare
grensesnittendringar.

## Førehandskontrollar

1. Kontroller Git-status og heile diffen. Bevar eksisterande brukarendringar.
2. Bruk Python 3.11 eller nyare i eit isolert utviklingsmiljø.
3. Køyr minst:

   ```text
   python -m pytest -q
   python -m ruff check .
   ```

4. Ved tryggleiks- eller utgivingsarbeid, køyr Bandit dersom det er installert:

   ```text
   python -m bandit -q -r meshpi scripts
   ```

   Vurder låge varsel konkret. Ingen medium eller høge funn skal ignorerast.

5. Kontroller endra POSIX-skript med `sh -n` og endra PowerShell-skript med ein
   PowerShell-syntakskontroll på Windows.
6. Ved ei utgiving som kan migrere data, ta ein trygg eksport med
   `meshpi export` før plattformtesten. Eksporten kan innehalde private data og
   skal ikkje leggjast i Git, bygg- eller stagingområdet.

Repoet har ikkje ein eigen CI-workflow. Desse portane må derfor køyrast og
rapporterast lokalt.

## Avhengigheiter og plattformlåser

Direkte køyretidsavhengigheiter skal samsvare mellom `pyproject.toml` og
`locks/requirements.in`. Når ei avhengigheit blir endra:

1. Oppdater begge filene.
2. Regenerer `locks/linux.txt` på Linux, `locks/macos.txt` på macOS og
   `locks/windows.txt` på Windows.
3. Bruk forma som står i hovudet på kvar eksisterande låsefil:

   ```text
   pip-compile --allow-unsafe --generate-hashes --strip-extras \
     --output-file=<plattformfil> locks/requirements.in
   ```

   Tilpass berre stiane til plattforma. Ikkje kopier ei låsefil mellom
   operativsystema som erstatning for plattformgenerering.

4. Køyr full testsuite på nytt.
5. Installer frå kvar plattformfil med `pip --require-hashes` på den aktuelle
   plattforma.
6. Commit dei regenererte plattformlåsene saman med avhengigheitsendringa når
   releaseendringa blir committa.
7. Signer manifestet på nytt. Ei endra låsefil gjer førre signatur ugyldig.

## Versjonsauke

Kontroller desse stadene ved ei versjonsendring:

- `pyproject.toml`
- `meshpi/__init__.py`
- versjonsforventningar i `tests/`
- versjonsnummer, utgåvetekst og wheel-lenkje i `website/index.html` for ei
  stabil utgiving
- utgåvenotat og kanalmanifest i `website/version.json` eller
  `website/beta/version.json`

Ikkje handrediger dynamiske hashar, storleikar, publiseringstid eller signatur.
`scripts/prepare_release.py` skal generere dei.

## Signeringsnøkkel

Den private RSA-utgivingsnøkkelen skal berre finnast utanfor repoet, Git,
byggmappa og stagingområdet. Oppgi stien med `--signing-key` eller den
prosesslokale miljøvariabelen `MESHPI_SIGNING_KEY`; variabelen skal innehalde
stien, aldri PEM-innhaldet.

Les den gitignorerte `LOCAL_TESTING.md` før lokal signering dersom ho finst.
Ho kan dokumentere kvar den godkjende peikaren ligg. Dersom peikaren ikkje er
eintydig, stopp og spør operatøren. Ikkje søk breitt i brukarmapper etter
private nøklar, og ikkje skriv moglege nøkkelstiar eller nøkkelinnhald til
terminal, logg eller chat.

## Byggje og signere

Køyr frå prosjektrota etter versjonsauke, plattformlåser og grøne testar.

Stabil utgiving:

```text
python scripts/prepare_release.py --signing-key <privat-nøkkelsti>
```

Beta:

```text
python scripts/prepare_release.py --channel beta \
  --release-note "Kort utgåvenotat" \
  --signing-key <privat-nøkkelsti>
```

`--release-note` kan gjentakast og er påkravd for beta. Ei stabil utgiving kan
bruke `--seed-beta` når betakanalen enno ikkje har eit manifest. Då blir det
laga eit signert betamanifest som peikar på den stabile utgivinga, slik at
`meshpi update --beta` ikkje møter HTTP 404.

Releaseverktøyet:

1. byggjer `build/release-<versjon>/meshpi-<versjon>-py3-none-any.whl`;
2. reknar storleik og SHA-256 for wheel, tre låsefiler og tre installatørar;
3. oppdaterer rett kanalmanifest under `website/`;
4. signerer det kanoniske manifestet med RSA-PKCS1v1.5/SHA-256;
5. kopierer kanalmanifestet til utgivingsmappa som `version.json`;
6. kan kopiere eit seed-manifest som `beta-version.json`.

Genererte filer under `build/` skal ikkje commitast.

## Kontroll etter signering

1. Køyr full testsuite og Ruff på nytt. Signaturtestane skal vere grøne.
2. Kontroller at manifestkanalen og versjonstypen samsvarar.
3. Verifiser manifestsignaturen med `meshpi.signing`.
4. Kontroller faktisk filstorleik og SHA-256 for alle sju manifestbundne
   artefaktar.
5. Kontroller at stabilmanifestet er byteuendra etter eit betabygg.
6. Kontroller at nettside, utgåvetekst og wheel-lenkje peikar på rett kanal og
   versjon.

Ikkje stage ei utgiving dersom nokon av desse kontrollane feilar.

## Stagingstruktur

Staging publiserer ingenting av seg sjølv. Bruk den godkjende stagingrota og
publiseringsflyten for operatørmiljøet; `AGENTS.md` kan definere den konkrete
lokale destinasjonen for AI-agentar. Ikkje bruk ein konkurrerande FTP-, SFTP-,
WinSCP- eller automatisk synkroniseringsflyt.

### Stabil kanal

Under `<stagingrot>/meshpi/` skal ei ny stabil utgiving ha:

```text
index.html
styles.css
script.js
.htaccess
version.json
install-linux.sh
install-macos.sh
install-windows.ps1
uninstall-linux.sh
uninstall-macos.sh
uninstall-windows.ps1
LICENSE
locks/linux.txt
locks/macos.txt
locks/windows.txt
downloads/meshpi-<versjon>-py3-none-any.whl
```

Nettsidefilene og manifestet kjem frå `website/`, skripta frå `installers/`,
lisensen frå prosjektrota, låsene frå `locks/` og wheel-en frå den aktuelle
utgivingsmappa.

### Betakanal

Under `<stagingrot>/meshpi/beta/` skal ei ny beta ha:

```text
index.html
version.json
install-linux.sh
install-macos.sh
install-windows.ps1
locks/linux.txt
locks/macos.txt
locks/windows.txt
downloads/meshpi-<prerelease>-py3-none-any.whl
```

`website/beta/index.html` og `website/beta/version.json` er kjeldene for sida
og manifestet. Eit generert `beta-version.json` frå `--seed-beta` skal
stagingast som `beta/version.json`, ikkje med byggjenamnet.

Beta skal ikkje erstatte filer i stabilrota. Historiske wheels og andre
eksisterande stagingfiler skal bevarast med mindre ei eiga, uttrykkeleg
oppryddingsoppgåve seier noko anna; staging kan bli spegla slik at lokal
sletting også slettar offentleg.

## Stagingkontroll

Før publisering:

1. Kontroller at alle stagingfiler kjem frå den godkjende kjelda ovanfor.
2. Kontroller at det stagea manifestet er gyldig signert.
3. Kontroller storleik og SHA-256 for wheel, tre låser og tre installatørar mot
   det stagea manifestet.
4. Bytekontroller nettsidefiler, avinstallatørar og `LICENSE` mot kjelda når dei
   inngår i kanalen.
5. Kontroller at inga privat nøkkel, `.env`, database, profil, logg, eksport,
   lokalt byggjemiljø eller privat utviklingsinstruks er lagd til.
6. Kontroller at beta- og stabiltreet ikkje er blanda.

## Publisering og offentleg verifikasjon

Staging er ikkje publisering. Publiser berre når operatøren uttrykkeleg har
bestilt det, og bruk den gjeldande godkjende publiseringsflyten for miljøet.
Prosjektet skal ikkje føresette automatisk opplasting.

Etter vellukka publisering, last ned rett offentleg kanalmanifest og kontroller:

1. at `latest_version` og `channel` er rette;
2. at manifestsignaturen blir godkjend av `meshpi.signing`;
3. at storleik og SHA-256 stemmer for alle sju manifestbundne artefaktar;
4. at nettsida, kanalmanifestet og wheel-lenkja svarar utan HTTP-feil;
5. at den andre kanalen framleis er uendra når utgivinga berre gjaldt éin kanal.

Ikkje køyr ein offentleg installatør før signaturen og alle sju
artefaktkontrollane er grøne.

## Plattformtest etter publisering

Test kvar utgiving på Linux, macOS og Windows. Legg djupare regresjonstest til
på plattformene og tenestemodellane endringa rører. Kontroller minst:

- installert versjon;
- `current`- og ved behov `previous`-peikaren;
- tenestestatus i `always`- eller `session`-modusen som er relevant;
- `meshpi doctor --offline`;
- at eksisterande database, profilar og konfigurasjon er bevarte;
- at rollback fungerer når endringa rører oppdaterings- eller installasjonsflyt.

Ved installatørendringar skal same offentlege installatør køyrast to gonger for
å avdekkje idempotens- og tenesterace. Mac-testen skal særleg kontrollere
LaunchAgent-byte; Linux-testen systemd og private socketrettar; Windows-testen
per-brukar-autostart, prosessvakt og bokstavlege stiar.

Før ein produksjonsklient blir oppdatert, skal automatisk rollback vere prøvd i
eit isolert testoppsett for den aktuelle releaseflyten.

Live-testing følgjer radiosikkerheita i `AGENTS.md` og eventuelle lokale
testinstruksjonar. Ei release autoriserer ikkje public-sending eller endring av
Meshtastic-konfigurasjon.

## Uavhengig autorisasjon og rekkjefølgje

Bygg/test, signering, commit, push, tagg, GitHub Release, lokal staging og
offentleg venes.org-publisering er åtte separate handlingar. Kvar handling
krev at den konkrete bestillinga dekkjer henne.

- Ei bestilling om venes.org-publisering autoriserer ikkje commit, push, tagg
  eller GitHub Release.
- Ei bestilling om commit, push, tagg eller GitHub Release autoriserer ikkje
  staging eller venes.org-publisering.
- Ei releasebestilling utan nærare publiseringsomfang autoriserer berre dei
  uttrykkeleg nemnde bygg-, test-, signerings- og artefaktstega.
- Staging autoriserer berre endring av den godkjende lokale stagingrota; det
  er ikkje offentleg publisering.

Når alle stega faktisk er bestilte, er normal teknisk rekkjefølgje:

1. valider kjelde, versjon og plattformlåser;
2. bygg, signer og verifiser releaseartefaktane;
3. utfør dei særskilt bestilte Git-handlingane;
4. stage dei godkjende bytea og køyr stagingkontrollen;
5. førehandsvis og publiser med den godkjende globale venes.org-flyten;
6. verifiser offentlege byte og plattformer.

Rekkjefølgja gir ikkje i seg sjølv autorisasjon til noko steg. Git-versjonen,
kanalmanifestet, stagingbytea og dei offentlege filene skal representere same
utgiving når dei aktuelle stega er fullførte. Bruk ei presis commitmelding når
commit er bestilt.

## Ferdigmelding

Ferdigmeldinga skal skilje mellom det som er bygd, signert, stagea, publisert
og plattformtesta. Ikkje hevde at eit steg er utført dersom det berre er lese,
simulert eller planlagt.
