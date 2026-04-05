FROM python:3.11-slim

LABEL maintainer="Tobias Terzer"
LABEL description="Spooly Bridge — Verbindet Klipper/Moonraker mit Spooly"

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# Konfiguration wird ueber Umgebungsvariablen oder Mountpoints bereitgestellt
# Beispiel: docker run -e SPOOLY_KEY=spooly_br_xxx spooly-bridge
ENV SPOOLY_KEY=""
ENV MOONRAKER_URL="http://host.docker.internal:7125"
ENV SPOOLY_URL="https://api.spooly.eu/api"
ENV POLL_INTERVALL="60"

CMD ["sh", "-c", "spooly-bridge --key ${SPOOLY_KEY} --moonraker-url ${MOONRAKER_URL} --spooly-url ${SPOOLY_URL} --intervall ${POLL_INTERVALL}"]
