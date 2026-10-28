ARG PYTHON_BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}
WORKDIR /app
COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app app
ENV PYTHONPATH=/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
