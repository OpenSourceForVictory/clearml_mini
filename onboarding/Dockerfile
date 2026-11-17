ARG PYTHON_BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}
WORKDIR /workspace
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
COPY onboarding/requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY onboarding/ /workspace/onboarding/
COPY scripts/ /workspace/scripts/
COPY worker/ /workspace/worker/
COPY node_agent/ /workspace/node_agent/
ENV PYTHONPATH=/workspace PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "onboarding.server", "--socket", "/run/mini-eval-managed-onboarding/control.sock", "--state", "/var/lib/mini-eval-onboarding", "--api-url", "http://api:8000"]
