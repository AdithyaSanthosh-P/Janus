# Janus (Samsung PRISM Theme 05) — this is also the first real verification
# that the codebase actually runs on the declared 3.10-3.12 range (the dev
# environment this project was built in only has Python 3.14 available).
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY tests ./tests
# Evaluation-kit files the test suite imports (tests/test_kit_*.py drive the
# kit's own harness/scorer and eval_submission.py stages 1-2 against agent/).
COPY harness ./harness
COPY agent ./agent
COPY eval_submission.py submission.yaml ./

RUN pip install --no-cache-dir .[dev]

# Default: run the full test suite — this is what "docker build . && docker
# run <image>" is expected to do per docs/prototype_version_plan.md's V4
# test gate ("Docker image builds, runs public suite from clean checkout").
CMD ["python", "-m", "pytest", "tests/", "-v"]
