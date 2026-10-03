# Image de l'application UrbiaBail (Django + Gunicorn).
FROM python:3.12-slim-bookworm AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libldap2-dev libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /venv
COPY requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends libldap-2.5-0 libsasl2-2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 urbiabail
COPY --from=build /venv /venv
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_MANIFEST=1
WORKDIR /app
COPY --chown=urbiabail . .
RUN SECRET_KEY=build python manage.py collectstatic --noinput \
    && mkdir /documents && chown urbiabail /documents
USER urbiabail
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 3 --access-logfile -"]
