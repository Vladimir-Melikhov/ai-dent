"""Поля админки, которые не заставляют вписывать единицы и знаки руками.

В базе остаётся обычный текст («200 мм», «2.8×», «0–40°»): его читают чат, сравнение и страница товара.
В форме человек вводит только число (точка или запятая — всё равно); единица видна в подписи и дописывается сама.
"""
import re
from decimal import Decimal, InvalidOperation

from django import forms

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
FIXED = "фикс"


def parse_number(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation:
        raise forms.ValidationError("Введите число, например 2.8 или 2,8") from None


def fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def numbers(text: str) -> list[Decimal]:
    return [parse_number(m) for m in NUMBER.findall(text or "")]


class _Base(forms.CharField):
    placeholder = ""

    def __init__(self, *, placeholder="", **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.TextInput(attrs={"inputmode": "decimal", "placeholder": placeholder,
                                                           "autocomplete": "off", "size": 12}))
        super().__init__(**kwargs)


class NumberField(_Base):
    """Одно число. Хранится как «число + единица»: «200 мм», «2.8×»."""

    def __init__(self, unit: str, *, glue: str = " ", **kwargs):
        self.unit, self.glue = unit, glue
        super().__init__(**kwargs)

    def prepare_value(self, value):
        found = NUMBER.search(str(value or ""))
        return found.group().replace(",", ".") if found else (value or "")

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return ""
        found = NUMBER.fullmatch(re.sub(r"\s*(мм|г|гр|×|x|х|mm)\.?$", "", value.strip(), flags=re.I).strip())
        if not found:
            raise forms.ValidationError("Только число, например 200 — единицу допишем сами")
        number = parse_number(found.group())
        if number <= 0:
            raise forms.ValidationError("Число должно быть больше нуля")
        return f"{fmt(number)}{self.glue}{self.unit}"


class RangeField(_Base):
    """Одно число или диапазон «от–до». `single` — как записать одно число; allow_fixed — слово «фикс»."""

    def __init__(self, unit: str, *, single, allow_fixed: bool = False, **kwargs):
        self.unit, self.single, self.allow_fixed = unit, single, allow_fixed
        super().__init__(**kwargs)
        self.widget.attrs["inputmode"] = "text"
        self.widget.attrs["size"] = 14

    def prepare_value(self, value):
        text = str(value or "")
        if text.strip().lower() == FIXED:
            return FIXED
        found = numbers(text)
        if len(found) == 2:
            return f"{fmt(found[0])}–{fmt(found[1])}"
        if len(found) == 1 and "–" not in text:
            return fmt(found[0]) if text.startswith("до") or not text.startswith("0–") else text
        return text

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return ""
        text = value.strip().lower()
        if self.allow_fixed and text.startswith(("фикс", "нет", "-", "—")):
            return FIXED
        found = numbers(text)
        if len(found) == 1:
            return self.single(fmt(found[0]), self.unit)
        if len(found) == 2 and found[0] < found[1]:
            return f"{fmt(found[0])}–{fmt(found[1])}{self.unit if self.unit == '°' else ' ' + self.unit}"
        raise forms.ValidationError(
            "Число, диапазон (например 0-40)" + (" или слово «фикс»" if self.allow_fixed else "")
        )


def with_current(choices: list[str], current: str) -> list[tuple[str, str]]:
    """Пункты меню; значение, уже сохранённое в базе, не теряется, даже если его нет в списке."""
    values = list(choices)
    if current and current not in values:
        values.append(current)
    return [("", "— выберите —")] + [(v, v) for v in values]


class CharacteristicsField(forms.Field):
    """Характеристики обычным текстом вместо JSON.

        Гарантия: 12 месяцев        ← одиночная характеристика
        [Оптика]                    ← группа: ниже её строки «Название: значение»
        Увеличение: 2.8x

    Хранится как раньше: словарь «название → значение» и «группа → список строк»."""

    widget = forms.Textarea(attrs={"rows": 10, "style": "width:100%;font-family:inherit"})

    def prepare_value(self, value):
        if isinstance(value, str):
            return value
        value = value or {}
        singles = [f"{k}: {v}" for k, v in value.items() if not isinstance(v, (list, dict))]
        groups = []
        for key, val in value.items():
            if isinstance(val, dict):
                val = [f"{k}: {v}" for k, v in val.items()]
            if isinstance(val, list):
                groups.append(f"[{key}]\n" + "\n".join(str(x) for x in val))
        parts = ["\n".join(singles)] if singles else []
        parts += groups
        return "\n\n".join(parts)

    def to_python(self, value):
        result: dict = {}
        group = None
        for number, raw in enumerate((value or "").splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            head = re.fullmatch(r"\[(.+)\]", line)
            if head:
                group = head.group(1).strip()
                result.setdefault(group, [])
            elif group is not None:
                result[group].append(line)
            else:
                name, sep, rest = line.partition(":")
                if not sep or not name.strip() or not rest.strip():
                    raise forms.ValidationError(f"Строка {number}: нужен вид «Название: значение»")
                result[name.strip()] = rest.strip()
        return result
