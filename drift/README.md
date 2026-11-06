# Morgenopdatering

Michaels boligblik kører i skyen. Jacobs computer og browser behøver ikke være tændt.

cron-job.org starter `.github/workflows/morgen.yml` med `workflow_dispatch` hver dag kl. **06.17 Europe/Copenhagen** og derefter hver time til kl. 21.17. Crontab: `17 6-21 * * *`. Tidszonen følger dansk sommer- og vintertid. Jobbet findes på <https://console.cron-job.org/jobs/8805172> (login kræves).

POST-body i normal drift:

```json
{"ref":"main","inputs":{"automatic":true,"force_refresh":false}}
```

GitHubs egen UTC-plan er bevaret som ekstra startmulighed. Den gav ingen observerede `schedule`-kørsler ved fejlsøgningen 6. november 2026. Fejlen var før runnerstart; GitHubs interne årsag kunne ikke fastslås. Den eksterne timer undgår afhængigheden af GitHubs tidsstyrede start.

`scripts/morning.py` kontrollerer den **offentlige** boligoversigt. Alle ni forventede kilder skal have status `ok`, korrekt søgeområde og tidsstempel fra i dag efter kl. 06 dansk tid. Ellers hentes data igen. Samtidige starter sættes i kø. Når dagens data er friske, springes både hentning og udgivelse over.

Efter udgivelse hentes den offentlige JSON igen og sammenlignes med kørselskvitteringen. Der er fire forsøg for at håndtere kortvarig CDN-forsinkelse. Først derefter gemmes `seneste.json` med kildestatus, datatidspunkt og GitHub-kørselslink. En forsinket kilde giver en fejlet automatisk kørsel, så næste planlagte start forsøger igen. Sidens eksisterende advarsel viser for gamle morgendata.

## Afprøvet automatisk 6. november 2026

Begge starter nedenfor kom fra timerens tidsplan. Ingen manuel testknap eller manuel workflow-start blev brugt.

| Planlagt dansk tid | Timerens faktiske start | GitHub-kørsel | Resultat |
| --- | --- | --- | --- |
| 10.55.00 | 10.55.16 | [42198662940](https://github.com/jacobagger-lab/michaels-boligblik/actions/runs/42198662940) | Tvungen kontrolhentning, udgivelse og offentlig efterkontrol lykkedes. |
| 10.58.00 | 10.58.10 | [42199021119](https://github.com/jacobagger-lab/michaels-boligblik/actions/runs/42199021119) | Normal drift: alle kilder friske; hentning og udgivelse korrekt sprunget over. |

Begge timerkald gav HTTP 200 med det tilsvarende GitHub-kørsels-ID i svaret. Første kørsel gav 251 unikke boliger inden for 12 km og alle ni kilder `ok`. `firstSeen` og `isBaseline` var uændret for alle 252 eksisterende annoncer. 11 Python-tests bestod. Testtidspunkterne er efterfølgende erstattet af den normale morgenplan.

## Adgang og fornyelse

Timerens fine-grained GitHub-token har Actions read/write til **kun** `jacobagger-lab/michaels-boligblik` og den obligatoriske Metadata read-adgang. Tokenværdien skal aldrig gemmes i repository, log eller dokumentation.

Jacob valgte 90 dage: adgangsnøglen udløber **4. februar 2027 kl. 10.46.30 dansk tid**. Forny den inden da i GitHubs tokenindstillinger, og opdatér Authorization-headeren i cron-job.org. Den gamle nøgle kan derefter tilbagekaldes. Kør igen en rigtig planlagt test og sammenhold timerhistorik, GitHub-kørsel og offentlig datafil; et HTTP 200 fra timeren beviser kun, at GitHub accepterede starten.
