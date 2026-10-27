
from rest_framework import serializers
from .models import CustomUser,company,Domain
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.contenttypes.models import ContentType
from django_tenants.utils import schema_context
# from Core .models import TaxSystem,crncy_mstr
from Core .models import TaxSystem,crncy_mstr,state_mstr
from Core .serializer import StateSerializer
from tenant_users.tenants.models import UserTenantPermissions
from calendars .models import employee_leave_request
from EmpManagement .models import GeneralRequest
from .models import UserNotificationInbox
from django.db.models import Count
from OrganisationManager.models import UserBranchAccess, brnch_mstr
       
class CustomUserSerializer(serializers.ModelSerializer):
    tenants = serializers.PrimaryKeyRelatedField(queryset=company.objects.all(), many=True, write_only=True)
    allocated_tenants = serializers.SerializerMethodField(read_only=True)
    user_groups = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CustomUser
        fields = '__all__'
        extra_kwargs = {
            'password': {'write_only': True, 'required': False, 'allow_null': True, 'default': None}
        }

    # v1.62.0 the sign-in code (otp) and its time were returned with every user of the list – never read or written here
    def get_fields(self):
        f = super().get_fields()
        for k in ('otp', 'otp_created_at'):
            f.pop(k, None)
        return f

    def _visible_ids(self):
        """v1.26.0: an administrator sees only their own companies of a login shared with another account."""
        req = self.context.get('request') if hasattr(self, 'context') else None
        u = getattr(req, 'user', None)
        if u is None or not getattr(u, 'is_authenticated', False) or u.is_superuser:
            return None
        try:
            from ZeoCloud.services import is_platform_admin
            if is_platform_admin(u):
                return None
        except Exception:
            pass
        return set(u.tenants.values_list('id', flat=True))

    def get_allocated_tenants(self, obj):
        tenants = obj.tenants.all().select_related('country', 'currency')   # v1.28.1
        ids = self._visible_ids_memo()
        if ids is not None and obj.pk != self.context['request'].user.pk:
            tenants = tenants.filter(id__in=ids)
        serializer = UserAllocatedCompanySerializer(
            tenants,
            many=True,
            # v1.62.0 speed: one memo for the whole user list – states, tax and the branch list of a company are read
            # once, not once per user and company (the user list made ~3,800 queries)
            context={"user": obj, "request": self.context.get('request') if hasattr(self, 'context') else None,
                     '_zmemo': self.context.setdefault('_zmemo', {}) if isinstance(getattr(self, 'context', None), dict) else {}}
        )
        return serializer.data

    def _visible_ids_memo(self):
        # v1.62.0 speed: the requester's companies once per list (was one query per listed user)
        ctx = self.context if isinstance(getattr(self, 'context', None), dict) else None
        if ctx is None:
            return self._visible_ids()
        if '_zvisible' not in ctx:
            ctx['_zvisible'] = self._visible_ids()
        return ctx['_zvisible']

    def _group_names_map(self, sch):
        """v1.62.0 speed: {login id: [group names]} of the open company, read once per list (two queries per user before)."""
        ctx = self.context if isinstance(getattr(self, 'context', None), dict) else None
        if ctx is None or getattr(self, 'parent', None) is None:
            return None   # a single user: the plain lookups below
        memo = ctx.setdefault('_zmemo', {})
        key = ('groups', sch)
        if key not in memo:
            out = {}
            try:
                through = UserTenantPermissions.groups.through
                with schema_context(sch):
                    for pid in UserTenantPermissions.objects.values_list('profile_id', flat=True):
                        out[pid] = []
                    from ZeoApps import groupscope   # v1.62.0 isolation: the company's own copy instead of the template, its name
                    for pid, gid in through.objects.order_by('group_id', 'id').values_list('usertenantpermissions__profile_id', 'group_id'):
                        out[pid].append(gid)
                    for pid in out:
                        out[pid] = [groupscope.label(g) for g in groupscope.substitute(out[pid])]
            except Exception:
                out = None
            memo[key] = out
        return memo[key]
    def get_user_groups(self, obj):
        """Retrieve assigned groups for the user via UserTenantPermissions."""
        req = self.context.get('request') if hasattr(self, 'context') else None
        sch = req.query_params.get('schema') if req is not None and hasattr(req, 'query_params') else None
        if sch and sch != 'public':   # v1.26.0: the groups of the open company (an invited login has its own there)
            gmap = self._group_names_map(sch)
            if gmap is not None and obj.pk in gmap:
                return gmap[obj.pk]
            try:
                with schema_context(sch):
                    utp = UserTenantPermissions.objects.get(profile=obj)
                    from ZeoApps import groupscope   # v1.62.0 isolation
                    return [groupscope.label(g) for g in utp.groups.all()]
            except Exception:
                pass
        try:
            user_tenant_permissions = UserTenantPermissions.objects.get(profile=obj)
            return [group.name for group in user_tenant_permissions.groups.all()]  # Fetch group names
        except UserTenantPermissions.DoesNotExist:
            return []  # Return empty if no groups assigned
    def to_representation(self, instance):
        # v1.28.1: allocated_tenants and user_groups are SerializerMethodFields already – computing them a second
        # time here doubled the work of every /users/api/user/<id>/ call
        return super().to_representation(instance)

    def create(self, validated_data):
        tenants_data = validated_data.pop('tenants', [])
        password = validated_data.pop('password', None)
        user = super().create(validated_data)
        if password:  # Only set password if provided
            user.set_password(password)
        user.save()
        
        # Add tenants to user
        user.tenants.set(tenants_data)
        return user

    def update(self, instance, validated_data):
        # Pop optional fields
        tenants_data = validated_data.pop('tenants', None)
        password = validated_data.pop('password', None)

        # Update password only if provided and not None
        if password is not None:
            instance.set_password(password)

        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()

        # Update tenants if provided
        if tenants_data is not None:
            ids = self._visible_ids()
            if ids is not None:   # v1.26.0: companies the requester does not manage are kept (a shared login keeps its other account)
                keep = [c for c in instance.tenants.all() if c.pk not in ids]
                tenants_data = [c for c in tenants_data if c.pk in ids] + keep
            instance.tenants.set(tenants_data)
        
        return instance

