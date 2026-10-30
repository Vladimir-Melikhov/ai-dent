# Параметры кода передаются и провайдеру SMS (Notificore: срок 30–300 сек, попыток 1–5)
OTP_TTL = 60
OTP_CODE_DIGITS = 4
OTP_KEY = "otp:{phone}"
OTP_ATTEMPTS_KEY = "otp:attempts:{phone}"
OTP_MAX_ATTEMPTS = 2

OTP_RESEND_KEY = "otp:resend:{phone}"
OTP_RESEND_COOLDOWN = 60

OTP_IP_KEY = "otp:ip:{ip}"
OTP_IP_HOURLY_LIMIT = 10
