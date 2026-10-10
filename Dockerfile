FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
ENV VMS_DATA_DIR=/data
VOLUME /data
EXPOSE 8081
CMD ["python", "-m", "app.server"]
