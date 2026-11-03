"""Генератор сообщений с ПДн для тестов маскирования: имена, телефоны, почта, адреса, документы.

Фиксированное зерно — набор одинаков при каждом запуске. Каждый образец знает, какие фрагменты
не должны дойти до модели.
"""

import random
import re
from dataclasses import dataclass

MALE = (
    "Иван Пётр Алексей Дмитрий Сергей Андрей Михаил Николай Владимир Александр Евгений Олег Игорь Юрий "
    "Константин Артём Максим Кирилл Тимур Руслан Георгий Роман Лев Ашот Арсен Вячеслав Анатолий Григорий"
).split()
FEMALE = (
    "Мария Анна Елена Ольга Наталья Татьяна Ирина Светлана Екатерина Юлия Марина Галина Людмила Оксана "
    "Дарья Вера Надежда Любовь Алла Инна Жанна Зарина Нино Ксения Полина Алина Виктория Маргарита"
).split()
PATRONYMIC_M = "Иванович Петрович Сергеевич Алексеевич Николаевич Владимирович Ильич Андреевич Юрьевич".split()
PATRONYMIC_F = "Ивановна Петровна Сергеевна Алексеевна Николаевна Владимировна Ильинична Андреевна Юрьевна".split()
SURNAMES = (
    "Иванов Петров Смирнов Кузнецов Попов Васильев Соколов Михайлов Новиков Фёдоров Морозов Волков "
    "Алексеев Лебедев Семёнов Егоров Павлов Козлов Степанов Николаев Орлов Андреев Макаров Никитин "
    "Захаров Зайцев Соловьёв Борисов Яковлев Григорьев Романов Воробьёв Сергеев Фролов Дмитриев Королёв "
    "Гусев Киселёв Ильин Максимов Поляков Сорокин Виноградов Белов Медведев Антонов Тарасов Жуков "
    "Баранов Комаров Давыдов Беляев Богданов Сидоров Титов Миронов Крылов Карпов Власов Мельников Чернов "
    "Котов Быков Голубев Цветков Галкин Краснов Носов Серов Зимин Левин Блинов Мухин Рыбаков Кудрявцев "
    "Пупкин Кривошеин Подберёзкин Шевченко Ткаченко Бондаренко Коваленко Петросян Абрамян Мкртчян "
    "Мамедов Алиев Гасанов Беридзе Гогишвили Кацман Гольдберг Ким Цой"
).split()
STREETS = "Ленина Гагарина Пушкина Советская Мира Садовая Тверская Красноармейская Набережная Профсоюзная".split()
CITIES = "Москва Новосибирск Екатеринбург Казань Самара Омск Пермь Воронеж Тюмень Краснодар".split()
DOMAINS = "mail.ru yandex.ru gmail.com bk.ru inbox.ru list.ru rambler.ru icloud.com clinic-dent.ru".split()
PRODUCTS = [
    "микроскоп Carl Zeiss EXTARO 300", "лупы Zumax SLF 3,5×", "лупу Lercher TTL", "наконечник NSK Pana-Max2 M4",
    "галилеевскую лупу 2,5×", "призматические лупы 4,0×", "Мора-интерфейс", "осветитель LED 80 000 лк",
]

LATIN = dict(zip("абвгдеёжзийклмнопрстуфхцчшщыэюя",
                 "a b v g d e yo zh z i y k l m n o p r s t u f kh ts ch sh shch y e yu ya".split()))
LATIN.update({"ь": "", "ъ": ""})


def latin(word: str) -> str:
    return "".join(LATIN.get(c, c) for c in word.lower()).capitalize()


@dataclass
class Sample:
    text: str
    secrets: list[str]  # фрагменты, которых не должно быть в тексте для модели


def _person(rng: random.Random):
    female = rng.random() < 0.5
    first = rng.choice(FEMALE if female else MALE)
    surname = rng.choice(SURNAMES)
    if female and re.search(r"(ов|ев|ёв|ин|ын)$", surname):
        surname += "а"
    patronymic = rng.choice(PATRONYMIC_F if female else PATRONYMIC_M)
    return first, surname, patronymic


def _name(rng: random.Random) -> tuple[str, list[str]]:
    first, surname, patronymic = _person(rng)
    forms = [
        (f"{first} {surname}", [first, surname]),
        (f"{surname} {first} {patronymic}", [surname, first, patronymic]),
        (f"{surname} {first[0]}.{patronymic[0]}.", [surname]),
        (f"{first[0]}. {patronymic[0]}. {surname}", [surname]),
        (f"{first} {patronymic}", [first, patronymic]),
        (f"{surname.upper()} {first.upper()}", [surname.upper(), first.upper()]),
        (f"{first.lower()} {surname.lower()}", [first.lower(), surname.lower()]),
        (f"{latin(first)} {latin(surname)}", [latin(first), latin(surname)]),
    ]
    intro = [
        "Меня зовут {}.", "ФИО: {}.", "С уважением, {}.", "Контактное лицо — {}.", "Это {} из клиники.",
        "Передайте менеджеру, что звонил {}.", "{} просит перезвонить.", "Я — {}, врач-стоматолог.",
    ]
    text, secret = rng.choice(forms)
    template = rng.choice(intro)
    if text[0].islower() and not template.startswith(("Меня зовут", "ФИО")):
        template = rng.choice(["Меня зовут {}.", "ФИО: {}."])
    return template.format(text), secret


