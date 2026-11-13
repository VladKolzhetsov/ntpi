# openJiuwen Challenge 2026 — образ харнесса.
#
# Боевой запуск (регламент):
#   docker run --read-only --tmpfs /tmp \
#       -v /host/tasks:/data/tasks \
#       -e LLM_BASE_URL=... -e LLM_API_KEY=... [-e LLM_BUDGET=...] \
#       <image>
# (команда по умолчанию: python /app/run_agent.py --tasks-dir /data/tasks)
#
# CMD без ENTRYPOINT: команда по умолчанию — регламентная, но её можно
# переопределить, не ломаясь о собственный ENTRYPOINT.
#
# Контекст сборки урезает .dockerignore (.venv, .env, experiments/, tasks/).

FROM python:3.12-slim

WORKDIR /app

# Единственная сторонняя зависимость рантайма (solutions/llm.py).
# Версии закреплены по .venv: requests 2.34.2 / urllib3 2.8.0.
RUN pip install --no-cache-dir requests==2.34.2 urllib3==2.8.0

COPY run_agent.py README.md ./
COPY solutions ./solutions

# Чистая ФС (--read-only): не писать .pyc и не буферизовать логи.
# SSL-верификация прокси отключена по умолчанию (переопределяется -e).
# LLM_BUDGET: организаторы бюджет не передают; без него отключаются
# MIN_VIABLE-гейт и триаж-отсечение (remaining=None), и один сбойный
# coding-цикл может тянуться до дедлайна. Переопределяется -e.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLM_SSL_VERIFY=false \
    LLM_BUDGET=5000

CMD ["python", "/app/run_agent.py", "--tasks-dir", "/data/tasks"]
