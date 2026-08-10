FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin arena

COPY pyproject.toml requirements-build.lock requirements.lock README.md LICENSE ./
COPY arena_farmer.py arena_dashboard.py arena_health.py arena_history.py arena_optimizer.py arena_supervisor.py arena_version_monitor.py ./
COPY dashboard ./dashboard
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-build.lock \
    && python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && python -m pip install --no-cache-dir --no-deps --no-build-isolation . \
    && python -m pip check

COPY docker/entrypoint.sh /usr/local/bin/arena-hero-entrypoint
RUN chmod 0755 /usr/local/bin/arena-hero-entrypoint

USER 10001:10001
ENTRYPOINT ["arena-hero-entrypoint"]
CMD ["arena-hero-agent", "--worker-target", "18", "--beacon-policy", "pursue", "--history-db", "/data/history.sqlite3", "--no-compatibility-marker"]
