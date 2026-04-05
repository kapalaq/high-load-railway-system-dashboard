# Высоконагруженная система мониторинга железнодорожного транспорта

Система реального времени в формате «цифрового двойника» для мониторинга телеметрии локомотивов. Одновременно принимает потоки данных от 10 локомотивов, вычисляет индекс технического состояния, сохраняет обработанные снимки в базе данных и отображает всё в режиме реального времени на React-фронтенде.

---

## Архитектура

```
[Simulator] --WebSocket--> [Ingestion Service]
                                        |
                                        v
                                 [Redis Streams]          ← буфер + воспроизведение
                                        |
                                        v
                               [Processing Service]       ← индекс здоровья и тд.
                                  /            \
                        (напрямую)             (асинхронно)
                               /                  \
                      [Redis Pub/Sub]          [TimescaleDB]
                      metrics:live                  |
                               |                    |
                               +--------+-----------+
                                        |
                               [Query API + WS Hub]       ← REST + WebSocket
                                        |
                                 [React Frontend]
```

### Сервисы

| Сервис | Директория | Порт | Роль |
|---|---|---|---|
| `redis` | — | 6379 | Redis Streams (брокер) + Pub/Sub (fan-out) |
| `timescaledb` | — | 5432 | История телеметрии (`locomotive` DB) |
| `query-postgres` | — | 5433 | База данных авторизации (`general_api_db`) |
| `ingestion` | `./ingestion` | 8001 | Приём WebSocket → запись в Redis Streams |
| `processing` | `./processing_service` | — | Вычисление индекса состояния, запись в TimescaleDB |
| `query-api` | `./query-api` | 8000 | REST API + WebSocket Hub для фронтенда |
| `simulator` | `./simulator` | — | Генерация симулированной телеметрии |
| `pgadmin` | — | 5050 | Веб-интерфейс для управления базами данных |

---

## Требования

- Docker Desktop
- `docker compose` v2+

Другие локальные зависимости не требуются.

---

## Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd high-load-railway-system-dashboard

# 2. Запустить все сервисы
docker compose up --build -d

# 3. Подождать ~20 секунд, пока пройдут health checks, затем проверить:
curl http://localhost:8001/health    # ingestion
curl http://localhost:8000/docs      # OpenAPI-документация query-api
```

Все сервисы запускаются в правильном порядке через Docker health checks.

---


## Переменные окружения

Все переменные имеют значения по умолчанию — файл `.env` не обязателен для локальной разработки.

### `ingestion`
| Переменная | По умолчанию | Описание |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379` | Подключение к Redis |
| `STREAM_NAME` | `telemetry:raw` | Ключ Redis Stream |
| `STREAM_MAXLEN` | `100000` | Максимальная длина потока (~30 мин при 10 Hz) |

### `processing_service`
| Переменная | По умолчанию | Описание |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379` | Подключение к Redis |
| `DB_URL` | `postgresql://user:password@timescaledb:5432/locomotive` | Подключение к TimescaleDB |

### `query-api`
| Переменная | По умолчанию | Описание |
|---|---|---|
| `DATABASE_URL` | `postgresql://user:password@query-postgres/general_api_db` | База данных авторизации |
| `REDIS_URL` | `redis://redis:6379` | Redis Pub/Sub |
| `TIMESCALE_URL` | `postgresql://user:password@timescaledb:5432/locomotive` | История телеметрии |
| `RUN_MIGRATIONS_UPON_LAUNCH` | `true` | Применять Alembic-миграции при запуске |

### `simulator`
| Переменная | По умолчанию | Описание |
|---|---|---|
| `INGESTION_URL` | `ws://ingestion:8001/ws/telemetry` | WebSocket ingestion |
| `QUERY_API_WS_URL` | `ws://query-api:8000/api/websocket/ws` | WebSocket query-api (мониторинг RTT) |
| `HZ` | `1` | Частота отправки телеметрии (событий/сек на локомотив) |
| `RECONNECT_DELAY_S` | `2` | Задержка переподключения (секунды) |

---

## API

### Авторизация

#### Регистрация пользователя
```http
POST /api/auth/register
Content-Type: application/json

{
  "username": "driver1",
  "password": "secret",
  "email": "driver1@rail.kz",
  "full_name": "Driver One",
  "role": "driver",
  "train_id": "KZ8A-L001"
}
```

Роли: `driver`, `dispatcher`, `admin`

Поле `train_id` привязывает пользователя к конкретному локомотиву и включается в JWT-токен.

#### Получение JWT-токена
```http
POST /api/auth/token
Content-Type: application/x-www-form-urlencoded

username=driver1&password=secret
```