# class CustomUserSerializer(UserSerializer):
# from oauth2_provider.models import AccessToken
from django.utils import timezone
import uuid
from datetime import timedelta




class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        # Determine if the user is attempting to authenticate with username or email
        username_or_email = attrs.get("username")
        password = attrs.get("password")

        # Check if username_or_email is a valid email format
        if "@" in username_or_email:
            user = CustomUser.objects.filter(email=username_or_email).first() or CustomUser.objects.filter(email__iexact=username_or_email, is_active=True).first()
        else:
            user = CustomUser.objects.filter(username=username_or_email).first()

        if user:
            # Check if the user is active, if not, prevent login
            if not user.is_active:
                raise serializers.ValidationError("Your account is deactivated. Please contact support.")
            from ZeoCloud import sso as _sso   # v1.54.0 the company signs in with its identity provider only
            _p = _sso.password_blocked(user)
            if _p is not None:
                from Translations.i18n import tr as _tr
                raise serializers.ValidationError(_tr('Your company signs in with "{0}" – use that button.', _tr(_p.name)))
            from ZeoCloud import security as _sec   # v1.52.0 lock after wrong passwords, authenticator app, sessions
            _s = _sec.sec(user)
            if _sec.locked(user, _s):
                _m = int((_s.locked_until - timezone.now()).total_seconds() // 60) + 1
                if _m > 1440:   # v1.62.0 locked until HR unlocks it (start password)
                    from Translations.i18n import tr as _tr
                    raise serializers.ValidationError(_tr('Your account is locked after wrong tries – ask HR to unlock it.'))
                raise serializers.ValidationError(f"Too many wrong tries – try again in {_m} minute(s).")

            # Validate the user based on the is_ess field
            from ZeoCloud import directory as _dir   # v1.54.0 a directory (Active Directory) domain: the domain controller checks the password
            _dirp = _dir.provider_ldap(user.email) if "@" in username_or_email else None
            if user.is_ess and user.username == username_or_email and "@" not in username_or_email:   # v1.62.0 the company's Sign-in ID rule
                from ZeoAdmin.giveaccess import email_required as _ereq
                if _ereq(user):
                    from Translations.i18n import tr as _tr
                    raise serializers.ValidationError(_tr('Sign in with your e-mail address.'))
            if (user.is_ess and (user.username == username_or_email or ("@" in username_or_email and (user.email or '').lower() == username_or_email.lower()))) or (
                    not user.is_ess and user.email == username_or_email) or (
                    _dirp is not None and (user.email or '').lower() == username_or_email.lower()):
                _ok, _how = _dir.password_ok(user, password)
                if _ok:
                    self.user = user
                    _sec.login_ok(user)
                    from ZeoCloud import netguard as _ng   # v1.61.0 the company's networks
                    _rq = self.context.get("request")
                    if _ng.refused_everywhere(user, _ng.client_ip(_rq)):
                        _ng.refused_signin(user, _rq)
                        from Translations.i18n import tr as _tr
                        raise serializers.ValidationError(_tr('Your company allows ZEO only from its own networks. Your address {0} is not one of them.', _ng.client_ip(_rq)))
                    from ZeoCloud import geo as _geo   # v1.62.0 the company's countries
                    _cc = _geo.refused_everywhere(user, _ng.client_ip(_rq))
                    if _cc:
                        _sec.event('country_refused', user=user, ip=_ng.client_ip(_rq), detail=f'Sign-in with the right password refused: address {_ng.client_ip(_rq)} is in {_cc}.')
                        from Translations.i18n import tr as _tr
                        raise serializers.ValidationError(_tr('Your company allows ZEO only from its countries. Your address {0} is in {1}.', _ng.client_ip(_rq), _cc))
                    from ZeoAdmin.giveaccess import default_state as _dstate   # v1.62.0 the company default password
                    _ds = _dstate(user)
                    if _ds is not None:
                        from Translations.i18n import tr as _tr
                        if _ds['expired']:
                            raise serializers.ValidationError(_tr('Your start password has expired – ask HR to give you access again.'))
                        from django.core import signing
                        return {"first_check": True, "user_id": user.id, "username": user.username,
                                "challenge": signing.dumps({"u": user.id, "s": _ds['schema']}, salt="zeo-first-check")}
                    if _s.totp_on:   # v1.52.0 the authenticator code comes next: no tokens yet
                        from django.core import signing
                        return {"two_factor": "totp", "user_id": user.id, "username": user.username,
                                "challenge": signing.dumps({"u": user.id}, salt="zeo-2fa-login")}
                    data = {}   # v1.62.0 the tokens come from the session below (simplejwt's own check could not take an employee's e-mail)
                    _req = self.context.get("request")
                    _t = _sec.tokens(user, _req, _how)
                    data["access"], data["refresh"] = _t["access"], _t["refresh"]
                    _rules = _sec.strictest(user)
                    data["password_expired"] = _sec.password_expired(user)
                    data["setup_2fa"] = _sec.needs_2fa(user, _rules)
                    data["user_id"] = user.id
                    data["username"] = user.username
                    data["must_change_password"] = user.must_change_password
                    data["tenants"] = [
                        {
                            "id": tenant.id,
                            "name": tenant.name,
                            "schema_name": tenant.schema_name
                        }
                        for tenant in user.tenants.all()
                    ]
                    data["tenant_id"] = [tenant.schema_name for tenant in user.tenants.all()]
                    return data
                else:
                    _sec.fail(user, _s)
                    raise serializers.ValidationError("Invalid password")
            else:
                if user.is_ess:
                    raise serializers.ValidationError("ESS users must authenticate with their username")
                else:
                    raise serializers.ValidationError("Non-ESS users must authenticate with their email")
        else:
            raise serializers.ValidationError("User not found")

        return super().validate(attrs)
    
class CompanySerializer(serializers.ModelSerializer):
    branches = serializers.SerializerMethodField()
    tax_details = serializers.SerializerMethodField()
    currency_details = serializers.SerializerMethodField()
    state_label = serializers.SerializerMethodField()  # Avoid duplicate logic
    states = serializers.SerializerMethodField()
    notification_count = serializers.SerializerMethodField()
    latest_notifications = serializers.SerializerMethodField()
    # country = serializers.CharField(source='country.country_name', read_only=True)
    # state = serializers.CharField(source='state.state_name', read_only=True)
    # currency = serializers.CharField(source='currency.currency_name', read_only=True)

    class Meta:
        model = company
        fields = '__all__'

    def _zmemo(self, key, fn):
        """v1.62.0 speed: the same value for every company of one response (states / tax of a country, a company's
        branch list) is read once – shared through the serializer context ('_zmemo')."""
        ctx = self.context if isinstance(getattr(self, 'context', None), dict) else None
        if ctx is None:
            return fn()
        memo = ctx.setdefault('_zmemo', {})
        if key not in memo:
            memo[key] = fn()
        return memo[key]

    def validate_name(self, v):
        """v1.28.1: two companies cannot carry the same name (ignoring case and spacing) – the second one could not be
        told apart in "Choose a company", imports and reports."""
        name = ' '.join(str(v or '').split())
        if not name:
            raise serializers.ValidationError('Enter the company name.')
        qs = company.objects.filter(name__iexact=name)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(f'A company named "{name}" already exists. Use a different name (for example add the emirate or the branch).')
        return name

    def get_branches(self, obj):
        from OrganisationManager.models import brnch_mstr
        from django_tenants.utils import schema_context

        """
        Return branch id and name for this company (tenant)
        """
        try:
            with schema_context(obj.schema_name):
                qs = brnch_mstr.objects.all()
                request = self.context.get('request')
                user = getattr(request, 'user', None)
                if user is not None and user.is_authenticated and not user.is_superuser:
                    # only the branches this user may work in (User branch access / own branch)
                    from tenant_users.tenants.models import UserTenantPermissions
                    from OrganisationManager.models import UserBranchAccess
                    from EmpManagement.models import emp_master
                    if not _zeo_is_admin(user):
                        ids = [i for i in UserBranchAccess.objects.filter(user=user).values_list('branch__id', flat=True) if i]
                        if not ids:
                            ids = list(emp_master.objects.filter(users=user).values_list('emp_branch_id', flat=True))
                        qs = qs.filter(id__in=ids)
                return list(qs.values("id", "branch_name"))
        except Exception:
            return []
    def get_states(self, obj):
        if obj.country:
            def _states():
                states = state_mstr.objects.filter(country=obj.country, is_active=True).select_related('country')   # v1.28.1
                return StateSerializer(states, many=True).data
            return self._zmemo(('states', obj.country_id), _states)   # v1.62.0 once per country
        return []
    def get_tax_details(self, obj):
        """Fetch tax details dynamically from the TaxSystem model"""
        def _tax():
            tax = TaxSystem.objects.filter(country=obj.country, is_active=True).first()
            if tax:
                return {"tax_name": tax.tax_name, "tax_percentage": tax.tax_percentage}
            return None  # If no tax is found
        return self._zmemo(('tax', obj.country_id), _tax)   # v1.62.0 once per country
    def get_currency_details(self, obj):
        currency = obj.currency
        if not currency and obj.country:
            currency = self._zmemo(('currency', obj.country_id), lambda: obj.country.currency.first())   # v1.62.0 once per country

        if currency:
            return {
                "currency_name": currency.currency_name,
                "currency_code": currency.currency_code,
                "symbol": currency.symbol
            }

        return None
    def get_state_label(self, obj):
        return obj.country.get_state_label() if obj.country else None  # Use method from model
    def _want_notifications(self):
        req = self.context.get('request') if hasattr(self, 'context') else None
        return bool(req is not None and hasattr(req, 'query_params') and req.query_params.get('notifications'))

    def get_notification_count(self, obj):

        user = self.context.get("user")

        if not user or not self._want_notifications():   # v1.28.1 only when asked (?notifications=1)
            return 0

        total_count = 0

        try:

            with schema_context(obj.schema_name):

                # =====================================
                # LEAVE REQUEST NOTIFICATIONS
                # =====================================

                leave_count = employee_leave_request.objects.filter(
                    status='pending'
                ).count()

                total_count += leave_count

                # =====================================
                # GENERAL REQUEST NOTIFICATIONS
                # =====================================

                general_request_count = GeneralRequest.objects.filter(
                    status='pending'
                ).count()

                total_count += general_request_count

        except Exception:
            pass

        return total_count

    # =========================================================
    # LATEST NOTIFICATIONS
    # =========================================================

    def get_latest_notifications(self, obj):

        user = self.context.get("user")

        if not user or not self._want_notifications():   # v1.28.1 only when asked (?notifications=1)
            return []

        notifications = []

        try:

            with schema_context(obj.schema_name):

                # =====================================
                # LEAVE REQUESTS
                # =====================================

                leave_requests = employee_leave_request.objects.filter(
                    status='pending'
                ).order_by('-id')[:5]

                for leave in leave_requests:

                    notifications.append({
                        "type": "leave_request",
                        "title": "Leave Request",
                        "message": f"{leave.employee.first_name} submitted leave request",
                        "created_at": leave.created_at
                    })

                # =====================================
                # GENERAL REQUESTS
                # =====================================

                general_requests = GeneralRequest.objects.filter(
                    status='pending'
                ).order_by('-id')[:5]

                for req in general_requests:

                    notifications.append({
                        "type": "general_request",
                        "title": "General Request",
                        "message": req.subject,
                        "created_at": req.created_at
                    })

        except Exception:
            pass

        # SORT ALL NOTIFICATIONS
        notifications = sorted(
            notifications,
            key=lambda x: x['created_at'],
            reverse=True
        )

        return notifications[:10]
    def to_representation(self, instance):
        """Override to return names instead of IDs for country, state, and currency"""
        rep = super().to_representation(instance)
        if instance.country:
            rep['country'] = instance.country.country_name
        if instance.state:
            rep['state'] = instance.state.state_name
        if instance.currency:
            rep['currency'] = instance.currency.currency_name
        return rep
class Non_EssUserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'is_ess', 'is_staff', 'is_superuser', 'tenants']

