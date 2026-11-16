import re

from django.db import migrations


def normalize(apps, schema_editor):
    """Существующие номера — к виду +7XXXXXXXXXX; если такой номер уже занят, запись не трогаем."""
    User = apps.get_model("accounts", "User")
    taken = set(User.objects.values_list("phone", flat=True))
    for user in User.objects.all():
        digits = re.sub(r"\D", "", user.phone)
        if len(digits) == 11 and digits[0] in "78":
            digits = digits[1:]
        if len(digits) != 10:
            continue
        phone = "+7" + digits
        if phone == user.phone or phone in taken:
            continue
        taken.discard(user.phone)
        taken.add(phone)
        user.phone = phone
        user.save(update_fields=["phone"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunPython(normalize, migrations.RunPython.noop)]
