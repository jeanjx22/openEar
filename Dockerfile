FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for potential native packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create directories for data and logs
RUN mkdir -p /app/data /app/logs

# C4: HEALTHCHECK verifies the application is alive by checking the
# heartbeat file written every 60s by the scheduler. The file must
# have been modified within the last 120 seconds.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os, time; s=os.stat('/tmp/openear_heartbeat'); exit(0 if time.time()-s.st_mtime<120 else 1)"

CMD ["python", "-m", "src.main"]
