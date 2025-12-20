# 1. Basera på python:3.11-slim
FROM python:3.11-slim

# Sätt miljövariabler för att förhindra .pyc-filer och buffring
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Installera curl för healthcheck (saknas ofta i slim-images)
# Detta är nödvändigt för att krav 6 ska fungera
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 2. Installera dependencies
# OBS: Du måste generera requirements.txt från din pyproject.toml först.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 4. Skapa non-root user
# Vi gör detta innan COPY för att hantera rättigheter smidigare
RUN addgroup --system appgroup && adduser --system --group appuser

# 3. Kopiera app och data
COPY app/ ./app
COPY data/ ./data

# Sätt ägarskap till non-root användaren
RUN chown -R appuser:appgroup /app

# Byt till användaren
USER appuser

# 5. Exponera port
EXPOSE 8000

# 6. Healthcheck
# --fail flaggan gör att curl returnerar exit code != 0 om servern ger 400+ error
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# 7. Starta med uvicorn
# Anpassa "app.main:app" till var din FastAPI-instans faktiskt ligger
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]