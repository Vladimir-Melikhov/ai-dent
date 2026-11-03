"""Слой 4 маскирования: имена, фамилии и отчества по словарю русского языка (pymorphy3).

Словарь OpenCorpora помечает слова как имя (Name), фамилию (Surn) и отчество (Patr) и даёт
вероятность каждого разбора. Слово-омоним («Вера», «Роман», «Слава») считается именем, когда
написано с большой буквы не в начале предложения или стоит рядом с другим именем.
Защищённые слова (бренды, оптические термины) не маскируются никогда — см. Vocabulary.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

import pymorphy3
from pymorphy3.units import DictionaryAnalyzer

_morph = pymorphy3.MorphAnalyzer()

PERSONAL = frozenset({"Name", "Surn", "Patr"})

TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:[-'’][A-Za-zА-Яа-яЁё]+)*")
CYRILLIC = re.compile("[А-Яа-яЁё]")

# Фамилии, которых нет в словаре: по окончанию — только с большой буквы и не в начале предложения
SURNAME_SUFFIX = re.compile(
    r"(?:енко|швили|дзе|ян|янц|ов|ова|ев|ева|ин|ина|ын|ына|ский|ская|цкий|цкая|вич|вна|ук|юк|чук)$"
)

# Оптические термины и написания брендов кириллицей: в словаре они — имена и фамилии
STATIC_PREFIXES = ("галиле", "кеплер", "цейс")
# Строчные слова, которые словарь считает только именами, а в тексте они обычные: «авиа, авто или ж/д»
NOT_NAMES_LOWER = frozenset({"авиа"})
STATIC_WORDS = (
    "Галилей Кеплер Мора Цейс Цейсс Зейсс Лерхер Зумакс Наканиши Лейка Хайне Лабомед Никон Кэнон "
    "Сони Шотт Вариоскоп Экстаро Пико Прима"
).split()


@dataclass(frozen=True)
class Analysis:
    personal: float  # вероятность, что слово — имя, фамилия или отчество (по словарным разборам)
    patronymic: float
    known: bool  # слово есть в словаре
    pos: str | None  # часть речи самого вероятного разбора


@lru_cache(maxsize=50_000)
def analyze(word: str) -> Analysis:
    parses = _morph.parse(word)
    # Только словарные разборы: догадка по окончанию («эндодонтия» → имя) не считается
    known = [p for p in parses if len(p.methods_stack) == 1 and isinstance(p.methods_stack[0][0], DictionaryAnalyzer)]
    personal = sum(p.score for p in known if PERSONAL & p.tag.grammemes)
    patronymic = sum(p.score for p in known if "Patr" in p.tag.grammemes)
    return Analysis(personal, patronymic, bool(known), parses[0].tag.POS if parses else None)


def _static_forms() -> frozenset[str]:
    """Все падежные формы защищённых слов — только своей леммы: «Сони» не защищает «Соню»."""
    forms = set()
    for word in STATIC_WORDS:
        lower = word.lower().replace("ё", "е")
        forms.add(lower)
        for parse in _morph.parse(lower):
            if parse.normal_form.replace("ё", "е") == lower:
                forms.update(f.word.replace("ё", "е") for f in parse.lexeme)
    return frozenset(forms)


STATIC_FORMS = _static_forms()


# --- ключ «по звучанию»: Sirona = Сирона = Сироны, Zeiss = Цейс = Цейсса

_CYR2LAT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "a b v g d e e zh z i i k l m n o p r s t u f h ts ch sh sh _ i _ e iu ia".split(),
))
_CYR2LAT = {k: ("" if v == "_" else v) for k, v in _CYR2LAT.items()}
_CYR_ENDINGS = sorted(
    "ами ями ого его ому ему ыми ими ой ей ою ею ом ем ам ям ах ях ые ие ую юю ая яя ий ый а я ы и у ю е о".split(),
    key=len, reverse=True,
)
_LATIN_RULES = [
    (r"sch", "sh"), (r"ch", "h"), (r"ph", "f"), (r"ck", "k"), (r"qu", "kv"), (r"q", "k"), (r"x", "ks"),
    (r"w", "v"), (r"tz", "s"), (r"ts", "s"), (r"c(?=[eiy])", "s"), (r"c", "k"), (r"z", "s"), (r"y", "i"),
    (r"j", "i"), (r"kh", "h"), (r"th", "t"), (r"ei", "ai"), (r"ee", "i"), (r"oo", "u"), (r"ou", "u"),
    (r"^sht", "st"), (r"^shp", "sp"), (r"[^a-z]", ""), (r"(.)\1+", r"\1"),
]
_LATIN_RULES = [(re.compile(p), r) for p, r in _LATIN_RULES]


@lru_cache(maxsize=50_000)
def sound_key(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if CYRILLIC.search(w):
        for ending in _CYR_ENDINGS:
            if w.endswith(ending) and len(w) - len(ending) >= 3:
                w = w[: -len(ending)]
                break
        w = "".join(_CYR2LAT.get(c, c) for c in w)
    for pattern, replacement in _LATIN_RULES:
        w = pattern.sub(replacement, w)
    return w.rstrip("aeiou") or w


# --- латиница: Ivan Petrov → Иван Петров

_LAT_DIGRAPHS = [
    ("iya", "ия"), ("iyu", "ию"), ("shch", "щ"), ("sch", "ш"), ("yo", "ё"), ("yu", "ю"), ("ya", "я"), ("ye", "е"), ("zh", "ж"), ("kh", "х"),
    ("ts", "ц"), ("ch", "ч"), ("sh", "ш"), ("ph", "ф"), ("th", "т"), ("iy", "ий"), ("yy", "ый"), ("ey", "ей"),
    ("ay", "ай"), ("oy", "ой"), ("ck", "к"), ("qu", "кв"), ("ja", "я"), ("ju", "ю"), ("jo", "ё"),
]
_LAT1 = dict(zip("abcdefghijklmnopqrstuvwxyz", "а б к д е ф г х и й к л м н о п к р с т у в в кс й з".split()))


def to_cyrillic(word: str) -> str:
    w, out, i = word.lower(), [], 0
    while i < len(w):
        for latin, cyr in _LAT_DIGRAPHS:
            if w.startswith(latin, i):
                out.append(cyr)
                i += len(latin)
                break
        else:
            out.append(_LAT1.get(w[i], w[i]))
            i += 1
    return "".join(out)


def latin_personal(word: str) -> float:
    """Похожесть латинского слова на имя: перебираем варианты транслитерации, берём лучший."""
    variants = {to_cyrillic(word)}
    consonant = "бвгджзклмнпрстфхцчшщ"
    for pattern, replacement in (
        (rf"л(?=[{consonant}])", "ль"),  # Olga → Ольга
        (rf"(?<=[{consonant}])(?=[яюёе])", "ь"),  # Darya → Дарья, Grigorev → Григорьев
        (rf"(?<=[{consonant}])й(?=[{consonant}])", "ы"),  # Davydova → Давыдова
    ):
        variants |= {re.sub(pattern, replacement, v) for v in variants}
    return max(analyze(v).personal for v in variants)


# --- поиск имён

_START_SKIP = set(" \t\"'«»“”„()[]-–—•*·")


def sentence_start(text: str, index: int) -> bool:
    j = index - 1
    while j >= 0 and text[j] in _START_SKIP:
        j -= 1
    return j < 0 or text[j] in ".!?…\n:;|"


def _classify_cyrillic(text: str, start: int, word: str) -> str | None:
    """'name' — точно имя; 'cand' — может быть частью имени рядом с другим; None — не имя."""
    parts = word.lower().split("-")
    analyses = [analyze(p) for p in parts]
    if len(parts) > 1 and not all(a.personal > 0 for a in analyses):
        # «Мора-интерфейс», «Санкт-Петербург»: имя — только если имена все части («Анна-Мария»)
        return "cand" if word[0].isupper() and any(a.personal > 0 for a in analyses) else None
    a = max(analyses, key=lambda x: x.personal)
    cap = word[0].isupper()
    if not cap and word in NOT_NAMES_LOWER:
        return None
    if word.isupper() and len(word) <= 4:
        # ИНН, КПП, ПЭК — аббревиатуры; «АЛЛА», «АШОТ» заглавными — имя, только если рядом другое слово-имя
        return "weak" if a.personal >= 0.5 else "cand"
    at_start = sentence_start(text, start)
    if a.patronymic >= 0.2 and (cap or a.patronymic >= 0.5):
        return "name"
    if a.known and a.personal >= 0.8:
        return "name"
    if cap and not at_start and a.personal >= 0.05:
        return "name"
    if cap and at_start and a.personal >= 0.5:
        return "name"
    if cap and not at_start and not a.known and len(word) >= 5 and SURNAME_SUFFIX.search(word.lower()):
        return "name"
    if cap and (a.personal > 0 or not a.known):
        return "cand"
    return None


def _classify_latin(word: str) -> str | None:
    if not (word[0].isupper() and word[1:].islower() and len(word) >= 3):
        return None
    return "latin" if latin_personal(word) >= 0.5 else "cand"


def find_names(text: str, protects) -> list[tuple[int, int]]:
    """Отрезки с именами. text — с вымаранными защищёнными местами; protects(word) — защищено ли слово."""
    tokens = [(m.start(), m.end(), m.group()) for m in TOKEN.finditer(text)]
    states, scores = [], []
    for start, _, word in tokens:
        if len(word) < 2 or protects(word):
            states.append(None)
        elif CYRILLIC.search(word):
            states.append(_classify_cyrillic(text, start, word))
        else:
            states.append(_classify_latin(word))
        scores.append(analyze(word.lower()).personal if CYRILLIC.search(word) else latin_personal(word))
    # Слово, которого нет в словаре: «Кацман», «КРИВОШЕИНА», «Gogishvili» — рядом с именем это фамилия
    unknown = [not analyze(w.lower() if CYRILLIC.search(w) else to_cyrillic(w)).known for _, _, w in tokens]

    # Группы соседних слов в одной строке: «Вера Кузнецова», «Ivan Petrov», «Иванов Мкртчян»
    found, i = [], 0
    while i < len(tokens):
        if states[i] is None:
            i += 1
            continue
        j = i
        while (j + 1 < len(tokens) and states[j + 1] is not None
               and re.fullmatch(r"[ \t]{1,3}", text[tokens[j][1]:tokens[j + 1][0]])):
            j += 1
        group = states[i:j + 1]
        span = range(i, j + 1)
        # «ПОЛЯКОВ ЛЕВ», «Роман Кацман», «Roman Katsman» в начале фразы: оба слова бывают именами
        # или одно бывает именем, а другого нет в словаре
        both_names = len(group) >= 2 and all(scores[k] > 0 or unknown[k] for k in span) and (
            max(scores[i:j + 1]) >= 0.5 or (any(scores[k] > 0 for k in span) and any(unknown[k] for k in span))
        )
        # «Nadezhda Katsman», «Roman Polyakov» — два латинских слова: одно похоже на имя или оба бывают именами
        latin_pair = len(group) >= 2 and all(tokens[k][2].isascii() for k in span) and (
            max(scores[i:j + 1]) >= 0.3 or all(scores[k] > 0 for k in span)
        )
        if "name" in group or ("weak" in group and len(group) >= 2) or both_names or latin_pair:
            # кандидаты-латиница рядом с кириллическим именем — тоже часть имени
            found += [tokens[k][:2] for k in range(i, j + 1) if states[k] is not None]
        elif "latin" in group and len(group) >= 2:
            found += [tokens[k][:2] for k in range(i, j + 1)]
        i = j + 1
    return found
