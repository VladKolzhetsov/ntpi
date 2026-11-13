# Инструкция по запуску проекта в docker-контейнере

Быстрая проверка

# 0) В .env указать свой API_KEY, затем **в корне проекта выполнить последовательно:**  

# 1) сборка образа
```
docker build -t hack:latest .
```
# 2) копия 4 тренировочных задач (набор не портим) + права на запись
```
rm -rf /tmp/check-tasks && mkdir -p /tmp/check-tasks
cp -r tasks/task-dir/task-dir/{search_0001,math_0000,data_analysis_0002,coding_0003} \
      /tmp/check-tasks/
rm -f /tmp/check-tasks/*/answer.txt /tmp/check-tasks/*/workspace/answer.txt
chmod -R 777 /tmp/check-tasks
```

# 3) прогон с флагами организаторов
```
docker run --rm --name run-check \
  --env-file .env \
  -v /tmp/check-tasks:/data/tasks:rw \
  --read-only --tmpfs /tmp:rw,size=2g -e HOME=/tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --cpus 8 --memory 16g --pids-limit 512 \
  --workdir /app --entrypoint python \
  hack:latest /app/run_agent.py --tasks-dir /data/tasks
```

# 4) ответы должны лежать на хосте
```
cat /tmp/check-tasks/*/answer.txt
```

Ожидаемо: решено (answer.txt): 4/4, 11 баллов, ~500 экв. за ~15 с, и вывод DONE 158 389 1983.
Что здесь важно
- --env-file .env — ключевой момент. В .env ключи называются API_BASE/API_KEY, а клиент умеет их fallback'ом читать. Если вместо этого писать -e LLM_BASE_URL="$API_BASE" в терминале без экспортированного .env, переменная уедет пустой → падение с кодом 4.
- chmod -R 777 /tmp/check-tasks обязателен: с --cap-drop ALL контейнер без CAP_DAC_OVERRIDE не пишет в каталог чужого uid (иначе чистый выход с кодом 3 на старте).

**К этому моменту уже всё должно работать как задумано**

Вариант с явными переменными
Если хотите передавать боевые имена LLM_*:
```
set -a && . ./.env && set +a          # экспортировать .env в текущую оболочку
```
```
echo "${#API_KEY}"                    # не должен быть 0
```
```
docker run --rm \
  -e LLM_BASE_URL="$API_BASE" -e LLM_API_KEY="$API_KEY" \
  -v /tmp/check-tasks:/data/tasks:rw \
  --read-only --tmpfs /tmp:rw,size=2g -e HOME=/tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --workdir /app --entrypoint python \
  hack:latest /app/run_agent.py --tasks-dir /data/tasks
```

Дополнительно
# сохранить отчёт на хосте (организаторы $OUT не монтируют)
```
mkdir -p /tmp/check-out && chmod 777 /tmp/check-out
#   ...добавьте к команде: -v /tmp/check-out:/out:rw -e OUT=/out
cat /tmp/check-out/run_report.json
```

# проверить, что ключ доехал до контейнера
```
docker run --rm --env-file .env --entrypoint sh hack:latest -c 'echo ${#API_KEY}'
```

# больше задач (первые 8 по порядку триажа)
#   ...замените хвост команды на: /app/run_agent.py --tasks-dir /data/tasks --limit 8

# 0 токенов: только порядок триажа
```
docker run --rm --env-file .env -v /tmp/check-tasks:/data/tasks \
  --entrypoint python hack:latest /app/run_agent.py --tasks-dir /data/tasks --dry-run
```


# openJiuwen Challenge 2026 — токен-эффективный харнесс

