from __future__ import annotations

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class User(AbstractUser):
    nickname = models.CharField(max_length=64, blank=True)
    objects = UserManager()

    class Meta:
        app_label = "custom_user_app"
