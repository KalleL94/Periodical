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
