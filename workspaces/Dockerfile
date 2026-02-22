#################################################
# AGENT WORKSPACES
#################################################

FROM python:3.12

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    curl \
    less vim build-essential gettext \
    openssh-server \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/run/sshd \
    && mkdir -p /etc/ssh/authorized_keys

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN mkdir -p /var/log/supervisor \
    && mkdir -p /var/log/nginx \
    && mkdir -p /var/log/projects \
    && mkdir -p /etc/supervisor/conf.d \
    && mkdir -p /etc/nginx/conf.d

COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY ssh/sshd_config /etc/ssh/sshd_config
COPY ssh/keys/ssh_host_ed25519_key ssh/keys/ssh_host_ed25519_key.pub /etc/ssh/
COPY ssh/keys/ssh_host_rsa_key ssh/keys/ssh_host_rsa_key.pub /etc/ssh/
RUN chmod 600 /etc/ssh/ssh_host_*_key && chmod 644 /etc/ssh/ssh_host_*_key.pub

EXPOSE 22 7000

WORKDIR /home

CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf"]