Ответ:
```json
{ "access_token": "<jwt>", "token_type": "bearer" }
```

Токен действителен **24 часа**. Полезная нагрузка JWT содержит `sub` (ID пользователя), `role` и `train_id`.

---

### WebSocket — телеметрия в реальном времени

```
ws://localhost:8000/api/websocket/ws?token=<jwt>&train_id=<train_id>
```

После подключения сервер непрерывно отправляет обработанные снимки телеметрии по указанному `train_id`. Рассылка происходит при каждом новом цикле обработки.

**Пример получаемого сообщения:**
```json
{
  "train_id": "KZ8A-L001",
  "timestamp": "2026-04-05T10:23:45.123Z",
  "health_score": 82.4,
  "health_category": "Норм",
  "alert_count": 0,
  "params": {
    "speed":      { "raw": 95.2, "unit": "км/ч", "status": "норм" },
    "temp_motor": { "raw": 78.1, "unit": "°C",   "status": "норм" }
  },
  "route_info": {
    "route_name": "Астана - Караганда - Алматы",
    "total_distance_km": 1211,
    "current_position_km": 340.5,
    "stops": [
      { "name": "Астана",    "distance_km": 0,    "status": "пройдено" },
      { "name": "Караганда", "distance_km": 211,  "status": "пройдено" },
      { "name": "Алматы",    "distance_km": 1211, "status": "впереди" }
    ]
  }
}
```

Маршрутизация сообщений реализована через Redis Pub/Sub канал `metrics:live`: Processing Service публикует снимок → query-api получает и рассылает подключённым WebSocket-клиентам по `train_id`.

---

### REST — историческая телеметрия

```http
GET /api/historic/telemetry/{train_id}?distance_km=350
Authorization: Bearer <jwt>
```

Возвращает сохранённый снимок телеметрии, ближайший к указанному значению `distance_km` по маршруту локомотива.

---

### OpenAPI-документация

Интерактивная документация доступна по адресу `http://localhost:8000/docs` после запуска стека.

---

## Локомотивы и маршруты

Симулятор генерирует данные для 10 локомотивов на двух маршрутах.

| Train ID | Тип | Модель | Маршрут                 |
|---|---|---|-------------------------|
| KZ8A-L001 | Электровоз | KZ8A | Астана → Караганда → Алматы |
| KZ8A-L002 | Электровоз | KZ8A | Астана → Караганда → Алматы |
| KZ8A-L003 | Электровоз | KZ8A | Алматы → Караганда → Астана |
| KZ8A-L004 | Электровоз | KZ8A | Астана → Караганда → Алматы |
| KZ8A-L005 | Электровоз | KZ8A | Алматы → Караганда → Астанаa |
| TE33A-L006 | Тепловоз | TE33A (GE/Kurastyru) | Астана → Караганда → Алматы |
| TE33A-L007 | Тепловоз | TE33A | Алматы → Караганда → Астана |
| TE33A-L008 | Тепловоз | TE33A | Астана → Караганда → Алматы |
| TE33A-L009 | Тепловоз | TE33A | Алматы → Караганда → Астана |
| TE33A-L010 | Тепловоз | TE33A | Астана → Караганда → Алматы |

---

## Индекс технического состояния (здоровья)

Вычисляется в `processing_service/processing.py` для каждого сообщения телеметрии.

### Оценка (0–100)

1. Каждый параметр проверяется на соответствие допустимым диапазонам, заданным в `processing_service/config.json`
2. Параметры вне диапазона дают **штраф** (предупреждение: 5–10 баллов, критическое: 15–30 баллов)
3. Сырая оценка = 100 − сумма штрафов
4. Применяется EMA-сглаживание с `alpha = 0.2` (настраивается в `config.json`)

### Категории

| Категория | Диапазон оценки |
|-----------|---|
| Норма     | 75 – 100 |
| Warning   | 40 – 75 |
| Critical  | 1 – 40 |

### Отслеживаемые параметры

**KZ8A (электровоз):** speed, temp_motor, temp_oil, temp_converters, temp_air, pantograph_voltage, pressure_oil, pressure_main_tank, pressure_brake, pressure_air, tractive_force, energy_usage, current_ampere, brake_force

**TE33A (тепловоз):** speed, temp_motor, temp_oil, temp_converters, temp_air, pressure_oil, pressure_main_tank, pressure_brake, pressure_air, tractive_force, fuel_liters, current_ampere, brake_force

---

## Схема баз данных

### TimescaleDB — база данных `locomotive`

