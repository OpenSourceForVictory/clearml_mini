ARG PYTHON_BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /workspace
COPY backend/requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /workspace/backend/
COPY runner/ /workspace/runner/
COPY scripts/ /workspace/scripts/
ENV PYTHONPATH=/workspace
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
