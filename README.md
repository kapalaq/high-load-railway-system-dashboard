# High-Load Railway System Dashboard

A real-time digital twin dashboard for locomotive telemetry. Streams sensor data from 10 locomotives simultaneously, computes health indices, stores processed snapshots, and visualizes everything live.

---

## Architecture

```
[10 Simulators] --WebSocket--> [Ingestion Service]
                                       |
                                       v
                                [Redis Streams]          ← burst buffer
                                       |
                                       v
                              [Processing Service]       ← health index, EMA
                                 /            \
                       (immediate)              (async)
                              /                  \
                     [Redis Pub/Sub]          [TimescaleDB]
                              |                    |
                              +--------+-----------+
                                       |
                              [Query API + WS Hub]       ← REST + WebSocket
                                       |
                               [React Frontend]
```

### Services

| Service | Directory | Port | Role |
|---|---|---|---|
| `redis` | — | 6379 | Streams broker + Pub/Sub fan-out |
| `timescaledb` | — | 5432 | Processed telemetry history (`locomotive` DB) |
| `ingestion` | `./ingestion` | 8001 | WebSocket receiver → Redis Streams |
| `processing` | `./processing_service` | — | Computes health index, writes TimescaleDB |
| `query-api` | `./query-api` | 8000 | REST API + WebSocket hub for frontend |
| `query-postgres` | — | 5433 | User auth database (`general_api_db`) |
| `pgadmin` | — | 5050 | Database admin UI |
| `simulator` | `./simulator` | — | Generates fake telemetry for 10 locos |

---

## Prerequisites

- Docker Desktop (or Docker Engine + Compose plugin)
- `docker compose` v2+

No other local dependencies are required.

---

## Quick Start

```bash
# 1. Clone the repo
git clone <repo-url>
cd high-load-railway-system-dashboard

# 2. Start all services
docker compose up --build

# 3. Wait ~20 seconds for all health checks to pass, then verify:
curl http://localhost:8001/health    # ingestion
curl http://localhost:8000/docs      # query-api OpenAPI docs
```

All services start in dependency order via Docker health checks.

---

## Environment Variables

All variables have defaults — no `.env` file is required for local development.

### `ingestion`
| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379` | Redis connection |
| `STREAM_NAME` | `telemetry:raw` | Redis Stream key |
| `STREAM_MAXLEN` | `100000` | Max stream length (~30 min at 10 Hz) |

### `processing_service`
| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379` | Redis connection |
| `DB_URL` | `postgresql://user:password@timescaledb:5432/locomotive` | TimescaleDB connection |

### `query-api`
| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://user:password@query-postgres/general_api_db` | Auth DB |
| `REDIS_URL` | `redis://redis:6379` | Redis Pub/Sub |
| `TIMESCALE_URL` | `postgresql://user:password@timescaledb:5432/locomotive` | Telemetry history |

### `simulator`
| Variable | Default | Description |
|---|---|---|
| `INGESTION_URL` | `ws://ingestion:8001/ws/telemetry` | Ingestion WebSocket |
| `QUERY_API_WS_URL` | `ws://query-api:8000/api/websocket/ws` | Query API WS (RTT monitoring) |
| `HZ` | `10` | Telemetry frequency per locomotive |
| `RECONNECT_DELAY_S` | `2` | Reconnect backoff seconds |

---

## API Reference

### Authentication

#### Register a user
```http
POST /api/auth/register
Content-Type: application/json

{
  "username": "driver1",
  "password": "secret",
  "email": "driver1@rail.kz",
  "full_name": "Driver One",
  "role": "DRIVER",
  "train_id": "KZ8A-L001"
}
```

Roles: `DRIVER`, `DISPATCHER`, `ADMIN`

#### Get a JWT token
```http
POST /api/auth/token
Content-Type: application/x-www-form-urlencoded

username=driver1&password=secret
```

Returns:
```json
{ "access_token": "<jwt>", "token_type": "bearer" }
```

Tokens are valid for **24 hours**.

---

