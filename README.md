# ICA Schedule

Rotationsschema och OB-beräkning för 10-personers team.

## Features

- 10-veckors rotationsschema (N1/N2/N3 pass)
- OB-beräkning enligt svenska regler (OB1-OB5)
- Helgdagshantering (påsk, midsommar, jul, etc.)
- Semesterhantering per användare
- Behörighetssystem (user ser bara egen lön, admin ser allt)

## Installation

```bash
# Klona repo
git clone <repo-url>
cd ica-schedule

# Skapa virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Installera dependencies
pip install -r requirements.txt

# Kör migrering (skapar databas och användare)
python migrate_to_db.py

# Starta server
uvicorn app.main:app --reload
```

## Användare

Efter migrering finns dessa konton (lösenord: `London1`):

| Username | Roll | Beskrivning |
|----------|------|-------------|
| admin | Admin | Ser alla löner, kan skapa användare |
| ddf412 | User | Kalle (ID 6) |
| ... | User | Övriga teammedlemmar |

**Byt lösenord efter första inloggning!**

## Struktur

```
app/
├── auth/           # Autentisering (JWT, lösenord)
├── core/           # Affärslogik (schema, OB-beräkning)
├── database/       # SQLAlchemy models
├── routes/         # FastAPI routes
├── static/         # CSS
└── templates/      # Jinja2 templates
data/
├── persons.json    # Persondata
├── rotation.json   # Rotationsschema
└── ob_rules.json   # OB-regler
```

## Produktion

1. Ändra `SECRET_KEY` i `app/auth/auth.py`
2. Sätt `secure=True` för cookies (kräver HTTPS)
3. Använd riktig databasbackup

## Tech Stack

- FastAPI
- SQLAlchemy + SQLite
- Jinja2 templates
- JWT auth med argon2 password hashing
