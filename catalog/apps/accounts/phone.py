import re


def normalize_phone(raw) -> str | None:
    """8…/7…/+7… → +7XXXXXXXXXX; None — если номер некорректный."""
    if not isinstance(raw, str):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return "+7" + digits
