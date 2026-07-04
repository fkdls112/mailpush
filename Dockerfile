FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash mailpush

WORKDIR /home/mailpush

# Install Python dependencies
RUN pip install --no-cache-dir \
    aioimaplib>=1.0.0 \
    "fastapi>=0.100.0" \
    "uvicorn[standard]>=0.23.0" \
    "pydantic>=2.0.0" \
    "aiohttp>=3.8.0" \
    "pyyaml>=6.0"

# Copy application code
COPY --chown=mailpush:mailpush mailpush/ ./mailpush/

# Copy entrypoint
COPY --chown=mailpush:mailpush docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /home/mailpush/.config/mailpush && \
    chown mailpush:mailpush /home/mailpush/.config/mailpush

USER mailpush
ENV PYTHONPATH="/home/mailpush:$PYTHONPATH"
ENV PATH="/home/mailpush/.local/bin:$PATH"

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f -s http://localhost:8080/api/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