### WebSocket — Live Telemetry

```
ws://localhost:8000/api/websocket/ws?token=<jwt>&train_id=<train_id>
```

Connect to receive a continuous stream of processed telemetry snapshots for the given `train_id`. The server broadcasts on every new processing cycle.

**Example message received:**
```json
{
  "train_id": "KZ8A-L001",
  "timestamp": "2026-04-05T10:23:45.123Z",
  "health_score": 82.4,
  "health_category": "Normal",
  "alert_count": 0,
  "params": {
    "speed":      { "raw": 95.2, "unit": "км/ч", "status": "normal" },
    "temp_motor": { "raw": 78.1, "unit": "°C",   "status": "normal" }
  },
  "route_info": {
    "route_name": "Astana - Karaganda - Almaty",
    "total_distance_km": 1211,
    "current_position_km": 340.5,
    "stops": [
      { "name": "Astana",    "distance_km": 0,    "status": "passed" },
      { "name": "Karaganda", "distance_km": 211,  "status": "passed" },
      { "name": "Almaty",    "distance_km": 1211, "status": "upcoming" }
    ]
  }
}
```

---

### REST — Historical Telemetry

```http
GET /api/historic/telemetry/{train_id}?distance_km=350
Authorization: Bearer <jwt>
```

Returns the stored telemetry snapshot closest to `distance_km` along the route for `train_id`.

---

### OpenAPI Docs

Interactive docs available at `http://localhost:8000/docs` once the stack is running.

---

## Locomotives & Trains

The simulator generates data for 10 locomotives across two routes.

| Train ID | Type | Loco Model | Route |
|---|---|---|---|
| KZ8A-L001 | Electric | KZ8A (Alstom/EKZ) | Astana → Karaganda → Almaty |
| KZ8A-L002 | Electric | KZ8A | Astana → Karaganda → Almaty |
| KZ8A-L003 | Electric | KZ8A | Almaty → Karaganda → Astana |
| KZ8A-L004 | Electric | KZ8A | Astana → Karaganda → Almaty |
| KZ8A-L005 | Electric | KZ8A | Almaty → Karaganda → Astana |
| TE33A-L006 | Diesel | TE33A (GE/Kurastyru) | Astana → Karaganda → Almaty |
| TE33A-L007 | Diesel | TE33A | Almaty → Karaganda → Astana |
| TE33A-L008 | Diesel | TE33A | Astana → Karaganda → Almaty |
| TE33A-L009 | Diesel | TE33A | Almaty → Karaganda → Astana |
| TE33A-L010 | Diesel | TE33A | Astana → Karaganda → Almaty |

---

## Health Index

Computed in `processing_service/processing.py` for every telemetry message.

### Score (0–100)
1. Each metric is checked against ranges defined in `processing_service/config.json`
2. Out-of-range metrics apply a **penalty** (warning: 5–10 pts, critical: 15–30 pts)
3. Raw score = 100 − total penalties
4. EMA smoothing applied with `alpha = 0.2` (configurable in `config.json`)

### Categories
| Category | Score Range |
|---|---|
| Normal | 75 – 100 |
| Warning | 40 – 75 |
| Critical | 1 – 40 |
| RUN | 0 – 1 |

### Monitored Metrics

**KZ8A (Electric):** speed, temp_motor, temp_oil, temp_converters, temp_air, pantograph_voltage, pressure_oil, pressure_main_tank, pressure_brake, pressure_air, tractive_force, energy_usage, current_ampere, brake_force

**TE33A (Diesel):** speed, temp_motor, temp_oil, temp_converters, temp_air, pressure_oil, pressure_main_tank, pressure_brake, pressure_air, tractive_force, fuel_liters, current_ampere, brake_force

---

## Database Schema

### TimescaleDB — `locomotive` database

