# syntax=docker/dockerfile:1.4
#################################################
# MANAGEMENT
#################################################

FROM python:3.12

# The credentials app shells out to pass and gpg. They are installed so the containerised mode is
# complete, but the credentials workflow runs on the host, where the GPG agent already lives.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    pass \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# management/invoke.yml is a symlink to the repository root invoke.yml and is copied as one, so the
# container resolves it at /invoke.yml. That target lives outside this build context and is supplied
# by the compose bind mount; without it web.environment resolves no settings at all.
COPY . /management

WORKDIR /management

EXPOSE 8000

CMD ["uvicorn", "web.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--reload"]
