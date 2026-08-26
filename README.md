# Arvuti seisundi jälgija

Elisa kodutöö "Ruumiandur, mis oskab ise rääkida" jaoks. Riistvara asemel
loeb see arvuti enda andureid — mida ise parasjagu käes on.

## Käivitamine

pip install -r requirements.txt

python app.py

## Mis see teeb
Loeb iga 5 sekundi järel arvuti CPU koormust, mälu kasutust ja aku
laetust, salvestab need ja näitab töölaual.

Ei loe helisid, kaamerapilti ega muud isikustatavat — ainult kolm arvu.

## AI kasutus
Ehitatud koos Claude'iga, mitme iteratsiooni kaudu — algul liiga
keeruline (mitu faili, mitu tuba), lihtsustatud üheks failiks
kasutuslihtsuse pärast.
