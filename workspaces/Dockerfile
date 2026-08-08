# syntax=docker/dockerfile:1.4
#################################################
# AGENT WORKSPACES
#################################################

FROM python:3.12

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    nginx \
    supervisor \
    curl \
    tar \
    ripgrep \
    postgresql-client \
    redis-tools \
    acl \
    less vim build-essential gettext \
    openssh-server \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

COPY --from=opencode install.sh /tmp/opencode-install.sh
RUN bash /tmp/opencode-install.sh --no-modify-path \
    && install -m 755 /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && rm -rf /root/.opencode /tmp/opencode-install.sh

RUN curl -fsSL https://claude.ai/install.sh | bash \
    && install -m 755 "$(readlink -f /root/.local/bin/claude)" /usr/local/bin/claude \
    && rm -rf /root/.local /root/.claude /root/.cache/claude

RUN mkdir -p /var/run/sshd \
    && mkdir -p /etc/ssh/authorized_keys \
    && chown root:root /etc/ssh/authorized_keys \
    && chmod 755 /etc/ssh/authorized_keys

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN mkdir -p /var/log/supervisor \
    && mkdir -p /var/log/nginx \
    && mkdir -p /var/log/workspaces \
    && mkdir -p /etc/supervisor/conf.d \
    && mkdir -p /etc/nginx/conf.d \
    && mkdir -p /workspaces/state

COPY container/docker-entrypoint.sh /usr/local/bin/workspaces-entrypoint
COPY container/load_user_passwords.sh \
     container/load_supervisor_secret.sh container/create_log_directories.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/workspaces-entrypoint \
    /usr/local/bin/load_user_passwords.sh \
    /usr/local/bin/load_supervisor_secret.sh \
    /usr/local/bin/create_log_directories.sh

COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY supervisor/supervisord.conf.template /etc/supervisor/templates/supervisord.conf.template
COPY ssh/sshd_config /etc/ssh/sshd_config
COPY ssh/keys/ssh_host_ed25519_key ssh/keys/ssh_host_ed25519_key.pub /etc/ssh/
COPY ssh/keys/ssh_host_rsa_key ssh/keys/ssh_host_rsa_key.pub /etc/ssh/
RUN chmod 600 /etc/ssh/ssh_host_*_key && chmod 644 /etc/ssh/ssh_host_*_key.pub

EXPOSE 22 7000

WORKDIR /home

ENTRYPOINT ["/usr/local/bin/workspaces-entrypoint"]
CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf"]