Учебный проект по [openJiuwen Core](https://openjiuwen.com) (Python SDK для LLM-агентов)
и одновременно решение квалификационного задания хакатона **openJiuwen Challenge 2026**:
харнесс, который решает 100 задач (search / math / data_analysis / coding)
в рамках бюджета токенов.

## Идея решения

Основной принцип — **максимум детерминизма, LLM только там, где без неё нельзя**.
Токен-бюджет тратится лишь на «компиляцию» вопроса в исполняемый артефакт (план/код),
всё остальное — бесплатные детерминированные шаги.

Общий каркас одинаков для всех типов задач:

```
вопрос → [LLM: компиляция в артефакт] → [детерминированный executor] → answer.txt
                ↓ ошибка / неоднозначность
             fallback-лестница
```

| Тип | Артефакт плана | Executor | Контекст для LLM |
|---|---|---|---|
| search | DSL `find` / `get` / `count` | обход графа `KnowledgeBase` | `kb.schema()` |
| math | python-программа | `python3` в workspace | — (арифметика) |
| data_analysis | python-программа | `python3` + stdlib `csv` | схема CSV |
| coding | исправленный код | прогон тестов + фидбэк ошибок | файлы/тесты |

Ключевые решения:

- **Против RAG**: база знаний мала, входные токены в 10 раз дешевле выходных,
  embeddings недоступны. Вместо векторного поиска — разбор карточек в dict
  и `resolve()` по пересечению имён (мини-граф без networkx).
- **Один generic-класс `Node`** (тип сущности — label из имени файла),
  а не классы под каждый тип.
- **Планировщик не получает список сущностей** — только схему полей и вопрос
  (размер промпта O(1) от размера базы); связывание сущностей детерминированное.
- **math и data_analysis — один код-планировщик** «NL → python» (data_analysis
  лишь добавляет схему CSV); **coding** — ограниченный цикл ремонта
  (фикс → тесты → ошибки → фикс) с капом итераций по бюджету.
- **Диспетчер тратит бюджет «по плотности»**: порядок задач задаётся
  отношением баллов к ожидаемой стоимости типа, каждая задача получает
  пер-задачный envelope, а при дефиците хвост отпадает без трат
  (см. «Как диспетчер расходует бюджет»).
- Невыполнимые задачи (по регламенту 15–25%) детектируются флагом
  `unanswerable` в плане.

## Способ оценки расхода токенов

Бюджет задаётся в **выходных токенах**. Учёт ведёт единый `BudgetTracker`
(`solutions/llm.py`), через который проходят все вызовы LLM:

```
расход = completion_tokens + prompt_tokens / 10
```

- reasoning-токены считаются выходными;
- кеш провайдера не учитывается — все входные токены считаются полностью;
- стоимость задачи известна заранее по типу (измерено на живом прокси):
  search ≈ 115–135 output-экв. (один вызов планировщика, одно-/двуххоповый
  вопрос), math ≈ 60, data_analysis ≈ 70, coding — переменная
  (растёт с числом итераций ремонта); fallback-шаги тоже оценены.

Перед каждым вызовом проверяется мягкий лимит (`can_spend()`), поэтому харнесс
сам останавливает дорогие ветки, не дожидаясь, пока прокси перестанет отвечать.
Сводка расхода доступна через `tracker.snapshot()` и пишется в лог прогона.

### Как диспетчер расходует бюджет (`solutions/runner.py`)

Прогон 100 задач в бюджете — это рюкзак: максимум суммы баллов при
ограничении по output-эквивалентам. Так как баллы (`reward`) в регламенте
случайны относительно сложности, а стоимость типа известна из живых замеров,
жадное приближение делается **до первого вызова LLM** (0 токенов):

```
priority = reward / EXPECTED_COST[type]      # плотность «баллы на эквивалент»
```

| Тип | `EXPECTED_COST` (экв.) | `MIN_VIABLE` | Обоснование |
|---|---|---|---|
| search | 140 | 110 | один вызов планировщика (114–141 экв.) или fallback-QA |
| math | 80 | 45 | одна трансляция в код (47–79 экв.) |
| data_analysis | 260 | 90 | код + profile в промпте (119–442 экв.) |
| coding | 460 | 140 | полный модуль в ответе (155–584 экв.) |

Механика:

- **Порядок обхода** — по убыванию плотности (при равенстве — дешевле первым,
  затем по id). Если бюджета не хватит, потеряем наименее выгодные задачи,
  а не случайные.
- **Пер-задачный envelope** — `EXPECTED_COST[type] × 3`, ограниченный остатком.
  Солвер получает его как бюджет своего `BudgetTracker` (`ScopedBudget`),
  зеркалящий расход в глобальный: при превышении `LLMClient` сам отказывает
  (`LLMBudgetError`), и солвер штатно сворачивается в `failed`. Одна задача
  физически не может выжечь весь общий бюджет (защита от спирали ремонтов
  в coding и от дорогой fallback-ветки).
- **Worst-case границы (по умолчанию — search)**. Солвер может опубликовать
  `cost_worst(task)` — детерминированную верхнюю границу полной лестницы
  (план + ремонт + fallback), вычислимую до первого вызова (0 токенов).
  Тогда envelope задачи = сырой границе, а для **порядка** она нормируется
  на профиль типа: `EXPECTED_COST × worst / median(worst типа)`. Нормировка
  обязательна: граница (~595) и ожидание (80/260/460) несопоставимы —
  сырые значения задвигали бы search в хвост (при B=5000 симуляция давала
  0 решённых search против 8). Порядок внутри типа не меняется — эффект
  подтверждён однотипным A/B (+6.7% эффективности). Флаг
  `--worst-case [TYPES]`: по умолчанию `search`, без значения — все типы,
  `none` — выключить.
- **Дефицит** — перед задачей проверяется остаток: меньше `MIN_VIABLE[type]` →
  `skipped`, `answer.txt` не создаётся, токенов не тратится.
- **Дедлайн** — независимо от бюджета (`--time-limit`, по умолчанию 3600 с).

Пример на тренировочном наборе (8 задач, 20 баллов, живой прокси):

| Бюджет | Решено | Баллы | Расход |
|---|---|---|---|
| 5000 | 8/8 (все verified) | 20/20 | 988.7 экв. |
| 600 | 5/8 (хвост `skipped`) | 15/20 | 537.1 экв. |

Во втором прогоне отрезаны именно низкоплотные задачи (`search_0001` 1/140
и оба coding'а 2/460) — без единого потраченного токена.

Отчёт прогона: `<OUT>/run_report.json` (сводка + задачи) и
`<OUT>/run_log.jsonl` (строка на задачу сразу по её завершении — регламент
требует сохранять решение после каждой задачи).

## Структура

| Путь | Назначение |
|---|---|
| `run_agent.py` | **Точка входа регламента**: `python /app/run_agent.py --tasks-dir /data/tasks`. Аргументы, окружение, сводка. |
| `solutions/runner.py` | **Диспетчер**: тип задачи → солвер нужной версии, общий бюджет, триаж по плотности, пер-задачные envelope, дедлайн, отчёт. |
| `solutions/llm.py` | LLM-клиент (OpenAI-совместимый /chat/completions) + центральный учёт токенов; opportunistic json-режим (`response_format`, авто-откат при HTTP 400). |
| `solutions/planning.py` | Общий каркас компиляции: дисциплинированный вызов LLM + извлечение JSON из ответа. |
| `solutions/task_parser.py` | Парсинг `task.json` (вопрос, тип, проверки, workspace). |
| `solutions/question.py` | Извлечение чистого вопроса из инструкции задачи. |
| `solutions/base.py` | Общий каркас солверов: контракт `SolveResult`, статусы, запись `answer.txt` в обе позиции. |
| `solutions/math/` | Math-солверы (v1 «в лоб», v2 код-планировщик) с таблицей версий — подробности в `solutions/math/README.md`. |
| `solutions/data_analysis/` | Data-analysis-солверы: precheck (0 токенов), v1 «в лоб», v2 код-планировщик — подробности в `solutions/data_analysis/README.md`. |
| `solutions/coding/` | Coding-солверы: v1 «в лоб», v2 прегон тестов + ремонтный цикл — подробности в `solutions/coding/README.md`. |
| `solutions/search/` | Search-пайплайн: `v2/` (kb / planner / executor / solver / complexity с cost_worst; `v1/` — прежняя) — подробное описание в `solutions/search/README.md`. |
| `solutions/tests/` | Юнит-тесты (сейчас 444/444 зелёных, без сети). |
| `solutions/calllog.py` | Логирование LLM-вызовов в JSONL для экспериментов (off by default — боевой прогон не пишет ни байта). |
| `experiments/run_experiment.py` | **Скрипт эксперимента**: серия прогонов с включённым логгером (`--repeat N` — повторы из-за недетерминизма прокси); всё в одном каталоге серии (`experiments/runs/`, в .gitignore). |
| `experiments/compare.py` | **Сравнение серий**: таблицы медиан/шума по типам и задачам + рекомендации против `EXPECTED_COST`/`MIN_VIABLE` (без сети и токенов; константы правим вручную). |
| `Dockerfile` + `.dockerignore` | Боевой образ харнесса (см. «Запуск в Docker»); `.dockerignore` не пускает в context `.venv`, `.env`, `experiments/`, `tasks/`. |

Вспомогательные и учебные файлы:

| Файл | Что показывает |
|---|---|
| `01_workflow_agent.py` | WorkflowAgent — фиксированный граф Start → LLM → End. |
| `02_react_agent.py` | ReActAgent с инструментами (`@tool`). |
| `03_mcp_agent.py` + `mcp_server_demo.py` | MCP-клиент (stdio) поверх ReActAgent. |
| `04_openJiuwen_registration_agent.py` | Регистрация команды и heartbeat на сервере организаторов. |
| `collect_harness_report.py` | Harness-отчёт о среде агента (env + дерево процессов). |
| `AGENTS.md` | Полный контекст проекта и текущий статус для AI-агента. |

## Запуск

```bash
# окружение уже создано в .venv
# 1) настрой .env (API_BASE, API_KEY, MODEL_NAME; MODEL_PROVIDER=openai для OpenAI-совместимых API)

# 2) тесты харнесса (без сети)
.venv/bin/python -m unittest discover -s solutions -t .
# 3) живой smoke-тест LLM-клиента (покажет usage и расход)
.venv/bin/python solutions/llm.py
# 3a) живой e2e search-пайплайна на тренировочных задачах
.venv/bin/python -m solutions.search.v2.solver

# 3b) эксперимент с логированием LLM-вызовов (промпт/рассуждения/выход/usage)
.venv/bin/python experiments/run_experiment.py --only math_0000,search_0001
# -> experiments/runs/<stamp>-<метка>/{series.json,r1/}, песочница рядом
# 3c) сравнение серий: медианы по типам/задачам, рекомендации по константам
.venv/bin/python experiments/compare.py experiments/runs/<каталог-серии>

# 4) учебные примеры
.venv/bin/python 01_workflow_agent.py
.venv/bin/python 02_react_agent.py
.venv/bin/python 03_mcp_agent.py
```

Всё запускается из корня проекта через `.venv/bin/python` — глобальный python
и sudo не используются.

## Запуск в Docker

Боевой образ харнесса (`Dockerfile` в корне). Единственная сторонняя
зависимость рантайма — `requests` (запинена вместе с `urllib3` по `.venv`);
всё остальное — stdlib, поэтому базой взят `python:3.12-slim`.

```bash
# 1) сборка (контекст урезает .dockerignore; .env/team.json в образ не попадают)
docker build -t ojc-harness .

# 2) боевой прогон: read-only root + tmpfs /tmp, задачи — примонтированный
#    каталог (ответы пишутся в него в обе позиции: <task>/answer.txt и
#    <task>/workspace/answer.txt)
docker run --rm --read-only --tmpfs /tmp \
    -v /host/tasks:/data/tasks \
    -e LLM_BASE_URL="$LLM_BASE_URL" \
    -e LLM_API_KEY="$LLM_API_KEY" \
    -e LLM_BUDGET=5000 \
    ojc-harness
```

Команда по умолчанию — регламентная (`python /app/run_agent.py
--tasks-dir /data/tasks`), её можно переопределить или дополнить флагами:

```bash
docker run --rm --read-only --tmpfs /tmp \
    -v /host/tasks:/data/tasks -e LLM_BASE_URL=... -e LLM_API_KEY=... \
    ojc-harness python /app/run_agent.py --tasks-dir /data/tasks --dry-run
```

Механика и особенности:

- **`CMD` без `ENTRYPOINT`** — так работают и запуск «как есть», и полное
  переопределение команды (с `ENTRYPOINT` второй вариант сломался бы).
- **Окружение задаётся через `-e`**: `LLM_BASE_URL` / `LLM_API_KEY`
  (обязательны), `LLM_BUDGET`, `TASKS` / `OUT` (по умолчанию `/data/tasks` и
  `/tmp/run_agent_out`), `LLM_SSL_VERIFY` (`false` выставлен в образе).
  `.env` в образ не попадает; при локальной разработке в нём используются
  dev-имена `API_BASE`/`API_KEY` — для прогона их маппят на
  `LLM_BASE_URL`/`LLM_API_KEY`.
- **`LLM_BUDGET=5000` выставлен в образе**: если бюджет не передан, `remaining`
  = `None` и отключаются `MIN_VIABLE`-гейт с триаж-отсечением — один сбойный
  coding-цикл тянулся бы до дедлайна. Переопределяется через `-e`.
- **`--read-only` не мешает**: ответы идут в примонтированный `/data/tasks`,
  отчёты (`run_report.json`, `run_log.jsonl`) — в `/tmp/run_agent_out`
  (tmpfs), `PYTHONDONTWRITEBYTECODE=1` запрещает запись байткода в `/app`.
- **Бюджет и дедлайн**: `LLM_BUDGET` — в output-эквивалентах; дедлайн по
  умолчанию 3600 с (регламент ~1 час), отчёты пишутся по задаче сразу, так
  что решения переживают остановку контейнера.
- **Права на `$TASKS` — критичны**: с `--cap-drop ALL` контейнер лишён
  `CAP_DAC_OVERRIDE`, поэтому root подчиняется обычным правам файлов и **не
  может писать в каталог, принадлежащий другому uid**, даже при `-v
  "$TASKS:/data/tasks:rw"`. Нужен `chmod -R 777` на каталог задач (как
  организаторы делают для `$OUT`). Харнесс проверяет это **до первого
  вызова LLM** и падает с кодом 3 и внятным сообщением, вместо часа работы
  с нулём записанных ответов.
- **Ответ без файла ≠ решённая задача**: если обе позиции `answer.txt`
  записать не удалось, задача помечается `failed`, а не `answered` — отчёт
  всегда совпадает с тем, что видит проверяющий.

Замеры образа: сборка **86 с**, размер **196 МБ** (лимиты регламента —
1000 с и 4 ГБ); smoke-прогон на тренировке в `--read-only` (4 задачи разных
типов) — **4/4 verified за ~520 экв.**

### Команда проверки организаторов

Проверка запускает контейнер так (`$TASKS` — каталог задач, `$OUT` —
каталог для отчётов на хосте):

```bash
mkdir -p "$OUT" && chmod 777 "$OUT"

docker build -t "hack:latest" && \
timeout -k 1000s 1h docker run --rm --name "run-${TEAM}" \
  --network hack-net \
  -e LLM_BASE_URL -e LLM_API_KEY \
  -v "$TASKS:/data/tasks:rw" \
  --read-only --tmpfs /tmp:rw,size=2g -e HOME=/tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --cpus 8 --memory 16g --pids-limit 512 \
  --workdir /app --entrypoint python \
  "hack:latest" \
  /app/run_agent.py --tasks-dir /data/tasks
```

Совместимость проверена живым прогоном именно с этими флагами: `--entrypoint
python` + `/app/run_agent.py` работает (в образе `Entrypoint=null`), read-only
ФС и `HOME=/tmp` не мешают, отчёты идут в `/tmp`. Важно: **`$OUT` в
контейнер не монтируется**, поэтому `run_report.json` останется в его
`/tmp` и пропадёт; на баллы это не влияет — они считаются по `answer.txt`
в `$TASKS`. Для отладки добавьте `-v "$OUT:/tmp/run_agent_out"`.

**Требование к `$TASKS`**: `chmod -R 777` (см. выше про `--cap-drop ALL`) —
иначе харнесс остановится на старте с кодом 3.

### Ключи LLM: `.env` ≠ `-e`

В `.env` проекта ключи называются **`API_BASE`/`API_KEY`**, а прогон ждёт
`LLM_BASE_URL`/`LLM_API_KEY`. Если писать `-e LLM_BASE_URL="$API_BASE"` в
терминале, где `.env` не экспортирован, в контейнер уедет **пустая строка**,
и харнесс упадёт с кодом 4. Рабочие варианты:

```bash
# (а) передать .env целиком — клиент сам прочтёт API_BASE/API_KEY
docker run --rm --env-file .env ...

# (б) экспортировать .env в текущую оболочку и смонтировать имена
set -a && . ./.env && set +a
docker run --rm -e LLM_BASE_URL="$API_BASE" -e LLM_API_KEY="$API_KEY" ...

# (в) держать в .env уже боевые имена LLM_BASE_URL/LLM_API_KEY
```

Быстрая проверка, что ключ доехал:

```bash
docker run --rm --env-file .env --entrypoint sh <образ> -c 'echo ${#API_KEY}'
```

Коды выхода харнесса на старте: **3** — некуда записать ответы;
**4** — нет настроек LLM.

### Минимальная проверка работоспособности

```bash
rm -rf /tmp/check-tasks && mkdir -p /tmp/check-tasks
cp -r tasks/task-dir/task-dir/{search_0001,math_0000,data_analysis_0002,coding_0003} \
      /tmp/check-tasks/ && chmod -R 777 /tmp/check-tasks

docker run --rm --env-file .env \
  -v /tmp/check-tasks:/data/tasks:rw \
  --read-only --tmpfs /tmp:rw,size=2g -e HOME=/tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --workdir /app --entrypoint python hack:latest \
  /app/run_agent.py --tasks-dir /data/tasks

cat /tmp/check-tasks/*/answer.txt     # 1983, 389, 158, DONE
```

Ожидаемо: `4/4`, `11 баллов`, ~350–600 экв. за 10–25 с.

## Регистрация на Challenge 2026

Команда зарегистрирована (2026-11-04): `registration_id` в `registration_state.json`,
подпись SSHSIG ключом `~/.ssh/agent-registry-key`, сервер организаторов
`https://openjiuwen.iir.nsu.ru/mcp`.

```bash
# периодический heartbeat (~раз в 30 минут)
.venv/bin/python 04_openJiuwen_registration_agent.py --heartbeat
```

## Ключевые концепции openJiuwen

1. **Два типа агентов**: `WorkflowAgent` (строго по графу) и `ReActAgent`
   (сам выбирает действия в цикле «рассуждение → действие → наблюдение»).
2. **Компоненты workflow**: `Start` / `End` / `LLMComponent`, данные через
   шаблоны `${компонент.поле}`.
3. **Инструменты**: `@tool` (docstring → описание, type hints → схема аргументов),
   регистрация через `ability_manager` + `Runner.resource_mgr`.
4. **MCP-клиент**: `McpServerConfig` + `add_mcp_server`; не забывать
   `await client.disconnect()` в той же задаче.
5. **Runner**: исполняет графы, держит реестр процессов/инструментов/MCP-серверов.

## Полезные ссылки

- PyPI: https://pypi.org/project/openjiuwen/
- Официальный сайт: https://openjiuwen.com
- Хакатон: https://techarena2026.ru/open-jiuwen-challenge
