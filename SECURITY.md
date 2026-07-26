# Tryggleik i MeshPi

## Rapportere sårbarheiter

Ikkje publiser detaljar om ei utesta sårbarheit i ein offentleg issue. Kontakt
vedlikehaldaren privat gjennom kontaktinformasjonen på `venes.org`, og oppgi
MeshPi-versjon, plattform, reproduksjonssteg og mogleg konsekvens. Ikkje legg
ved private meldingar, node-ID-ar, token, databasar eller utgjevingsnøklar.

## Tillitsmodell for oppdateringar

`meshpi update` hentar manifestet over HTTPS og godkjenner det berre når
RSA-PKCS1v1.5/SHA-256-signaturen kjem frå ein allowlista og ikkje tilbakekalla
`key_id`. Oppdateraren lastar deretter ned installatør, plattformlås og
wheel-pakke til ei privat mellombels mappe. Signert storleik og SHA-256 må
stemme for alle tre før den lokalt verifiserte installatøren blir køyrd.
Oppdateraren køyrer aldri `update_command` eller annan kommando frå manifestet.

HTTPS er framleis viktig mot nedgradering og tenestenekt, men å endre
manifestet eller artefaktane på webserveren er ikkje nok til å få kode køyrd
utan den private utgjevingsnøkkelen.

## Nøkkelrotasjon og tilbakekalling

Nøkkelregisteret ligg i `meshpi/signing.py` og i kvar installatør. Normal
rotasjon skjer slik:

1. Generer den nye private nøkkelen utanfor repoet og utgjevingsmappa.
2. Legg berre den offentlege nøkkelen og ny `key_id` til i appen, dei tre
   installatørane og utgjevingsverktøyet.
3. Publiser ei overgangsutgiving signert med den gamle, framleis tiltrudde
   nøkkelen.
4. Når overgangsutgivinga er distribuert, signer nye manifest med den nye
   nøkkelen.
5. Behald den gamle offentlege nøkkelen så lenge støtta klientar kan møte eldre
   signerte manifest, eller legg han i tilbakekallingslista dersom han er
   kompromittert.

Tilbakekalling kan ikkje magisk reparere ein klient som berre kjenner ein
kompromittert nøkkel. Ein slik klient må få ny tillit gjennom ei separat,
autentisert distribusjonsrute eller manuell installasjon. Private nøklar skal
aldri liggje i Git, byggjeartefaktar, loggar eller webmappa.

## Lokale grenser

På Linux og macOS bruker standardinstallasjonen ein privat Unix-socket. På
Windows er IPC avgrensa til ein eksklusivt reservert loopback-TCP-port.
IPC-tokenet blir kontrollert på begge. Meshtastic TCP på port 4403 er
ukryptert og bør berre brukast på eit betrodd nett eller over ein verna tunnel.
MeshPi endrar ikkje konfigurasjonen på radioen og sender ikkje meldingar
automatisk.

Linux always-modus gir sockettilgang gjennom operatøren si primærgruppe berre
når gruppa er privat for éin konto. Ei delt primærgruppe blir avvist under
installasjon, slik at andre lokale brukarar ikkje kan opne IPC-socketen eller
tømme tilkoplingskvoten.

## Nettstad og avhengigheiter

HSTS på `venes.org` er med vilje avgrensa til sjølve verten. Ikkje legg til
`includeSubDomains` eller søk om preload før alle noverande og framtidige
underdomene er kontrollerte og alltid fungerer over HTTPS. Ei feil utviding
kan gjere andre tenester under domenet utilgjengelege i lang tid.

Alle køyretidsavhengigheiter er versjons- og hash-låste per plattform.
`meshtastic` deklarerer `bleak` som eit direkte køyretidskrav, også når MeshPi
berre bruker TCP eller seriell. Det kan derfor ikkje fjernast trygt frå
låsefilene utan ei oppstraumsendring eller ein vedlikehalden fork. Nye
utgivingar skal CVE-kontrollere dei faktiske installerte miljøa på Windows,
Linux og macOS.

## Støtta versjonar

Tryggleiksrettingar blir normalt leverte i siste publiserte MeshPi-versjon.
Oppgrader før du rapporterer ein feil som kan vere retta i ei nyare utgiving.
