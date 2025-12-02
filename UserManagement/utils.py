from rest_framework_simplejwt.tokens import RefreshToken

def get_tokens_for_user(user, tenant_schema):
    refresh = RefreshToken.for_user(user)
    refresh['tenant'] = tenant_schema

    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }

import random
from django.utils import timezone
from datetime import timedelta

def generate_otp():
    return str(random.randint(100000, 999999))

def is_otp_valid(user):
    if not user.otp_created_at:
        return False
    return timezone.now() <= user.otp_created_at + timedelta(minutes=5)
