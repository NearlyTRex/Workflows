FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

# Pick up Debian security fixes published since the base image was built
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt
COPY fixture_app ./fixture_app

USER 1000:1000
CMD ["python", "-c", "import fixture_app; print(fixture_app.newer('1.10.0', '1.9.0'))"]
