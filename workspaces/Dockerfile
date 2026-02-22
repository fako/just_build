#################################################
# AGENT WORKSPACES
#################################################

FROM python:3.12

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    curl \
    less vim build-essential gettext \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN mkdir -p /var/log/supervisor \
    && mkdir -p /var/log/nginx \
    && mkdir -p /var/log/projects \
    && mkdir -p /etc/supervisor/conf.d \
    && mkdir -p /etc/nginx/conf.d

COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY supervisor/supervisord.conf /etc/supervisor/supervisord.conf

EXPOSE 7000

WORKDIR /home

CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf"]
