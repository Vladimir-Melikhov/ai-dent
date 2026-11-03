"""Маскирование ПДн в тексте клиента перед отправкой в модель.

Клиент ничего не замечает: в чате и в нашей БД — его исходный текст, модель получает копию,
прошедшую пять слоёв:

1. Нормализация — невидимые символы, цифры других алфавитов (８, ٨, 𝟖, 8\ufe0f\u20e3), латинские буквы-двойники
   в русских словах и наоборот, буква О вместо нуля в номерах.
2. Шаблоны — почта (и «ivan собака mail точка ru»), телефоны любых стран, номера словами, карты,
   счета, СНИЛС, ИНН/ОГРН, паспорт, ссылки на соцсети и мессенджеры, ники, адреса, госномер, IP,
   любые длинные номера.
3. Контекст — значение после «меня зовут», «ФИО», «адрес», «дата рождения», «паспорт», «ИНН»,
   «пароль», «тел.», «телеграм», подпись после «С уважением».
4. Морфология — имена, фамилии, отчества и инициалы по словарю русского языка (names.py).
5. Защита и контрольный проход — бренды, названия и артикулы каталога, цены, даты, номера заказов
   не трогаются; затем текст проверяется ещё раз, пока находки не кончатся.

Страховка, а не гарантия: фамилию строчными без контекста или адрес без «ул.» и номера дома
можно не поймать — поэтому агент не спрашивает личные данные и направляет в форму.
"""

import logging
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select

from ..db.engine import async_session_factory
from ..db.models import Brand, KnowledgeBaseVersion, Product
from . import names

logger = logging.getLogger(__name__)

# Метки по убыванию приоритета: при наложении находок остаётся старшая
LABELS = (
    "email", "ссылка", "номер карты", "документ", "реквизит", "телефон", "дата рождения", "данные",
    "адрес", "имя", "ник", "IP", "госномер", "номер",
)
PRIORITY = {label: i for i, label in enumerate(LABELS)}
_PLACEHOLDER = re.compile(r"\[(" + "|".join(re.escape(label) for label in LABELS) + r")\]")


@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    label: str
    rank: int = 1  # при наложении побеждает контекст (0), затем сильный шаблон (1), затем слабый (2)


# ============================================================ 1. нормализация

_DASHES = dict.fromkeys(map(ord, "‐‑‒―−﹘﹣－"), "-")
_LATIN_TWINS = str.maketrans("aceopxyABCEHKMOPTX", "асеорхуАВСЕНКМОРТХ")
_CYRILLIC_TWINS = str.maketrans("асеорхуАВСЕНКМОРТХ", "aceopxyABCEHKMOPTX")
_TWINS_LATIN = set("aceopxyABCEHKMOPTX")
_TWINS_CYRILLIC = set("асеорхуАВСЕНКМОРТХ")
_MIXED_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")
_ZERO_IN_NUMBER = re.compile(r"(?<![A-Za-zА-Яа-яЁё])(?=[\dOoОо]*\d)(?=[\dOoОо]*[OoОо])[\dOoОо]{3,}(?![A-Za-zА-Яа-яЁё])")


def _char(ch: str) -> str:
    if ch.isascii():
        return ch
    category = unicodedata.category(ch)
    if category == "Cf" or "\ufe00" <= ch <= "\ufe0f" or ch == "\u20e3":
        return ""  # невидимые: мягкий перенос, нулевой пробел, селекторы эмодзи, рамка 8\ufe0f\u20e3
    if category == "Zs":
        return " "
    if category in ("Zl", "Zp"):
        return "\n"
    if category == "Nd":
        return str(unicodedata.digit(ch))
    if category == "No":
        name = unicodedata.name(ch, "")
        if any(w in name for w in ("CIRCLED", "PARENTHESIZED", "DIGIT", "NUMBER")) and not any(
            w in name for w in ("SUPERSCRIPT", "SUBSCRIPT", "FRACTION")
        ):
            value = unicodedata.numeric(ch, None)  # ⑧ → 8, ⑩ → 10
            if value is not None and float(value).is_integer():
                return str(int(value))
    if "\uff01" <= ch <= "\uff5e" or "\U0001d400" <= ch <= "\U0001d7ff":
        return unicodedata.normalize("NFKC", ch)  # полноширинные и «жирные» буквы и знаки
    return ch


