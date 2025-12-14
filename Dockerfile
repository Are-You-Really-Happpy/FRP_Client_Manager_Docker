FROM python:3.11-slim

WORKDIR /app

# 1. Install curl and tar for downloading
RUN apt-get update && apt-get install -y curl tar && rm -rf /var/lib/apt/lists/*

# 2. Set FRP Version
ARG FRP_VERSION=0.65.0

# 3. Auto-detect architecture and download corresponding frpc
RUN ARCH=$(uname -m) && \
    case $ARCH in \
        x86_64) FRP_ARCH="amd64" ;; \
        aarch64) FRP_ARCH="arm64" ;; \
        *) echo "Unsupported architecture: $ARCH"; exit 1 ;; \
    esac && \
    echo "Downloading frp v${FRP_VERSION} for ${FRP_ARCH}..." && \
    curl -L -o frp.tar.gz "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${FRP_ARCH}.tar.gz" && \
    tar -xzf frp.tar.gz && \
    mv frp_${FRP_VERSION}_linux_${FRP_ARCH}/frpc /app/frpc && \
    mv frp_${FRP_VERSION}_linux_${FRP_ARCH}/frpc.toml /app/frpc.toml && \
    rm -rf frp.tar.gz frp_${FRP_VERSION}_linux_${FRP_ARCH} && \
    chmod +x /app/frpc

# Copy manager
COPY manager /app/manager

# Install dependencies
WORKDIR /app/manager
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple

EXPOSE 8000

CMD ["python", "main.py"]