```sql
CREATE TABLE IF NOT EXISTS telemetry (
    time             TIMESTAMPTZ      NOT NULL,
    train_id         TEXT             NOT NULL,
    health_score     DOUBLE PRECISION,
    health_category  CHAR(255),
    alert_count      INTEGER,
    params           JSONB,
    route_info       JSONB
)

-- Гипертаблица с разбивкой по времени (интервал 1 час)
SELECT create_hypertable('telemetry', 'time', chunk_time_interval => INTERVAL '1 hour');

CREATE INDEX ON telemetry (train_id, time DESC);
```

Схема создаётся автоматически при запуске processing service.

### PostgreSQL — база данных `general_api_db` (авторизация)

Управляется через Alembic-миграции, которые применяются автоматически при запуске query-api.

```sql
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    email           TEXT,
    full_name       TEXT,
    hashed_password TEXT NOT NULL,
    role            TEXT NOT NULL,       -- driver | dispatcher | admin
    is_active       BOOLEAN DEFAULT TRUE,
    train_id        TEXT                 -- привязанный локомотив
);
```

---

## Redis-каналы

| Ключ | Тип | Источник | Потребитель | Описание |
|---|---|---|---|---|
| `telemetry:raw` | Stream | ingestion | processing | Сырая телеметрия (maxlen 100k) |
| `metrics:live` | Pub/Sub | processing | query-api | Обработанные снимки для live-feed |

---

## Управление базами данных (pgAdmin)

Доступ по адресу `http://localhost:5050`

| Поле | Значение |
|---|---|
| Email | `admin@email.com` |
| Пароль | `admin` |

Подключения к серверам (добавить вручную):

**База авторизации:**
- Host: `query-postgres`, Port: `5432`, User: `user`, Password: `password`, DB: `general_api_db`

**База телеметрии:**
- Host: `timescaledb`, Port: `5432`, User: `user`, Password: `password`, DB: `locomotive`

---

## Полезные команды

```bash
# Просмотр логов конкретного сервиса
docker compose logs -f processing

# Перезапуск только симулятора
docker compose restart simulator

# Высоконагрузочный тест: поднять частоту до 100 Hz на локомотив
docker compose stop simulator
HZ=100 docker compose up -d simulator

# Проверить длину Redis Stream
docker compose exec redis redis-cli XLEN telemetry:raw

# Подключиться к TimescaleDB
docker compose exec timescaledb psql -U user -d locomotive

# Подключиться к базе авторизации
docker compose exec query-postgres psql -U user -d general_api_db

# Применить миграции вручную
docker compose exec query-api alembic upgrade head
```

---

## WebSocket тест-клиент

```bash
# Интерактивный клиент (запрашивает JWT-токен и коды поездов)
python processing_service/test.py
```

---

## Структура проекта

```
/
├── docker-compose.yml
│
├── ingestion/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py              ← FastAPI-приложение, lifespan
│   ├── routes.py            ← /health + /ws/telemetry
│   └── models.py            ← TelemetryMessage, RouteInfo, Metric
│
├── processing_service/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config.json          ← диапазоны параметров, штрафы, EMA alpha
│   ├── main.py              ← потребитель Redis Stream, пакетная запись в БД
│   ├── processing.py        ← вычисление индекса состояния
│   ├── db.py                ← схема TimescaleDB + инициализация
│   └── test.py              ← WebSocket тест-клиент
│
├── query-api/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/        ← файлы миграций
│   └── app/
│       ├── main.py          ← FastAPI-приложение, регистрация роутеров
│       ├── database.py      ← движки БД, Redis-диспетчер, lifespan
│       ├── config/base.py   ← AppConfig (переменные окружения)
│       ├── auth/            ← регистрация, вход, JWT, роли
│       ├── websocket/       ← /api/websocket/ws + ConnectionManager
│       └── historic_data/   ← /api/historic/telemetry/{train_id}
│
├── simulator/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config.py            ← список локомотивов, маршруты, HZ, URL-адреса
│   ├── main.py              ← асинхронный оркестратор + мониторинг RTT
│   └── generators.py        ← синусоидальная генерация телеметрии
│
└── frontend/                ← React-дашборд (подключается к ws://localhost:8000)
```

---

## Безопасность

- Значение `SECRET_KEY` в `query-api/app/auth/config.py` является заглушкой. В production-среде его необходимо переопределить через переменную окружения.
- В `simulator/config.py` содержится захардкоженный тестовый JWT для мониторинга RTT — замените его реальным токеном, полученным через `POST /api/auth/token`.
- Учётные данные БД (`user` / `password`) предназначены только для локальной разработки.
