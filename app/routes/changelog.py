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
        "version": "0.0.20",
        "date": "2026-04-23",
        "entries": [
            {
                "type": "nyhet",
                "text": "Externt REST API v1 med API-nyckelautentisering för integration mot tredjepartssystem",
            },
            {"type": "nyhet", "text": "OB-ersättning beräknas nu korrekt vid sjukfrånvaro (sick-OB)"},
            {"type": "nyhet", "text": "Passbyte på samma dag är nu möjligt"},
        ],
    },
    {
        "version": "0.0.19",
        "date": "2026-04-08",
        "entries": [
            {
                "type": "nyhet",
                "text": "Partiell frånvaro: registrera 'left_at' för halvdagsfrånvaro med karens-fördelning",
            },
            {"type": "nyhet", "text": "Förbättrad dashboard-navigering och schemavisning"},
        ],
    },
    {
        "version": "0.0.18",
        "date": "2026-03-19",
        "entries": [
            {"type": "nyhet", "text": "Jourövertid och övertid visas nu i separata rader i månadsvyn"},
            {"type": "nyhet", "text": "Offentlig månads- och veckovy tillgänglig utan inloggning"},
            {"type": "nyhet", "text": "Excel-export för månadsvy"},
            {"type": "nyhet", "text": "Semesterveckoväljarens hoppar nu automatiskt förbi ledigdagar"},
            {"type": "nyhet", "text": "Flerspråkigt gränssnitt: Svenska / Engelska väljs per användare"},
            {"type": "nyhet", "text": "Samarbetsstatistik-sida (gemensamma pass med kollegor)"},
            {"type": "fix", "text": "Kollegors synlighet i passbyteslistan rättad"},
            {"type": "fix", "text": "Personhistorik hämtar nu schema baserat på rätt datum"},
            {"type": "fix", "text": "Semesterdagsavmarkering fungerar korrekt"},
        ],
    },
    {
        "version": "0.0.17",
        "date": "2026-02-21",
        "entries": [
            {"type": "nyhet", "text": "Anställningstransition: stöd för att byta anställningstyp mitt i en period"},
            {"type": "nyhet", "text": "Anpassningsbara faktorer för OB-, övertids- och joursatser per användare"},
            {"type": "nyhet", "text": "Övertidsförlängning: pass kan förlängas med övertid direkt i schemat"},
            {"type": "nyhet", "text": "Anpassade ersättningssatser (OB, OT, jour) per person"},
            {"type": "nyhet", "text": "Månadsbrytningsvy med detaljerad uppdelning av ersättningstyper"},
            {
                "type": "nyhet",
                "text": "Sparade semesterdagar: möjlighet att spara och ta ut semester över räkenskapsår",
            },
            {"type": "nyhet", "text": "Semesterförbättringar: automatisk stängning av passerade semesterår"},
            {"type": "nyhet", "text": "Storhelgsindikator i schemavy"},
            {"type": "nyhet", "text": "Personhistorik: visa hur en persons schema sett ut historiskt"},
            {"type": "fix", "text": "Rättad beräkning av jourövertid vid midnattspassering"},
        ],
    },
    {
        "version": "0.0.15",
        "date": "2026-01-25",
        "entries": [
            {"type": "nyhet", "text": "Dashboard-förbättringar: bättre översikt med kommande pass och ersättningar"},
            {"type": "nyhet", "text": "Jouröverride: möjlighet att manuellt överskriva vem som har jour"},
            {"type": "nyhet", "text": "Betalningsvy per månad och år med summerat utfall"},
            {"type": "nyhet", "text": "Uppdaterade joursatser för OC_WEEKEND och OC_HOLIDAY"},
            {"type": "nyhet", "text": "Konsekvent tabellformatering i alla vyer"},
            {"type": "nyhet", "text": "Kollegor visas i dagvyn och personliga vyer"},
            {"type": "fix", "text": "Jouröverride rättad i dashboardens dagsvy"},
        ],
    },
    {
        "version": "0.0.14",
        "date": "2025-12-30",
        "entries": [
            {"type": "nyhet", "text": "Lönehistorik: hantering av löneändringar över tid"},
            {"type": "nyhet", "text": "Rotationsperioder: schema kan konfigureras med flera rotationserar"},
            {"type": "nyhet", "text": "Angränsande månader visas i kalendervy"},
            {"type": "nyhet", "text": "Hälsokontrollendpoint med databas-status"},
            {"type": "fix", "text": "Övertids- och frånvaroberäkning rättad"},
            {"type": "fix", "text": "Datumkonsistens förbättrad i hela applikationen"},
            {"type": "fix", "text": "Konfigurationsvalidering vid uppstart"},
        ],
    },
    {
        "version": "0.0.12",
        "date": "2025-12-23",
        "entries": [
            {"type": "nyhet", "text": "OB-koder visas som etiketter i schema- och dagvyer"},
            {"type": "fix", "text": "Jourberedskap separeras korrekt från ordinarie pass i dashboarden"},
            {"type": "fix", "text": "Storhelgsbricka baseras nu på datum istället för veckotyp"},
            {"type": "fix", "text": "Jourtimmar räknas inte längre dubbelt i passlistan"},
        ],
    },
    {
        "version": "0.0.11",
        "date": "2025-12-22",
        "entries": [
            {"type": "nyhet", "text": "Datumväljare i dagvyn för snabb navigering"},
            {"type": "nyhet", "text": "Skattetabellintegration: nettolön beräknas med korrekt skattetabell"},
        ],
    },
    {
        "version": "0.0.9",
        "date": "2025-12-21",
        "entries": [
            {"type": "nyhet", "text": "Frånvarotypen 'Tjänstledigt' lagd till"},
            {"type": "nyhet", "text": "Komplett frånvarohantering med karens och OB-justering"},
            {"type": "nyhet", "text": "Automatisk databasbackup vid driftsättning"},
        ],
    },
    {
        "version": "0.0.7",
        "date": "2025-12-19",
        "entries": [
            {"type": "nyhet", "text": "iCal-export: exportera ditt schema till kalenderapp"},
            {"type": "nyhet", "text": "Veckovy med rutnätslayout"},
        ],
    },
    {
        "version": "0.0.1",
        "date": "2025-12-08",
        "entries": [
            {"type": "nyhet", "text": "Initial release: schemavisning per år, månad och vecka"},
            {"type": "nyhet", "text": "OB-ersättningsberäkning baserad på rotationsschema"},
            {"type": "nyhet", "text": "Personlig vy med daglig lön och ersättning"},
            {"type": "nyhet", "text": "Administratörsgränssnitt för användare och inställningar"},
            {"type": "nyhet", "text": "Passbyte mellan kollegor"},
            {"type": "nyhet", "text": "Inloggning med cookie-baserad session"},
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
