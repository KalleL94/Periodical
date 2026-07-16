# Implementationsplan: Issue #290 — Koppla vikarier till användarkonton med full löneintegration

**Datum:** 2026-07-16
**Issue:** #290 "feat: link substitutes to user accounts with full pay integration for pre-employment shifts"
**Bygger på:** PR #289 (`refactor/day-view-canonical-path`) — dagvyn är migrerad till den kanoniska vägen. Planen bygger uteslutande mot den koden, inte main.

## 1. Målbild

En vikarie (`Substitute`) ska kunna kopplas till ett `User`-konto via en nullbar `user_id`. För en kopplad användare ska vikariepass som ligger **före `employment_start_date`** renderas i användarens personliga vyer (dag/vecka/månad/år/statistik) istället för before-employment-masken, tydligt märkta som vikariepass, och räknas in i timmar, OB och lön — prissatt som **timavlönad** enligt exakt samma prissättningsprimitiv som befintliga HOURLY-users.

Grundprincip: **bygg ett lager i den kanoniska vägen**, inte specialkod per vy. Det finns exakt en injiceringspunkt — before-employment-grenen i `_populate_single_person_day` (period.py:1904-1928). Alla fyra personliga vyer trådar redan `employment_start` in i `generate_period_data`, så ett byte där i denna gren slår automatiskt igenom överallt, och `_process_day_for_summary`/`compute_day_ob_pay` prissätter dagen "gratis".

## 2. Nulägesanalys (verifierad mot koden)

### Datamodell
- `Substitute` (database.py:274-293): `id, name, is_active, created_at, created_by` + `shifts`-relation. **Ingen** `user_id`, **ingen** lön.
- `SubstituteShift` (296-315): `substitute_id, date, shift_code` (N1/N2/N3/OC). Inga egna tider — härleds ur `ShiftType` via `calculate_shift_hours(date, code)`, precis som rotationspass.
- `OvertimeShift`/`Absence`/`OnCallOverride` (180-252) har alla `substitute_id`-kolumn. `OvertimeShift.ot_pay` har kommentaren "Always 0.0 for substitutes (hours tracked, no pay)".

### Prissättning av timavlönade idag (återanvänds — inte uppfinns om)
- `WageType.HOURLY` (database.py:90-94). `User.wage` lagrar då **timlönen direkt** (kr/tim), inte månadslön.
- `get_effective_monthly_wage` (wages.py:102-118): för HOURLY returnerar `int(hourly_rate * 173.33)` — en månadsekvivalent så att alla `/173.33`-baserade beräkningar (inkl. OB via `calculate_ob_pay`) blir konsekventa.
- `get_ot_hourly_rate_from_stored_wage` (wages.py:121-134): för HOURLY används `stored_wage` direkt som OT-timpris.
- Baslön för HOURLY: `summarize_month_for_person` (summary.py:346-357) ersätter den teoretiska månadsbasen med `worked_hours × hourly_rate` via `_hourly_corrected_gross`. OB beräknas per dag av `compute_day_ob_pay` (ob.py:303-339) med månadsekvivalenten som `base_salary`.
- **Detta är hela den prissättningsväg vi ska återanvända.** Ingen ny OB-logik behövs; verifieringspunkten är att substitutdagar matas med `hourly_wage × 173.33` som `base_salary` och inga custom-rates.

### Kanonisk väg och injiceringspunkten
- `_populate_single_person_day` (period.py:1875-1928): tar emot `employment_start`, anropar `_resolve_day_person` → `show_off_before_employment`. Om sant → **early return med OFF, before_employment=True**. **Detta är den enda punkt vi ändrar för schemadelen.**
- Substitutinfrastruktur finns redan för alla-personer-vyn: `_build_substitute_day` (557-661), `_fetch_substitute_shifts` (522-539); prioritet absence > OT > schemalagt pass > OFF med `person_id="sub-<id>"`. Den koden är **display-only för team-vyn** och rör inte lön.
- `mask_days_to_employment` (792-833): maskar dagar **efter** anställningsslut — berörs inte; bara before-start-grenen.