def _untwin(match: re.Match) -> str:
    word = match.group()
    cyrillic = sum(1 for c in word if "А" <= c <= "я" or c in "Ёё")
    latin = sum(1 for c in word if c.isascii())
    if cyrillic and latin:
        if cyrillic >= latin and all(c in _TWINS_LATIN for c in word if c.isascii()):
            return word.translate(_LATIN_TWINS)
        if latin > cyrillic and all(c in _TWINS_CYRILLIC for c in word if not c.isascii()):
            return word.translate(_CYRILLIC_TWINS)
    return word


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    if not text.isascii():
        text = "".join(_char(c) for c in text)
    text = text.translate(_DASHES)
    text = _MIXED_WORD.sub(_untwin, text)
    return _ZERO_IN_NUMBER.sub(lambda m: re.sub("[OoОо]", "0", m.group()), text)


# ============================================================ 2. шаблоны

SEP = r"[\s\-–—]*"
_AT = r"(?:\s*@\s*|\s*[(\[{<]\s*(?:at|@|собака|собачка)\s*[)\]}>]\s*|\s+(?:at|собака|собачка|эт)\s+)"
_DOT = r"(?:\s*\.\s*|\s*[(\[{<]\s*(?:dot|точка|\.)\s*[)\]}>]\s*|\s+(?:dot|точка|тчк)\s+)"
_TLD = r"(?:ru|рф|ру|su|com|ком|net|org|info|biz|pro|io|me|by|kz|ua|uz|am|ge|de|online|site|store|tech)"
_MAILHOST = r"(?:g-?mail|yandex|ya|mail|bk|inbox|list|internet|rambler|outlook|hotmail|icloud|yahoo|proton(?:mail)?|мейл|майл|яндекс|гмейл|джимейл)"
_NUMWORD = (
    r"(?:ноль|нуль|один|одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|одиннадцать|двенадцать|"
    r"тринадцать|четырнадцать|пятнадцать|шестнадцать|семнадцать|восемнадцать|девятнадцать|двадцать|тридцать|"
    r"сорок|пятьдесят|шестьдесят|семьдесят|восемьдесят|девяносто|сто|двести|триста|четыреста|пятьсот|шестьсот|"
    r"семьсот|восемьсот|девятьсот)"
)
_STREET = (
    r"(?:ул(?:ица|\.|(?=\s))|пр(?:оспект|осп\.?|-т\.?|\.)|пер(?:еулок|\.)|ш(?:оссе|\.)|наб(?:ережная|\.)|"
    r"бул(?:ьвар|\.)|б-р|пл(?:ощадь|\.)|мкр(?:н\.?|\.)?|микрорайон|проезд|пр-д|аллея|тупик|тракт|квартал)"
)
_HOUSE_NUMBER = r"\d+[а-яА-Яa-zA-Z]?(?:[/\-.]\d+[а-яА-Я]?)?"
_HOUSE_PART = (
    r"(?:\s*,?\s*(?:(?:д(?:ом|\.)?|корп(?:ус|\.)?|к\.?|стр(?:оение|\.)?|кв(?:артира|\.)?|оф(?:ис|\.)?|"
    r"пом(?:ещение|\.)?|лит(?:ера?|\.)?|под(?:ъезд|\.)?|эт(?:аж|\.)?)\s*№?\s*"
    rf"(?:{_HOUSE_NUMBER}|[А-ЯA-Z](?![\w]))|№?\s*{_HOUSE_NUMBER}))"
)
_STREET_NAME = r"(?:\d{1,2}-?(?:я|й|го|ая|ой)?\s+)?(?:[А-ЯЁ]\.\s?)?[А-ЯЁа-яёA-Za-z0-9][\wЁё\-]*(?:\s+[А-ЯЁ][\wЁё\-]*){0,2}"

