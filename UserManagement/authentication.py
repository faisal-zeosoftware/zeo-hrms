from django.contrib.auth.backends import BaseBackend
from django.contrib.auth import get_user_model
from .models import CustomUser
from django.contrib.auth.backends import ModelBackend

class CustomAuthBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username:
            try:
                # Attempt to authenticate with username if is_ess is True
                user = CustomUser.objects.get(username=username)
                if user.is_ess:
                    if user.check_password(password):
                        return user
                else:
                    print(f"User {username} is not ESS, so username authentication is not allowed.")
                    return None
            except CustomUser.DoesNotExist:
                # If username fails, attempt to authenticate with email if is_ess is False
                try:
                    user = CustomUser.objects.get(email=username)
                    if not user.is_ess:
                        if user.check_password(password):
                            return user
                    else:
                        print(f"User {username} is ESS, so email authentication is not allowed.")
                except CustomUser.DoesNotExist:
                    return None
        return None