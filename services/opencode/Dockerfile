FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    tar \
    && rm -rf /var/lib/apt/lists/*

COPY install.sh /tmp/opencode-install.sh
RUN bash /tmp/opencode-install.sh --no-modify-path \
    && install -m 755 /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && rm -rf /root/.opencode /tmp/opencode-install.sh

WORKDIR /home

EXPOSE 4096

CMD ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"]