### Summa/statistik
- `_process_day_for_summary` (summary.py:703+) läser `hours`, `shift`, `start/end`, `oncall_pay`, `ot_pay`, och OB via `compute_day_ob_pay`. before_employment-dagar är maskade till OFF ⇒ bidrar 0. Så snart en substitutdag får riktig shift+tider+hours bidrar den automatiskt till timmar och OB. Baslönen är dock knuten till månadens enda `base_salary`/`wage_type` (summary.py:257, 350) — mixed-month-utmaningen (se §5).
- `summarize_year_for_person` → statistics.py:71; `dashboard.py:90` → `summarize_month_for_person`. Båda ärver integrationen gratis.

### Person-change-flödet
- `admin_person_change_submit` (admin_users.py:921-1059): `successor_mode ∈ {existing, new, none}`. "new" skapar `User(wage_type=MONTHLY, is_active=0)` + `start_employment`/`add_person_change`, allt i en transaktion. Template `admin_person_change.html` har radioknappar (rad 60-67) och `existing_user_id`-select.

### Migrationsmönster
- `migrations/migrate_*.py`: fristående `sqlite3`-skript, idempotenta (`PRAGMA table_info` innan `ALTER TABLE ADD COLUMN`), DB-path som `sys.argv[1]`. Prod-DB kräver backup före körning.

## 3. Datamodelländringar

### 3.1 `user_id` på `substitutes`
```python
user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
user = relationship("User", foreign_keys=[user_id])
```
Nullbar. En vikarie kopplas till högst en användare. Uniktvång avstås på DB-nivå (en användare kan teoretiskt ha varit två vikarie-entiteter historiskt); istället helper `get_linked_substitutes_for_user(session, user_id) -> list[Substitute]` som slår samman alla kopplade vikariers pass.

### 3.2 Timlön — **på `Substitute`, inte per pass**
```python
hourly_wage = Column(Integer, nullable=True)  # kr/tim, samma semantik som User.wage vid WageType.HOURLY
```
**Motivering:** en sommarvikarie har en timlön för hela perioden; retroaktiv inmatning blir ett enda fält; återanvänder exakt HOURLY-users semantik så samma prissättningsprimitiv kan användas utan ny kod. **Trade-off (dokumenteras):** ingen temporal validitet (ingen mid-period lönehöjning). Utbyggnadsväg vid behov: `substitute_wage_history`-tabell analog med `WageHistory` — uttryckligen inte v1.

### 3.3 Migrationsskript
`migrations/migrate_substitute_account_link.py` enligt mönstret i `migrate_add_wage_type.py`: idempotent (`PRAGMA table_info`), DB-path som argv. Prod: **backa upp DB först**; dokumentera i docstring + DEPLOYMENT-anteckning.

## 4. Prissättning av vikariepass

### 4.1 Schemadelen (injicering i kanoniska vägen)
I `generate_period_data` (period.py:193+), när `person_id is not None`:
1. Resolvera positionens innehavare-user_id och deras kopplade vikarier.
2. Batch-hämta kopplade vikariers `SubstituteShift`/`Absence`/`OvertimeShift` för intervallet — återanvänd `_fetch_substitute_shifts` m.fl. Lägg mapparna + `hourly_wage` i `DayLookupContext`.

I `_populate_single_person_day`, i `show_off_before_employment`-grenen (1905), innan OFF-fallbacken: slå upp `(linked_sub_id, current_day)`. Om pass/absence/OT finns, bygg dagen med prioritet **absence > OT > schemalagt pass** (samma kedja som `_build_substitute_day`), med `shift`, `original_shift`, `hours`, `start`, `end` (via `calculate_shift_hours`), `ob` = `calculate_ob_hours(start, end, combined_ob_rules)`, samt nya flaggor `is_substitute=True`, `substitute_id`, `substitute_hourly_wage`. `before_employment` utelämnas/False. Om inget substitutpass finns → behåll nuvarande OFF-return.

