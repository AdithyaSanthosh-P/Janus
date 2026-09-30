# Janus (Samsung PRISM Theme 05) — this is also the first real verification
# that the codebase actually runs on the declared 3.10-3.12 range (the dev
# environment this project was built in only has Python 3.14 available).
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY tests ./tests
COPY scripts ./scripts

RUN pip install --no-cache-dir .[dev]

# Default: run the full test suite — this is what "docker build . && docker
# run <image>" is expected to do per docs/prototype_version_plan.md's V4
# test gate ("Docker image builds, runs public suite from clean checkout").
CMD ["python", "-m", "pytest", "tests/", "-v"]
