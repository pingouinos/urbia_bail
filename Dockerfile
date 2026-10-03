# Image de l'application UrbiaBail (Django + Gunicorn).
FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 1000 urbiabail
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_MANIFEST=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=urbiabail . .
RUN SECRET_KEY=build python manage.py collectstatic --noinput \
    && mkdir /documents && chown urbiabail /documents
USER urbiabail
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --access-logfile -"]
