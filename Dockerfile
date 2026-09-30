FROM python:3.13-alpine
WORKDIR /app
COPY app.py protocol.py ./
COPY static ./static
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CONFIG_PATH=/data/config.json WEB_PORT=8080
EXPOSE 8080/tcp 40003/udp
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"
CMD ["python", "app.py"]
