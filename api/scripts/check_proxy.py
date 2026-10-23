"""Проверка подключения к Claude API.

Работает и с прокси, и без него: проверяет тот путь, который задан переменными
ANTHROPIC_PROXY_URL / ANTHROPIC_BASE_URL (обе пустые — напрямую).

Платный: несколько коротких запросов к модели, порядка нескольких центов. Запуск вручную:
    docker compose run --rm api python scripts/check_proxy.py
"""

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402

from app.ai.client import BETAS, create_client, describe_route  # noqa: E402
from app.config import get_settings  # noqa: E402

settings = get_settings()
client = create_client(settings)


def request(**kwargs) -> dict:
    """Параметры как у агента; в проверке расхождение истории — ошибка, а не тихий пропуск."""
    params = {
        "model": settings.anthropic_model,
        "max_tokens": 2000,
        "betas": BETAS,
        "fallbacks": "default",
        "thinking": {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "error"}},
        "output_config": {"effort": settings.agent_effort},
    }
    params.update(kwargs)
    return params


def text_of(message) -> str:
    return "".join(block.text for block in message.content if block.type == "text").strip()


def check_stream():
    """1–2. Поток идёт по частям, беты доходят до API."""
    started = time.monotonic()
    moments = []
    with client.beta.messages.stream(**request(messages=[{
        "role": "user", "content": "Перечисли через запятую 40 разных русских существительных, без пояснений.",
    }])) as stream:
        for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                moments.append(time.monotonic())
        message = stream.get_final_message()
    if len(moments) < 3:
        raise AssertionError(f"пришло {len(moments)} фрагментов текста — поток не разбит на части")
    spread = moments[-1] - moments[0]
    if spread < 0.05:
        raise AssertionError(f"все фрагменты пришли за {spread:.3f} с — похоже, прокси буферизует поток")
    if message.input_transformations is None:
        raise AssertionError("в ответе нет input_transformations — бета thinking-binding-controls не дошла")
    return (f"{len(moments)} фрагментов за {spread:.2f} с, первый через {moments[0] - started:.2f} с; "
            f"беты приняты; модель {message.model}")


def check_system_message():
    """3. Сообщение с ролью system внутри messages."""
    message = client.beta.messages.create(**request(messages=[
        {"role": "user", "content": "Как дела?"},
        {"role": "system", "content": "Ответь ровно одним словом: ПРОВЕРКА"},
    ]))
    return f"ответ: {text_of(message)[:40]!r}"


def check_cache():
    """4. Повторный запрос с тем же префиксом читает его из кэша (TTL 1 час, как у базы знаний)."""
    text = f"Проверка кэша {uuid.uuid4()}. " + (
        "Ai-Dent продаёт стоматологическое оборудование: микроскопы, лупы, установки, наконечники, рентген. " * 120
    )
    system = [{"type": "text", "text": text, "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
    messages = [{"role": "user", "content": "Ответь одним словом: да"}]
    first = client.beta.messages.create(**request(system=system, messages=messages, max_tokens=500))
    second = client.beta.messages.create(**request(system=system, messages=messages, max_tokens=500))
    if not second.usage.cache_read_input_tokens:
        raise AssertionError(f"чтения из кэша нет (запись {first.usage.cache_creation_input_tokens})")
    return (f"запись {first.usage.cache_creation_input_tokens} токенов, "
            f"чтение {second.usage.cache_read_input_tokens} токенов")


def check_count_tokens():
    """5. Подсчёт токенов — им api считает размер базы знаний."""
    result = client.messages.count_tokens(
        model=settings.anthropic_model,
        system="Ты консультант магазина стоматологического оборудования.",
        messages=[{"role": "user", "content": "Какой микроскоп выбрать?"}],
    )
    if result.input_tokens <= 0:
        raise AssertionError("подсчёт вернул 0 токенов")
    return f"{result.input_tokens} токенов"


def check_thinking_replay():
    """6. Два хода с инструментом: блоки мышления возвращаются без изменений и принимаются API."""
    tools = [{
        "name": "get_time",
        "description": "Текущее время на сервере магазина.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    }]
    # Выше effort — выше шанс, что модель подумает и в истории окажутся блоки мышления
    common = {"tools": tools, "output_config": {"effort": "high"}}
    messages = [{"role": "user", "content": "Который час? Узнай через инструмент get_time."}]
    first = client.beta.messages.create(**request(messages=messages, **common))
    if first.stop_reason != "tool_use":
        raise AssertionError(f"модель не вызвала инструмент (stop_reason={first.stop_reason})")
    call = next(block for block in first.content if block.type == "tool_use")
    thinking = sum(block.type == "thinking" for block in first.content)
    messages += [
        {"role": "assistant", "content": first.content},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id, "content": "12:00"}]},
    ]
    second = client.beta.messages.create(**request(messages=messages, **common))
    messages += [
        {"role": "assistant", "content": second.content},
        {"role": "user", "content": "Спасибо. А который будет час через два часа?"},
    ]
    third = client.beta.messages.create(**request(messages=messages, **common))
    replayed = thinking + sum(block.type == "thinking" for block in second.content)
    note = "" if replayed else " — блоков мышления не было, подписи не проверены, запустите ещё раз"
    return f"3 запроса прошли, в истории блоков мышления: {replayed}; ответ: {text_of(third)[:40]!r}{note}"


CHECKS = [
    ("Поток по частям и беты", check_stream),
    ("Роль system внутри messages", check_system_message),
    ("Кэш промпта", check_cache),
    ("Подсчёт токенов", check_count_tokens),
    ("Блоки мышления без изменений", check_thinking_replay),
]


def explain(error: Exception) -> str:
    if isinstance(error, anthropic.APIStatusError):
        request_id = error.response.headers.get("request-id", "—")
        return f"HTTP {error.status_code}: {error.message} (request-id {request_id})"
    if isinstance(error, anthropic.APIConnectionError):
        return f"нет соединения с API — проверьте сеть, VPN или прокси ({error})"
    return str(error)


def main() -> int:
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY не задан — впишите ключ в .env и перезапустите проверку")
        return 2
    print(f"Claude API {describe_route(settings)}, модель {settings.anthropic_model}, effort {settings.agent_effort}\n")
    failed = 0
    for number, (title, check) in enumerate(CHECKS, 1):
        try:
            print(f"{number}. OK    {title}: {check()}")
        except Exception as error:  # noqa: BLE001 — нужен итог по всем проверкам
            failed += 1
            print(f"{number}. FAIL  {title}: {explain(error)}")
            if isinstance(error, (anthropic.AuthenticationError, anthropic.APIConnectionError)):
                break
    print("\nПодключение работает" if not failed else f"\nНе прошло проверок: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
