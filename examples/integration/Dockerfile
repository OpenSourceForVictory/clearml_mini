FROM python:3.11-slim
WORKDIR /app
COPY runner /app/runner
COPY *.py /app/
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
CMD ["python", "-m", "runner.cs_entry"]
