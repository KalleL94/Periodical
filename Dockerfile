# -------------------------------------------------------------------
# STAGE 1: Base
# Gemensam grund för alla stages.
# -------------------------------------------------------------------
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Installera systemberoenden som behövs överallt (curl för healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Skapa användare tidigt
RUN addgroup --system appgroup && adduser --system --group appuser

# -------------------------------------------------------------------
# STAGE 2: Builder
# Här installerar vi allt. Denna stage slängs bort i slutändan,
# så vi kan installera gcc och annat tungt utan att det hamnar i prod.
# -------------------------------------------------------------------
FROM base AS builder

# Installera byggverktyg (om du har libs som kräver kompilering)
RUN apt-get update && apt-get install -y --no-install-recommends build-essential

COPY requirements.txt .
# Vi installerar i en venv för att enkelt kunna kopiera hela miljön senare
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# -------------------------------------------------------------------
# STAGE 3: Development
# Detta är vad du kör lokalt.
# -------------------------------------------------------------------
FROM base AS dev

# Kopiera venv från builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Skapa directories som appen behöver skriva till
RUN mkdir -p logs && chown -R appuser:appgroup logs

# I dev vill vi ofta vara root för att kunna installera debug-verktyg "on the fly",
# men att köra som appuser är bättre praxis. Vi stannar som appuser här.
USER appuser

# Kopiera koden (men docker-compose volumes kommer oftast överskugga detta i dev)
COPY --chown=appuser:appgroup . .

# Dev-kommando med reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# -------------------------------------------------------------------
# STAGE 4: Production
# Denna image blir minimal och säker.
# -------------------------------------------------------------------
FROM base AS prod

# Kopiera BARA venv (dependencies) från builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Kopiera koden
COPY --chown=appuser:appgroup app/ ./app
COPY --chown=appuser:appgroup data/ ./data

# Skapa directories som appen behöver skriva till
RUN mkdir -p logs && chown -R appuser:appgroup logs

USER appuser

EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Prod-kommando (utan reload, optimerat)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
