FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# directory per output persistenti (opzionale, ma utile)
RUN mkdir -p /app/jobs /app/outputs

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.webapp:app", "--host", "0.0.0.0", "--port", "8000"]
