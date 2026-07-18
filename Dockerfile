# Official Playwright image — includes Chromium + all OS-level dependencies
# already installed, so no `playwright install --with-deps` / apt-get / root
# access is needed at build time (that's what was failing on Render's native
# Python buildpack). Version pinned to match requirements.txt exactly —
# Playwright refuses to run if the SDK version and browser binaries mismatch.
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render sets $PORT at runtime; default here just for local `docker run`.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT}"]
