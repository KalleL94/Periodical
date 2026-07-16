# Implementationsplan: Issue #206 — Enhetlig passhämtning för dagvyn

**Datum:** 2026-07-16
**Issue:** #206 "refactor: centralize shift-fetching -- day view and month view use separate code paths"

## 1. Målbild

Det ska finnas **en enda auktoritativ kodväg för passupplösning**: `generate_period_data()` i `app/core/schedule/period.py` (via `_populate_single_person_day`). Dagvyn (`show_day_for_person` i `app/routes/schedule_personal.py`) ska hämta sitt eget pass genom `generate_period_data(start=date, end=date, person_id=rotation_position, ...)` och plocka `result[0]`, precis som issue föreskriver — istället för att köra sin egen sekvens av sex DB-queries och manuell override-applicering.

Efter migreringen gäller:

- **Passupplösning** (shift, original_shift, hours, start, end, ob-timmar, oncall_pay, oncall_details, ot_pay/hours/details, before_employment, partial_absence) kommer uteslutande från det kanoniska day-dictet.
- **Presentationslager-beräkningar** som dagvyn är ensam om (ob_pay per dag, karens/sjuklön, sick_ob, absence_deduction, semestertillägg-flaggor) beräknas ovanpå det kanoniska dictet — inte genom en parallell passupplösning.
- **Rå-objekt som bara behövs för redigeringsformulär** (`oncall_override`, `shift_override`, `day_pay_override`, `absence`) hämtas fortfarande direkt, men de är då **icke-auktoritativa** för passvalet — de driver bara formulärens förifyllning och de detaljvyer period-datat inte exponerar. Detta måste dokumenteras tydligt i koden så att ingen återinför skuggberäkning.

Detta löser issuens grundproblem: en ny override-typ som läggs till i batch-funktionerna slår automatiskt igenom i dagvyn.

## 2. Nulägesanalys (verifierad mot koden)

### Dagvyns egna kodväg (`schedule_personal.py:73-567`)
Sekvens av direkta queries och manuell logik:
1. `OnCallOverride` (~142) + manuell ADD/REMOVE-applicering (~152-166)
2. `is_effective_oc`-beräkning (~168-171)
3. `ShiftOverride` (~174-186)
4. Veckosemester → SEM-maskering (~228-245) — **PR #281-guard**
5. `get_overtime_shift_for_date` (~250) för `is_full_ot`
6. OB-beräkning (~255-261)
7. `Absence` (~271) + partial-day trunkering (~273-297) + full-day SICK OB-nollning (~299-309)
8. `compute_ot_details` (~311) + OT-shift-overlay (~317-344)
9. On-call-ersättning, nollad vid absence (~346-355) — **PR #281-guard**
10. `DayPayOverride` (~358-376)
11. Karens/sjuklön/absence_deduction/sick_ob (~401-450)
12. `VACATION`-absence + veckosemester → `is_vacation_day` (~491-513)

### Kanonisk väg (`period.py`)
`_populate_single_person_day` (period.py:1875-2082) implementerar prioritetskedjan:
- **before_employment** → OFF, early return (1905-1928)
- **Absence** via `_populate_absence_day` (1939-1942): partial-day (truncering + OB) → VACATION/PARENTAL/övrig absence-shift. Nollar oncall_pay och ot_pay. Returnerar early.
- **Parental (veckobaserad)** via `_populate_parental_day` (1945)
- **`_resolve_effective_shift`** (1949): vacation(SEM) > shift_override > swap > rotation, med OB-timmar
- **oncall_override** via `_apply_oncall_override` (1977)
- **oncall pay** (1981) + omräkning vid OT (2013-2021)
- **OT-overlay** (2027-2041)
- **day_pay_override** (2044-2062)

Producerade nycklar i day-dict: `shift, original_shift, rotation_week, rotation_length, hours, start, end, ob, oncall_pay, oncall_details, ot_pay, ot_hours, ot_details, ob_hours_override, before_employment` (+ `partial_absence` för partiell frånvaro).