class UserListSerializer(serializers.ModelSerializer):
    # v1.22.0 branch rights: the user's branches ("User branch access", else the linked employee's branch)
    branches = serializers.SerializerMethodField()
    branch_names = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'last_login', 'is_ess', 'is_staff', 'is_superuser', 'tenants', 'branches', 'branch_names']
        read_only_fields = ['branches', 'branch_names']

    def _branches(self, obj):
        bmap = self.context.get('branch_map')
        if bmap is None:
            schema_name = self.context.get('schema_name')
            if not schema_name:
                from django.db import connection
                schema_name = connection.schema_name if connection.schema_name != 'public' else None
            if not schema_name:
                return []
            from .views import user_branch_map
            bmap = user_branch_map(schema_name, [obj.pk])
        return bmap.get(obj.pk, [])

    def get_branches(self, obj):
        return [{'id': b, 'name': n} for b, n in self._branches(obj)]

    def get_branch_names(self, obj):
        return ', '.join(n for _, n in self._branches(obj) if n)

class DomainSerializer(serializers.ModelSerializer):
    class Meta:
        model=Domain
        fields = '__all__'


class ValidateCredentialsSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()

    def validate(self, attrs):
        username_or_email = attrs.get("username")
        password = attrs.get("password")

        # Check username or email
        if "@" in username_or_email:
            user = CustomUser.objects.filter(email=username_or_email).first()
        else:
            user = CustomUser.objects.filter(username=username_or_email).first()

        if user is None:   # v1.54.0 a directory user signing in for the first time gets a login (when the company allows it)
            from ZeoCloud import directory as _dir
            user = _dir.first_login(username_or_email, password)
            if user is not None:
                return {"user_id": user.id}
        if user is None:
            raise serializers.ValidationError("User not found")

        if not user.is_active:
            raise serializers.ValidationError("User is deactivated")

        from ZeoCloud import sso as _sso   # v1.54.0 the company signs in with its identity provider only
        _p = _sso.password_blocked(user)
        if _p is not None:
            from Translations.i18n import tr as _tr
            raise serializers.ValidationError(_tr('Your company signs in with "{0}" – use that button.', _tr(_p.name)))
        from ZeoCloud import security as _sec   # v1.52.0 the same lock as the sign-in
        _s = _sec.sec(user)
        if _sec.locked(user, _s):
            _m = int((_s.locked_until - timezone.now()).total_seconds() // 60) + 1
            raise serializers.ValidationError(f"Too many wrong tries – try again in {_m} minute(s).")
        from ZeoCloud import directory as _dir
        if not _dir.password_ok(user, password)[0]:
            _sec.fail(user, _s)
            raise serializers.ValidationError("Invalid password")
        from ZeoCloud import netguard as _ng, geo as _geo   # v1.62.0 the company's networks and countries (e-mail code sign-in too)
        _rq = self.context.get("request")
        if _rq is not None:
            from Translations.i18n import tr as _tr
            if _ng.refused_everywhere(user, _ng.client_ip(_rq)):
                _ng.refused_signin(user, _rq)
                raise serializers.ValidationError(_tr('Your company allows ZEO only from its own networks. Your address {0} is not one of them.', _ng.client_ip(_rq)))
            _cc = _geo.refused_everywhere(user, _ng.client_ip(_rq))
            if _cc:
                _sec.event('country_refused', user=user, ip=_ng.client_ip(_rq), detail=f'Sign-in refused: address {_ng.client_ip(_rq)} is in {_cc}.')
                raise serializers.ValidationError(_tr('Your company allows ZEO only from its countries. Your address {0} is in {1}.', _ng.client_ip(_rq), _cc))

        # SUCCESS → credentials are valid
        return {"user_id": user.id}
# class UserAllocatedCompanySerializer(CompanySerializer):
#     branches = serializers.SerializerMethodField()

#     class Meta(CompanySerializer.Meta):
#         fields = "__all__"

#     def get_branches(self, obj):
#         from OrganisationManager.models import UserBranchAccess, brnch_mstr
#         from django_tenants.utils import schema_context

#         user = self.context.get("user")
#         if not user:
#             return []

#         try:
#             with schema_context(obj.schema_name):

#                 # 🔥 SUPERUSER → ALL BRANCHES
#                 if user.is_superuser:
#                     return list(
#                         brnch_mstr.objects.all().values(
#                             "id",
#                             "branch_name"
#                         )
#                     )

#                 # 👤 NORMAL USER → ASSIGNED BRANCHES
#                 qs = (
#                     UserBranchAccess.objects
#                     .filter(user=user)
#                     .prefetch_related("branch")
#                 )

#                 branches = []
#                 for access in qs:
#                     for b in access.branch.all():
#                         branches.append({
#                             "id": b.id,
#                             "name": b.branch_name
#                         })

#                 return branches

#         except Exception as e:
#             print(e)
#             return []
class UserAllocatedCompanySerializer(CompanySerializer):
    branches = serializers.SerializerMethodField()
    notifications = serializers.SerializerMethodField()

    class Meta(CompanySerializer.Meta):
        fields = "__all__"

    def _has_inbox(self, user, schema_name):
        """v1.62.0 speed: the companies in which this login has any inbox row, read once per response (the top bar's
        company menu asked 3 queries per branch and 3 per company – ~500 queries for a 42-company login)."""
        schemas = self._zmemo(('inbox_schemas', user.pk), lambda: set(
            UserNotificationInbox.objects.filter(user=user).values_list('schema_name', flat=True).distinct()))
        return schema_name in schemas

    def get_branches(self, obj):

        user = self.context.get("user")

        if not user:
            return []

        try:

            with schema_context(obj.schema_name):

                # SUPERUSER -> ALL BRANCHES
                if user.is_superuser:

                    # v1.62.0 speed: a company's branches once per response (the user list repeated them per user)
                    branches = self._zmemo(('branches', obj.schema_name), lambda: list(brnch_mstr.objects.all()))

                # NORMAL USER -> ONLY ALLOCATED BRANCHES
                else:

                    branch_ids = []

                    access_qs = (
                        UserBranchAccess.objects
                        .filter(user=user)
                        .prefetch_related("branch")
                    )

                    for access in access_qs:
                        branch_ids.extend(
                            access.branch.values_list(
                                "id",
                                flat=True
                            )
                        )

                    branches = brnch_mstr.objects.filter(
                        id__in=branch_ids
                    )

                branch_data = []

                # v1.28.1: the notification counts and the latest notifications are read only when the caller asks
                # (?notifications=1 – the top bar does); every other screen only needs the branch list, which makes
                # this call ~20x cheaper (it was 1.3 s on a 22-company login and runs on almost every page)
                req = self.context.get('request') if hasattr(self, 'context') else None
                want_notif = bool(req is not None and hasattr(req, 'query_params') and req.query_params.get('notifications'))
                if not want_notif or not self._has_inbox(user, obj.schema_name):   # v1.62.0 nothing in the inbox: the same empty rows without 3 queries per branch
                    return [{"id": b.id, "branch_name": b.branch_name, "notification_count": 0, "notification_summary": [], "notifications": []}
                            for b in branches]

                for branch in branches:

                    notifications = (
                        UserNotificationInbox.objects
                        .filter(
                            user=user,
                            schema_name=obj.schema_name,
                            branch_id=branch.id
                        )
                        .order_by("-created_at")
                    )

                    unread_notifications = notifications.filter(
                        is_read=False
                    )

                    # Notification type wise counts
                    grouped = (
                        unread_notifications
                        .values("notification_type")
                        .annotate(count=Count("id"))
                    )

                    notification_summary = [
                        {
                            "type": item["notification_type"],
                            "count": item["count"]
                        }
                        for item in grouped
                    ]

                    branch_data.append({

                        "id": branch.id,

                        "branch_name": branch.branch_name,

                        # Total unread notifications
                        "notification_count": unread_notifications.count(),

                        # Type-wise unread counts
                        "notification_summary": notification_summary,

                        # Latest notifications
                        "notifications": [
                            {
                                "id": item.id,
                                "title": item.title,
                                "message": item.message,
                                "type": item.notification_type,
                                "branch_name": item.branch_name,
                                "is_read": item.is_read,
                                "created_at": item.created_at
                            }
                            for item in notifications[:10]
                        ]
                    })

                return branch_data

        except Exception as e:
            print("Branch notification error:", str(e))
            return []
    def get_notifications(self, obj):

        user = self.context.get("user")

        if not user or not self._want_notifications():   # v1.28.1 only when asked (?notifications=1)
            return {"total_unread": 0, "request_wise_counts": {}, "latest_notifications": []}
        if not self._has_inbox(user, obj.schema_name):   # v1.62.0 nothing in the inbox of this company: the same answer without 3 queries
            return {"total_unread": 0, "request_wise_counts": {}, "latest_notifications": []}

        notifications = UserNotificationInbox.objects.filter(
            user=user,
            schema_name=obj.schema_name
        )

        unread_notifications = notifications.filter(
            is_read=False
        )

        grouped = unread_notifications.values(
            'notification_type'
        ).annotate(
            count=Count('id')
        )

        request_wise_counts = {
            item['notification_type']: item['count']
            for item in grouped
        }

        latest_notifications = notifications.order_by(
            '-created_at'
        )[:10]

        return {

            "total_unread": unread_notifications.count(),

            "request_wise_counts": request_wise_counts,

            "latest_notifications": [

                {
                    "id": item.id,
                    "title": item.title,
                    "message": item.message,
                    "type": item.notification_type,
                    "branch_name": item.branch_name,
                    "is_read": item.is_read,
                    "created_at": item.created_at
                }

                for item in latest_notifications
            ]
        }
    
class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)

    def validate_new_password(self, value):

        if len(value) < 8:
            raise serializers.ValidationError(
                "Password must be at least 8 characters."
            )

        return value
    
class ESSChangePasswordSerializer(serializers.Serializer):
    emp_code = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)

    def validate_new_password(self, value):
        if len(value) < 8:
            raise serializers.ValidationError(
                "Password must be at least 8 characters."
            )
        return value


def _zeo_is_admin(user):
    """v1.26.0: company administrator in the open company – an invited login's rights for this company included."""
    try:
        from ZeoCloud.rights import is_admin
        return is_admin(user)
    except ImportError:
        from tenant_users.tenants.models import UserTenantPermissions
        return UserTenantPermissions.objects.filter(profile=user, is_superuser=True).exists()