Prioritetskedjan bevaras: substitutpass fyller bara datum där användaren inte innehar positionen, eftersom vi enbart är i denna gren när `current_day < employment_start`.

### 4.2 Lönedelen (mixed-month-säker)
Utmaning: `summarize_month_for_person` har en enda `base_salary`/`wage_type` per månad, men en transitionsmånad blandar vikariedagar (timlön) och anställda dagar (månadslön).

**Lösning — prissätt substitutdagar separat:** utöka `_process_day_for_summary` så att en `is_substitute`-flaggad dag:
- Bas: `hours × substitute_hourly_wage` läggs på `brutto_pay` (inte via den fasta månadsbasen).
- OB: `compute_day_ob_pay(day, combined_rules, base_salary=substitute_hourly_wage*173.33, ob_rate_overrides=None)` — dagens OB prissätts med vikariens timlön.
- Timmar/antal pass/OB-timmar ackumuleras som vanligt.

**Verifiering mot Handels-timlogik:** test att en substitut-N2-dag ger identisk OB som en HOURLY-user med samma `wage` på samma datum.

### 4.3 OT-hantering och "no pay"-regeln
`OvertimeShift.ot_pay` förblir `0.0` i DB för substituter (källan för team-vyn ändras inte). I den personliga integrationen prissätts OT-timmar via `get_ot_hourly_rate_from_stored_wage`-mönstret (HOURLY ⇒ `hourly_wage` direkt). Prioritet OT > schemalagt pass förhindrar dubbelräkning samma dag.

## 5. Prioritetsordning och dubbelräkning (riskkritiskt)

1. **Position slår vikarie:** injicering endast i before-employment-grenen ⇒ på/efter `employment_start` vinner rotation/override-kedjan. Substitutpass på datum ≥ emp_start visas inte i personvyn — testas explicit.
2. **Team-vyn vs personvyn:** alla-personer-vyn fortsätter rendera vikarien som separat `sub-<id>`-kolumn. Olika vyer ⇒ ingen dubbelräkning i en enskild total.
3. **Månadsrapporten (`build_substitute_month_summaries`):** verklig dubbelräkningsrisk. Beslut: en kopplad vikaries pre-employment-aktivitet attribueras till användaren; den fristående substitutrapportraden döljs eller märks "kopplad → räknas under <user>" för perioden. Okopplade vikarier oförändrade.
4. **OvertimeShift-dubbelräkning:** prioritet OT > pass i både `_build_substitute_day` och den nya personinjiceringen. Test krävs.

## 6. UI-ändringar

### 6.1 Person-change-flödet
- Nytt `successor_mode`-alternativ `"substitute"` ("Befintlig vikarie") med select över aktiva okopplade vikarier + fält för `new_username`/`new_password`/`new_wage` (månadslön för anställningen) + `substitute_hourly_wage` (om ej redan satt).
- Submit-handler atomiskt: skapa User (som "new"), `substitute.user_id = new_user.id`, sätt `hourly_wage`, `start_employment`/`add_person_change`, gemensam commit, `clear_schedule_cache()`.
- JS: visa/dölj blocket vid val (spegla befintlig successor_mode-logik rad 221-228).

### 6.2 Vikarieadmin (`substitutes.py`)
- Manage-sidan: fält för `hourly_wage` + select för retroaktiv koppling till `User`. POST-handlers `.../link` + utökad `.../save`. Kopplingsstatus + timlön i listan.

### 6.3 Märkning i vyerna
Day-dict bär `is_substitute=True`. "Vikarie"-badge i dag/vecka/månad/år-templates. Dagvyns pay-section: timlönebaserad rad. i18n-nycklar (sv/en).

## 7. Statistikintegration
Ingen separat kod: statistik/dashboard/år går genom `summarize_month_for_person` → `_process_day_for_summary`. Verifiera med test att totaler ökar med vikarieperioden.

