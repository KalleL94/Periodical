# app/routes/changelog.py
"""
Changelog / version history page.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth.auth import get_current_user_optional
from app.database.database import get_db
from app.routes.shared import render

router = APIRouter()

VERSIONS = [
    {
        "version": "0.28.1",
        "date": "2026-07-11",
        "entries": [
            {
                "type": "fix",
                "sv": "Byter två anställda rotationsplats med varandra syns bytet i schemat först från och med bytesdatumet. Deras personliga års- och månadssidor visar hela året över bytet, med rätt pass, lön och frånvaro för tiden på varje plats",
                "en": "When two employees trade rotation positions, the change only appears in the schedule from the effective date. Their personal year and month pages show the whole year across the change, with the correct shifts, wage and absences for the time at each position",
            },
            {
                "type": "fix",
                "sv": "Pass efter en anställds sista arbetsdag visas nu som OFF i schemat i stället för att ligga kvar som vanliga arbetspass, även när en efterträdare börjar först senare. Personen räknas inte längre in i månader efter att anställningen tagit slut",
                "en": "Shifts after an employee's last working day now show as OFF in the schedule instead of remaining as normal work shifts, including when a successor starts later. The person is no longer counted in months after their employment ended",
            },
            {
                "type": "fix",
                "sv": "Vid personbyte visas nu båda personerna i lagvyerna: varsin kolumn i månads- och årsvyn och varsin rad i veckovyn. I årsvyn döljs en avgången persons kolumn efter sista arbetsdagen och visas igen med Visa passerade dagar. En position utan innehavare märks som Vakant i stället för att visa fel namn",
                "en": "When a person changes, both persons now appear in the team views: separate columns in the month and year views and separate rows in the week view. In the year view a departed person's column is hidden after their last working day and revealed with Show past days. A position without a holder is labeled Vacant instead of showing the wrong name",
            },
            {
                "type": "fix",
                "sv": "Personliga scheman öppnas nu per person i stället för per rotationsplats, så länkar visar rätt person även efter ett byte. Års- och månadssummeringar räknas bara på personens egen anställningstid och egen lön, även när ett byte sker mitt i en månad",
                "en": "Personal schedules now open per person instead of per rotation position, so links show the right person even after a change. Year and month summaries are calculated only on the person's own employment period and own wage, including when a change happens mid-month",
            },
        ],
    },
    {
        "version": "0.28.0",
        "date": "2026-07-05",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Du kan nu rätta en felskriven lön eller ersättningssats direkt i historiken på din profil, utan att ta bort perioden och lägga in den på nytt. Tryck Redigera på raden, ändra beloppet och spara, så behålls periodens datum. Administratörer kan göra samma sak för andra användare",
                "en": "You can now correct a mistyped wage or compensation rate directly in the history on your profile, without deleting the period and re-entering it. Press Edit on the row, change the amount and save, and the period's dates are kept. Administrators can do the same for other users",
            },
        ],
    },
    {
        "version": "0.27.1",
        "date": "2026-07-04",
        "entries": [
            {
                "type": "fix",
                "sv": "Månads- och årsvyn (alla): schemat går nu att läsa på mobilen. Veckodag- och datumkolumnerna fryses medan personernas kolumner rullar under, och raderna är kompaktare så fler personer syns innan du behöver scrolla i sidled",
                "en": "Month and year view (all): the schedule is now readable on mobile. The weekday and date columns freeze while the people columns scroll underneath, and rows are more compact so more people fit before you need to scroll sideways",
            },
            {
                "type": "fix",
                "sv": "Veckovyn (alla) behåller sin scrollbara tabell med fryst namnkolumn på mobilen i stället för att kollapsa till en oläslig stapel. Din egen vecka, månad och årssammanställning visas nu som riktiga listor och tabeller i stället för smala kort med tomt utrymme",
                "en": "Team week view keeps its scrollable table with a frozen name column on mobile instead of collapsing into an unreadable stack. Your own week, month and year summary now render as proper lists and tables instead of narrow cards with empty space",
            },
            {
                "type": "fix",
                "sv": "Dagvyn på mobilen: skiftbytes- och övertidsformulären, tabellerna (vem jobbar, OB-timmar, OB-lön) och dagnavigeringen är nu ryddiga i stället för att klippas eller radbrytas fult. Startsidans avläsning för nästa pass och nettolön får också mer plats på smala skärmar",
                "en": "Day view on mobile: the shift-swap and overtime forms, the tables (who is working, OB hours, OB pay) and the day navigation are now tidy instead of clipping or wrapping raggedly. The dashboard's next-shift and net-pay readout also gets more room on narrow screens",
            },
        ],
    },
    {
        "version": "0.27.0",
        "date": "2026-06-27",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Översikten inleds nu med en svars-rad högst upp: ditt nästa pass och månadens nettolön som stora avläsningar, så du ser direkt när du jobbar och vad du tjänar. Lönejämförelsen mot förra månaden ligger nu ihop med nettolönen",
                "en": "The dashboard now opens with an answer band at the top: your next shift and this month's net pay as large readouts, so you can see at a glance when you work and what you earn. The pay comparison against last month now sits together with the net pay",
            },
            {
                "type": "förbättring",
                "sv": "Ny visuell design i hela appen: en lugnare, mörkare palett med dämpad teal-accent och egna färger per roll, nya typsnitt (rubriker i Bricolage, brödtext i Inter, siffror i IBM Plex Mono) och scheman som behandlas som en driftstavla med fasta rubriker och en markering för dagens datum. Typsnitten laddas nu lokalt i stället för från Google Fonts",
                "en": "New visual design across the app: a calmer, darker palette with a muted teal accent and dedicated colours per role, new typefaces (Bricolage for headings, Inter for body text, IBM Plex Mono for numbers) and schedules treated as an operations board with sticky headers and a marker for today's date. Fonts are now loaded locally instead of from Google Fonts",
            },
        ],
    },
    {
        "version": "0.26.2",
        "date": "2026-06-24",
        "entries": [
            {
                "type": "fix",
                "sv": "Antal pass i månads-, års- och statistikvyn räknar nu bara faktiskt arbetade pass (dag/kväll/natt och övertid). Beredskap, semester, sjuk- och frånvarodagar räknas inte längre som pass, så siffran kan vara lägre än tidigare",
                "en": "The shift count in the month, year and statistics views now counts only actually worked shifts (day/evening/night and overtime). On-call, vacation, sick and absence days no longer count as shifts, so the number may be lower than before",
            },
        ],
    },
    {
        "version": "0.26.1",
        "date": "2026-06-24",
        "entries": [
            {
                "type": "fix",
                "sv": "Månadsvy (alla): en vikaries frånvaro syns nu i schemat (visas som frånvaropass den dagen), även om vikarien inte har något inlagt arbetspass den månaden",
                "en": "Month view (all): a substitute's absence now appears in the schedule (shown as an absence shift on that day), even when the substitute has no scheduled shift that month",
            },
            {
                "type": "fix",
                "sv": "Månadsvy (alla): en vikaries övertidspass visas nu som ÖT i schemat, och beredskap på en jourdag minskas med de timmar som jobbats som övertid under jouren",
                "en": "Month view (all): a substitute's overtime now appears as OT in the schedule, and on-call standby on an on-call day is reduced by the hours worked as overtime during the on-call period",
            },
        ],
    },
    {
        "version": "0.26.0",
        "date": "2026-06-02",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Vikarier: lägg till sommarvikarier (utan inloggning) under Admin → Vikarier och fyll deras pass i en månadskalender (N1/N2/N3/OC, flera dagar). Vikarier visas som egen rad/kolumn i vecko- och månadsschemat för alla, och som kollega i din dag-, vecko- och månadsvy de dagar ni jobbar samma pass",
                "en": "Substitutes: add summer substitutes (no login) under Admin → Substitutes and fill their shifts in a month calendar (N1/N2/N3/OC, multiple days). Substitutes appear as their own row/column in the all-persons week and month schedules, and as a coworker in your day, week and month views on days you share a shift",
            },
            {
                "type": "förbättring",
                "sv": "Veckobaserad semester och föräldraledighet visas nu bara på dagar du faktiskt är schemalagd; lediga dagar (OFF) i en vald vecka förblir OFF i stället för att markeras som SEM/LEAVE. Detta matchar hur semesterdagar räknas",
                "en": "Week-based vacation and parental leave now only show on days you are actually scheduled; days off (OFF) in a selected week stay OFF instead of being marked as SEM/LEAVE, matching how leave days are counted",
            },
        ],
    },
    {
        "version": "0.25.0",
        "date": "2026-06-02",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Dagvy: byt vilket pass du jobbar på en dag – 'Manuellt pass' går nu att använda även på dagar där rotationen redan ger ett arbetspass (N1/N2/N3), inte bara på lediga dagar och jourdagar; välj nytt pass i listan eller återställ till rotationen, och bytet slår igenom i alla vyer",
                "en": "Day view: change which shift you work on a day – 'Manual shift' can now be used on days where the rotation already assigns a working shift (N1/N2/N3), not only on days off and on-call days; pick a new shift from the list or revert to the rotation, and the change is applied across all views",
            },
        ],
    },
    {
        "version": "0.24.1",
        "date": "2026-05-31",
        "entries": [
            {
                "type": "fix",
                "sv": "Karens vid sjukfrånvaro som sträcker sig över ett månadsskifte beräknas nu korrekt; tidigare kunde hela karensavdraget (8h) dras en gång till i den nya månaden, vilket gav ett för stort löneavdrag",
                "en": "Waiting-day (karens) deductions for sick leave that spans a month boundary are now calculated correctly; previously the full 8h waiting period could be charged again in the new month, over-deducting from pay",
            },
            {
                "type": "fix",
                "sv": "Veckobaserad semester räknas nu i schemalagda arbetsdagar (OFF-dagar exkluderas), precis som dag-baserad semester; tidigare räknades alltid 5 vardagar per vecka vilket gav fel semestersaldo för skiftarbetare",
                "en": "Week-based vacation is now counted in scheduled work days (OFF days excluded), the same way as day-level vacation; previously a flat 5 weekdays per week were counted, giving an incorrect balance for shift workers",
            },
            {
                "type": "förbättring",
                "sv": "Säkerhet vid inloggning: skydd mot lösenordsgissning (tillfälligt kontolås efter upprepade misslyckade försök), obligatoriskt lösenordsbyte tvingas nu fram på alla sidor (inte bara direkt efter inloggning), och inloggningssvar avslöjar inte längre om ett användarnamn finns",
                "en": "Login security: brute-force protection (temporary lockout after repeated failed attempts), the mandatory password change is now enforced on every page (not only right after login), and login responses no longer reveal whether a username exists",
            },
        ],
    },
    {
        "version": "0.24.0",
        "date": "2026-05-29",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Dagvy: manuell override av OB- och beredskapstimmar – klicka 'Redigera timmar' i lönesektionen för att justera antalet timmar per typ (t.ex. flytta 7h röd dag till vardag); lönen räknas om automatiskt med befintliga taxor och overriden slår igenom i alla vyer (dagvy, månadsvy, löneunderlag)",
                "en": "Day view: manual override of OB and on-call hours – click 'Edit hours' in the pay section to adjust the number of hours per type (e.g. move 7h public holiday to weekday rate); pay is recalculated automatically using existing rates and the override is applied consistently across all views (day view, month view, pay basis)",
            },
        ],
    },
    {
        "version": "0.23.1",
        "date": "2026-05-28",
        "entries": [
            {
                "type": "förbättring",
                "sv": "Månadsvy: lönespecifikationens radrubriker, Excel-knappen och OB-toggle-knappen visas nu korrekt på engelska när appen används på engelska; tidigare var dessa hårdkodade på svenska",
                "en": "Month view: pay slip row labels, the Excel export button and the OB toggle button now display correctly in English when the app is used in English; previously these were hardcoded in Swedish",
            },
        ],
    },
    {
        "version": "0.23.0",
        "date": "2026-05-28",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Frånvaro: stöd för sen ankomst – man kan nu registrera klockslag för när man kom till jobbet ('Kom HH:MM') på samma sätt som man redan kunde registrera tidig avgång; frånvarotimmar, OB och löneunderlag räknas om korrekt för den jobbade delen av passet",
                "en": "Absence: support for late arrival – it is now possible to register the time of arrival ('Kom HH:MM') the same way early departure was already supported; absent hours, OB and salary base are recalculated correctly for the worked portion of the shift",
            },
            {
                "type": "förbättring",
                "sv": "Excel-export (min månad): ny kolumn 'Kommentar' som visar 'Sen ankomst HH:MM' och/eller 'Slutade tidigt HH:MM' för dagar med partiell frånvaro; kolumnen utelämnas automatiskt om inga sådana dagar finns i månaden",
                "en": "Excel export (my month): new 'Kommentar' column showing 'Sen ankomst HH:MM' and/or 'Slutade tidigt HH:MM' for days with partial absence; the column is omitted automatically if no such days exist in the month",
            },
        ],
    },
    {
        "version": "0.22.0",
        "date": "2026-05-27",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Månadsvy: ny toggle i detaljerad breakdown – 'Visa OB per kalenderdag' fördelar OB och ÖT på de kalenderdagar timmarna faktiskt faller på istället för att lägga allt på startdagen; nattpass som sträcker sig över midnatt visas uppdelade",
                "en": "Month view: new toggle in the detailed breakdown – 'Show OB per calendar day' distributes OB and OT hours across the calendar days they actually fall on instead of attributing everything to the shift start day; night shifts crossing midnight are shown split accordingly",
            },
        ],
    },
    {
        "version": "0.21.3",
        "date": "2026-05-14",
        "entries": [
            {
                "type": "förbättring",
                "sv": "Månads- och årsvy (alla): filterinställningar (dolda kolumner och rotationsläge) sparas nu i webbläsaren och behålls vid sidladdning och navigering mellan månader/år",
                "en": "Month and year views (all): filter settings (hidden columns and rotation mode) are now saved in the browser and preserved across page loads and navigation between months/years",
            },
        ],
    },
    {
        "version": "0.21.2",
        "date": "2026-05-14",
        "entries": [
            {
                "type": "fix",
                "sv": "Cowork: semesterdagar (SEM) räknades som gemensamma pass i detaljvyn om båda personerna hade semester samma dag – enbart N1/N2/N3 räknas nu som arbetspass",
                "en": "Cowork: vacation days (SEM) were counted as shared shifts in the detail view when both persons had vacation on the same day – only N1/N2/N3 now count as work shifts",
            },
        ],
    },
    {
        "version": "0.21.1",
        "date": "2026-05-14",
        "entries": [
            {
                "type": "fix",
                "sv": "API: /next-shift tog inte hänsyn till manuella OC-ändringar (tillagda eller borttagna via dagvyn) – endpointen tillämpar nu oncall-overrides korrekt",
                "en": "API: /next-shift did not respect manually added or removed on-call shifts (via the day view) – the endpoint now applies oncall overrides correctly",
            },
        ],
    },
    {
        "version": "0.21.0",
        "date": "2026-05-12",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Vecko-, månads- och årsvy (alla): ny knapp 'Visa rotation' visar det ordinarie rotationsskiftet istället för det faktiska passet – ändrade dagar markeras med en asterisk",
                "en": "Week, month and year views (all): new 'Show rotation' button displays the original rotation shift instead of the actual shift – changed days are marked with an asterisk",
            },
            {
                "type": "fix",
                "sv": "Månadsvy (alla): rotationsskiftet (original_shift) skickades inte vidare från dagsammanfattningen – 'Visa rotation'-knappen fungerar nu korrekt",
                "en": "Month view (all): the rotation shift (original_shift) was not passed through from the day summary – the 'Show rotation' button now works correctly",
            },
            {
                "type": "nyhet",
                "sv": "Föräldraledighet: välj hela veckor i semestervyn – lagras som veckonummer i ett eget JSON-fält och påverkar inte semesterdagar",
                "en": "Parental leave: select whole weeks in the vacation view – stored as week numbers in a dedicated JSON field and does not consume vacation days",
            },
            {
                "type": "nyhet",
                "sv": "Schema: föräldraledigdagar visas med LEAVE-pass (ingen lön, ingen OB) i alla schemavyer",
                "en": "Schedule: parental leave days appear with a LEAVE shift (no pay, no OB) in all schedule views",
            },
            {
                "type": "nyhet",
                "sv": "Dagvy: LEAVE-badge visas för föräldraledigdagar, identiskt med sjuk- och tjänstledighetsbadges",
                "en": "Day view: LEAVE badge shown for parental leave days, matching sick and leave of absence badges",
            },
            {
                "type": "nyhet",
                "sv": "Årsvy: föräldraledigdagar och -timmar visas i en egen rad i sammanfattningstabellen",
                "en": "Year view: parental leave days and hours shown in a dedicated row in the summary table",
            },
            {
                "type": "nyhet",
                "sv": "Statistik: föräldraledighet inkluderas i frånvarodiagrammet (blå sektion)",
                "en": "Statistics: parental leave included in the absence chart (blue section)",
            },
            {
                "type": "nyhet",
                "sv": "Admin: föräldraledigveckor kan hanteras per användare i admin-semestervyn med eget formulär och spara-knapp",
                "en": "Admin: parental leave weeks can be managed per user in the admin vacation view with a dedicated form and save button",
            },
        ],
    },
    {
        "version": "0.20.2",
        "date": "2026-05-11",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Admin: rotationseror har nu en JSON-knapp som visar erans fullständiga data i en modal",
                "en": "Admin: rotation eras now have a JSON button that shows the era's full data in a modal",
            },
            {
                "type": "nyhet",
                "sv": "Admin: framtida rotationseror kan nu redigeras (startdatum och veckömönster) via en Ändra-knapp – nuvarande eras slutdatum uppdateras automatiskt",
                "en": "Admin: future rotation eras can now be edited (start date and weeks pattern) via an Edit button – the current era's end date is updated automatically",
            },
            {
                "type": "fix",
                "sv": "Admin: semesterns dagkalender visar nu skiftfärger och OFF-dagar för den redigerade användaren, identiskt med användarens egna vy",
                "en": "Admin: the vacation day calendar now shows shift colours and OFF days for the edited user, matching the user's own view",
            },
            {
                "type": "fix",
                "sv": "Admin: dagkalendern i semestervyn visar nu veckonummer-kolumn, identiskt med användarens egna vy",
                "en": "Admin: the vacation day calendar now shows a week-number column, matching the user's own view",
            },
            {
                "type": "fix",
                "sv": "Docker: containern körs nu som hostens användare (uid/gid) vilket löste SQLite-skrivfel vid monterade volymer i dev-miljön",
                "en": "Docker: the container now runs as the host user (uid/gid), fixing SQLite write errors on mounted volumes in the dev environment",
            },
        ],
    },
    {
        "version": "0.20.1",
        "date": "2026-05-10",
        "entries": [
            {
                "type": "fix",
                "sv": "API: medarbetare med beredskapspass (OC) visades inte i co-workers-listan i schemats dagendpoints",
                "en": "API: co-workers with on-call shifts (OC) were not included in the co-workers list in the schedule day endpoints",
            },
            {
                "type": "nyhet",
                "sv": "API: /schedule/today accepterar ?date=YYYY-MM-DD för att simulera schema för valfri dag",
                "en": "API: /schedule/today accepts ?date=YYYY-MM-DD to simulate the schedule for any given day",
            },
            {
                "type": "nyhet",
                "sv": "API: ny endpoint /shifts listar alla passkoder med label, tider och färg",
                "en": "API: new endpoint /shifts lists all shift type codes with label, times and color",
            },
        ],
    },
    {
        "version": "0.20.0",
        "date": "2026-05-08",
        "entries": [
            {
                "type": "fix",
                "sv": "Månadsvy: manuellt tilldelade pass (t.ex. N2) visades inte i kalenderrutnätet trots att de syntes i veckovy",
                "en": "Month view: manually assigned shifts (e.g. N2) were not shown in the calendar grid even though they appeared in week view",
            },
            {
                "type": "fix",
                "sv": "Månadsvy: dagens datum markerades inte med blå ram",
                "en": "Month view: today's date was not highlighted with a blue border",
            },
            {
                "type": "fix",
                "sv": "Intervalvy: rotationsvecka visades inte på söndagar",
                "en": "Range view: rotation week was not shown on Sundays",
            },
            {
                "type": "nyhet",
                "sv": "ISO-veckonummer (v17) i månadsvy och intervalvy är nu klickbara länkar till veckovy för den veckan",
                "en": "ISO week numbers (v17) in month and range views are now clickable links to the week view for that week",
            },
            {
                "type": "nyhet",
                "sv": "Alla-vyer (månad/år): ISO-veckonummer visas i datumkolumnen på måndagar och länkar till veckovyn för alla",
                "en": "All-person views (month/year): ISO week numbers appear in the date column on Mondays and link to the all-persons week view",
            },
            {
                "type": "nyhet",
                "sv": "Alla-vyer (månad/år): personfilterfält med kryssboxar per person samt markera/avmarkera alla",
                "en": "All-person views (month/year): person filter panel with per-person checkboxes and select/deselect all",
            },
            {
                "type": "nyhet",
                "sv": "Alla-vyer (månad/år): tydligare hover-markering vid musöverfart på dagrader",
                "en": "All-person views (month/year): more visible hover highlight on day rows",
            },
        ],
    },
    {
        "version": "0.19.1",
        "date": "2026-05-02",
        "entries": [
            {
                "type": "fix",
                "sv": "API: nattpass returnerade ingen skiftdata vid frånvaro – rotationspasset visas nu även när dagen är ledig",
                "en": "API: night shifts returned no shift data on absence days – rotation shift is now included even when the day is leave",
            },
            {
                "type": "nyhet",
                "sv": "API: overnight-fält på alla pass så integrationer vet när sluttiden är nästa kalenderdag",
                "en": "API: overnight field on all shifts so integrations know when end_time falls on the next calendar day",
            },
            {
                "type": "nyhet",
                "sv": "API: currently_active_shift på /status och /next-shift visar pågående nattpass från föregående dag baserat på klockan",
                "en": "API: currently_active_shift on /status and /next-shift shows an ongoing overnight shift from the previous day based on current time",
            },
            {
                "type": "nyhet",
                "sv": "API: /status accepterar ?date och ?time för att simulera status för valfri tidpunkt",
                "en": "API: /status accepts ?date and ?time to simulate status for any point in time",
            },
        ],
    },
    {
        "version": "0.19.0",
        "date": "2026-05-01",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Intervalvy: visa 1-10 veckor av ditt personliga schema i ett rutnät, nåbar via vecko- och månadsvy",
                "en": "Range view: display 1-10 weeks of your personal schedule in a grid, reachable from the week and month views",
            },
            {
                "type": "fix",
                "sv": "Dagvy: semesterdagar visades som vanligt arbetspass – SEM-badge och gul notis visas nu korrekt bredvid rotationspasset",
                "en": "Day view: vacation days were shown as regular working shifts – SEM badge and yellow notice now appear correctly alongside the rotation shift",
            },
        ],
    },
    {
        "version": "0.18.2",
        "date": "2026-04-30",
        "entries": [
            {
                "type": "fix",
                "sv": "Veckovy: veckobaserad semester visades inte, pass visades som vanligt arbetspass trots inlagd semester",
                "en": "Week view: week-based vacation was not shown, shifts appeared as normal working shifts despite scheduled vacation",
            },
            {
                "type": "fix",
                "sv": "Semester: veckobaserad semester var osynlig för användare vars user-ID inte matchar rotationsposition",
                "en": "Vacation: week-based vacation was invisible for users whose user ID does not match their rotation position",
            },
        ],
    },
    {
        "version": "0.18.1",
        "date": "2026-04-26",
        "entries": [
            {
                "type": "fix",
                "sv": "Timlön: brutto- och nettolön visas nu korrekt i alla vyer baserat på faktiska jobbade timmar istället för teoretiskt månadsunderlag",
                "en": "Hourly wage: gross and net pay now display correctly in all views based on actual worked hours instead of the theoretical monthly equivalent",
            },
            {
                "type": "fix",
                "sv": "Timlön: sjuklön beräknades fel när timlönsanvändare hade sjukfrånvaro – brutto stämmer nu med lönespecifikationens totalt",
                "en": "Hourly wage: sick pay was calculated incorrectly for hourly wage users – gross pay now matches the payslip spec total",
            },
            {
                "type": "fix",
                "sv": "API: OB-tillägg beräknades på fel underlag för timlönsanvändare i schema-endpointsen",
                "en": "API: OB supplements were calculated on the wrong base for hourly wage users in schedule endpoints",
            },
        ],
    },
    {
        "version": "0.18.0",
        "date": "2026-04-26",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Månadsvy: timlönsanvändare får nu en lönespecifikation med uppdelning per löneart – arbetade timmar, OB per kod, beredskap, övertid och sjuklön",
                "en": "Month view: hourly wage users now see a payslip-style breakdown with rows per pay type – worked hours, OB by code, on-call, overtime and sick pay",
            },
            {
                "type": "fix",
                "sv": "Månadsvy: bruttolön i sammanfattningen stämmer nu med lönespecifikationens totalt för timlönsanvändare",
                "en": "Month view: gross pay in the summary now matches the payslip spec total for hourly wage users",
            },
        ],
    },
    {
        "version": "0.17.0",
        "date": "2026-04-26",
        "entries": [
            {
                "type": "fix",
                "sv": "API: /next-shift returnerade felaktigt dagens pass – endpointen tar nu hänsyn till klockslag och hoppar över pass vars starttid redan passerat",
                "en": "API: /next-shift incorrectly returned today's shift – the endpoint now considers the current time and skips shifts whose start time has already passed",
            },
            {
                "type": "nyhet",
                "sv": "API: /next-shift stödjer nu valfria parametrar ?date och ?time för att simulera svaret för en godtycklig tidpunkt",
                "en": "API: /next-shift now supports optional ?date and ?time query parameters to simulate the response for any given point in time",
            },
        ],
    },
    {
        "version": "0.16.0",
        "date": "2026-04-25",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Timlön: konsulter kan nu ha timlön (kr/tim) istället för månadslön – alla beräkningar (OB, OT, frånvaro, beredskap) använder rätt sats",
                "en": "Hourly wage: consultants can now have an hourly rate (SEK/h) instead of a monthly salary – all calculations (OB, OT, absence, on-call) use the correct rate",
            },
        ],
    },
    {
        "version": "0.15.0",
        "date": "2026-04-25",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Manuellt pass: tilldela N1/N2/N3 på en ledig dag – visas som ordinarie pass i överlämning, kollegor och API",
                "en": "Manual shift: assign N1/N2/N3 on a day off – appears as a regular shift in handover, coworkers and API",
            },
            {
                "type": "nyhet",
                "sv": "Dagvyn samlar alla ändringsfunktioner bakom en 'Ändra dag/skift'-knapp",
                "en": "Day view consolidates all edit options behind an 'Edit day/shift' toggle button",
            },
        ],
    },
    {
        "version": "0.14.2",
        "date": "2026-04-25",
        "entries": [
            {
                "type": "fix",
                "sv": "Överlämningsrapport: övertidspersonal visas nu med rätt passgrupp och markeras med (ÖT)",
                "en": "Handover report: overtime workers now appear under the correct shift group and are marked with (ÖT)",
            },
        ],
    },
    {
        "version": "0.14.1",
        "date": "2026-04-25",
        "entries": [
            {
                "type": "fix",
                "sv": "API: övertidspersonal visas nu som kollega med rätt pass (t.ex. OT-N1), beredskap utan inringning visas inte, adminkonto filtreras bort",
                "en": "API: overtime workers now appear as coworkers with the correct shift (e.g. OT-N1), on-call without call-in is hidden, admin account is filtered out",
            },
        ],
    },
    {
        "version": "0.14.0",
        "date": "2026-04-24",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Överlämningsrapport: generera dagens passöverlämning från dagvyn, kopiera och klistra in i OneNote",
                "en": "Handover report: generate the daily shift handover from the day view, copy and paste into OneNote",
            },
        ],
    },
    {
        "version": "0.13.0",
        "date": "2026-04-23",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Passöverlåtelse: ge bort eller ta ett pass utan att byta tillbaka. Kollegan accepterar som vanligt.",
                "en": "Shift transfer: give away or take a shift without a mutual swap. The colleague still accepts.",
            },
        ],
    },
    {
        "version": "0.12.1",
        "date": "2026-04-23",
        "entries": [
            {
                "type": "fix",
                "sv": "iCal-export använde databas-ID istället för rotationsposition, vilket gav fel schema",
                "en": "iCal export used database ID instead of rotation position, causing wrong schedule",
            },
        ],
    },
    {
        "version": "0.12.0",
        "date": "2026-04-23",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Externt REST API v1 med API-nyckelautentisering för integration mot tredjepartssystem",
                "en": "External REST API v1 with API key authentication for third-party integrations",
            },
        ],
    },
    {
        "version": "0.11.0",
        "date": "2026-04-06",
        "entries": [
            {
                "type": "nyhet",
                "sv": "OB-ersättning beräknas nu korrekt vid sjukfrånvaro (sick-OB)",
                "en": "OB compensation now calculated correctly during sick leave",
            },
            {
                "type": "nyhet",
                "sv": "Passbyte på samma dag är nu möjligt",
                "en": "Same-day shift swaps are now possible",
            },
            {
                "type": "nyhet",
                "sv": "Partiell frånvaro: registrera 'left_at' för halvdagsfrånvaro med karens-fördelning",
                "en": "Partial absence: register 'left_at' for half-day absence with deductible distribution",
            },
            {
                "type": "nyhet",
                "sv": "Förbättrad dashboard-navigering och schemavisning",
                "en": "Improved dashboard navigation and schedule display",
            },
        ],
    },
    {
        "version": "0.10.0",
        "date": "2026-03-03",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Jourövertid och övertid visas nu i separata rader i månadsvyn",
                "en": "On-call overtime and overtime now shown in separate rows in the month view",
            },
            {
                "type": "nyhet",
                "sv": "Offentlig månads- och veckovy tillgänglig utan inloggning",
                "en": "Public month and week view available without login",
            },
            {
                "type": "nyhet",
                "sv": "Excel-export för månadsvy",
                "en": "Excel export for month view",
            },
            {
                "type": "nyhet",
                "sv": "Semesterveckoväljaren hoppar nu automatiskt förbi ledigdagar",
                "en": "Vacation week selector now automatically skips days off",
            },
            {
                "type": "nyhet",
                "sv": "Flerspråkigt gränssnitt: Svenska / Engelska väljs per användare",
                "en": "Multilingual interface: Swedish / English selectable per user",
            },
            {
                "type": "fix",
                "sv": "Kollegors synlighet i passbyteslistan rättad",
                "en": "Fixed colleague visibility in the shift swap list",
            },
            {
                "type": "fix",
                "sv": "Personhistorik hämtar nu schema baserat på rätt datum",
                "en": "Person history now fetches schedule based on the correct date",
            },
            {
                "type": "fix",
                "sv": "Semesterdagsavmarkering fungerar korrekt",
                "en": "Vacation day deselection now works correctly",
            },
        ],
    },
    {
        "version": "0.9.0",
        "date": "2026-02-07",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Anställningstransition: stöd för att byta anställningstyp mitt i en period",
                "en": "Employment transition: support for changing employment type mid-period",
            },
            {
                "type": "nyhet",
                "sv": "Anpassningsbara faktorer för OB-, övertids- och joursatser per användare",
                "en": "Configurable multipliers for OB, overtime and on-call rates per user",
            },
            {
                "type": "nyhet",
                "sv": "Övertidsförlängning: pass kan förlängas med övertid direkt i schemat",
                "en": "Overtime extension: shifts can be extended with overtime directly in the schedule",
            },
            {
                "type": "nyhet",
                "sv": "Anpassade ersättningssatser (OB, OT, jour) per person",
                "en": "Custom compensation rates (OB, OT, on-call) per person",
            },
            {
                "type": "nyhet",
                "sv": "Månadsbrytningsvy med detaljerad uppdelning av ersättningstyper",
                "en": "Month breakdown view with detailed split of compensation types",
            },
            {
                "type": "nyhet",
                "sv": "Sparade semesterdagar: möjlighet att spara och ta ut semester över räkenskapsår",
                "en": "Saved vacation days: carry over and pay out vacation days across fiscal years",
            },
            {
                "type": "nyhet",
                "sv": "Semesterförbättringar: automatisk stängning av passerade semesterår",
                "en": "Vacation improvements: automatic closing of past vacation years",
            },
            {
                "type": "nyhet",
                "sv": "Storhelgsindikator i schemavy",
                "en": "Public holiday indicator in schedule view",
            },
            {
                "type": "nyhet",
                "sv": "Personhistorik: visa hur en persons schema sett ut historiskt",
                "en": "Person history: view how a person's schedule has looked historically",
            },
            {
                "type": "nyhet",
                "sv": "Statistiksida med årsöversikt och lönetrenddiagram",
                "en": "Statistics page with yearly overview and pay trend chart",
            },
            {
                "type": "nyhet",
                "sv": "Samarbetsstatistik-sida (gemensamma pass med kollegor)",
                "en": "Collaboration statistics page (shared shifts with colleagues)",
            },
            {
                "type": "nyhet",
                "sv": "Svenska navigeringstexter och aktiva länkmarkeringar i navbaren",
                "en": "Localized navigation labels and active link highlighting in the navbar",
            },
            {
                "type": "fix",
                "sv": "Rättad beräkning av jourövertid vid midnattspassering",
                "en": "Fixed on-call overtime calculation at midnight crossings",
            },
            {
                "type": "fix",
                "sv": "Veckovy visade rotationsposition istället för personnamn",
                "en": "Week view showed rotation position instead of person name",
            },
        ],
    },
    {
        "version": "0.8.0",
        "date": "2026-01-09",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Dashboard-förbättringar: bättre översikt med kommande pass och ersättningar",
                "en": "Dashboard improvements: better overview with upcoming shifts and compensation",
            },
            {
                "type": "nyhet",
                "sv": "Jouröverride: möjlighet att manuellt överskriva vem som har jour",
                "en": "On-call override: ability to manually override who is on call",
            },
            {
                "type": "nyhet",
                "sv": "Betalningsvy per månad och år med summerat utfall",
                "en": "Payment view per month and year with summarized totals",
            },
            {
                "type": "nyhet",
                "sv": "Uppdaterade joursatser för OC_WEEKEND och OC_HOLIDAY",
                "en": "Updated on-call rates for OC_WEEKEND and OC_HOLIDAY",
            },
            {
                "type": "nyhet",
                "sv": "Konsekvent tabellformatering i alla vyer",
                "en": "Consistent table formatting across all views",
            },
            {
                "type": "nyhet",
                "sv": "Kollegor visas i dagvyn och personliga vyer",
                "en": "Colleagues shown in day view and personal views",
            },
            {
                "type": "fix",
                "sv": "Jouröverride rättad i dashboardens dagsvy",
                "en": "On-call override fixed in the dashboard day view",
            },
        ],
    },
    {
        "version": "0.7.0",
        "date": "2025-12-29",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Lönehistorik: hantering av löneändringar över tid",
                "en": "Wage history: tracking of wage changes over time",
            },
            {
                "type": "nyhet",
                "sv": "Rotationsperioder: schema kan konfigureras med flera rotationserar",
                "en": "Rotation eras: schedule can be configured with multiple rotation periods",
            },
            {
                "type": "nyhet",
                "sv": "Angränsande månader visas i kalendervy",
                "en": "Adjacent months shown in calendar view",
            },
            {
                "type": "nyhet",
                "sv": "Hälsokontrollendpoint med databas-status",
                "en": "Health check endpoint with database status",
            },
            {
                "type": "fix",
                "sv": "Övertids- och frånvaroberäkning rättad",
                "en": "Overtime and absence calculation fixed",
            },
            {
                "type": "fix",
                "sv": "Datumkonsistens förbättrad i hela applikationen",
                "en": "Date consistency improved throughout the application",
            },
            {
                "type": "fix",
                "sv": "Konfigurationsvalidering vid uppstart",
                "en": "Configuration validation on startup",
            },
        ],
    },
    {
        "version": "0.6.0",
        "date": "2025-12-22",
        "entries": [
            {
                "type": "nyhet",
                "sv": "OB-koder visas som etiketter i schema- och dagvyer",
                "en": "OB codes shown as labels in schedule and day views",
            },
            {
                "type": "nyhet",
                "sv": "Datumväljare i dagvyn för snabb navigering",
                "en": "Date picker in day view for quick navigation",
            },
            {
                "type": "nyhet",
                "sv": "Skattetabellintegration: nettolön beräknas med korrekt skattetabell",
                "en": "Tax table integration: net pay calculated using the correct tax table",
            },
            {
                "type": "fix",
                "sv": "Jourberedskap separeras korrekt från ordinarie pass i dashboarden",
                "en": "On-call duty correctly separated from regular shifts in the dashboard",
            },
            {
                "type": "fix",
                "sv": "Storhelgsbricka baseras nu på datum istället för veckotyp",
                "en": "Public holiday badge now based on date instead of week type",
            },
            {
                "type": "fix",
                "sv": "Jourtimmar räknas inte längre dubbelt i passlistan",
                "en": "On-call hours no longer double-counted in the shift list",
            },
        ],
    },
    {
        "version": "0.5.0",
        "date": "2025-12-21",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Frånvarotypen 'Tjänstledigt' lagd till",
                "en": "Absence type 'Leave of absence' added",
            },
            {
                "type": "nyhet",
                "sv": "Komplett frånvarohantering med karens och OB-justering",
                "en": "Complete absence tracking with deductible and OB adjustment",
            },
        ],
    },
    {
        "version": "0.4.0",
        "date": "2025-12-20",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Inloggning med cookie-baserad session och databaslagring",
                "en": "Login with cookie-based session and database persistence",
            },
            {
                "type": "nyhet",
                "sv": "Användarprofil och semesterhanteringsgränssnitt",
                "en": "User profile and vacation management interface",
            },
            {
                "type": "nyhet",
                "sv": "Administratörsgränssnitt för användare och inställningar",
                "en": "Admin interface for users and settings",
            },
            {
                "type": "nyhet",
                "sv": "Passbyte mellan kollegor",
                "en": "Shift swaps between colleagues",
            },
        ],
    },
    {
        "version": "0.3.0",
        "date": "2025-12-19",
        "entries": [
            {
                "type": "nyhet",
                "sv": "iCal-export: exportera ditt schema till kalenderapp",
                "en": "iCal export: export your schedule to a calendar app",
            },
            {
                "type": "nyhet",
                "sv": "Veckovy med rutnätslayout",
                "en": "Week view with grid layout",
            },
        ],
    },
    {
        "version": "0.2.0",
        "date": "2025-12-08",
        "entries": [
            {
                "type": "nyhet",
                "sv": "OB-regler och utökade skiftvyer",
                "en": "OB rules and expanded shift views",
            },
            {
                "type": "nyhet",
                "sv": "Svenska helgdagar och storhelgsberäkning",
                "en": "Swedish public holidays and major holiday calculation",
            },
            {
                "type": "nyhet",
                "sv": "Månads- och årsvy för hela teamet",
                "en": "Monthly and yearly view for the whole team",
            },
            {
                "type": "nyhet",
                "sv": "Detaljerad dagsvy per person",
                "en": "Detailed day view per person",
            },
            {
                "type": "nyhet",
                "sv": "Årsstatistik per person",
                "en": "Yearly statistics per person",
            },
        ],
    },
    {
        "version": "0.1.0",
        "date": "2025-12-08",
        "entries": [
            {
                "type": "nyhet",
                "sv": "Initial release: grundläggande schemavisning per vecka",
                "en": "Initial release: basic schedule view per week",
            },
            {
                "type": "nyhet",
                "sv": "OB-ersättningsberäkning baserad på rotationsschema",
                "en": "OB compensation calculation based on rotation schedule",
            },
            {
                "type": "nyhet",
                "sv": "Personlig veckovy med daglig ersättning",
                "en": "Personal week view with daily compensation",
            },
        ],
    },
]


@router.get("/changelog", response_class=HTMLResponse)
async def changelog_page(
    request: Request,
    db: Session = Depends(get_db),
):
    from app.core.utils import get_today

    user = await get_current_user_optional(request, db)
    return render(
        "changelog.html",
        {
            "request": request,
            "user": user,
            "now": get_today(),
            "versions": VERSIONS,
            "current_version": VERSIONS[0]["version"],
        },
    )
