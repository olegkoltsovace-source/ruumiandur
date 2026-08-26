# Arvuti seisundi jälgija — üks fail

Legendi taust: "Ruumiandur, mis oskab ise rääkida" (Elisa kodutöö).
Riistvara asemel loeb see arvuti enda päris andureid (protsessor, mälu,
aku) — see on ülesandes otseselt lubatud valik. Kogu programm — server,
veebivaade ja AI-kokkuvõte — on ühes failis, `app.py`, et käivitamine
oleks nii lihtne kui võimalik.

## Käivitamine
```bash
pip install -r requirements.txt
python app.py
```
See on kõik. Brauser avaneb ise aadressil `http://localhost:8000/`.
Terminaliaken, milles see töötab, ONGI server — ära seda sulge, kuni
prototüüpi kasutad.

Ilma `ANTHROPIC_API_KEY` keskkonnamuutujata kasutab kokkuvõte
deterministlikku eestikeelset malli. Kui tahad, et kokkuvõtte kirjutaks
Claude:
```bash
export ANTHROPIC_API_KEY=sk-ant-...     # Windows: set ANTHROPIC_API_KEY=sk-ant-...
python app.py
```

**Miks ainult üks fail?** Varasem versioon oli mitmes failis (server,
veebivaade eraldi HTML-failina, mitu käivitusskripti) ja tekitas
korduvalt segadust — kasutaja avas kogemata HTML-faili otse brauseris
(fail:// kaudu), mille taga polnud töötavat serverit. Üks fail tähendab,
et pole eksitavat "vale faili", mida avada.

## Kuidas see töötab
`python app.py` käivitab veebiserveri JA hakkab kohe, taustal, iga 5
sekundi järel lugema sinu arvuti `psutil` kaudu: protsessori koormus (%),
mälu kasutus (%), aku laetus (% ja kas laeb). Andmed salvestuvad kohalikku
`data.db` faili (tekib automaatselt sama kausta) ja on kohe näha
töölaual ning REST API kaudu (`/api/stats/latest`, `/api/stats`,
`/api/summary`). Lase paar minutit joosta enne "Genereeri kokkuvõte"
vajutamist — rohkem andmeid, sisukam kokkuvõte.

**Mida see EI loe:** helisid, kaamerapilti, faile, brauseri ajalugu,
kasutajanime ega mistahes muud isikustatavat infot. Ainult kolm protsenti.

## Testitud
Käivitasin korduvalt, sh arvutil ilma akuta — `battery_pct` jääb siis
lihtsalt `null`-iks, ilma vigadeta. Kontrollisin, et väljaspool vahemikku
olevad väärtused (0-100% väline) märgitakse kehtetuks ega mõjuta
kokkuvõtet. `/api/summary` andis korrektse eestikeelse teksti nii
malli- kui LLM-põhiselt.

**Kuidas andmed "vaikivad":** kui taustalugemine katkeb, logitakse viga
konsooli, aga lõim ei sure — proovib järgmisel intervallil uuesti. Kui
viimasest kirjest on üle 30s, näitab töölaud "andmed vaikivad".

**Piir "ebatavalise" ja normaalse vahel:** otsustab kood (fikseeritud
läved: CPU >85%, mälu >90%, aku <15% kui ei lae), mitte AI. AI saab
ainult juba tuvastatud sündmused ja agregeeritud statistika, mitte
toorandmeid — tema roll on sõnastus, mitte otsustamine.

## Piirangud ja riskid
- SQLite üks fail — 1000 kasutaja korral vajaks Postgresi ja eelarvutatud
  agregaate.
- CPU/mälu/aku on energiatarbimise proksid, mitte kalibreeritud
  vatimõõtmine (see vajaks OS-spetsiifilisi privilegeeritud liideseid).
- Autentimist ega rate-limitit pole (ülesandes otseselt välistatud).
- Ei ole tõeline "double-click ja käivitub ilma Pythonita" programm —
  Python ja üks `pip install` on siiski vajalikud. Päris .exe/.app
  nõuaks PyInstaller-tüüpi pakkimist otse sihtplatvormil (ei saa
  Linuxist Windowsi .exe-d ehitada) — järgmine samm, kui see on oluline.

## Järgmine samm
PyInstaller-põhine .exe/.app pakend, et Python poleks üldse vaja; päris
riistvaraandur MQTT kaudu; mitme masina jälgimine ühe töölaua peal.

## Mida jätsin välja ja miks
- Testid, CI, konteinerid, autentimine — ülesandes otseselt välistatud.
- Kalibreeritud vatimõõtmine — vt "Piirangud".
- Eraldi failideks jaotatud arhitektuur — konsolideerisin teadlikult
  üheks failiks kasutuslihtsuse kasuks, kuigi see teeb koodi pikemaks
  ühes kohas.

## AI tööriistade kasutus
Ehitatud koos Claude'iga (Sonnet 5, chat-liides). Käik läbi mitu
iteratsiooni: algversioon simuleeris mitut tuba mitme skriptiga, siis
lisati brauseripõhine mikrofoniandur (loobuti privaatsuse pärast), siis
sülearvuti aku/CPU eraldi skriptina (`local_sensor.py` + `simulator.py`
+ FastAPI projekt kausta struktuuriga). See osutus ikka liiga
keeruliseks käivitada tavakasutajale — korduv viga oli, et avati
`index.html` fail otse brauseris (`file://`), mitte läbi töötava
serveri, mis andis "Failed to fetch". Lõplik lahendus: kõik üheks
failiks, server avab brauseri ise, ei ole eraldi faile, mida valesti
avada.
