FROM python:3.11-slim
RUN pip install --no-cache-dir websockets requests
COPY collector.py /app/collector.py
CMD ["python", "-u", "/app/collector.py"]
