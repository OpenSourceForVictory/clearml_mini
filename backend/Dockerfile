ARG NODE_BASE_IMAGE=m.daocloud.io/docker.io/library/node:24-slim
ARG PYTHON_BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.11-slim
FROM ${NODE_BASE_IMAGE} AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_BASE_IMAGE}
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /workspace
COPY backend/requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /workspace/backend/
COPY runner/ /workspace/runner/
COPY worker/ /workspace/worker/
COPY scripts/ /workspace/scripts/
COPY --from=frontend /app/dist /workspace/frontend/dist
ENV PYTHONPATH=/workspace
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
