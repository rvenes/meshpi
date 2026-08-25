# MeshPi-nettsida

Dette er kjeldefilene for `https://venes.org/meshpi/`.
Den fullstendige byggje-, signerings-, staging- og verifikasjonsprosedyren står
i [`RELEASING.md`](../RELEASING.md).

Staging publiserer ingenting automatisk. Bruk berre den gjeldande, godkjende
publiseringsflyten for operatørmiljøet etter at kontrollane i
`RELEASING.md` er grøne.

## Stabil kanal

Under den godkjende `meshpi/`-stagingmappa skal ei stabil utgiving ha:

- `index.html`, `styles.css`, `script.js`, `.htaccess` og `version.json` frå
  `website/`;
- tre installatørar og tre avinstallatørar frå `installers/`;
- `LICENSE` frå prosjektrota;
- `linux.txt`, `macos.txt` og `windows.txt` under `locks/`;
- den aktuelle wheel-fila under `downloads/`.

`scripts/prepare_release.py` skal generere dynamiske manifestfelt og signatur.
Ikkje handrediger hashar, storleikar, publiseringstid eller signatur.

## Betakanal

Ei beta blir bygd med ein PEP 440-versjon som `0.9.0b1`:

```text
python scripts/prepare_release.py --channel beta \
  --release-note "Kort utgåvenotat" --signing-key <privat-nøkkelsti>
```

Under `meshpi/beta/` skal beta ha `index.html`, `version.json` og dei tre
installatørane i rota, tre plattformlåser under `locks/` og wheel under
`downloads/`. Betafiler skal aldri erstatte filer i stabilrota. Sida
`beta/index.html` forklarer risikoen, oppdateringskravet og full installasjonsveg
for alle som ønskjer å prøve den opne betakanalen.

Før første beta, eller når ein ferdig stabil versjon avsluttar ein betaserie,
kan den stabile releasebygginga bruke `--seed-beta`. Det lagar eit signert
betamanifest som peikar på den stabile utgåva, slik at `meshpi update --beta`
får ein trygg veg til den ferdige versjonen.

Før publisering skal det stagea kanalmanifestet vere gyldig signert, og
storleik og SHA-256 skal stemme for wheel, tre låsefiler og tre installatørar.
Etter publisering skal dei same sju artefaktane verifiserast frå dei offentlege
URL-ane før ein installatør blir køyrd.