```sql
CREATE TABLE telemetry (
    time             TIMESTAMPTZ      NOT NULL,
    train_id         TEXT             NOT NULL,
    health_score     DOUBLE PRECISION,
    health_category  TEXT,
    alert_count      INTEGER,
    params           JSONB,           -- enriched metric map
    route_info       JSONB            -- position and stops
);

-- Hypertable partitioned by time (1-hour chunks)
SELECT create_hypertable('telemetry', 'time', chunk_time_interval => INTERVAL '1 hour');

CREATE INDEX ON telemetry (train_id, time DESC);
```

The processing service creates this schema automatically at startup.

### PostgreSQL — `general_api_db` database (auth)

Managed via Alembic migrations that run automatically on query-api startup.

```sql
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    email           TEXT,
    full_name       TEXT,
    hashed_password TEXT NOT NULL,
    role            TEXT NOT NULL,   -- DRIVER | DISPATCHER | ADMIN
    is_active       BOOLEAN DEFAULT TRUE,
    train_id        TEXT             -- associated locomotive
);
```

---

## Redis Channels

| Key | Type | Producer | Consumer | Description |
|---|---|---|---|---|
| `telemetry:raw` | Stream | ingestion | processing | Raw telemetry (maxlen 100k) |
| `metrics:live` | Pub/Sub | processing | query-api | Processed snapshots for live feed |

---

## Database Admin (pgAdmin)

Access at `http://localhost:5050`

| Field | Value |
|---|---|
| Email | `admin@email.com` |
| Password | `admin` |

Add server connections manually:

**Auth DB:**
- Host: `query-postgres`, Port: `5432`, User: `user`, Password: `password`, DB: `general_api_db`

**Telemetry DB:**
- Host: `timescaledb`, Port: `5432`, User: `user`, Password: `password`, DB: `locomotive`

---

## Useful Commands

```bash
# View logs for a specific service
docker compose logs -f processing

# Restart just the simulator
docker compose restart simulator

# High-load test: bump to 100 Hz per locomotive
docker compose stop simulator
HZ=100 docker compose up -d simulator

# Check Redis stream length
docker compose exec redis redis-cli XLEN telemetry:raw

# Connect to TimescaleDB
docker compose exec timescaledb psql -U user -d locomotive

# Connect to auth DB
docker compose exec query-postgres psql -U user -d general_api_db

# Run DB migrations manually
docker compose exec query-api alembic upgrade head
```

---

## WebSocket Test Client

```bash
# Interactive test client (prompts for JWT token and train codes)
python processing_service/test.py
```

---

## Project Structure

```
/
├── docker-compose.yml
├── ARCHITECTURE.md
│
├── ingestion/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py              ← FastAPI app, lifespan
│   ├── routes.py            ← /health + /ws/telemetry
│   └── models.py            ← TelemetryMessage, RouteInfo, Metric
│
├── processing_service/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config.json          ← metric ranges, penalties, EMA alpha
│   ├── main.py              ← Redis Stream consumer, batch DB writer
│   ├── processing.py        ← health index computation
│   ├── db.py                ← TimescaleDB schema + init
│   └── test.py              ← WS test client
│
├── query-api/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/        ← DB migrations
│   └── app/
│       ├── main.py          ← FastAPI app, router registration
│       ├── database.py      ← engines, Redis dispatcher, lifespan
│       ├── config/base.py   ← AppConfig (env vars)
│       ├── auth/            ← register, login, JWT, roles
│       ├── websocket/       ← /api/websocket/ws + ConnectionManager
│       └── historic_data/   ← /api/historic/telemetry/{train_id}
│
├── simulator/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config.py            ← LOCOS, ROUTES, HZ, URLs
│   ├── main.py              ← async orchestrator + RTT monitor
│   └── generators.py        ← sinusoidal telemetry generation
│
└── frontend/                ← React dashboard (wire to ws://localhost:8000)
```

---

## Security Notes

- The default `SECRET_KEY` in `query-api/app/auth/config.py` is a placeholder. Override it via environment variable in production.
- The simulator's `config.py` contains a hardcoded test JWT for RTT monitoring — replace it with a real token obtained from `POST /api/auth/token`.
- Default database credentials (`user` / `password`) are for local development only.