def _phone(rng: random.Random) -> tuple[str, list[str]]:
    d = "9" + "".join(rng.choice("0123456789") for _ in range(9))
    a, b, c, e = d[:3], d[3:6], d[6:8], d[8:]
    form = rng.choice([
        f"+7 ({a}) {b}-{c}-{e}", f"8 {a} {b} {c} {e}", f"8{d}", f"7{d}", f"{a}-{b}-{c}-{e}", f"+7{d}",
        f"8-{a}-{b}-{c}-{e}", f"8 ({a}) {b}{c}{e}", " ".join("8" + d), f"8.{a}.{b}.{c}.{e}",
        f"+375 {a[1:]} {b}-{c}-{e}", f"тел. {b}-{c}-{e}",
    ])
    intro = rng.choice(["Мой телефон {}", "Звоните {}", "Номер {} (вотсап)", "{} — это мобильный", "перезвоните на {}"])
    secret = re.sub(r"\D", "", form)
    return intro.format(form), [secret[-7:]]


def _email(rng: random.Random) -> tuple[str, list[str]]:
    first, surname, _ = _person(rng)
    local = rng.choice([latin(first).lower() + "." + latin(surname).lower(), latin(surname).lower() + str(rng.randint(1, 99)),
                        latin(first).lower() + "_" + latin(surname).lower()])
    host, tld = rng.choice(DOMAINS).rsplit(".", 1)
    form = rng.choice([
        f"{local}@{host}.{tld}", f"{local} @ {host}.{tld}", f"{local} собака {host} точка {tld}",
        f"{local}(at){host}(dot){tld}", f"{local} [at] {host} [dot] {tld}", f"{local}@{host}.{tld}".upper(),
    ])
    return rng.choice(["Почта: {}", "пишите на {}", "мой email {}", "{} — для счёта"]).format(form), [local.lower()]


def _address(rng: random.Random) -> tuple[str, list[str]]:
    street, city = rng.choice(STREETS), rng.choice(CITIES)
    house, flat = rng.randint(1, 150), rng.randint(1, 400)
    form = rng.choice([
        f"г. {city}, ул. {street}, д. {house}, кв. {flat}", f"{city}, {street} {house}-{flat}",
        f"ул. {street} {house} кв {flat}", f"улица {street}, дом {house}, квартира {flat}",
        f"{rng.randint(100000, 699999)}, г. {city}, ул. {street}, {house}",
    ])
    intro = rng.choice(["Адрес доставки: {}", "Доставьте по адресу {}", "Живу: {}", "{}"])
    return intro.format(form), [street]


def _document(rng: random.Random) -> tuple[str, list[str]]:
    series, number = f"{rng.randint(10, 99)}{rng.randint(10, 99)}", f"{rng.randint(100000, 999999)}"
    inn = "".join(rng.choice("0123456789") for _ in range(rng.choice([10, 12])))
    card = " ".join("".join(rng.choice("0123456789") for _ in range(4)) for _ in range(4))
    options = [
        (f"паспорт {series} {number}", [number]), (f"серия {series} номер {number}", [number]),
        (f"ИНН {inn}", [inn]), (f"инн: {inn[:4]} {inn[4:8]} {inn[8:]}", [inn[4:8] + " " + inn[8:]]),
        (f"карта {card}", [card]), (f"СНИЛС {number[:3]}-{series[:3]}-{number[3:]} {series[2:]}", [f"{number[:3]}-{series[:3]}-{number[3:]}"]),
        (f"дата рождения {rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.randint(1950, 2004)}", ["рождения 0", "рождения 1", "рождения 2"]),
    ]
    return rng.choice(options)


def samples(count: int, seed: int = 2026) -> list[Sample]:
    rng = random.Random(seed)
    result = []
    parts = [_name, _phone, _email, _address, _document]
    for _ in range(count):
        chosen = rng.sample(parts, rng.randint(1, 3))
        texts, secrets = [], []
        for make in chosen:
            text, secret = make(rng)
            texts.append(text)
            secrets += secret
        texts.insert(rng.randint(0, len(texts)), f"Нужен {rng.choice(PRODUCTS)}.")
        result.append(Sample(" ".join(texts), secrets))
    return result


def leaked(sample: Sample, masked: str) -> list[str]:
    return [s for s in sample.secrets if s and s in masked]
