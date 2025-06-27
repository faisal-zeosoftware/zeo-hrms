from django.contrib.auth.base_user import BaseUserManager
from django.utils.translation import gettext_lazy as _
from django.db import transaction

class CustomUserManager(BaseUserManager):
    """
    Custom user model manager where email is the unique identifiers
    for authentication instead of usernames.
    """

    def create_user(self, username, password=None, email=None, is_ess=False, **extra_fields):
        if not username:
            raise ValueError(_("The Username must be set"))
        if not is_ess and not email:
            raise ValueError(_("The Email must be set for non-ESS users"))
        email = self.normalize_email(email) if email else None
        user = self.model(username=username, email=email, is_ess=is_ess, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        """
        Create and save a SuperUser with the given username, email, and password.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if not extra_fields.get("is_staff"):
            raise ValueError(_("Superuser must have is_staff=True."))
        if not extra_fields.get("is_superuser"):
            raise ValueError(_("Superuser must have is_superuser=True."))

        return self.create_user(username, password, email=email, **extra_fields)


    def delete_user(self, user_obj):
        with transaction.atomic(): # added transaction
            #Handle Tenant Relationships (Crucial!)
            for tenant in user_obj.tenants.all():
                if tenant.owner == user_obj:
                    # If user owns the tenant, delete the tenant
                    tenant.delete() # this will handle the user deletion within the tenant
                else:
                    # If user is just a member, remove them from the tenant
                    tenant.remove_user(user_obj)

            # Now, actually delete the user
            user_obj.delete()