FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 设置工作目录
WORKDIR /app

# 将 requirements.txt 复制到工作目录
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# 将主应用脚本复制到工作目录
COPY app.py . 

# 将模板文件复制到容器内的 templates 目录
COPY templates/ /app/templates/

# 将静态文件复制到容器内的 static 目录
COPY static/ /app/static/

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; port=os.environ.get('PORT', '5000'); opener=urllib.request.build_opener(urllib.request.ProxyHandler({})); opener.open(f'http://127.0.0.1:{port}/healthz', timeout=4)"

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "app.py"]
