# syntax=docker/dockerfile:1
#
# Multi-stage build:
#   deps    -> installs runtime dependencies once, reused by later stages
#   test    -> installs dev dependencies and runs the test suite; the build
#              FAILS here if any test fails, so a broken image can never be
#              produced or pushed
#   runtime -> minimal, non-root final image actually shipped

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

FROM base AS deps
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM base AS test
COPY --from=deps /install /usr/local
COPY requirements.txt requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini .
COPY waypoint/ ./waypoint/
COPY tests/ ./tests/
RUN python -m pytest -q

FROM base AS runtime
COPY --from=deps /install /usr/local

# Fixed, predictable UID/GID so a bind-mounted ./data host directory can be
# chowned to match (see README's "Persistence" section).
RUN groupadd --gid 1000 waypoint \
    && useradd --uid 1000 --gid waypoint --no-create-home --shell /usr/sbin/nologin waypoint \
    && mkdir -p /data \
    && chown waypoint:waypoint /data

# Depending on the test stage means `docker build` runs the test suite as
# part of every build, even though none of its layers end up in the final
# image (the runtime stage never COPYs --from=test).
COPY --from=test /app/waypoint /app/waypoint

USER waypoint
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"]

CMD ["python", "-m", "waypoint"]
