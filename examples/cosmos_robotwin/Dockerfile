ARG ROBOTWIN_BASE_IMAGE=pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel
FROM ${ROBOTWIN_BASE_IMAGE}
USER root
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG ROBOTWIN_COMMIT=ea8b21121ebb3cd201ff5b3fe361944ac94eda3f
ARG COSMOS_COMMIT=2ba852c1ff3ce923405bd47a65074488a3358781
ARG CUROBO_REF=v0.7.8
ARG MAX_JOBS=4
ENV DEBIAN_FRONTEND=noninteractive FORCE_CUDA=1 TORCH_CUDA_ARCH_LIST="7.5;8.9" \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics \
    PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates build-essential ninja-build ffmpeg \
    libvulkan1 libgl1 libegl1 libx11-6 libxext6 libglx0 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
# The pinned simulator wheels support Python 3.10/3.11, separate from the API.
RUN python -c "import sys; assert sys.version_info[:2] == (3, 11) or sys.version_info[:2] == (3, 10)"
RUN git init /opt/RoboTwin && cd /opt/RoboTwin \
    && git remote add origin https://github.com/RoboTwin-Platform/RoboTwin.git \
    && git fetch --depth 1 origin ${ROBOTWIN_COMMIT} && git checkout --detach FETCH_HEAD \
    && git -c submodule.XPolicyLab.shallow=true submodule update --init --depth 1 XPolicyLab
RUN git init /opt/cosmos-framework && cd /opt/cosmos-framework \
    && git remote add origin https://github.com/bevictory/cosmos-framework.git \
    && git fetch --depth 1 origin ${COSMOS_COMMIT} && git checkout --detach FETCH_HEAD
COPY examples/cosmos_robotwin/constraints.txt /tmp/constraints.txt
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install -c /tmp/constraints.txt -r /opt/RoboTwin/scripts/requirements.txt \
    && python -m pip install -c /tmp/constraints.txt -e /opt/RoboTwin/XPolicyLab \
    && python -m pip install -c /tmp/constraints.txt setuptools==69.5.1 'setuptools_scm>=6.2,<9' wheel ninja
RUN --mount=type=cache,target=/root/.cache/pip git clone --branch ${CUROBO_REF} --depth 1 https://github.com/NVlabs/curobo.git /opt/RoboTwin/envs/curobo \
    && python -m pip install -c /tmp/constraints.txt -e /opt/RoboTwin/envs/curobo --no-build-isolation \
    && python -m pip install -c /tmp/constraints.txt warp-lang==1.12.0
# Point-cloud sampling is optional upstream; include it for user task variants too.
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install -c /tmp/constraints.txt \
    'git+https://github.com/facebookresearch/pytorch3d.git@v0.7.8' --no-build-isolation
WORKDIR /app
COPY runner /app/runner
COPY examples/cosmos_robotwin /app/examples/cosmos_robotwin
ENV PYTHONPATH=/app COSMOS_ROOT=/opt/cosmos-framework ROBOTWIN_ROOT=/opt/RoboTwin
RUN python -m examples.cosmos_robotwin.prepare_environment \
    && for name in embodiments objects background_texture; do \
         ln -s /opt/mini-eval/assets/robotwin/3dc3b798668feb99ac61cc9086d84cbcc3d79186/$name /opt/RoboTwin/assets/$name; \
       done \
    && python -m examples.cosmos_robotwin.preflight --environment-only
CMD ["python", "-m", "runner.cs_entry"]
