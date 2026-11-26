# Keitaro Admin API v1 Wrapper & Editor

Веб-приложение (FastAPI + SQLAlchemy 2.0 + SQLite + Vanilla JS) для автоматизации создания рекламных кампаний в Keitaro и управления потоками офферов через локальные черновики с поддержкой мягкого удаления, закрепления весов (Pin) и безопасной публикации (Push).

## Технологический стек
* **Python 3.11+ / FastAPI** (асинхронный REST API)
* **SQLAlchemy 2.0 + aiosqlite + Alembic** (база данных и миграции с поддержкой внешних ключей)
* **Httpx** (единый асинхронный клиент для Keitaro Admin API v1)
* **Pytest + Respx** (тестирование математического ядра и интеграционных сценариев)

---

## Быстрый старт

### 1. Установка зависимостей
```bash
pip install -r requirements.txt
```

### 2. Настройка окружения
Создайте файл `.env` на основе `.env.example`:
```env
KEITARO_BASE_URL=https://your-tracker-domain.com
KEITARO_API_KEY=your_api_key_here
DEFAULT_DOMAIN_NAME=https://in.posdk.xyz/
DEFAULT_GROUP_NAME=FORTESTS
DEFAULT_SOURCE_NAME=FORTESTS
DATABASE_URL=sqlite+aiosqlite:///./keitaro_wrapper.db
```

### 3. Применение миграций БД
```bash
python -m alembic upgrade head
```

### 4. Запуск тестов
```bash
python -m pytest
```

### 5. Запуск приложения
```bash
python -m uvicorn app.main:app --reload
```
