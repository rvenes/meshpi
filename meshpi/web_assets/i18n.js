"use strict";

// Only explicit UI literals and the original static document are translation sources.
const meshpiI18n = (() => {
  const catalogs = {"ingen node": ["ingen node", "no node"], "feil": ["feil", "error"], "søkjer": ["søker", "searching"], "uviss": ["uviss", "uncertain"],
  "Ukjend node": [
    "Ukjent node",
    "Unknown node"
  ],
  "Privat meshklient": [
    "Privat meshklient",
    "Private mesh client"
  ],
  "Logg inn for å halde fram. Handlinga blei ikkje utført.": [
    "Logg inn for å fortsette. Handlingen ble ikke utført.",
    "Sign in to continue. The action was not performed."
  ],
  "Innlogging er nødvendig": [
    "Innlogging er nødvendig",
    "Sign-in required"
  ],
  "Sambandet blei brote. Prøver igjen automatisk.": [
    "Forbindelsen ble brutt. Prøver igjen automatisk.",
    "Connection lost. Retrying automatically."
  ],
  "Ugyldig svar": [
    "Ugyldig svar",
    "Invalid response"
  ],
  "Ukjend sendestatus. Kontroller sendestatus og historikken før du sender på nytt.": [
    "Ukjent sendestatus. Kontroller sendestatus og historikken før du sender på nytt.",
    "Unknown send status. Check the send status and history before sending again."
  ],
  "Sambandet blei brote. Prøver igjen ved neste oppfrisking.": [
    "Forbindelsen ble brutt. Prøver igjen ved neste oppdatering.",
    "Connection lost. Retrying on the next refresh."
  ],
  "Sambandet er fornya. Handlinga blei avvist før utføring. Prøv igjen når du er klar.": [
    "Forbindelsen er fornyet. Handlingen ble avvist før utføring. Prøv igjen når du er klar.",
    "Connection renewed. The action was rejected before execution. Try again when ready."
  ],
  "Førespurnaden feila.": [
    "Forespørselen mislyktes.",
    "The request failed."
  ],
  "Sambandet blei brote. Kontroller historikken før du sender på nytt.": [
    "Forbindelsen ble brutt. Kontroller historikken før du sender på nytt.",
    "Connection lost. Check the history before sending again."
  ],
  "Kanal": [
    "Kanal",
    "Channel"
  ],
  "Kanalar": [
    "Kanaler",
    "Channels"
  ],
  "Direkte meldingar": [
    "Direktemeldinger",
    "Direct messages"
  ],
  "Ingen meldingar enno": [
    "Ingen meldinger ennå",
    "No messages yet"
  ],
  "Ingen samtalar. Vel ein gateway i Tilkoplingar.": [
    "Ingen samtaler. Velg en gateway under Tilkoblinger.",
    "No conversations. Select a gateway in Connections."
  ],
  "sist {0}": [
    "sist {0}",
    "last {0}"
  ],
  "Ingen nodar passar søket eller filteret.": [
    "Ingen noder passer søket eller filteret.",
    "No nodes match the search or filter."
  ],
  "{0} min sidan": [
    "{0} min siden",
    "{0} min ago"
  ],
  "1 time sidan": [
    "1 time siden",
    "1 hour ago"
  ],
  "{0} timar sidan": [
    "{0} timer siden",
    "{0} hours ago"
  ],
  "kl. {0}": [
    "kl. {0}",
    "at {0}"
  ],
  "{0} / 237 byte": [
    "{0} / 237 byte",
    "{0} / 237 bytes"
  ],
  "Vel ei samtale før du sender.": [
    "Velg en samtale før du sender.",
    "Select a conversation before sending."
  ],
  "Daemonen er ikkje tilkopla.": [
    "Daemonen er ikke tilkoblet.",
    "The daemon is not connected."
  ],
  "Ventar på meldingshistorikken. Sending er sperra.": [
    "Venter på meldingshistorikken. Sending er sperret.",
    "Waiting for message history. Sending is blocked."
  ],
  "Denne historiske ruta er berre lesbar.": [
    "Denne historiske ruten er skrivebeskyttet.",
    "This historical route is read-only."
  ],
  "Send til alle på denne kanalen.": [
    "Send til alle på denne kanalen.",
    "Send to everyone on this channel."
  ],
  "DM til {0} · {1}": [
    "DM til {0} · {1}",
    "DM to {0} · {1}"
  ],
  "vald kanal": [
    "valgt kanal",
    "selected channel"
  ],
  "Ukjend sendestatus. Kontroller status før du sender på nytt.": [
    "Ukjent sendestatus. Kontroller status før du sender på nytt.",
    "Unknown send status. Check the status before sending again."
  ],
  "Vel ei samtale": [
    "Velg en samtale",
    "Select a conversation"
  ],
  "Kanalar og direkte meldingar": [
    "Kanaler og direktemeldinger",
    "Channels and direct messages"
  ],
  "Kanal · {0}": [
    "Kanal · {0}",
    "Channel · {0}"
  ],
  "Vel ein kanal eller start ein DM.": [
    "Velg en kanal eller start en DM.",
    "Select a channel or start a DM."
  ],
  "Hentar meldingshistorikk …": [
    "Henter meldingshistorikk …",
    "Loading message history …"
  ],
  "Du": [
    "Du",
    "You"
  ],
  "Ingen meldingar i denne samtalen enno.": [
    "Ingen meldinger i denne samtalen ennå.",
    "No messages in this conversation yet."
  ],
  "Gatewayen er bytt. Vel ei samtale før du sender.": [
    "Gatewayen er byttet. Velg en samtale før du sender.",
    "Gateway changed. Select a conversation before sending."
  ],
  "Sambandet til daemonen er brote": [
    "Forbindelsen til daemonen er brutt",
    "Connection to the daemon lost"
  ],
  "Ingen kjende nodar passar søket. Du kan òg skrive ein full node-ID.": [
    "Ingen kjente noder passer søket. Du kan også skrive en full node-ID.",
    "No known nodes match the search. You can also enter a full node ID."
  ],
  "Ingen kjende mottakarar enno. Skriv ein full node-ID.": [
    "Ingen kjente mottakere ennå. Skriv en full node-ID.",
    "No known recipients yet. Enter a full node ID."
  ],
  "Kanal {0}": [
    "Kanal {0}",
    "Channel {0}"
  ],
  "Ingen primærkanal er tilgjengeleg. Vent på kanaloversikta og prøv igjen.": [
    "Ingen primærkanal er tilgjengelig. Vent på kanaloversikten og prøv igjen.",
    "No primary channel is available. Wait for the channel list and try again."
  ],
  "Vel ein annan mottakar enn lokal node.": [
    "Velg en annen mottaker enn lokal node.",
    "Select a recipient other than the local node."
  ],
  "Vel ein mottakar og ein aktiv kanal.": [
    "Velg en mottaker og en aktiv kanal.",
    "Select a recipient and an active channel."
  ],
  "Meldinga er for lang. Maksimal lengd er 237 UTF-8-byte.": [
    "Meldingen er for lang. Maksimal lengde er 237 UTF-8-byte.",
    "The message is too long. Maximum length is 237 UTF-8 bytes."
  ],
  "Ukjend sendestatus. Meldinga kan vere send. Kontroller status eller historikken før eit nytt forsøk.": [
    "Ukjent sendestatus. Meldingen kan være sendt. Kontroller status eller historikken før et nytt forsøk.",
    "Unknown send status. The message may have been sent. Check the status or history before retrying."
  ],
  "Sendestatus kunne ikkje lesast. Prøv å kontrollere status igjen.": [
    "Sendestatus kunne ikke leses. Prøv å kontrollere status igjen.",
    "Could not read the send status. Try checking again."
  ],
  "Sendinga er registrert. Sjå leveringsstatus i historikken.": [
    "Sendingen er registrert. Se leveringsstatus i historikken.",
    "The send has been recorded. See the delivery status in the history."
  ],
  "Sendinga blir framleis handsama. Vent litt og kontroller status igjen.": [
    "Sendingen behandles fortsatt. Vent litt og kontroller status igjen.",
    "The send is still being processed. Wait a moment and check again."
  ],
  "Sendestatus kunne ikkje stadfestast. Kontroller historikken før du frigjev skrivefeltet. Inga melding er send på nytt.": [
    "Sendestatus kunne ikke bekreftes. Kontroller historikken før du frigjør skrivefeltet. Ingen melding er sendt på nytt.",
    "Could not confirm the send status. Check the history before unlocking the composer. No message has been resent."
  ],
  "Har du kontrollert historikken? Meldinga kan allereie vere send. Frigje skrivefeltet utan å sende noko?": [
    "Har du kontrollert historikken? Meldingen kan allerede være sendt. Frigjøre skrivefeltet uten å sende noe?",
    "Have you checked the history? The message may already have been sent. Unlock the composer without sending anything?"
  ],
  "Skrivefeltet er frigjeve. Ingen ny melding er send.": [
    "Skrivefeltet er frigjort. Ingen ny melding er sendt.",
    "Composer unlocked. No new message has been sent."
  ],
  "Node-ID": [
    "Node-ID",
    "Node ID"
  ],
  "Namn": [
    "Navn",
    "Name"
  ],
  "Kortnamn": [
    "Kortnavn",
    "Short name"
  ],
  "Maskinvare": [
    "Maskinvare",
    "Hardware"
  ],
  "Rolle": [
    "Rolle",
    "Role"
  ],
  "Batteri (%)": [
    "Batteri (%)",
    "Battery (%)"
  ],
  "Spenning": [
    "Spenning",
    "Voltage"
  ],
  "Sist høyrd": [
    "Sist hørt",
    "Last heard"
  ],
  "Tid": [
    "Tid",
    "Time"
  ],
  "Motteken": [
    "Mottatt",
    "Received"
  ],
  "Breiddegrad": [
    "Breddegrad",
    "Latitude"
  ],
  "Lengdegrad": [
    "Lengdegrad",
    "Longitude"
  ],
  "Høgd": [
    "Høyde",
    "Altitude"
  ],
  "Transport": [
    "Transport",
    "Transport"
  ],
  "Hopp": [
    "Hopp",
    "Hops"
  ],
  "Status": [
    "Status",
    "Status"
  ],
  "Handling": [
    "Handling",
    "Action"
  ],
  "Type": [
    "Type",
    "Type"
  ],
  "Lokal node": [
    "Lokal node",
    "Local node"
  ],
  "Oppdatert": [
    "Oppdatert",
    "Updated"
  ],
  "Eining": [
    "Enhet",
    "Device"
  ],
  "Miljø": [
    "Miljø",
    "Environment"
  ],
  "Luftkvalitet": [
    "Luftkvalitet",
    "Air quality"
  ],
  "Straum": [
    "Strøm",
    "Power"
  ],
  "Lokal statistikk": [
    "Lokal statistikk",
    "Local statistics"
  ],
  "Helse": [
    "Helse",
    "Health"
  ],
  "Vert": [
    "Vert",
    "Host"
  ],
  "Trafikkstyring": [
    "Trafikkstyring",
    "Traffic management"
  ],
  "Batteri": [
    "Batteri",
    "Battery"
  ],
  "Kanalbruk": [
    "Kanalbruk",
    "Channel utilization"
  ],
  "Sendetid": [
    "Sendetid",
    "Transmit airtime"
  ],
  "Oppetid": [
    "Oppetid",
    "Uptime"
  ],
  "Temperatur": [
    "Temperatur",
    "Temperature"
  ],
  "Luftfukt": [
    "Luftfuktighet",
    "Humidity"
  ],
  "Lufttrykk": [
    "Lufttrykk",
    "Air pressure"
  ],
  "Gassmotstand": [
    "Gassmotstand",
    "Gas resistance"
  ],
  "Luftkvalitetsindeks": [
    "Luftkvalitetsindeks",
    "Air quality index"
  ],
  "Avstand": [
    "Avstand",
    "Distance"
  ],
  "Lysstyrke": [
    "Lysstyrke",
    "Illuminance"
  ],
  "Kvitt lys": [
    "Hvitt lys",
    "White light"
  ],
  "Infraraudt lys": [
    "Infrarødt lys",
    "Infrared light"
  ],
  "UV-lys": [
    "UV-lys",
    "UV light"
  ],
  "Vindretning": [
    "Vindretning",
    "Wind direction"
  ],
  "Vindfart": [
    "Vindhastighet",
    "Wind speed"
  ],
  "Vindkast": [
    "Vindkast",
    "Wind gust"
  ],
  "Minste vindfart": [
    "Laveste vindhastighet",
    "Minimum wind speed"
  ],
  "Vekt": [
    "Vekt",
    "Weight"
  ],
  "Nedbør siste time": [
    "Nedbør siste time",
    "Rainfall in the last hour"
  ],
  "Nedbør siste døgn": [
    "Nedbør siste døgn",
    "Rainfall in the last 24 hours"
  ],
  "Jordfukt": [
    "Jordfuktighet",
    "Soil moisture"
  ],
  "Jordtemperatur": [
    "Jordtemperatur",
    "Soil temperature"
  ],
  "Ekstern temperatur": [
    "Ekstern temperatur",
    "External temperature"
  ],
  "Ekstern straum": [
    "Ekstern strøm",
    "External power"
  ],
  "{0} t ": [
    "{0} t ",
    "{0} h "
  ],
  "Vis tekniske detaljar": [
    "Vis tekniske detaljer",
    "Show technical details"
  ],
  "Nodeinfo er sendt. Ventar på svar frå noden …": [
    "Nodeinfo er sendt. Venter på svar fra noden …",
    "Node information sent. Waiting for a response …"
  ],
  "Utvekslinga fekk ikkje svar.": [
    "Utvekslingen fikk ikke svar.",
    "The exchange received no response."
  ],
  "Nodeinfo motteken. ": [
    "Nodeinfo mottatt. ",
    "Node information received. "
  ],
  "Noden sende ein offentleg nøkkel.": [
    "Noden sendte en offentlig nøkkel.",
    "The node sent a public key."
  ],
  "Noden sende ikkje ein offentleg nøkkel.": [
    "Noden sendte ikke en offentlig nøkkel.",
    "The node did not send a public key."
  ],
  " Vår offentlege nøkkel blei send med førespurnaden.": [
    " Vår offentlige nøkkel ble sendt med forespørselen.",
    " Our public key was included in the request."
  ],
  " Gatewayen hadde ingen offentleg nøkkel å sende.": [
    " Gatewayen hadde ingen offentlig nøkkel å sende.",
    " The gateway had no public key to send."
  ],
  "Identitet": [
    "Identitet",
    "Identity"
  ],
  "Langt namn": [
    "Langt navn",
    "Long name"
  ],
  "Samband": [
    "Forbindelse",
    "Connection"
  ],
  "Lokal gateway": [
    "Lokal gateway",
    "Local gateway"
  ],
  "Nodeinfo og offentleg nøkkel": [
    "Nodeinfo og offentlig nøkkel",
    "Node information and public key"
  ],
  "Bruk «Utveksle nodeinfo» for å sende vår nodeinformasjon og be om den andre noden sin informasjon og offentlege nøkkel.": [
    "Bruk «Utveksle nodeinfo» for å sende vår nodeinformasjon og be om den andre nodens informasjon og offentlige nøkkel.",
    "Use “Exchange node info” to send our node information and request the other node’s information and public key."
  ],
  "Siste telemetri": [
    "Siste telemetri",
    "Latest telemetry"
  ],
  "Lagra historikk": [
    "Lagret historikk",
    "Saved history"
  ],
  "Telemetrirapportar": [
    "Telemetrirapporter",
    "Telemetry reports"
  ],
  "Posisjonsrapportar": [
    "Posisjonsrapporter",
    "Position reports"
  ],
  "Traceroute": [
    "Traceroute",
    "Traceroute"
  ],
  "Siste posisjon": [
    "Siste posisjon",
    "Latest position"
  ],
  "Tidspunkt": [
    "Tidspunkt",
    "Timestamp"
  ],
  "Høgd (moh.)": [
    "Høyde (moh.)",
    "Altitude (m above sea level)"
  ],
  "Historikktal": [
    "Historikktall",
    "History counts"
  ],
  "Ingen lagra telemetrirapportar enno.": [
    "Ingen lagrede telemetrirapporter ennå.",
    "No saved telemetry reports yet."
  ],
  "{0} telemetrirapportar · grupperte etter måletype · nyaste måling først{1}.": [
    "{0} telemetrirapporter · gruppert etter måletype · nyeste måling først{1}.",
    "{0} telemetry reports · grouped by measurement type · newest measurement first{1}."
  ],
  " · viser dei siste 100 mottekne rapportane": [
    " · viser de siste 100 mottatte rapportene",
    " · showing the last 100 received reports"
  ],
  "{0}, rull for fleire kolonnar": [
    "{0}, rull for flere kolonner",
    "{0}, scroll for more columns"
  ],
  "Detaljar": [
    "Detaljer",
    "Details"
  ],
  "Ikkje oppgitt": [
    "Ikke oppgitt",
    "Not provided"
  ],
  "{0} hopp": [
    "{0} hopp",
    "{0} hops"
  ],
  "Denne ruta blei ikkje rapportert.": [
    "Denne ruten ble ikke rapportert.",
    "This route was not reported."
  ],
  "SNR ikkje oppgitt": [
    "SNR ikke oppgitt",
    "SNR not provided"
  ],
  "Start": [
    "Start",
    "Start"
  ],
  "Mål": [
    "Mål",
    "Destination"
  ],
  "Mellomnode": [
    "Mellomnode",
    "Intermediate node"
  ],
  "Lågaste oppgitte SNR": [
    "Laveste oppgitte SNR",
    "Lowest reported SNR"
  ],
  "Hopp med SNR": [
    "Hopp med SNR",
    "Hops with SNR"
  ],
  "{0} av {1}": [
    "{0} av {1}",
    "{0} of {1}"
  ],
  "Ingen lagra traceroute-forsøk enno.": [
    "Ingen lagrede traceroute-forsøk ennå.",
    "No saved traceroute attempts yet."
  ],
  "Traceroute til {0}": [
    "Traceroute til {0}",
    "Traceroute to {0}"
  ],
  "Svartid": [
    "Svartid",
    "Response time"
  ],
  "Hopp fram / retur": [
    "Hopp frem / retur",
    "Forward / return hops"
  ],
  "Ventar på svar frå noden. Resultatet blir lagra i historikken.": [
    "Venter på svar fra noden. Resultatet lagres i historikken.",
    "Waiting for the node to respond. The result will be saved in the history."
  ],
  "Framrute": [
    "Fremrute",
    "Forward route"
  ],
  "Returrute": [
    "Returrute",
    "Return route"
  ],
  "Mottakar": [
    "Mottaker",
    "Recipient"
  ],
  "Starta": [
    "Startet",
    "Started"
  ],
  "Avslutta": [
    "Avsluttet",
    "Finished"
  ],
  "Pakke-ID": [
    "Pakke-ID",
    "Packet ID"
  ],
  "Førespurnads-ID": [
    "Forespørsels-ID",
    "Request ID"
  ],
  "SNR viser mottaket frå førre node på den aktuelle ruta. Manglande målingar blir ikkje estimerte. Nodenamn blir henta frå den kjende nodelista.": [
    "SNR viser mottaket fra forrige node på den aktuelle ruten. Manglende målinger blir ikke estimert. Nodenavn hentes fra den kjente nodelisten.",
    "SNR describes reception from the previous node along the route. Missing measurements are not estimated. Node names come from the known node list."
  ],
  "Ingen lagra posisjonsrapportar enno.": [
    "Ingen lagrede posisjonsrapporter ennå.",
    "No saved position reports yet."
  ],
  "1 posisjonsrapport": [
    "1 posisjonsrapport",
    "1 position report"
  ],
  "{0} posisjonsrapportar": [
    "{0} posisjonsrapporter",
    "{0} position reports"
  ],
  "{0} · nyaste først{1}.": [
    "{0} · nyeste først{1}.",
    "{0} · newest first{1}."
  ],
  "Posisjonshistorikk, rull for fleire rapportar og kolonnar": [
    "Posisjonshistorikk, rull for flere rapporter og kolonner",
    "Position history, scroll for more reports and columns"
  ],
  "Posisjonshistorikk": [
    "Posisjonshistorikk",
    "Position history"
  ],
  "Satellittar": [
    "Satellitter",
    "Satellites"
  ],
  "Kart": [
    "Kart",
    "Map"
  ],
  "Google Maps ↗": [
    "Google Maps ↗",
    "Google Maps ↗"
  ],
  "Opne {0}, {1} i Google Maps (ny fane)": [
    "Åpne {0}, {1} i Google Maps (ny fane)",
    "Open {0}, {1} in Google Maps (new tab)"
  ],
  "Observasjons-ID": [
    "Observasjons-ID",
    "Observation ID"
  ],
  "Duplikatnøkkel": [
    "Duplikatnøkkel",
    "Duplicate key"
  ],
  "Posisjonstid": [
    "Posisjonstid",
    "Position time"
  ],
  "Høgd over havet (m)": [
    "Høyde over havet (m)",
    "Altitude above sea level (m)"
  ],
  "Høgd over ellipsoiden (m)": [
    "Høyde over ellipsoiden (m)",
    "Height above ellipsoid (m)"
  ],
  "Geoideseparasjon (m)": [
    "Geoideseparasjon (m)",
    "Geoidal separation (m)"
  ],
  "GPS-avvik (mm)": [
    "GPS-avvik (mm)",
    "GPS error (mm)"
  ],
  "Fart over bakken (m/s)": [
    "Hastighet over bakken (m/s)",
    "Ground speed (m/s)"
  ],
  "Kurs (°)": [
    "Kurs (°)",
    "Heading (°)"
  ],
  "GPS-fikskvalitet": [
    "GPS-fikskvalitet",
    "GPS fix quality"
  ],
  "GPS-fikstype": [
    "GPS-fikstype",
    "GPS fix type"
  ],
  "Posisjonskjelde": [
    "Posisjonskilde",
    "Location source"
  ],
  "Høgdekjelde": [
    "Høydekilde",
    "Altitude source"
  ],
  "Presisjonsbitar": [
    "Presisjonsbiter",
    "Precision bits"
  ],
  "Posisjonsdata": [
    "Posisjonsdata",
    "Position data"
  ],
  "Hoppgrense": [
    "Hoppgrense",
    "Hop limit"
  ],
  "Opphavleg hoppgrense": [
    "Opprinnelig hoppgrense",
    "Original hop limit"
  ],
  "Gateway-ID": [
    "Gateway-ID",
    "Gateway ID"
  ],
  "Gatewaytransport": [
    "Gatewaytransport",
    "Gateway transport"
  ],
  "Vis": [
    "Vis",
    "Show"
  ],
  "Vis detaljar for rapport {0}": [
    "Vis detaljer for rapport {0}",
    "Show details for report {0}"
  ],
  "Skjul": [
    "Skjul",
    "Hide"
  ],
  "{0} detaljar for rapport {1}": [
    "{0} detaljer for rapport {1}",
    "{0} details for report {1}"
  ],
  "Hentar nodeinformasjon …": [
    "Henter nodeinformasjon …",
    "Loading node information …"
  ],
  "Kunne ikkje hente nodeinformasjonen. Vel fana igjen for å prøve på nytt.": [
    "Kunne ikke hente nodeinformasjonen. Velg fanen igjen for å prøve på nytt.",
    "Could not load node information. Select the tab again to retry."
  ],
  "Nodeinfo-utveksling krev ein oppdatert MeshPi-daemon. ": [
    "Nodeinfo-utveksling krever en oppdatert MeshPi-daemon. ",
    "Exchanging node information requires an updated MeshPi daemon. "
  ],
  "Kontroller sletting": [
    "Kontroller sletting",
    "Check deletion"
  ],
  "Slettar …": [
    "Sletter …",
    "Deleting …"
  ],
  "Slett node": [
    "Slett node",
    "Delete node"
  ],
  "Den lokale gatewayen kan ikkje slettast.": [
    "Den lokale gatewayen kan ikke slettes.",
    "The local gateway cannot be deleted."
  ],
  "Svaret stadfestar ikkje den valde slettinga.": [
    "Svaret bekrefter ikke den valgte slettingen.",
    "The response does not confirm the selected deletion."
  ],
  "Slettinga er ikkje stadfesta. Kontroller nodelista før du prøver igjen.": [
    "Slettingen er ikke bekreftet. Kontroller nodelisten før du prøver igjen.",
    "Deletion is not confirmed. Check the node list before trying again."
  ],
  "Noden {0} er sletta frå nodelista. Historikken er bevart.": [
    "Noden {0} er slettet fra nodelisten. Historikken er bevart.",
    "Node {0} was deleted from the node list. History is preserved."
  ],
  "Noden kunne ikkje slettast.": [
    "Noden kunne ikke slettes.",
    "Could not delete the node."
  ],
  "Slettinga er ikkje stadfesta. Bruk «Kontroller sletting»; ingen ny sletting blir send. ": [
    "Slettingen er ikke bekreftet. Bruk «Kontroller sletting»; ingen ny sletting blir sendt. ",
    "Deletion is not confirmed. Use “Check deletion”; no new deletion will be sent. "
  ],
  "Statusen er ukjend etter sambandsbrotet.": [
    "Statusen er ukjent etter forbindelsesbruddet.",
    "The status is unknown after the connection was lost."
  ],
  "Slette {0} ({1}) frå nodelista til {2} ({3})?\n\nKontakten blir fjerna frå den lokale radioen og MeshPi-lista. Meldingshistorikk og andre lagra rapportar blir bevarte. Noden kan dukke opp att ved ny trafikk.": [
    "Slette {0} ({1}) fra nodelisten til {2} ({3})?\n\nKontakten fjernes fra den lokale radioen og MeshPi-listen. Meldingshistorikk og andre lagrede rapporter blir bevart. Noden kan dukke opp igjen ved ny trafikk.",
    "Delete {0} ({1}) from the node list of {2} ({3})?\n\nThe contact will be removed from the local radio and MeshPi list. Message history and other saved reports will be preserved. The node may reappear with new traffic."
  ],
  "Utveksle posisjonsdata med {0}? Dette kan dele lokal posisjon når kanalen tillèt det.": [
    "Utveksle posisjonsdata med {0}? Dette kan dele lokal posisjon når kanalen tillater det.",
    "Exchange position data with {0}? This may share the local position when the channel permits it."
  ],
  "Handling starta mot {0}. Resultatet blir lagra i historikken.": [
    "Handling startet mot {0}. Resultatet lagres i historikken.",
    "Action started for {0}. The result will be saved in the history."
  ],
  "Ingen lagra profilar.": [
    "Ingen lagrede profiler.",
    "No saved profiles."
  ],
  "Ingen nye einingar funne.": [
    "Ingen nye enheter funnet.",
    "No new devices found."
  ],
  "Sambandet er brote. Koplar til på nytt …": [
    "Forbindelsen er brutt. Kobler til på nytt …",
    "Connection lost. Reconnecting …"
  ],
  "Sanntid koplar til på nytt …": [
    "Sanntid kobler til på nytt …",
    "Reconnecting live updates …"
  ],
  "Gateway eller innlogging er endra. Lukk og opne innstillingane på nytt.": [
    "Gateway eller innlogging er endret. Lukk og åpne innstillingene på nytt.",
    "Gateway or sign-in changed. Close and reopen settings."
  ],
  "Vel eit heilt tal frå 1 til 36500 dagar, eller det eigne valet utan aldersgrense.": [
    "Velg et helt tall fra 1 til 36500 dager, eller valget uten aldersgrense.",
    "Select a whole number from 1 to 36500 days, or choose no age limit."
  ],
  "Gateway: {0} · {1}": [
    "Gateway: {0} · {1}",
    "Gateway: {0} · {1}"
  ],
  "Ingen aktiv gateway. Vel ei tilkopling først.": [
    "Ingen aktiv gateway. Velg en tilkobling først.",
    "No active gateway. Select a connection first."
  ],
  "Aldersgrensa gjeld meldingar, telemetri, posisjonar og nodehandlingar. Meldingsgrensa er {0}. Utan aldersgrense opphevar ikkje denne grensa. Lagra no: {1}.": [
    "Aldersgrensen gjelder meldinger, telemetri, posisjoner og nodehandlinger. Meldingsgrensen er {0}. Uten aldersgrense opphever ikke denne grensen. Lagret nå: {1}.",
    "The age limit applies to messages, telemetry, positions and node actions. The message limit is {0}. Choosing no age limit does not remove this limit. Currently saved: {1}."
  ],
  "Hentar innstillingar …": [
    "Henter innstillinger …",
    "Loading settings …"
  ],
  "Ukjent resultat. Handlinga blir ikkje send på nytt automatisk. Lukk og opne innstillingane for å kontrollere lagringsval og tal før du prøver igjen.": [
    "Ukjent resultat. Handlingen sendes ikke på nytt automatisk. Lukk og åpne innstillingene for å kontrollere lagringsvalg og tall før du prøver igjen.",
    "Unknown result. The action will not be retried automatically. Close and reopen settings to check storage preferences and counts before trying again."
  ],
  "utan aldersgrense": [
    "uten aldersgrense",
    "with no age limit"
  ],
  "i {0} dagar": [
    "i {0} dager",
    "for {0} days"
  ],
  "Lagre historikk {0} for {1} ({2})? {3}": [
    "Lagre historikk {0} for {1} ({2})? {3}",
    "Save history {0} for {1} ({2})? {3}"
  ],
  "Eksisterande og framtidig historikk eldre enn denne grensa blir sletta automatisk. Dette kan ikkje angrast.": [
    "Eksisterende og fremtidig historikk eldre enn denne grensen slettes automatisk. Dette kan ikke angres.",
    "Existing and future history older than this limit will be deleted automatically. This cannot be undone."
  ],
  "Meldingsgrensa gjeld framleis.": [
    "Meldingsgrensen gjelder fortsatt.",
    "The message limit still applies."
  ],
  "Lagringsvalet er lagra.": [
    "Lagringsvalget er lagret.",
    "Storage preference saved."
  ],
  "{0} ({1}): {2}. {3} Førehandsvisinga er gyldig i fem minutt.": [
    "{0} ({1}): {2}. {3} Forhåndsvisningen er gyldig i fem minutter.",
    "{0} ({1}): {2}. {3} The preview is valid for five minutes."
  ],
  "Eldre enn {0} dagar (før {1}).": [
    "Eldre enn {0} dager (før {1}).",
    "Older than {0} days (before {1})."
  ],
  "Alt i det valde omfanget, uansett alder.": [
    "Alt i det valgte omfanget, uansett alder.",
    "Everything in the selected scope, regardless of age."
  ],
  "Kontroller omfanget før du stadfestar sletting.": [
    "Kontroller omfanget før du bekrefter sletting.",
    "Check the scope before confirming deletion."
  ],
  "Førehandsvisinga er utgått. Vis kva som blir sletta på nytt.": [
    "Forhåndsvisningen er utløpt. Vis hva som slettes på nytt.",
    "The preview has expired. Preview the deletion again."
  ],
  "Slett {0} frå {1} ({2})? Dette kan ikkje angrast.": [
    "Slett {0} fra {1} ({2})? Dette kan ikke angres.",
    "Delete {0} from {1} ({2})? This cannot be undone."
  ],
  "Sletta: {0}.": [
    "Slettet: {0}.",
    "Deleted: {0}."
  ],
  "Kunne ikkje hente nøklane.": [
    "Kunne ikke hente nøklene.",
    "Could not load keys."
  ],
  "Innlogging og nøklar": [
    "Innlogging og nøkler",
    "Sign-in and keys"
  ],
  "Logg inn i MeshPi": [
    "Logg inn i MeshPi",
    "Sign in to MeshPi"
  ],
  "Opne innloggingsadressa: {0}": [
    "Åpne innloggingsadressen: {0}",
    "Open the sign-in address: {0}"
  ],
  "Passkeys er knytte til denne adressa. Du kan bruke Bitwarden, Apple Passord, Windows Hello eller ein tryggleiksnøkkel. Legg gjerne til ein ekstra nøkkel.": [
    "Passkeys er knyttet til denne adressen. Du kan bruke Bitwarden, Apple Passord, Windows Hello eller en sikkerhetsnøkkel. Legg gjerne til en ekstra nøkkel.",
    "Passkeys are linked to this address. You can use Bitwarden, Apple Passwords, Windows Hello or a security key. Consider adding a backup key."
  ],
  "Denne nettlesaren støttar ikkje passkeys. Bruk ein oppdatert nettlesar eller ein eingongskode.": [
    "Denne nettleseren støtter ikke passkeys. Bruk en oppdatert nettleser eller en engangskode.",
    "This browser does not support passkeys. Use an updated browser or a one-time code."
  ],
  "Passkeys krev den faste HTTPS-adressa. På localhost brukar du ein eingongskode frå terminalen på MeshPi-maskina.": [
    "Passkeys krever den faste HTTPS-adressen. På localhost bruker du en engangskode fra terminalen på MeshPi-maskinen.",
    "Passkeys require the fixed HTTPS address. On localhost, use a one-time code from the terminal on the MeshPi machine."
  ],
  "Logg inn for å halde fram. Utkastet ditt er bevart i denne fana.": [
    "Logg inn for å fortsette. Utkastet ditt er bevart i denne fanen.",
    "Sign in to continue. Your draft is preserved in this tab."
  ],
  "Innlogginga blei avbroten eller tok for lang tid. Du kan prøve igjen.": [
    "Innloggingen ble avbrutt eller tok for lang tid. Du kan prøve igjen.",
    "Sign-in was cancelled or timed out. You can try again."
  ],
  "Kunne ikkje fullføre innlogginga.": [
    "Kunne ikke fullføre innloggingen.",
    "Could not complete sign-in."
  ],
  "Innlogginga blei ikkje godkjend.": [
    "Innloggingen ble ikke godkjent.",
    "Sign-in was not approved."
  ],
  "Fjern": [
    "Fjern",
    "Remove"
  ],
  "Fjern nøkkelen {0}": [
    "Fjern nøkkelen {0}",
    "Remove key {0}"
  ],
  "Fjerne nøkkelen «{0}»? Alle nettlesarar blir logga ut. Du kan få ein ny eingongskode frå terminalen.": [
    "Fjerne nøkkelen «{0}»? Alle nettlesere blir logget ut. Du kan få en ny engangskode fra terminalen.",
    "Remove key “{0}”? All browsers will be signed out. You can get a new one-time code from the terminal."
  ],
  "Ingen passkeys er registrerte enno.": [
    "Ingen passkeys er registrert ennå.",
    "No passkeys have been registered yet."
  ],
  "Ingen nøkkel blei vald.": [
    "Ingen nøkkel ble valgt.",
    "No key was selected."
  ],
  "Ingen nøkkel blei oppretta.": [
    "Ingen nøkkel ble opprettet.",
    "No key was created."
  ],
  "Nøkkelen er lagra.": [
    "Nøkkelen er lagret.",
    "Key saved."
  ],
  "Byt til lys modus": [
    "Bytt til lys modus",
    "Switch to light mode"
  ],
  "Byt til mørk modus": [
    "Bytt til mørk modus",
    "Switch to dark mode"
  ],
  "Ventar på svar": [
    "Venter på svar",
    "Waiting for response"
  ],
  "Fullført": [
    "Fullført",
    "Completed"
  ],
  "Feila": [
    "Mislyktes",
    "Failed"
  ],
  "Ukjend status": [
    "Ukjent status",
    "Unknown status"
  ],
  "Logg inn for å sjå oppdateringar.": [
    "Logg inn for å se oppdateringer.",
    "Sign in to view updates."
  ],
  "Innstillingar": [
    "Innstillinger",
    "Settings"
  ],
  "Innstillingar · Ny versjon": [
    "Innstillinger · Ny versjon",
    "Settings · New version"
  ],
  "Ny versjon av MeshPi: {0}. Opne terminalen på {1} og køyr {2}. Sjå Innstillingar → Oppdateringar.": [
    "Ny versjon av MeshPi: {0}. Åpne terminalen på {1} og kjør {2}. Se Innstillinger → Oppdateringer.",
    "New MeshPi version: {0}. Open the terminal on {1} and run {2}. See Settings → Updates."
  ],
  "MeshPi-maskina": [
    "MeshPi-maskinen",
    "the MeshPi machine"
  ],
  "Oppdateringskontrollen er slått av på denne maskina.": [
    "Oppdateringskontrollen er slått av på denne maskinen.",
    "Update checks are disabled on this machine."
  ],
  "Kunne ikkje sjå etter ny versjon. Prøver igjen seinare.": [
    "Kunne ikke se etter ny versjon. Prøver igjen senere.",
    "Could not check for updates. Retrying later."
  ],
  "Nettlesaren er klar til å installere MeshPi.": [
    "Nettleseren er klar til å installere MeshPi.",
    "The browser is ready to install MeshPi."
  ],
  "Installasjonen blei avbroten. Du kan prøve igjen frå nettlesarmenyen.": [
    "Installasjonen ble avbrutt. Du kan prøve igjen fra nettlesermenyen.",
    "Installation was cancelled. You can try again from the browser menu."
  ],
  "Installasjonen kunne ikkje startast. Bruk rettleiinga for nettlesaren din.": [
    "Installasjonen kunne ikke startes. Bruk veiledningen for nettleseren din.",
    "Could not start installation. Follow the instructions for your browser."
  ],
  "Opne den private HTTPS-adressa for å installere appen på denne eininga.": [
    "Åpne den private HTTPS-adressen for å installere appen på denne enheten.",
    "Open the private HTTPS address to install the app on this device."
  ],
  "Appstøtta kunne ikkje aktiverast. Last sida på nytt, eller bruk nettlesarmenyen.": [
    "Appstøtten kunne ikke aktiveres. Last siden på nytt, eller bruk nettlesermenyen.",
    "Could not enable app support. Reload the page or use the browser menu."
  ],
  "Koplar til daemonen …": [
    "Kobler til daemonen …",
    "Connecting to the daemon …"
  ],
  "Nodar": [
    "Noder",
    "Nodes"
  ],
  "Samtalar": [
    "Samtaler",
    "Conversations"
  ],
  "Hentar samtalar …": [
    "Henter samtaler …",
    "Loading conversations …"
  ],
  "Arkiver": [
    "Arkiver",
    "Archive"
  ],
  "Nye meldingar ↓": [
    "Nye meldinger ↓",
    "New messages ↓"
  ],
  "Kontroller sendestatus": [
    "Kontroller sendestatus",
    "Check send status"
  ],
  "Eg har kontrollert historikken": [
    "Jeg har kontrollert historikken",
    "I have checked the history"
  ],
  "Melding": [
    "Melding",
    "Message"
  ],
  "Vel ei sendbar samtale.": [
    "Velg en samtale som kan sende.",
    "Select a conversation that allows sending."
  ],
  "0 / 237 byte": [
    "0 / 237 byte",
    "0 / 237 bytes"
  ],
  "Send melding": [
    "Send melding",
    "Send message"
  ],
  "Søk etter node": [
    "Søk etter node",
    "Search nodes"
  ],
  "Filtrer nodar etter transport": [
    "Filtrer noder etter transport",
    "Filter nodes by transport"
  ],
  "Alle": [
    "Alle",
    "All"
  ],
  "Via RF": [
    "Via RF",
    "Via RF"
  ],
  "Via MQTT": [
    "Via MQTT",
    "Via MQTT"
  ],
  "RF + MQTT": [
    "RF + MQTT",
    "RF + MQTT"
  ],
  "Sorter nodar": [
    "Sorter noder",
    "Sort nodes"
  ],
  "Alfabetisk": [
    "Alfabetisk",
    "Alphabetical"
  ],
  "Hentar nodar …": [
    "Henter noder …",
    "Loading nodes …"
  ],
  "Lukk ×": [
    "Lukk ×",
    "Close ×"
  ],
  "Logg inn med passkey": [
    "Logg inn med passkey",
    "Sign in with a passkey"
  ],
  "Eingongskode": [
    "Engangskode",
    "One-time code"
  ],
  "På MeshPi-maskina: køyr": [
    "På MeshPi-maskinen: kjør",
    "On the MeshPi machine, run"
  ],
  "i terminalen. Koden gir tilgang til denne installasjonen, varer i fem minutt og kan brukast éin gong.": [
    "i terminalen. Koden gir tilgang til denne installasjonen, varer i fem minutter og kan brukes én gang.",
    "in the terminal. The code grants access to this installation, expires in five minutes and can be used once."
  ],
  "Logg inn med kode": [
    "Logg inn med kode",
    "Sign in with a code"
  ],
  "Nøklane dine": [
    "Nøklene dine",
    "Your keys"
  ],
  "Namn på ny nøkkel": [
    "Navn på ny nøkkel",
    "New key name"
  ],
  "Legg til passkey": [
    "Legg til passkey",
    "Add passkey"
  ],
  "For å endre nøklar må du ha logga inn dei siste fem minutta. Logg ut og inn igjen om økta er eldre. Lokal eingongskode kan òg brukast dersom du mistar nøklane.": [
    "For å endre nøkler må du ha logget inn de siste fem minuttene. Logg ut og inn igjen hvis økten er eldre. Lokal engangskode kan også brukes hvis du mister nøklene.",
    "To manage keys, you must have signed in within the last five minutes. Sign out and back in if your session is older. You can also use a local one-time code if you lose your keys."
  ],
  "Logg ut": [
    "Logg ut",
    "Sign out"
  ],
  "NODEINFORMASJON": [
    "NODEINFORMASJON",
    "NODE INFORMATION"
  ],
  "Node": [
    "Node",
    "Node"
  ],
  "Oversikt": [
    "Oversikt",
    "Overview"
  ],
  "Telemetri": [
    "Telemetri",
    "Telemetry"
  ],
  "Posisjon": [
    "Posisjon",
    "Position"
  ],
  "Send DM": [
    "Send DM",
    "Send DM"
  ],
  "Utveksle nodeinfo": [
    "Utveksle nodeinfo",
    "Exchange node info"
  ],
  "Utveksle posisjon": [
    "Utveksle posisjon",
    "Exchange positions"
  ],
  "Start traceroute": [
    "Start traceroute",
    "Start traceroute"
  ],
  "Ny direkte melding": [
    "Ny direktemelding",
    "New direct message"
  ],
  "Vel ein node i lista, eller skriv ein full node-ID.": [
    "Velg en node i listen, eller skriv en full node-ID.",
    "Select a node from the list or enter a full node ID."
  ],
  "Opne samtale": [
    "Åpne samtale",
    "Open conversation"
  ],
  "Tilkoplingar": [
    "Tilkoblinger",
    "Connections"
  ],
  "Éin aktiv gateway. Byte gjeld også terminalklienten.": [
    "Én aktiv gateway. Byttet gjelder også terminalklienten.",
    "One active gateway. Changes also apply to the terminal client."
  ],
  "Finn TCP- og USB-einingar": [
    "Finn TCP- og USB-enheter",
    "Find TCP and USB devices"
  ],
  "Søk etter BLE": [
    "Søk etter BLE",
    "Scan for BLE"
  ],
  "Manuelt mål på vertsmaskina": [
    "Manuelt mål på vertsmaskinen",
    "Manual target on the host machine"
  ],
  "Kople til": [
    "Koble til",
    "Connect"
  ],
  "Installer MeshPi": [
    "Installer MeshPi",
    "Install MeshPi"
  ],
  "Få MeshPi som eigen app med ikon på heimskjermen eller skrivebordet.": [
    "Få MeshPi som egen app med ikon på startskjermen eller skrivebordet.",
    "Get MeshPi as an app with an icon on your home screen or desktop."
  ],
  "Android, Windows og Linux:": [
    "Android, Windows og Linux:",
    "Android, Windows and Linux:"
  ],
  "Bruk «Installer MeshPi» når knappen er tilgjengeleg, eller installasjonsvalet i nettlesarmenyen i Chrome eller Edge.": [
    "Bruk «Installer MeshPi» når knappen er tilgjengelig, eller installasjonsvalget i nettlesermenyen i Chrome eller Edge.",
    "Use “Install MeshPi” when the button is available, or the installation option in the Chrome or Edge browser menu."
  ],
  "iPhone og iPad:": [
    "iPhone og iPad:",
    "iPhone and iPad:"
  ],
  "Opne sida i Safari, trykk på delingsknappen og vel «Legg til på heimskjermen». Vel å opne som webapp dersom du får det valet.": [
    "Åpne siden i Safari, trykk på delingsknappen og velg «Legg til på Hjem-skjermen». Velg å åpne som webapp hvis du får det valget.",
    "Open the page in Safari, tap Share and select “Add to Home Screen”. Choose to open as a web app if offered."
  ],
  "Mac:": [
    "Mac:",
    "Mac:"
  ],
  "I Safari vel du «Arkiv» → «Legg til i Dock». Chrome og Edge har òg eit installasjonsval.": [
    "I Safari velger du «Arkiv» → «Legg til i Dock». Chrome og Edge har også et installasjonsvalg.",
    "In Safari, choose “File” → “Add to Dock”. Chrome and Edge also have an installation option."
  ],
  "Appen treng samband til MeshPi-tenaren og tilgang gjennom Tailscale. Meldingar og nodeinformasjon blir ikkje lagra for bruk utan nett.": [
    "Appen trenger forbindelse til MeshPi-serveren og tilgang gjennom Tailscale. Meldinger og nodeinformasjon lagres ikke for bruk uten nett.",
    "The app requires a connection to the MeshPi server and access through Tailscale. Messages and node information are not saved for offline use."
  ],
  "App og tilkoplingar": [
    "App og tilkoblinger",
    "App and connections"
  ],
  "Installer app": [
    "Installer app",
    "Install app"
  ],
  "Lukk varsel automatisk etter 10 sekund": [
    "Lukk varsler automatisk etter 10 sekunder",
    "Close notices automatically after 10 seconds"
  ],
  "Slå av om du vil la varsla stå til du lukkar dei sjølv. Valet blir lagra i denne nettlesaren.": [
    "Slå av hvis du vil la varslene stå til du lukker dem selv. Valget lagres i denne nettleseren.",
    "Turn this off to keep notices visible until you close them. This preference is saved in this browser."
  ],
  "Oppdateringar": [
    "Oppdateringer",
    "Updates"
  ],
  "Ser etter ny versjon …": [
    "Ser etter ny versjon …",
    "Checking for updates …"
  ],
  "Opne terminalen på maskina som køyrer MeshPi, og køyr:": [
    "Åpne terminalen på maskinen som kjører MeshPi, og kjør:",
    "Open the terminal on the machine running MeshPi, and run:"
  ],
  "Oppdateringa blir utført i terminalen. Last denne sida inn på nytt etterpå.": [
    "Oppdateringen utføres i terminalen. Last denne siden på nytt etterpå.",
    "The update runs in the terminal. Reload this page afterwards."
  ],
  "Hentar aktiv gateway …": [
    "Henter aktiv gateway …",
    "Loading the active gateway …"
  ],
  "Lagring av historikk": [
    "Lagring av historikk",
    "History storage"
  ],
  "Kvar gateway har sin eigen database. Desse vala gjeld den aktive gatewayen i både web- og terminalklienten. Nye gatewayar lagrar historikk i 30 dagar.": [
    "Hver gateway har sin egen database. Disse valgene gjelder den aktive gatewayen i både web- og terminalklienten. Nye gatewayer lagrer historikk i 30 dager.",
    "Each gateway has its own database. These preferences apply to the active gateway in both the web and terminal clients. New gateways save history for 30 days."
  ],
  "Eksisterande historikk har inga ny aldersgrense før du vel og stadfestar ei innstilling.": [
    "Eksisterende historikk får ingen ny aldersgrense før du velger og bekrefter en innstilling.",
    "No new age limit applies to existing history until you select and confirm a preference."
  ],
  "Ta vare på historikk": [
    "Ta vare på historikk",
    "Keep history"
  ],
  "Ei veke (7 dagar)": [
    "En uke (7 dager)",
    "One week (7 days)"
  ],
  "Ein månad (30 dagar)": [
    "En måned (30 dager)",
    "One month (30 days)"
  ],
  "Vel tal på dagar": [
    "Velg antall dager",
    "Choose number of days"
  ],
  "Utan aldersgrense": [
    "Uten aldersgrense",
    "No age limit"
  ],
  "Tal på dagar (1–36500)": [
    "Antall dager (1–36500)",
    "Number of days (1–36500)"
  ],
  "Lagre lagringsval": [
    "Lagre lagringsvalg",
    "Save storage preference"
  ],
  "Tøm lagra historikk": [
    "Tøm lagret historikk",
    "Clear saved history"
  ],
  "Sletting kan ikkje angrast. Nodeoversikta, profilar, innlogging og radioinnstillingar blir bevarte. Uavklarte utgåande meldingar og pågåande nodehandlingar blir òg bevarte.": [
    "Sletting kan ikke angres. Nodeoversikten, profiler, innlogging og radioinnstillinger blir bevart. Uavklarte utgående meldinger og pågående nodehandlinger blir også bevart.",
    "Deletion cannot be undone. The node list, profiles, sign-in and radio settings will be preserved. Unresolved outgoing messages and ongoing node actions will also be preserved."
  ],
  "Kva vil du rydde?": [
    "Hva vil du rydde?",
    "What would you like to clear?"
  ],
  "Berre meldingar": [
    "Bare meldinger",
    "Messages only"
  ],
  "All historikk: meldingar, telemetri, posisjonar og nodehandlingar": [
    "All historikk: meldinger, telemetri, posisjoner og nodehandlinger",
    "All history: messages, telemetry, positions and node actions"
  ],
  "Kva tidsrom vil du slette?": [
    "Hvilket tidsrom vil du slette?",
    "What time period would you like to delete?"
  ],
  "Eldre enn ei veke (7 dagar)": [
    "Eldre enn en uke (7 dager)",
    "Older than one week (7 days)"
  ],
  "Eldre enn ein månad (30 dagar)": [
    "Eldre enn en måned (30 dager)",
    "Older than one month (30 days)"
  ],
  "Eldre enn valt tal på dagar": [
    "Eldre enn valgt antall dager",
    "Older than the chosen number of days"
  ],
  "Alt, uansett alder": [
    "Alt, uansett alder",
    "Everything, regardless of age"
  ],
  "Vis kva som blir sletta": [
    "Vis hva som slettes",
    "Preview deletion"
  ],
  "Stadfest sletting": [
    "Bekreft sletting",
    "Confirm deletion"
  ],
  "Lukk varsel": [
    "Lukk varsel",
    "Dismiss notice"
  ],
  "Chat": [
    "Chat",
    "Chat"
  ],
  "Meldingshistorikk": [
    "Meldingshistorikk",
    "Message history"
  ],
  "Skriv ei melding …": [
    "Skriv en melding …",
    "Write a message …"
  ],
  "Søk etter node …": [
    "Søk etter node …",
    "Search nodes …"
  ],
  "Registrerte mottak via den aktive gatewayen. RF + MQTT tyder observert via begge.": [
    "Registrerte mottak via den aktive gatewayen. RF + MQTT betyr observert via begge.",
    "Reception recorded through the active gateway. RF + MQTT means observed through both."
  ],
  "Til dømes Windows-PC eller iPhone": [
    "For eksempel Windows-PC eller iPhone",
    "For example, Windows PC or iPhone"
  ],
  "Lukk nodeinformasjon": [
    "Lukk nodeinformasjon",
    "Close node information"
  ],
  "Nodeinformasjon": [
    "Nodeinformasjon",
    "Node information"
  ],
  "Søk etter namn eller node-ID": [
    "Søk etter navn eller node-ID",
    "Search by name or node ID"
  ],
  "Kjende mottakarar": [
    "Kjente mottakere",
    "Known recipients"
  ],
  "Vertsnamn, seriellsti eller ble://identifikator": [
    "Vertsnavn, seriellsti eller ble://identifikator",
    "Hostname, serial path or ble://identifier"
  ],
  "Lukk installasjonsrettleiinga": [
    "Lukk installasjonsveiledningen",
    "Close installation instructions"
  ],
  "Mørk modus": [
    "Mørk modus",
    "Dark mode"
  ],
  "nettopp": [
    "nettopp",
    "just now"
  ],
  "kanalrute": [
    "kanalrute",
    "channel route"
  ],
  "meldingar": [
    "meldinger",
    "messages"
  ],
  "telemetrirapportar": [
    "telemetrirapporter",
    "telemetry reports"
  ],
  "posisjonar": [
    "posisjoner",
    "positions"
  ],
  "nodehandlingar": [
    "nodehandlinger",
    "node actions"
  ],
  "MeshPi {0} er tilgjengeleg. Du køyrer {1}.": [
    "MeshPi {0} er tilgjengelig. Du kjører {1}.",
    "MeshPi {0} is available. You are running {1}."
  ],
  "MeshPi {0} – ingen nyare stabil versjon er tilgjengeleg.": [
    "MeshPi {0} – ingen nyere stabil versjon er tilgjengelig.",
    "MeshPi {0} – no newer stable version is available."
  ],
  "Språk": [
    "Språk",
    "Language"
  ],
  "Språkvalet blir lagra i denne nettlesaren.": [
    "Språkvalget lagres i denne nettleseren.",
    "Your language preference is saved in this browser."
  ],
  "tilkopla": [
    "tilkoblet",
    "connected"
  ],
  "koplar til": [
    "kobler til",
    "connecting"
  ],
  "fråkopla": [
    "frakoblet",
    "disconnected"
  ],
  "feila": [
    "mislyktes",
    "failed"
  ],
  "sendt": [
    "sendt",
    "sent"
  ],
  "levert": [
    "levert",
    "delivered"
  ],
  "motteken": [
    "mottatt",
    "received"
  ],
  "ventar": [
    "venter",
    "waiting"
  ],
  "kølagt": [
    "lagt i kø",
    "queued"
  ],
  "ukjend": [
    "ukjent",
    "unknown"
  ]
};
  const languages = ["nb", "nn", "en"];
  let language = "nn";
  let hasSavedLanguage = false;
  const initialText = [];
  const initialAttributes = [];
  const listeners = new Set();
  try {
    const saved = window.localStorage.getItem("meshpi-web-language");
    if (languages.includes(saved)) {language = saved;hasSavedLanguage = true;}
  } catch (_) { /* Keep the existing language when storage is restricted. */ }
  if (!hasSavedLanguage) {
    try {
      const requested = new URL(window.location.href).searchParams.get("language");
      if (languages.includes(requested)) language = requested;
    } catch (_) { /* Keep the default when the location is unavailable. */ }
  }
  function t(key, values = []) {
    const text = language === "nn" ? key : catalogs[key]?.[language === "nb" ? 0 : 1] ?? key;
    return String(text).replace(/\{(\d+)\}/g, (match, index) =>
      Number(index) < values.length ? String(values[Number(index)]) : match);
  }
  function locale() {return {nb:"nb-NO",nn:"nn-NO",en:"en-GB"}[language];}
  function apply() {
    document.documentElement.lang = language;
    // Preserve the choice in offline navigation after a service-worker restart.
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("language",language);
      window.history.replaceState(window.history.state,"",url);
    } catch (_) { /* Some embedded browsers restrict history updates. */ }
    for (const item of initialText) {
      // Do not overwrite text replaced by the application after initial parsing.
      if (item.node.isConnected && item.node.nodeValue === item.last) {
        item.last = item.prefix + t(item.source) + item.suffix;
        item.node.nodeValue = item.last;
      }
    }
    for (const item of initialAttributes) {
      if (item.node.isConnected && item.node.getAttribute(item.attribute) === item.last) {
        item.last = t(item.source);
        item.node.setAttribute(item.attribute, item.last);
      }
    }
    for (const picker of document.querySelectorAll("[data-language-picker]")) picker.value = language;
    const manifest = document.querySelector('link[rel="manifest"]');
    if (manifest) manifest.href = `${document.documentElement.dataset.basePath}/manifest.webmanifest?language=${language}`;
  }
  function setLanguage(value) {
    if (!languages.includes(value)) return false;
    language = value;
    try {window.localStorage.setItem("meshpi-web-language", language);}
    catch (_) { /* The language still changes for this page. */ }
    apply();
    for (const listener of listeners) listener();
    return true;
  }
  function capture() {
    const walker = document.createTreeWalker(document.body, 4);
    let node;
    while ((node = walker.nextNode())) {
      if (["SCRIPT", "STYLE", "CODE"].includes(node.parentElement?.tagName)) continue;
      const source = node.nodeValue.trim();
      if (!(source in catalogs)) continue;
      initialText.push({node,source,prefix:node.nodeValue.match(/^\s*/)[0],
        suffix:node.nodeValue.match(/\s*$/)[0],last:node.nodeValue});
    }
    for (const element of document.querySelectorAll("[aria-label], [title], [placeholder]")) {
      for (const attribute of ["aria-label", "title", "placeholder"]) {
        const source = element.getAttribute(attribute);
        if (source in catalogs) initialAttributes.push({node:element,attribute,source,last:source});
      }
    }
    for (const picker of document.querySelectorAll("[data-language-picker]")) {
      picker.addEventListener("change", () => setLanguage(picker.value));
    }
    apply();
  }
  // This script is deferred before the client, so capture never visits received user data.
  capture();
  return {t,locale,setLanguage,getLanguage:()=>language,onChange:fn=>listeners.add(fn)};
})();
