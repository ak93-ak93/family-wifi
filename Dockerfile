FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY family_wifi ./family_wifi
ENV FAMILY_WIFI_CONFIG=/data/config.yaml FAMILY_WIFI_STATE=/data/state.json PORT=8787
EXPOSE 8787
CMD ["python", "-m", "family_wifi"]
