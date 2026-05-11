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
            {
                "type": "nyhet",
                "sv": "Automatisk databasbackup vid driftsättning",
                "en": "Automatic database backup on deployment",
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