### Månadsvyn (Problem 2)
`build_calendar_grid_for_month` (summary.py:408) returnerar `summary` (via `summarize_month_for_person` → `generate_month_data`) **och** `grid` (via `generate_period_data` på utökat intervall). Båda går numera genom `generate_period_data`, men i **två separata anrop**. Route (schedule_personal.py:880-892) läser `days_in_month = calendar_data["summary"]` för SEM-räkning m.m. och `calendar_grid = calendar_data["grid"]` för rendering. De kan divergera eftersom de är olika invocations och `summary`-vägen dessutom maskerar/summerar separat.

### Gap: vad dagvyn behöver som period-datat saknar (riskinventering)

| Behov i dagvyn | Finns i period-dict? | Åtgärd |
|---|---|---|
| `shift, original_shift, hours, start, end` | Ja | Använd direkt |
| `ob` (OB-**timmar**) | Ja (`ob`) | Använd direkt |
| **`ob_pay`** (OB-**kronor** per dag) | **Nej** — beräknas i `_process_day_for_summary` (summary.py:723-725) | Beräkna i dagroute från canonical `start/end/ob_hours_override`, återanvänd samma gren som summary |
| `oncall_pay, oncall_details` | Ja | Använd direkt (löser PR #281-guard #2) |
| `ot_pay, ot_hours, ot_details` | Ja | Använd direkt |
| `ot_shift_id` (för redigeringslänk) | **Nej** (details har ej id) | Behåll en lätt separat `get_overtime_shift_for_date` **enbart** för id, eller lägg `ot_shift_id` i ot_details |
| `absence` (rå-objekt till template) | Delvis (`partial_absence`) | Fortsätt hämta `Absence` för form/detalj, men INTE för passval |
| `absence_deduction, absence_shift_hours` | Nej (löneslager) | Behåll dagvyns beräkning ovanpå canonical |
| `is_karens, karens_hours_today, sjuklon_hours_today, sick_ob_pay_today` | Nej | Behåll dagvyns beräkning |
| `oncall_override, shift_override, day_pay_override` (rå-objekt till formulär) | Nej (bara effekten) | Behåll direkta queries **endast för formulär** |
| `is_effective_oc, has_rotation_oc` | Nej (härledbart) | Härled ur canonical `shift.code`/`original_shift.code` + oncall_override |
| `is_vacation_day` | Nej (härledbart) | Härled ur `shift.code == "SEM"` + VACATION-absence |

**Prestanda/cache:** `generate_period_data(date, date, ...)` för en enda person är billigt (batch-funktionerna kör med `start==end`). Ingen ändring av `clear_schedule_cache` krävs — den rensar bara `lru_cache` i `core.py`/`ob.py` (rotationsberäkning), inte DB-data, och den kanoniska vägen använder samma cachade `determine_shift_for_date`.

## 3. Känt olöst fynd: OT-overlay efter semesterupplösning

I `_populate_single_person_day` kör OT-overlayn (period.py:2027-2041) **oavsett** vad `_resolve_effective_shift` returnerade. Absence-dagar returnerar early (så OT slår inte igenom där), men **veckobaserad semester (SEM)** faller igenom `_resolve_effective_shift` utan early return, varefter ett OT-pass på en semestervecka ersätter SEM. Inget befintligt skip/xfail-test för detta hittades i tests/.

**Rekommendation: bryt ut detta till separat issue/PR, kör det INTE i samma migrering.** Skäl:
- Migreringen ska vara beteendebevarande (dagvyn ska bli identisk med den kanoniska vägen). Om vi samtidigt ändrar den kanoniska vägens semantik blandar vi en refaktorering med en beteendefix, vilket gör konsistenstesterna svårlästa (två rörliga mål).
- Dagens dagväg har ingen egen SEM-vs-OT-guard, så beteendet efter migrering blir det kanoniska vägens (OT slår igenom SEM). Det är en **medveten, dokumenterad** regressionsyta som fångas av ett nytt konsistenstest.
- Fixen (låt SEM/vacation ha prioritet över OT-overlay, analogt med hur absence returnerar early) hör hemma i `_populate_single_person_day` och ska ha egna dedikerade tester för lönepåverkan.

Skapa uppföljningsissue: "OT-overlay applied after vacation resolution — OT shift on a vacation week overrides SEM in the canonical path". Lägg ett `xfail`-markerat test som dokumenterar nuläget (se teststrategi steg 0).

## 4. Stegvis migrering (TDD-vänlig)

Varje steg är självständigt testbart och lämnar appen i körbart skick. Följ TDD: skriv/utöka test → se rött (eller grönt-som-skyddsnät) → refaktorera → grönt.

### Steg 0 — Bredda skyddsnätet (endast tester, ingen produktionskod)
Utöka `tests/test_day_view_consistency.py` (finns redan, 2 tester) med parametriserade fall som jämför dagvyns renderade värden mot `generate_period_data(date, date, person_id)[0]` för **varje override-lager**:
- Ren rotationsdag (N1/N2/N3, OC, OFF)
- `ShiftOverride` (N1→N3)
- `OnCallOverride` ADD och REMOVE
- Accepterat `ShiftSwap`
- Veckobaserad semester (SEM) — redan finns
- Dagsnivå `VACATION`-absence
- Partiell frånvaro (`left_at`/`arrived_at`) + OB-omräkning
- Full-day `SICK` (oncall-nollning) — redan finns
- `PARENTAL` (vecko + dagsnivå)
- `DayPayOverride` (ob_hours + oncall_hours)
- `OvertimeShift` (full call-in vs extension)
- before_employment (och after-employment)

Assertera på canonical dict-fält (`shift.code`, `hours`, `ob`, `oncall_pay`, `ot_pay`) **och** på nyckelvärden i renderad HTML. Detta är gult/grönt idag och blir regressionsvakt under hela migreringen.

Lägg också det `xfail`-markerade OT-på-semestervecka-testet (dokumenterar fyndet i §3) så det är spårbart utan att blockera.

### Steg 1 — Hämta målpersonens canonical dict
I `show_day_for_person`: person-specifikt anrop:
```
canonical = generate_period_data(date_obj, date_obj, person_id=rotation_position, session=db, user_rates_map={rotation_position: _user_rates})[0]
```
Behåll all-persons-anropet (~457) enbart för coworkers. (Två anrop, men billiga för en dag.) Notera: all-persons-vägen (`person_id=None`) bygger via `_build_person_day_basic`, som **inte** ger lönefält — därför behövs det detaljerade person-specifika anropet. Tråda in `user_rates_map` precis som `build_calendar_grid_for_month` (summary.py:465-473) för korrekt OT-prissättning. Ännu ingen beteendeändring.

### Steg 2 — Migrera passupplösning (shift/hours/start/end/ob/original_shift)
Ersätt dagvyns block som beräknar `shift`, `original_shift`, `hours`, `start_dt`, `end_dt` (rad ~108-193, 224-245, 317-344) med värden ur `canonical`.

**Tas bort i detta steg:**
- Manuell `OnCallOverride`-applicering på shift (~152-166)
- `ShiftOverride`-query + applicering (~174-186)
- **PR #281-guard: veckosemester→SEM (~228-245)** — nu hanterad av `_resolve_effective_shift`
- OT-shift-overlay (~317-344)
- `before_employment`-maskering av shift (~132-139) — canonical sätter `before_employment` och OFF

Härled `has_rotation_oc = original_shift and original_shift.code == "OC"` och `is_effective_oc = canonical shift.code == "OC"`. Kör konsistenstester + hela sviten: grönt.

### Steg 3 — Migrera OB-timmar, on-call och OT-lön
- `ob_hours` (timmar): ta från `canonical["ob"]`.
- `ob_pay` (kronor): beräkna med samma gren som `_process_day_for_summary` (summary.py:717-730). **Extrahera en delad hjälpfunktion** (t.ex. `compute_day_ob_pay(day, combined_rules, monthly_salary, ob_rate_overrides)`) som både `_process_day_for_summary` och dagroute anropar — så OB-pay-logiken inte dupliceras.
- `oncall_pay = canonical["oncall_pay"]`, `oncall_details = canonical["oncall_details"]`.
- `ot_pay/ot_hours/ot_details` från canonical; `ot_shift_id` via kvarvarande lätt query enbart för id, eller (renare) lägg till id i `ot_details` i period.py.

**Tas bort i detta steg:**
- `temp_ot_check`/`is_full_ot`-block (~250-261)
- OB-beräkning via `calculate_ob_hours/pay` för full shift (~255-261)
- `compute_ot_details`-anropet (~311) för lön (behåll ev. för karens-input, se steg 4)
- **PR #281-guard: oncall-nollning vid absence (~346-355)** — nu hanterad av `_populate_absence_day` som sätter `oncall_pay=0`

### Steg 4 — Migrera frånvarohantering (partial, full, VACATION)
Passvalet vid frånvaro kommer nu från canonical (`_populate_absence_day` ger SEM/LEAVE/SICK-shift + `partial_absence` med truncerade tider). Dagvyn behåller **endast** löneslagerdelen:
- Fortsätt hämta rå `absence`-objektet (för template + karens/sjuklön/deduction), men använd det **inte** för att räkna om shift/hours/OB.
- `absence_deduction`, `absence_shift_hours`, karens/sjuklön/sick_ob (~401-450): behåll, men mata dem med canonical `start/end/hours`.

**Tas bort:** dagvyns partial-day-trunkering av `end_dt/start_dt` + OB-omräkning (~273-297) och full-day-SICK-OB-nollning (~299-309).

Verifiera noggrant: dagvyns nuvarande partial-logik nollar OB endast om `not is_full_ot and not is_effective_oc`; canonical `_populate_absence_day` beräknar `ob = calculate_ob_hours(...)` för partial och `{}` för OC. Konsistenstestet för partiell frånvaro (steg 0) är kritiskt här.

### Steg 5 — Migrera `is_vacation_day` och DayPayOverride
- `is_vacation_day`: härled ur `canonical["shift"].code == "SEM"` **eller** VACATION-absence (behåll VACATION-absence-queryn ~505-513 om templaten behöver skilja veckosemester från dagsemester).
- `DayPayOverride`: canonical applicerar redan override. Behåll den direkta queryn **endast** för rå-objektet till redigeringsformuläret och för `ob_pay`-grenen i steg 3.

**Tas bort:** dagvyns egen `apply_ob_hours_override`/`apply_oncall_hours_override`-applicering (~363-376).

### Steg 6 — Rå-objekt enbart för formulär, dokumentera invariant
Kvar i dagroute som **medvetet icke-auktoritativa** queries (endast för formulärförifyllning/detaljlänkar): `OnCallOverride`, `ShiftOverride`, `DayPayOverride`, `Absence`, samt `ot_shift_id`. Lägg en tydlig kommentar: "These fetches drive edit-form prefill and detail rendering ONLY. Shift/hours/pay resolution comes exclusively from `generate_period_data`; do not reintroduce shadow calculations here (issue #206)."

### Steg 7 (Problem 2, kan göras separat) — En datakälla för månadsvyn
Målet: `days_in_month` (summary) och `calendar_grid` (grid) ska härledas ur **ett** `generate_period_data`-resultat för det utökade intervallet.
- Refaktorera `build_calendar_grid_for_month` så att `summarize_month_for_person` återanvänder `extended_days` (filtrerat till aktuell månad) istället för att internt anropa `generate_month_data` en andra gång. Skicka `year_days=<filtrerade extended_days>` — parametern finns redan i signaturen (summary.py:223, 264-269).
- Säkerställ att `mask_days_to_employment` och `user_rates_map` appliceras **en gång** på det gemensamma datat innan både summary och grid härleds.

Verifiera med befintliga månads-/år-konsistenstester att totals är oförändrade.

## 5. Sammanställning: borttagen punktguard/dubbellogik per steg

| Steg | Borttaget | Ersatt av (kanonisk) |
|---|---|---|
| 2 | OnCallOverride-applicering, ShiftOverride-query+apply, OT-overlay, before_employment-mask, **PR #281 SEM-guard** | `_resolve_effective_shift` + `_apply_oncall_override` + OT-block + before_employment |
| 3 | Full-shift OB-beräkning, `is_full_ot`-block, OT-lönberäkning, **PR #281 oncall-nollning vid absence** | `canonical["ob"]`, `oncall_pay`, `ot_*`; delad `compute_day_ob_pay` |
| 4 | Partial-day trunkering + OB-omräkning, full-day-SICK OB-nollning | `_populate_absence_day` |
| 5 | `apply_ob_hours_override`/`apply_oncall_hours_override`-applicering | canonical DayPayOverride-hantering |

## 6. Bevarande av prioritetskedjan

Kravet "Before-employment → Absence → Vacation → Normal shift" + alla override-lager bevaras eftersom hela kedjan redan finns i `_populate_single_person_day` i exakt denna ordning. Migreringen **flyttar** dagvyn till denna kedja snarare än att återimplementera den. Konsistenstesterna (steg 0) låser fast att varje lager ger identiskt resultat i båda vägarna innan respektive dubbellogik tas bort.

## 7. Riskanalys

1. **`ob_pay` per dag saknas i period-datat.** Högsta risken för avvikelse (kronor). Mitigering: extrahera delad `compute_day_ob_pay`-helper (steg 3); dedikerat konsistenstest på OB-kronor för N1/N2/N3 + midnattspass.
2. **Partiell frånvaro: subtil OB-skillnad.** OB-grenvillkoren skiljer sig (OC-hantering). Mitigering: parametriserat konsistenstest med `left_at`/`arrived_at` på både OC- och N-dagar.
3. **`ot_shift_id` och andra detaljfält** som period-datat inte exponerar. Mitigering: behåll minimala läs-queries enbart för formulär/detaljlänk; överväg att lägga `id` i `ot_details`.
4. **OT-på-semestervecka (§3).** Efter migrering ärver dagvyn den kanoniska vägens beteende. Mitigering: `xfail`-test + separat uppföljningsissue; besluta explicit att fixa i egen PR.
5. **`user_rates_map`-trådning.** Om den glöms blir OT-pris fel. Mitigering: spegla `build_calendar_grid_for_month`-mönstret (summary.py:465-473); konsistenstest med anpassad OT-rate.
6. **Prestanda.** Ett extra person-specifikt `generate_period_data`-anrop. För en enda dag försumbart. Ingen cacheinvalidering påverkas.
7. **`is_effective_oc`/`has_rotation_oc`-härledning** styr vilken pay-tabell templaten renderar. Mitigering: konsistenstestet `_oncall_totals` (finns redan).

## 8. Teststrategi

- **Skyddsnät (steg 0):** utöka `tests/test_day_view_consistency.py` till parametriserad matris över alla override-lager, med assertion mot både canonical dict och renderad HTML.
- **Delad helper-enhetstest:** `compute_day_ob_pay` testas isolerat mot `calculate_ob_pay` för representativa skift inkl. midnattspass.
- **Månadsvyn (steg 7):** kör `tests/test_schedule_views_person_change.py` + befintliga månads-/semester-tester för att bekräfta oförändrade totals.
- **xfail-dokumentation:** OT-på-semestervecka som `@pytest.mark.xfail(reason="issue #NNN: OT overlay applied after vacation")`.
- **Full svit** efter varje steg; commit per grönt steg för lätt bisektion.

## 9. Föreslagen commit-/PR-sekvens
1. Tester: bredda konsistensmatris + xfail (steg 0)
2. Extrahera `compute_day_ob_pay`-helper (förberedelse för steg 3)
3. Dagvyn: passupplösning via canonical (steg 1-2)
4. Dagvyn: OB/oncall/OT-lön via canonical (steg 3)
5. Dagvyn: frånvaro + DayPayOverride via canonical (steg 4-5)
6. Dagvyn: dokumentera formulär-only-queries, städa (steg 6)
7. (Separat PR) Månadsvyn: en datakälla (steg 7)
8. (Separat PR/issue) OT-efter-semester-fix

### Kritiska filer för implementation
- app/routes/schedule_personal.py
- app/core/schedule/period.py
- app/core/schedule/summary.py
- tests/test_day_view_consistency.py
- app/core/schedule/ob.py
