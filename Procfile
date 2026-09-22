web: python manage.py migrate --noinput && gunicorn myproject.wsgi --bind 0.0.0.0:${PORT:-8000} --log-file -
