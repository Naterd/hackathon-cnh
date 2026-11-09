FROM python:3.12-slim
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION PYTHONUNBUFFERED=1
USER 1000
EXPOSE 8080
# One process per pod: the Dash0 operator's auto-instrumentation doesn't follow forked workers.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
