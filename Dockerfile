FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin arena

COPY pyproject.toml requirements-build.lock requirements.lock README.md LICENSE ./
COPY arena_farmer.py arena_dashboard.py arena_health.py arena_history.py arena_optimizer.py arena_supervisor.py arena_version_monitor.py ./
COPY dashboard ./dashboard
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-build.lock \
    && python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && python -m pip install --no-cache-dir --no-deps --no-build-isolation . \
    && python -m pip check

USER 1000:1000
