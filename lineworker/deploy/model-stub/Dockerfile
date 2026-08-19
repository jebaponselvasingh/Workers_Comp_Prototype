# The e2e profile's stand-in for Ollama (Story 6.1, AD-15).
#
# Deliberately not built from `server/`'s image or its lockfile: this container
# must be buildable and runnable with no relationship to the application, so
# that it can never accidentally import server code and so that a change to the
# server's dependency set cannot change what the stub answers.
#
# `pip install` rather than `uv sync` for the same reason — there is no project
# here, only a script.
#
# **Exact pins, including the transitive set.** AD-15 sells this container on
# reproducibility: the same commit must produce the same scripted answers on
# every CI run, and `fastapi>=0.115,<1.0` does not give that — a release inside
# the range changes what is installed between two builds of an unchanged tree,
# which is the one property the e2e gate is not allowed to lose. Pinning only
# the two direct packages would leave the same hole one level down (starlette
# and pydantic are where request parsing actually lives), so the resolved set
# is written out. Refresh it deliberately, by re-resolving and editing here.
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
      "fastapi==0.141.1" \
      "uvicorn==0.52.4" \
      "starlette==1.6.0" \
      "pydantic==2.13.4" \
      "annotated-types==0.8.0" \
      "anyio==4.14.2" \
      "click==8.4.2" \
      "h11==0.16.0" \
      "idna==3.19"

COPY app.py ./

# 11434 is Ollama's own port, so the e2e api container's OLLAMA_BASE_URL differs
# from the dev one only in host name. Never published to the host: like every
# other service but nginx, this is reachable on the compose network alone.
EXPOSE 11434
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "11434"]
