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

WinSCP lastar publiseringsmappa automatisk opp til webhotellet.
