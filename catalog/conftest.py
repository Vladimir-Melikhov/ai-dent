import os

import pytest


def pytest_configure(config):
    if os.environ.get("DENTAI_TESTS") != "1":
        pytest.exit("Запускайте тесты через scripts/run_tests.sh — он поднимает окружение на тестовых Postgres и Redis")


@pytest.fixture(autouse=True)
def _no_external_notifications(settings):
    settings.TELEGRAM_BOT_TOKEN = ""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture
def redis_client():
    from apps.chat.services import redis_client

    redis_client.flushdb()
    return redis_client


@pytest.fixture
def channel(redis_client):
    """Слушатель канала диалога: то, что после коммита получил бы api."""
    subscriptions = []

    def listen(conversation_id):
        pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(f"chat:conv:{conversation_id}")
        pubsub.get_message(timeout=1)
        subscriptions.append(pubsub)
        return pubsub

    yield listen
    for pubsub in subscriptions:
        pubsub.close()


def next_published(pubsub):
    import json

    message = pubsub.get_message(timeout=2)
    assert message is not None, "в канал ничего не опубликовано"
    return json.loads(message["data"])
