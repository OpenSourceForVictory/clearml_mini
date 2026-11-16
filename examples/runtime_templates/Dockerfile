ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}
COPY runner /opt/mini-eval/runner
COPY examples/runtime_templates/*.py /app/
ENV PYTHONPATH=/opt/mini-eval
WORKDIR /app
