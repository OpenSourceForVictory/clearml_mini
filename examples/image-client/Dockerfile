# Offline example base already includes Python; deliberately remove bundled runner.
FROM mini-eval-client-base:1
USER root
RUN rm -rf /app/runner /workspace/runner
WORKDIR /app
COPY examples/image-client/client.py /app/image_check.py
# Platform overrides even an image's unrelated default entrypoint.
ENTRYPOINT ["/bin/false"]
