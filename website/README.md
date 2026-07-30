# MeshPi-nettsida

Dette er kjeldefilene for `https://venes.org/meshpi/`.

Ved ei ny utgåve:

1. Regenerer og test plattformfilene i `locks/` dersom avhengigheiter er endra.
2. Bygg wheel-fila og legg henne i `downloads/` i publiseringsmappa.
3. Oppdater versjon, filstorleik og SHA-256 for wheel og låsefiler i
   `version.json`.
4. Oppdater versjonsnummer og utgåvenotat i `index.html`.
5. Kopier `index.html`, `styles.css`, `script.js`, `version.json` og `locks/` til
   `H:\Koding\Venes.org\meshpi`.
6. Kopier installasjons- og avinstalleringsskripta frå `installers/` og
   `LICENSE` frå rota.
7. Kontroller manifestet mot faktiske byte, installer alle tre plattformer og
   test automatisk rollback før produksjonsklienten blir oppdatert.

Ei intern beta blir bygd med ein PEP 440-versjon som `0.9.0b1`:

```text
python scripts/prepare_release.py --channel beta \
  --release-note "Kort utgåvenotat" --signing-key <privat-nøkkelsti>
```

Beta skal publiserast under `meshpi/beta/` med `version.json` og installatørane
i rota, låsefiler under `locks/` og wheel under `downloads/`. Betafiler skal
aldri erstatte den stabile `meshpi/version.json`. Sida `beta/index.html` er
med vilje ikkje lenkja frå hovudsida.

Før første beta kan den stabile releasebygginga bruke `--seed-beta`. Det lagar
eit signert betamanifest som peikar på den stabile utgåva, slik at
`meshpi update --beta` svarar at ingen nyare utgåve finst i staden for HTTP 404.

WinSCP lastar publiseringsmappa automatisk opp til webhotellet.
