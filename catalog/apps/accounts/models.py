import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models


phone_validator = RegexValidator(
    regex=r"^\+?[1-9]\d{9,14}$",
    message="Телефон в формате +79991234567",
)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password, **extra_fields):
        if not phone:
            raise ValueError("Телефон обязателен")
        user = self.model(phone=phone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra_fields)

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.DIRECTOR)
        return self._create_user(phone, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        CUSTOMER = "customer", "Клиент"
        STAFF = "staff", "Персонал"
        DIRECTOR = "director", "Директор"

    phone = models.CharField(
        max_length=20, unique=True, validators=[phone_validator], verbose_name="Телефон"
    )
    email = models.EmailField(null=True, blank=True, verbose_name="Email")
    first_name = models.CharField(max_length=150, blank=True, verbose_name="Имя")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER, verbose_name="Роль")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self):
        return self.phone


class AnonSession(models.Model):
    session_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    merged_into = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="merged_sessions"
    )

    class Meta:
        verbose_name = "Анонимная сессия"
        verbose_name_plural = "Анонимные сессии"

    def __str__(self):
        return str(self.session_id)


class ShortlistItem(models.Model):
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="shortlist_items"
    )
    session = models.ForeignKey(
        AnonSession, null=True, blank=True, on_delete=models.CASCADE, related_name="shortlist_items"
    )
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="shortlisted_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Избранное"
        verbose_name_plural = "Избранное"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | models.Q(session__isnull=False),
                name="shortlist_user_or_session",
            ),
        ]
        unique_together = [("user", "product"), ("session", "product")]

    def __str__(self):
        return f"{self.product_id} -> {self.user_id or self.session_id}"
