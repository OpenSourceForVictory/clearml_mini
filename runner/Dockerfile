FROM python:3.11-slim
WORKDIR /workspace
COPY runner/requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY runner/ /workspace/runner/
ENTRYPOINT ["python", "-m", "runner.mock_eval"]
