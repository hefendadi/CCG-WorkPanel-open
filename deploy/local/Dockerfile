FROM python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY webapp/requirements-test.txt webapp/requirements-webapp.txt /tmp/
RUN python -m pip install --no-cache-dir -r /tmp/requirements-test.txt
RUN groupadd --gid 10001 demoapp && useradd --uid 10001 --gid 10001 --create-home demoapp
COPY --chown=demoapp:demoapp webapp /app/webapp
COPY --chown=demoapp:demoapp demo /app/demo
COPY --chown=demoapp:demoapp docs /app/docs
COPY --chown=demoapp:demoapp deploy/local /app/deploy/local
RUN install -d -o demoapp -g demoapp /app/webapp/data
USER demoapp
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "webapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