# Сильные шаблоны: действуют и внутри защищённых мест (почта с брендом в домене — всё равно почта)
STRONG = [
    ("email", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("email", re.compile(
        rf"(?<![\w.+-])[\w.+-]{{2,64}}{_AT}(?:[\w-]+(?:{_DOT}[\w-]+)*{_DOT}{_TLD}|{_MAILHOST}(?:(?:{_DOT}|\s+){_TLD})?)(?![\w-])",
        re.I,
    )),
    ("ссылка", re.compile(
        r"(?:https?://)?(?:www\.|m\.)?(?:t\.me|telegram\.(?:me|dog)|vk\.(?:com|ru|me)|ok\.ru|instagram\.com|"
        r"instagr\.am|facebook\.com|fb\.(?:com|me)|wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com|viber\.click|"
        r"x\.com|twitter\.com|tiktok\.com|linkedin\.com|youtube\.com/@|dzen\.ru|my\.mail\.ru|hh\.ru/resume|"
        r"disk\.yandex\.\w+|yadi\.sk|drive\.google\.com|docs\.google\.com|max\.ru)(?:/[^\s,;)\]]*)?", re.I,
    )),
    ("ник", re.compile(r"(?<![\w@.])@[A-Za-z][A-Za-z0-9_.]{2,31}\b")),
    # Номер карты: группы по четыре цифры через пробел или дефис, либо 16–19 цифр подряд
    ("номер карты", re.compile(r"(?<!\d)(?:\d{4}([ \-])\d{4}\1\d{4}\1\d{1,7}|\d{16,19})(?!\d)")),
    # СНИЛС: 123-456-789 01
    ("реквизит", re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[ \-]\d{2}(?!\d)")),
    # +7 / 8 / 7 и десять цифр в любом оформлении
    ("телефон", re.compile(
        rf"(?<![\d+])(?:\+7|8|7(?={SEP}\(?\d{{3}}))[\s\-–—(]*\d{{3}}[\s\-–—)]*\d{{3}}{SEP}\d{{2}}{SEP}\d{{2}}(?!\d)"
    )),
    # Мобильный без кода страны: 9XX XXX XX XX
    ("телефон", re.compile(rf"(?<!\d)9\d{{2}}{SEP}\d{{3}}{SEP}\d{{2}}{SEP}\d{{2}}(?!\d)")),
    # Другие страны: +375 29 123-45-67, +49 (30) 1234567
    ("телефон", re.compile(r"(?<![\w+])\+(?!7)\d{1,3}[\s\-–—().]*\d(?:[\s\-–—().]*\d){5,12}(?!\d)")),
    # Паспорт: «4506 123456», «45 06 №123456», «серия 4506 номер 123456», «4506-123456»
    ("документ", re.compile(
        r"(?<!\d)(?:серия\s*:?\s*)?\d{2}\s?\d{2}(?:\s*,?\s*(?:№|номер|н\.|#|-|/)\s*:?\s*|\s+)\d{6}(?!\d)", re.I,
    )),
    # Номер словами: «восемь девятьсот девяносто девять сто двадцать три…»
    ("номер", re.compile(rf"(?<![\w]){_NUMWORD}(?:[\s,\-–—.]+(?:{_NUMWORD}|\d{{1,4}})){{5,}}(?![\w])", re.I)),
]

# Слабые шаблоны: работают по тексту с вымаранными защищёнными местами (артикулы, цены, даты, каталог)
WEAK = [
    # Латиница с подчёркиванием — почти всегда ник: ivan_petrov, dr_petrova_stom
    ("ник", re.compile(r"(?<![\w@.])[A-Za-z][A-Za-z0-9.]*_[A-Za-z0-9_.]*[A-Za-z0-9](?![\w])")),
    ("IP", re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])")),
    # ИНН, ОГРН, ОГРНИП, СНИЛС без разделителей — 10–15 цифр; расчётный счёт — 20
    ("реквизит", re.compile(r"(?<!\d)(?:\d{10,15}|\d{20,34})(?!\d)")),
    ("госномер", re.compile(
        r"(?<![\w])[АВЕКМНОРСТУХABEKMHOPCTYX]\s?\d{3}\s?[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3}(?![\w])", re.I,
    )),
    ("адрес", re.compile(rf"(?<![\w]){_STREET}\s*{_STREET_NAME}{_HOUSE_PART}*")),
    ("адрес", re.compile(rf"(?<![\w])[А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)?\s+{_STREET}(?![\w]){_HOUSE_PART}+")),
    ("адрес", re.compile(
        rf"(?<![\w])(?:д(?:ом|\.)|кв(?:артира|\.)|корп(?:ус|\.))\s*№?\s*\d+[а-яА-Я]?(?:[/\-]\d+)?{_HOUSE_PART}*", re.I,
    )),
    # «Тверская 12-45» после «на» или запятой, «Ленина 5 кв 12»
    ("адрес", re.compile(
        r"(?:(?<=на )|(?<=, ))[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\s+\d{1,3}[а-я]?(?:/\d+)?\s*-\s*\d{1,4}(?!\d)"
    )),
    ("адрес", re.compile(
        r"(?<![\w])[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\s+\d{1,3}[а-я]?(?:/\d+)?\s*,?\s*(?:кв\.?|квартира)\s*\d{1,4}(?!\d)"
    )),
    # Индекс в начале адреса: «630099, г. Новосибирск»
    ("адрес", re.compile(r"(?<!\d)[1-6]\d{5}(?=\s*,\s*(?:г\.|город|[А-ЯЁ]))")),
]

# Инициалы рядом с фамилией: «Иванов И.И.», «И. И. Иванов», «J. Smith»; группа surname — для проверки по словарю
_INITIALS = [
    re.compile(r"(?<![\w])(?P<surname>[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)\s+[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.?)?(?![\w])"),
    re.compile(r"(?<![\w.])[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.\s?)?(?P<surname>[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)"),
    re.compile(r"(?<![\w.])[A-Z]\.\s?(?:[A-Z]\.\s?)?(?P<surname>[A-Z][a-z]+)"),
]


def _initials(blanked: str, protects) -> list[Hit]:
    hits = []
    for pattern in _INITIALS:
        for m in pattern.finditer(blanked):
            surname = m.group("surname")
            a = names.analyze(names.to_cyrillic(surname) if surname.isascii() else surname.lower())
            # «5500 К. Джойстик», «Москва И.» — обычное слово рядом с буквой, не фамилия
            if protects(surname) or (a.known and a.personal == 0):
                continue
            hits.append(Hit(*m.span(), "имя", 2))
    return hits


# Любой длинный номер в произвольном оформлении: 10+ цифр через пробелы, точки, скобки, дефисы
_DIGIT_RUN = re.compile(r"(?<![\w])\+?\d(?:[\s\-.()]{0,3}\d){9,}(?![\w])")
_DECIMAL_LIST = re.compile(r"\d{1,3}[.,]\d{1,2}(?:\s+\d{1,3}[.,]\d{1,2})+")
_THOUSANDS_RANGE = re.compile(r"\d{1,3}(?: \d{3})+\s*-\s*\d{1,3}(?: \d{3})+")


def _digit_runs(blanked: str) -> list[Hit]:
    hits = []
    for m in _DIGIT_RUN.finditer(blanked):
        value = m.group().strip()
        if _DECIMAL_LIST.fullmatch(value) or _THOUSANDS_RANGE.fullmatch(value):
            continue  # «2.5 3.0 3.5 4.0» — список увеличений, «80 000-125 000» — диапазон
        hits.append(Hit(m.start(), m.end(), "номер", 2))
    return hits


# ============================================================ 3. контекст

_GAP = r"[\s:=—–\-№#.,]*(?:(?:у\s+меня|мой|моя|мое|моё|мои|клиента|компании|организации|такой|такие|следующий|is)[\s:=—–\-№#.,]*)?"
_NUMBER_VALUE = re.compile(r"\+?\d[\d\s\-–—()./]{2,40}\d")
_DATE_VALUE = re.compile(
    r"\d{1,2}[./\-\s]\d{1,2}[./\-\s]\d{2,4}|"
    r"\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+\d{4}(?:\s*г(?:ода|\.)?)?|"
    r"(?:19|20)\d{2}(?:\s*г(?:ода|\.)?)?",
    re.I,
)
_HANDLE_VALUE = re.compile(r"@?[A-Za-z][A-Za-z0-9_.]{3,31}")
_VALUES = {"date": _DATE_VALUE, "number": _NUMBER_VALUE, "handle": _HANDLE_VALUE, "index": re.compile(r"\d{6}")}
# Между триггером имени или фразы и значением — только пробелы, двоеточие, тире: «ФИО, телефон» — перечисление
_TIGHT_GAP = r"[ \t:=—–\-]*"


def _trigger(pattern: str, flags=re.I, gap: str = _GAP) -> re.Pattern:
    return re.compile(rf"(?<![\[\w])(?:{pattern})(?![\w]){gap}", flags)


# (триггер, метка, что берём после него)
_VALUE_TRIGGERS = [
    (_trigger(r"дата\s+рождения|год\s+рождения|д\.\s?р\.|д/р|родил(?:ся|ась)|рожд(?:ения|\.)|г\.\s?р\.|born|dob|date\s+of\s+birth"),
     "дата рождения", "date"),
    (_trigger(r"инн|огрн(?:ип)?|кпп|бик|окпо|октмо|снилс|р/с|р\.\s?с\.|к/с|к\.\s?с\.|л/с|"
              r"расч[её]тн\w*\s+сч[её]т\w*|корр?\.?\s*сч[её]т\w*|лицев\w*\s+сч[её]т\w*|номер\s+сч[её]та"),
     "реквизит", "number"),
    (_trigger(r"паспорт\w*|загран\w*|номер\s+паспорта|водительск\w*(?:\s+удостоверени\w*)?|в/у|права|"
              r"полис\w*|омс|дмс|свидетельств\w*|удостоверени\w*|код\s+подразделения"),
     "документ", "number"),
    (_trigger(r"тел(?:\.|ефон\w*)?|моб(?:\.|ильн\w*)?|сот(?:\.|ов\w*)|номер(?:\s+телефона)?|звоните|позвоните|"
              r"набер\w*|whats\s?app|ватсап\w*|вотсап\w*|вацап\w*|viber|вайбер\w*|telegram|телеграм\w*|тг|tg|phone"),
     "телефон", "number"),
    (_trigger(r"(?:telegram|телеграм\w*|телег\w*|тг|tg|инст\w*|instagram|vk|вк|вконтакте|ник\w*|username|юзернейм\w*|"
              r"skype|скайп\w*)(?:\s+(?:в|во)\s+[а-яёa-z]+)?"),
     "ник", "handle"),
    (_trigger(r"индекс\w*"), "адрес", "index"),
]
# После этих слов маскируем всё до конца фразы
_CLAUSE_TRIGGERS = [
    (_trigger(r"адрес(?:\s+доставки|\s+проживания|\s+регистрации)?|по\s+адресу|проживаю|живу|прописан[аы]?|"
              r"зарегистрирован[аы]?\s+по|доставьте\s+(?:на|по)|address", gap=_TIGHT_GAP), "адрес"),
    (_trigger(r"выдан[аоы]?", gap=_TIGHT_GAP), "документ"),
    (_trigger(r"пароль|логин|password|passwd|login|пин(?:-?код)?|pin|cvv|cvc|код\s+(?:из|в)\s+смс|"
              r"код\s+подтверждения|кодовое\s+слово|секретн\w*\s+слово", gap=_TIGHT_GAP), "данные"),
]
# Имя после этих слов; lower=True — и строчными («меня зовут вася»)
_NAME_TRIGGERS = [
    (_trigger(r"меня\s+зовут|зовут\s+меня|мо[её]\s+имя|имя\s+мо[её]|фамилия(?:,?\s+имя)?(?:,?\s+отчество)?|"
              r"имя(?:\s+и\s+фамилия)?|отчество|ф\.\s?и\.\s?о\.?|фио|зовут|звать|зовите\s+меня|"
              r"контактное\s+лицо|my\s+name\s+is|name\s*:", gap=_TIGHT_GAP), True),
    (_trigger(r"с\s+уважением,?|я\s*[—–-]|получатель|покупатель|заказчик|представитель|"
              r"i\s+am|i'm|best\s+regards,?|regards,?|sincerely,?", gap=_TIGHT_GAP), False),
    (_trigger(r"ип", gap=_TIGHT_GAP), True),
]
_NAME_PIECE = re.compile(r"[A-ZА-ЯЁ]\.|[A-Za-zА-Яа-яЁё]+(?:[-'’][A-Za-zА-Яа-яЁё]+)*")
_RU_STOP = set(
    "и а но или из с со в во на по от до для к ко о об у я мы вы он она они это этот эта мне меня нас "
    "хочу хотел хотела хотим нужен нужна нужно нужны ищу ищем подскажите интересует интересуют клиника "
    "клиники компания компании врач стоматолог доктор директор менеджер прошу пожалуйста спасибо здравствуйте "
    "добрый день вечер утро привет можно когда где как что какой какая какие сколько есть был была "
    "ооо ао зао пао оао нко гбу гауз фгбу ип".split()
)
_EN_STOP = set("and or i am a an the to of in at for from with need want looking interested please my we is".split())
_ABBR = set(
    "ул д кв г гор пр просп корп стр оф пер наб ш пл мкр мкрн обл р н пос с п пгт рп бул к лит пом эт под им ст дер респ т е".split()
)
_ADDRESS_HINT = re.compile(rf"\d|(?<![\w]){_STREET}|(?<![\w])(?:корп|стр|лит|оф|кв|под|эт|пом|д)\.?\s*[А-ЯA-Z](?![\w])", re.I)


def _stops_name(word: str, lower_ok: bool) -> bool:
    if len(word) == 2 and word.endswith("."):
        return False  # инициал
    low = word.lower()
    if low in _RU_STOP or low in _EN_STOP:
        return True
    if word[0].isupper():
        return False
    if not lower_ok or word.isascii():
        return True
    # строчными берём только то, что похоже на имя: «вася», «петров», незнакомую фамилию — но не «телефон»
    a = names.analyze(low)
    return a.known and a.personal == 0


def _name_value(text: str, pos: int, lower_ok: bool, protects) -> list[Hit]:
    hits = []
    for _ in range(3):
        m = _NAME_PIECE.match(text, pos)
        if not m or _stops_name(m.group(), lower_ok) or protects(m.group()):
            break
        hits.append(Hit(m.start(), m.end(), "имя", 0))
        pos = m.end()
        pos += len(re.match(r"[ \t]*", text[pos:]).group())
    return hits


def _sentence_end(text: str, start: int, limit: int = 150) -> int:
    end = min(len(text), start + limit)
    for i in range(start, end):
        ch = text[i]
        if ch in "\n!?;":
            return i
        if ch == "." and (i + 1 == len(text) or text[i + 1] in " \n"):
            word = re.search(r"([A-Za-zА-Яа-яЁё]+)$", text[start:i])
            # «ул.», «д.», инициал «Б.» — не конец фразы
            if not (word and (word.group(1).lower() in _ABBR or len(word.group(1)) == 1)):
                return i
    return end


# «Адрес вашего офиса?» — вопрос про наш адрес, а не адрес клиента
_NOT_CLIENT_ADDRESS = re.compile(
    r"\s*(?:вашего|вашей|ваш|ваша|ваши|магазина|офиса|склада|шоурума|салона|сайта|компании|самовывоза|пункта)\b", re.I,
)


def _clause_value(text: str, pos: int, label: str) -> Hit | None:
    """До конца фразы; адрес — первая часть до запятой и дальше, пока в частях есть номер или «ул.»."""
    if pos >= len(text) or not (text[pos].isalnum() or text[pos] in "«\"'"):
        return None  # «(ИНН, ФИО, телефон, адрес) клиент вводит…» — перечисление, а не значение
    end = _sentence_end(text, pos)
    if label == "адрес":
        if _NOT_CLIENT_ADDRESS.match(text, pos):
            return None
        cut = pos
        for n, part in enumerate(re.finditer(r"[^,]+", text[pos:end])):
            if n and not _ADDRESS_HINT.search(part.group()):
                break
            cut = pos + part.end()
        end = cut
    value = text[pos:end].rstrip(" \t.,")
    return Hit(pos, pos + len(value), label, 0) if value.strip() else None


def _context(text: str, protects) -> list[Hit]:
    hits = []
    for pattern, label, kind in _VALUE_TRIGGERS:
        for m in pattern.finditer(text):
            v = _VALUES[kind].match(text, m.end())
            if not v:
                continue
            if kind == "number" and len(re.sub(r"\D", "", v.group())) < 5:
                continue
            if kind == "handle" and protects(v.group().lstrip("@")):
                continue
            hits.append(Hit(v.start(), v.end(), label, 0))
    for pattern, label in _CLAUSE_TRIGGERS:
        for m in pattern.finditer(text):
            hit = _clause_value(text, m.end(), label)
            if hit:
                hits.append(hit)
    for pattern, lower_ok in _NAME_TRIGGERS:
        for m in pattern.finditer(text):
            hits += _name_value(text, m.end(), lower_ok, protects)
    return hits


# ============================================================ 5. защита каталога

# Цены, даты, артикулы, номера заказов и КП — не номера и не реквизиты
_PROTECTED = [
    _PLACEHOLDER,
    re.compile(
        r"(?<!\d)\d{1,3}(?:[ \u202f]\d{3})*(?:[.,]\d{1,2})?\s?(?:₽|руб(?:\.|лей|ля|ль)?|(?-i:р\.)|\$|€|usd|eur|евро|"
        r"долл\w*|тыс\.?(?:\s?руб\w*\.?)?|млн\.?(?:\s?руб\w*\.?)?)|(?<!\d)\d+(?:[.,]\d{1,2})?\s?(?:₽|руб\w*\.?)",
        re.I,
    ),
    re.compile(r"(?<!\d)(?:\d{1,2}[./]\d{1,2}[./](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})(?!\d)"),
    # «арт. 572-0012», «артикул BLTTLXCV», «SKU: CZE3E» — но не «Artyom»
    re.compile(
        r"(?<![\w])(?:артикул\w*|арт\.|арт(?=\s*\d)|art\.|art(?=\s*\d)|sku|код\s+товара)(?![\w])\s*[:№#]?\s*"
        r"[A-Za-zА-Яа-я0-9][\w\-./]*",
        re.I,
    ),
    re.compile(r"(?<![\w-])(?:AD|KP|КП|L)-\d+(?![\w-])"),
]
_STATIC_PHRASES = re.compile(
    r"(?<![\w])(?:карл[аеу]?\s+(?:цейсс?|зейсс)(?:а|у|ом|е)?|carl\s+zeiss|i\.\s?c\.\s?lercher|nsk\s+nakanishi)(?![\w])", re.I,
)
VOCABULARY_TTL = 600


class Vocabulary:
    """Защищённые слова: бренды, названия и артикулы каталога, латиница из базы знаний."""

    def __init__(self, phrases: list[str] = (), extra_text: str = "") -> None:
        phrases = sorted({p.strip() for p in phrases if p and len(p.strip()) >= 3}, key=len, reverse=True)
        self.phrases = (
            re.compile(r"(?<![\w])(?:" + "|".join(re.escape(p) for p in phrases) + r")(?![\w])", re.I)
            if phrases else None
        )
        words = [w for p in phrases for w in names.TOKEN.findall(p)]
        words += re.findall(r"\b[A-Za-z][A-Za-z]{2,}\b", extra_text)
        self.keys = {k for k in map(names.sound_key, words) if len(k) >= 3}

    def spans(self, text: str) -> list[tuple[int, int]]:
        found = [m.span() for m in _STATIC_PHRASES.finditer(text)]
        if self.phrases:
            found += [m.span() for m in self.phrases.finditer(text)]
        return found

    def protects(self, word: str) -> bool:
        parts = [word] + (word.split("-") if "-" in word else [])
        return any(self._protects(p) for p in parts if p)

    def _protects(self, word: str) -> bool:
        lower = word.lower().replace("ё", "е")
        if lower in names.STATIC_FORMS or lower.startswith(names.STATIC_PREFIXES):
            return True
        if names.sound_key(word) not in self.keys:
            return False
        # Бренд по звучанию не прикрывает слово, которое скорее имя: Sony ≠ «Соня». Такие бренды кириллицей
        # («Карл Цейс», «Никон») — только явным списком: names.STATIC_WORDS и _STATIC_PHRASES
        a = names.analyze(lower)
        return a.personal < 0.5 and a.patronymic == 0


_vocabulary = Vocabulary()
_vocabulary_loaded_at = 0.0


def set_vocabulary(vocabulary: Vocabulary) -> None:
    global _vocabulary, _vocabulary_loaded_at
    _vocabulary, _vocabulary_loaded_at = vocabulary, time.monotonic()


async def refresh_vocabulary(force: bool = False) -> None:
    """Бренды, товары и активная база знаний из БД — не чаще раза в 10 минут; сбой — остаётся прежний список."""
    if not force and _vocabulary_loaded_at and time.monotonic() - _vocabulary_loaded_at < VOCABULARY_TTL:
        return
    try:
        async with async_session_factory() as session:
            brands = list(await session.scalars(select(Brand.name)))
            products = (await session.execute(select(Product.name, Product.sku))).all()
            kb = await session.scalar(
                select(KnowledgeBaseVersion.content).where(KnowledgeBaseVersion.is_active.is_(True))
            )
    except Exception:
        logger.warning("Маскирование: не удалось загрузить бренды и товары", exc_info=True)
        return
    set_vocabulary(Vocabulary(brands + [n for n, _ in products] + [s for _, s in products if s], kb or ""))


# ============================================================ сборка


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = "\x00" * (end - start)
    return "".join(chars)


def _apply(text: str, hits: list[Hit]) -> str:
    merged: list[Hit] = []
    for hit in sorted(hits, key=lambda h: (h.start, -h.end)):
        if hit.end <= hit.start:
            continue
        last = merged[-1] if merged else None
        if last and hit.start < last.end:
            best = min(last, hit, key=lambda h: (h.rank, PRIORITY[h.label]))
            merged[-1] = Hit(last.start, max(last.end, hit.end), best.label, best.rank)
        elif last and hit.label == last.label and (
            (hit.label == "имя" and re.fullmatch(r"[ \t]+", text[last.end:hit.start]))
            or (hit.label == "адрес" and re.fullmatch(r"[\s,]*", text[last.end:hit.start]))
        ):
            merged[-1] = Hit(last.start, hit.end, hit.label, min(last.rank, hit.rank))
        else:
            merged.append(hit)
    out, pos = [], 0
    for hit in merged:
        out += [text[pos:hit.start], f"[{hit.label}]"]
        pos = hit.end
    out.append(text[pos:])
    return "".join(out)


def _mask_once(text: str, vocabulary: Vocabulary) -> str:
    protected = vocabulary.spans(text)
    for pattern in _PROTECTED:
        protected += [m.span() for m in pattern.finditer(text)]
    blanked = _blank(text, protected)

    hits = [Hit(*m.span(), label) for label, pattern in STRONG for m in pattern.finditer(text)]
    hits += [Hit(*m.span(), label, 2) for label, pattern in WEAK for m in pattern.finditer(blanked)]
    hits += _digit_runs(blanked)
    hits += _initials(blanked, vocabulary.protects)
    hits += _context(text, vocabulary.protects)
    hits += [Hit(s, e, "имя", 2) for s, e in names.find_names(blanked, vocabulary.protects)]
    return _apply(text, hits)


def mask_pii(text: str) -> str:
    vocabulary = _vocabulary
    text = normalize(text)
    before = Counter(_PLACEHOLDER.findall(text))
    # Контрольный проход: маскируем, пока текст меняется (обычно хватает одного прохода)
    for _ in range(3):
        masked = _mask_once(text, vocabulary)
        if masked == text:
            break
        text = masked
    total = Counter(_PLACEHOLDER.findall(text)) - before
    if total:
        logger.info("Маскирование: %s", ", ".join(f"{label}×{n}" for label, n in total.items()))
    return text
