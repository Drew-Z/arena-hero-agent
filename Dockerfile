FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin arena

COPY pyproject.toml README.md LICENSE ./
COPY arena_farmer.py arena_health.py arena_optimizer.py arena_supervisor.py arena_version_monitor.py ./
RUN python -m pip install --no-cache-dir .

COPY docker/entrypoint.sh /usr/local/bin/arena-hero-entrypoint
RUN chmod 0755 /usr/local/bin/arena-hero-entrypoint

USER 10001:10001
ENTRYPOINT ["arena-hero-entrypoint"]
CMD ["arena-hero-agent", "--worker-target", "12", "--beacon-policy", "retreat", "--no-compatibility-marker"]