## 8. Teststrategi (konsistensmatris-mönstret)

Utöka `tests/test_day_view_consistency.py` med nytt lager "kopplad vikarie, pre-employment":
- Seeda User med `employment_start` = X, `Substitute(user_id, hourly_wage=H)` med pass på datum < X (N1/N2/N3/OC), en substitut-absence-dag och en substitut-OT-dag.
- Assertera att dagvyn renderar substitutpasset (shift/hours/OB/lön) och att `generate_period_data(day, day, person_id)[0]` ger samma `shift.code`, `hours`, `ob`, `is_substitute=True`.
- Assertera prioritet: datum ≥ X visar rotation, även om vikariepass ligger där.

Ytterligare: prissättningstest (substitut-N2 = HOURLY-user-N2 med samma wage), mixed-month-test (vikarie 1-14 + anställd 15-30 ⇒ korrekt brutto), dubbelräkningsvakt för månadsrapporten och OT+pass samma dag, migrations-idempotens, person-change-atomicitet (rollback lämnar varken User eller länk).

## 9. Riskanalys

1. **Mixed-month-prissättning** (högst): prissätt substitutdagar separat (§4.2), rör inte månadsbasen. Dedikerat test.
2. **Retroaktiv lönesättning:** `hourly_wage` sätts efter jobbade pass; retroaktiv ändring flyttar redan rapporterade belopp. Dokumentera; utbyggnadsväg = `substitute_wage_history`.
3. **OT "no pay"-regeln:** `0.0` behålls i DB (team-källan); personintegrationen prissätter via HOURLY-OT-raten; dokumentera invarianten.
4. **Dubbelräkning rapport/team** (§5.3-5.4): explicit beslut + tester.
5. **Skatt på pre-employment-månader:** netto beräknas med användarens skattetabell även för vikarieperioden. Acceptabelt v1; dokumentera.
6. **Prioritetsläckage:** injicering enbart i before-employment-grenen; test låser fast.
7. **Prestanda:** återanvänd batch-fetchers; kör bara när innehavaren har kopplad vikarie.

## 10. Stegvis TDD-vänlig sekvens (commit/PR-struktur)

1. **Datamodell + migration**: `user_id`+`hourly_wage`, relation, migrationsskript, idempotens-test.
2. **Batch-hämtning + context**: `get_linked_substitutes_for_user`, substitutmappar i `DayLookupContext`. Ingen renderingsändring än.
3. **Kanonisk injicering** (TDD): konsistensmatris-lager (rött) → substitutgren i `_populate_single_person_day` (grönt).
4. **Löneintegration i summan** (TDD): prissättningstest (rött) → `_process_day_for_summary`-gren + OT (grönt). Mixed-month-test.
5. **Dubbelräkningsvakt i rapporten** (TDD).
6. **Person-change-UI**: `successor_mode="substitute"`, atomisk handler, template + JS, atomicitetstest.
7. **Vikarieadmin-UI**: `hourly_wage`, retroaktiv koppling.
8. **Vy-märkning + i18n**.
9. **PR**: full svit grön, CHANGELOG-post, DEPLOYMENT-anteckning om migration + DB-backup.

## 11. Bevarande av kanonisk-väg-invarianten
All schemaupplösning för substitutdagar sker i `_populate_single_person_day`; all prissättning återanvänder `compute_day_ob_pay`/hourly-primitiven. Inga vy-lokala skuggberäkningar — samma invariant som issue #206.

### Kritiska filer
- app/database/database.py
- app/core/schedule/period.py
- app/core/schedule/summary.py
- app/routes/admin_users.py
- app/routes/substitutes.py
- app/core/schedule/wages.py, app/core/schedule/ob.py (prissättningsprimitiv)
- tests/test_day_view_consistency.py (konsistensmatris)
- migrations/migrate_substitute_account_link.py (ny)
