FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

# Pick up Debian security fixes published since the base image was built
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
# pip isn't needed at runtime, and it vendors its own copies of libraries such as
# urllib3, which the image scan would otherwise report against the app
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt \
    && pip uninstall --yes pip
COPY fixture_app ./fixture_app

USER 1000:1000
CMD ["python", "-c", "import fixture_app; print(fixture_app.newer('1.10.0', '1.9.0'))"]
