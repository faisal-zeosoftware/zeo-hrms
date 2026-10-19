from django.shortcuts import render
from django.contrib.auth.models import User,Group,Permission
from .serializers import CustomUserSerializer,CustomTokenObtainPairSerializer,CompanySerializer,DomainSerializer,UserListSerializer,Non_EssUserListSerializer,ValidateCredentialsSerializer,UserAllocatedCompanySerializer,ChangePasswordSerializer
from . models import CustomUser,company,Domain
from . permissions import (IsOwnerOrReadOnly,
                           IsSuperUser,IsEssUserOrReadOnly)
from OrganisationManager.serializer import PermissionSerializer,GroupSerializer
# from . custom_auth import GlobalJWTAuthentication
from rest_framework.response import Response
from rest_framework import status,generics,viewsets,permissions
from rest_framework.permissions import IsAuthenticated,AllowAny,IsAuthenticatedOrReadOnly,IsAdminUser
from rest_framework.decorators import action
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.views import APIView
from rest_framework import viewsets, filters
from .signals import add_company_to_superusers
from tenant_users.tenants.models import UserTenantPermissions
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from django_tenants.utils import schema_context
from .models import CustomUser
from .serializers import UserListSerializer
from django.core.exceptions import ValidationError
from django.db.models import Q
from EmpManagement .models import emp_master

# from .permissions import CompanyPermission
from django.http import Http404
# Create your views here.

#usergroups or roles
class RegisterUserAPIView(viewsets.ModelViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer

    def get_serializer_context(self):
        return {'request': self.request}

    def create(self, request, *args, **kwargs):
        """v1.26.0: an e-mail / user name that already has a ZEO login is invited to the chosen companies instead of
        failing with "already exists" (one login works for several companies – like Odoo Online and Zoho)."""
        from . import invites
        d = request.data
        target = invites.existing_login(d.get('email'), d.get('username'))
        if target is None:
            return super().create(request, *args, **kwargs)
        email = str(d.get('email') or '').strip().lower()
        if email and (target.email or '').lower() != email and (target.username or '').lower() != email:
            # only the user name is taken – by another person: a new login needs another user name
            return Response({'username': ['This user name is taken by another login. Choose another user name.']}, status=status.HTTP_400_BAD_REQUEST)
        if target.pk == request.user.pk:
            return Response({'detail': 'This is your own login.'}, status=status.HTTP_400_BAD_REQUEST)
        ids = d.get('tenants') or []
        if not isinstance(ids, (list, tuple)):
            ids = [ids]
        ids = [int(i) for i in ids if str(i).strip().isdigit()]
        if not ids:
            sch = request.query_params.get('schema') or request.headers.get('X-Schema') or ''
            ids = list(company.objects.filter(schema_name=sch).values_list('id', flat=True)) if sch else []
        comps = list(company.objects.filter(pk__in=ids))
        if not comps:
            return Response({'detail': 'This e-mail already has a ZEO login. Choose the company to invite it to.', 'existing': True},
                            status=status.HTTP_400_BAD_REQUEST)
        made, already, refused = invites.create(target, comps, request.user, is_staff=bool(d.get('is_staff')), is_superuser=bool(d.get('is_superuser')))
        if not made:
            if already:
                return Response({'detail': f'{target.email or target.username} already has access to ' + ', '.join(c.name for c in already) + '.',
                                 'existing': True}, status=status.HTTP_409_CONFLICT)
            return Response({'detail': 'You can only invite people to your own companies.'}, status=status.HTTP_403_FORBIDDEN)
        names = ', '.join(i.company.name for i in made)
        msg = (f'{target.email or target.username} already has a ZEO login, so an invitation to {names} was sent. '
               f'The company is added to their login when they accept it on their company list.')
        return Response({'invited': True, 'existing': True, 'detail': msg, 'user_id': target.pk,
                         'invites': [invites.invite_json(i, for_inviter=True) for i in made],
                         'already': [c.name for c in already]}, status=status.HTTP_202_ACCEPTED)

    
    @action(detail=True, methods=['get'])
    def user_permissions(self, request, pk=None):
        user = self.get_object()
        permissions = UserTenantPermissions.objects.filter(profile_id=user.id)
        serializer = PermissionSerializer(permissions, many=True)
        return Response(serializer.data)
    @action(detail=True, methods=['get'])
    def tenants(self, request, pk=None):
        user_profile = self.get_object()
        tenants = user_profile.tenants.all()

        # v1.28.1: this is the top bar's company / branch menu – it wants the notification counts (the plain
        # /users/api/user/<id>/ no longer computes them unless ?notifications=1)
        class _Q(dict):
            def get(self, k, d=None): return '1' if k == 'notifications' else super().get(k, d)
        class _R:
            query_params = _Q()
            user = request.user
            # v1.37.1: the company logo (ImageField) is rendered as an absolute URL – the stand-in request must offer it
            def build_absolute_uri(self, location=None):
                return request.build_absolute_uri(location)
        serializer = UserAllocatedCompanySerializer(
            tenants.select_related('country', 'currency'),
            many=True,
            context={"user": user_profile, "request": _R()}
        )

        return Response(serializer.data)
    # @action(detail=True, methods=['get'])
    # def tenants(self, request, pk=None):
    #     user_profile = self.get_object()
    #     tenants = user_profile.tenants.all()

    #     # 🔥 SUPERUSER → ALL branches
    #     if user_profile.is_superuser:
    #         serializer = CompanySerializer(tenants, many=True)
    #     else:
    #         serializer = UserAllocatedCompanySerializer(
    #             tenants,
    #             many=True,
    #             context={"user": user_profile}
    #         )

    #     return Response(serializer.data)
    
    # ---- v1.26.0: a login shared with another ZEO account (invited) is not this administrator's to change or delete.
    # They may give or take away access to their own companies; e-mail, password and the login itself stay the person's.
    LOGIN_FIELDS = ('username', 'email', 'password', 'is_superuser', 'is_staff', 'is_active', 'is_ess')

    def _managed_ids(self, request):
        if request.user.is_superuser:
            return None
        try:
            from ZeoCloud.services import is_platform_admin
            if is_platform_admin(request.user):
                return None
        except Exception:
            pass
        return set(request.user.tenants.values_list('id', flat=True))

    def _foreign(self, request, user):
        """Companies of this login that the requester does not have (empty for a normal user of the account)."""
        mine = self._managed_ids(request)
        if mine is None or user.pk == request.user.pk:
            return set()
        return set(user.tenants.values_list('id', flat=True)) - mine

    def _remove_access(self, request, user):
        # the rights record of a login is shared by all its companies – it stays; the invited rights for these
        # companies (ZeoCloud CompanyAccess) are removed with the access (signal)
        mine = self._managed_ids(request) or set()
        comps = list(user.tenants.filter(id__in=mine))
        user.tenants.remove(*comps)
        return comps

    def update(self, request, *args, **kwargs):
        user = self.get_object()
        if self._foreign(request, user):
            def same(a, b):
                return str(a if a is not None else '').strip().lower() == str(b if b is not None else '').strip().lower()
            touched = [f for f in self.LOGIN_FIELDS if f in request.data and (
                bool(request.data.get('password')) if f == 'password' else not same(request.data.get(f), getattr(user, f, '')))]
            if touched:
                from Translations.i18n import tr
                return Response({'detail': tr('This login also belongs to another ZEO account. Only its owner can change the e-mail, user name or password; you can change the access to your companies.'),
                                 'shared_login': True, 'fields': touched}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if self._foreign(request, user):
            from Translations.i18n import tr
            comps = self._remove_access(request, user)
            return Response({'detail': tr('Access to {0} removed. The login stays for its other ZEO account.', ', '.join(c.name for c in comps)),
                             'shared_login': True}, status=status.HTTP_200_OK)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def deactivate_user(self, request, pk=None):
        user = self.get_object()
        if self._foreign(request, user):   # v1.26.0: switching off a shared login would lock the person out of their own account
            from Translations.i18n import tr
            comps = self._remove_access(request, user)
            return Response({"message": tr('Access to {0} removed. The login stays for its other ZEO account.', ', '.join(c.name for c in comps)),
                             'shared_login': True}, status=status.HTTP_200_OK)
        user.is_active = False
        user.save()
        # logger.info(f"User {user.username} has been deactivated by {request.user.username}")
        return Response({"message": "User has been deactivated successfully"}, status=status.HTTP_200_OK)

def user_branch_map(schema_name, user_ids):
    """v1.22.0 branch rights: {user id: [(branch id, branch name), ...]} from "User branch access", falling back to the
    branch of the user's employee."""
    from OrganisationManager.models import UserBranchAccess
    out = {}
    user_ids = list(user_ids)
    with schema_context(schema_name):
        for uba in UserBranchAccess.objects.filter(user_id__in=user_ids).prefetch_related('branch'):
            lst = out.setdefault(uba.user_id, [])
            for b in uba.branch.all():
                if (b.id, b.branch_name) not in lst:
                    lst.append((b.id, b.branch_name))
        for e in emp_master.objects.filter(users_id__in=[u for u in user_ids if not out.get(u)], emp_branch_id__isnull=False).select_related('emp_branch_id'):
            if not out.get(e.users_id):
                out[e.users_id] = [(e.emp_branch_id_id, e.emp_branch_id.branch_name)]
    return out


class BranchUserListMixin:
    """v1.22.0 branch rights: branch-restricted back-office users only list users of their branches (by "User branch
    access" or by their employee's branch) and themselves; ?branch=<id> lists the users of one branch."""

    def scope_users(self, qs, schema_name):
        from AccessControl.access import allowed_branches
        mine = allowed_branches(self.request)
        ids = None
        if mine is not None:
            all_ids = list(qs.values_list('id', flat=True))
            bmap = user_branch_map(schema_name, all_ids)
            allowed = {str(b) for b in mine}
            ids = {u for u, brs in bmap.items() if any(str(b) in allowed for b, _ in brs)} | {self.request.user.pk}
        br = self.request.GET.get('branch')
        if br not in (None, ''):
            if not str(br).isdigit():
                return qs.none()
            bmap = user_branch_map(schema_name, qs.values_list('id', flat=True))
            in_branch = {u for u, brs in bmap.items() if any(b == int(br) for b, _ in brs)}
            ids = in_branch if ids is None else (ids & in_branch)
        return qs if ids is None else qs.filter(id__in=ids)

    def get_serializer_context(self):
        ctx_ = super().get_serializer_context()
        ctx_['schema_name'] = self.request.GET.get('schema')
        return ctx_

    def list(self, request, *args, **kwargs):
        # one query for the branches of all listed users (UserListSerializer.branches)
        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)
        rows = page if page is not None else list(qs)
        schema_name = request.GET.get('schema')
        context = self.get_serializer_context()
        context['branch_map'] = user_branch_map(schema_name, [u.pk for u in rows]) if schema_name else {}
        ser = self.get_serializer_class()(rows, many=True, context=context)
        return self.get_paginated_response(ser.data) if page is not None else Response(ser.data)


class TenantUserListView(BranchUserListMixin, generics.ListAPIView):  # v1.22.0 branch rights
    permission_classes = [IsAuthenticated]
    serializer_class = UserListSerializer

    def get_queryset(self):
        # Get the schema name from the request parameters
        schema_name = self.request.GET.get('schema')

        # Ensure the schema name is provided
        if not schema_name:
            raise ValidationError({"error": "Schema name is required"})

        # Use schema_context to access the correct tenant's users
        with schema_context(schema_name):
            # Filter users based on schema_name and only show active users
            qs = CustomUser.objects.filter(
                tenants__schema_name=schema_name,
                is_active=True)  # Filter for users that are active
            # ).exclude(is_ess=True)
        return self.scope_users(qs, schema_name)  # v1.22.0 branch rights

from django.contrib.auth import login

# from .authentication import CentralizedJWTAuthentication
class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class CompanyViewSet(viewsets.ModelViewSet):
    queryset = company.objects.all()
    serializer_class = CompanySerializer

    def create(self, request, *args, **kwargs):
        """v1.26.0: the company is created in the background (it took 6–7 minutes inside the request and timed out).
        Answers 202 {job, schema}; poll GET /users/api/company-jobs/<job>/. Send sync=1 to wait (scripts)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        apps = request.data.get('apps')
        keys = [a.strip() for a in str(apps).split(',') if a.strip()] if apps else ['hrms', 'payroll', 'projects']
        from .company_jobs import start, status as job_status
        user = request.user if getattr(request.user, 'is_authenticated', False) else None
        sync = str(request.data.get('sync') or request.query_params.get('sync') or '').lower() in ('1', 'true', 'yes')
        job, schema = start(dict(serializer.validated_data), keys, user, run_now=sync)
        if sync:
            st = job_status(job) or {}
            if st.get('state') == 'failed':
                return Response({'error': st.get('error') or 'The company could not be created.', 'job': job}, status=status.HTTP_400_BAD_REQUEST)
            obj = company.objects.filter(pk=st.get('company_id')).first()
            return Response(dict(self.get_serializer(obj).data, job=job, setup=st), status=status.HTTP_201_CREATED)
        return Response({'job': job, 'schema': schema, 'name': serializer.validated_data.get('name'),
                         'status_url': f'/users/api/company-jobs/{job}/',
                         'message': 'The company is being created. This takes about 1–3 minutes.'}, status=status.HTTP_202_ACCEPTED)

    def perform_create(self, serializer):   # kept for API compatibility (create() above does the work)
        instance = serializer.save()
        apps = self.request.data.get('apps')
        if apps:
            try:
                from ZeoApps.services import setup_new_company
                setup_new_company(instance, [a.strip() for a in str(apps).split(',') if a.strip()],
                                  self.request.user if getattr(self.request.user, 'is_authenticated', False) else None)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('apps of the new company %s', instance.schema_name)


class CompanyJobView(APIView):
    """v1.26.0 – progress of a background company creation."""
    permission_classes = [IsAuthenticated]

    def get(self, request, job):
        from .company_jobs import status as job_status
        st = job_status(job)
        if not st:
            return Response({'detail': 'Unknown or expired job.'}, status=status.HTTP_404_NOT_FOUND)
        if st.get('by') and st.get('by') != request.user.pk and not request.user.is_superuser:
            return Response({'detail': 'Not your job.'}, status=status.HTTP_403_FORBIDDEN)
        return Response({k: v for k, v in st.items() if k != 'by'})
    # permission_classes = [IsAuthenticated]

   

class DomainViewset(viewsets.ModelViewSet):
    queryset=Domain.objects.all()
    serializer_class = DomainSerializer



class UserDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, tenant_id):
        # Ensure the user is part of the tenant
        try:
            tenant = company.objects.get(schema_name=tenant_id)
            user = CustomUser.objects.get(id=request.user.id, tenants=tenant)
        except (company.DoesNotExist, CustomUser.DoesNotExist):
            return Response({"detail": "Not found."}, status=404)

        serializer = CustomUserSerializer(user)
        return Response(serializer.data)

class NoEssUerListView(BranchUserListMixin, generics.ListAPIView):  # v1.22.0 branch rights
    permission_classes = [IsAuthenticated]   # v1.22.0 branch rights: was open
    serializer_class = Non_EssUserListSerializer

    def get_queryset(self):
        # Get the schema name from the request parameters
        schema_name = self.request.GET.get('schema')

        # Ensure the schema name is provided
        if not schema_name:
            raise ValidationError({"error": "Schema name is required"})

        # Use schema_context to access the correct tenant's users
        with schema_context(schema_name):
            qs = CustomUser.objects.filter(
                tenants__schema_name=schema_name,
                is_active=True
            ).filter(
                Q(is_ess=False) | Q(is_ess__isnull=True)
            )
        return self.scope_users(qs, schema_name)  # v1.22.0 branch rights

class GroupPermTenantUserListView(BranchUserListMixin, generics.ListAPIView):  # v1.22.0 branch rights
    permission_classes = [IsAuthenticated]
    serializer_class = UserListSerializer

    def get_queryset(self):
        # Get the schema name from the request parameters
        schema_name = self.request.GET.get('schema')

        # Ensure the schema name is provided
        if not schema_name:
            raise ValidationError({"error": "Schema name is required"})

        # Use schema_context to access the correct tenant's users
        with schema_context(schema_name):
            # Filter users based on schema_name and only show active users
            qs = CustomUser.objects.filter(
                tenants__schema_name=schema_name,
                is_active=True  # Filter for users that are active
            )
        return self.scope_users(qs, schema_name)  # v1.22.0 branch rights

class ValidateCredentialsView(APIView):
    def post(self, request):
        serializer = ValidateCredentialsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return Response({
            "message": "Credentials correct. Now call /send-otp/.",
            "user_id": serializer.validated_data["user_id"]
        })
from django.utils import timezone
from django.core.mail import send_mail
import random    
class SendOTPView(APIView):
    def post(self, request):
        user_id = request.data.get("user_id")

        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            return Response({"error": "User not found"}, status=400)

        import random
        from django.utils import timezone
        
        otp = random.randint(100000, 999999)
        user.otp = str(otp)
        user.otp_created_at = timezone.now()
        user.is_2fa_verified = False
        user.save()

        # Send email
        send_mail(
            "Your OTP Code",
            f"Your OTP is {otp}. It is valid for 5 minutes.",
            "no-reply@example.com",
            [user.email]
        )

        return Response({"message": "OTP sent successfully"})
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
class VerifyOTPView(APIView):
    def post(self, request):
        user_id = request.data.get("user_id")
        otp = request.data.get("otp")

        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            return Response({"error": "User not found"}, status=400)

        # validate OTP
        if user.otp != str(otp):
            return Response({"error": "Invalid OTP"}, status=400)

        # OTP Expired?
        if user.otp_created_at + timezone.timedelta(minutes=5) < timezone.now():
            return Response({"error": "OTP expired"}, status=400)

        # Set 2FA Verified
        user.is_2fa_verified = True
        user.save()

        # Generate JWT tokens
        refresh = RefreshToken.for_user(user)
        refresh['otp'] = True   # v1.25.0: this login passed the code – the mobile app may then register the phone

        # TENANTS LIST
        tenants_data = [
            {
                "id": tenant.id,
                "name": tenant.name,
                "schema_name": tenant.schema_name
            }
            for tenant in user.tenants.all()
        ]

        tenant_ids = [tenant.schema_name for tenant in user.tenants.all()]

        # Final Response
        return Response({
            "message": "Login successful",
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user_id": user.id,
            "username": user.username,
            "tenants": tenants_data,   # ➜ ADDED
            "tenant_id": tenant_ids    # ➜ ADDED
        })
class SendResetPasswordOTP(APIView):
    def post(self, request):
        from django.core.mail import EmailMessage
        from .utils import generate_otp
        email = request.data.get("email")

        if not email:
            return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = CustomUser.objects.filter(email__iexact=str(email).strip(), is_active=True).order_by('id').first()
            if user is None:
                raise CustomUser.DoesNotExist
        except CustomUser.DoesNotExist:
            return Response({"error": "No user has this e-mail address. Check the spelling or ask your administrator."}, status=status.HTTP_404_NOT_FOUND)

        # Generate OTP
        otp = generate_otp()
        user.otp = otp
        user.otp_created_at = timezone.now()
        user.save()

        # Send email
        subject = "Password Reset OTP"
        message = f"Your OTP for resetting password is: {otp}"
        try:
            email_message = EmailMessage(subject, message, to=[user.email])
            email_message.send()
        except Exception as e:   # v1.28.1: a clear answer instead of a server error when the mail server is not set up
            import logging
            logging.getLogger(__name__).exception('reset code e-mail')
            return Response({"error": f"The code could not be e-mailed ({str(e)[:120]}). Ask your administrator to check the e-mail settings."}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({"message": "OTP sent successfully"}, status=status.HTTP_200_OK)

class VerifyResetOTP(APIView):
    def post(self, request):
        email = request.data.get("email")
        otp = request.data.get("otp")

        if not email or not otp:
            return Response({"error": "Email and OTP are required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        # Check OTP match
        if user.otp != otp:
            return Response({"error": "Invalid OTP"}, status=status.HTTP_400_BAD_REQUEST)

        # Check expiry (valid for 10 minutes)
        if (timezone.now() - user.otp_created_at).seconds > 600:
            return Response({"error": "OTP expired"}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"message": "OTP verified successfully"}, status=status.HTTP_200_OK)
class ResetPassword(APIView):
    def post(self, request):
        email = request.data.get("email")
        new_password = request.data.get("new_password")
        otp = str(request.data.get("otp") or '').strip()

        try:
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        # v1.28.1: the new password is accepted only with the code that was e-mailed (before, the e-mail address alone
        # was enough to set a new password); the code is valid for 10 minutes
        if not user.otp or not user.otp_created_at or otp != str(user.otp) or (timezone.now() - user.otp_created_at).total_seconds() > 600:
            return Response({"error": "The code is wrong or has expired. Ask for a new code."}, status=status.HTTP_400_BAD_REQUEST)
        if not new_password or len(str(new_password)) < 8:
            return Response({"error": "The new password must have at least 8 characters."}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new_password)
        if hasattr(user, 'must_change_password'):
            user.must_change_password = False
        
        # Clear OTP after successful reset
        user.otp = None
        user.otp_created_at = None
        user.save()

        return Response({"message": "Password reset successful"}, status=status.HTTP_200_OK)
class ChangePasswordView(APIView):

    # permission_classes = [IsAuthenticated]

    def post(self, request):

        serializer = ChangePasswordSerializer(
            data=request.data
        )

        if serializer.is_valid():

            user = request.user

            old_password = serializer.validated_data[
                "old_password"
            ]

            new_password = serializer.validated_data[
                "new_password"
            ]

            if not user.check_password(old_password):
                return Response(
                    {
                        "success": False,
                        "message": "Old password is incorrect."
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            user.set_password(new_password)

            user.must_change_password = False

            user.save()

            return Response(
                {
                    "success": True,
                    "message": "Password changed successfully."
                },
                status=status.HTTP_200_OK
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST
        )

def get_ess_employee(emp_code):
    """
    Search all tenant schemas and return the employee.
    """

    for tenant in company.objects.exclude(schema_name="public"):

        try:
            with schema_context(tenant.schema_name):

                employee = (
                    emp_master.objects
                    .select_related("users")
                    .filter(
                        emp_code=emp_code,
                        is_ess=True,
                        is_active=True
                    )
                    .first()
                )

                if employee:
                    return employee

        except Exception:
            continue

    return None

from django.core.mail import EmailMessage
from .utils import generate_otp
class ESSSendResetPasswordOTP(APIView):

    def post(self, request):

        emp_code = request.data.get("emp_code")

        if not emp_code:
            return Response(
                {
                    "error": "Employee Code is required"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        employee = get_ess_employee(emp_code)

        if employee is None:
            return Response(
                {
                    "error": "ESS Employee not found"
                },
                status=status.HTTP_404_NOT_FOUND
            )

        if not employee.users:
            return Response(
                {
                    "error": "Employee login account not found"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        user = employee.users

        email = employee.emp_company_email or employee.emp_personal_email

        if not email:
            return Response(
                {
                    "error": "Employee email not available"
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        user = employee.users

        otp = generate_otp()

        user.otp = otp
        user.otp_created_at = timezone.now()
        user.is_2fa_verified = False
        user.save()

        subject = "Password Reset OTP"

        message = (
            f"Your OTP for resetting password is: {otp}"
        )

        EmailMessage(
            subject,
            message,
            to=[email]
        ).send()

        return Response(
            {
                "message": "OTP sent successfully"
            },
            status=status.HTTP_200_OK
        )

class ESSVerifyResetOTP(APIView):

    def post(self, request):
        emp_code = request.data.get("emp_code")
        otp = request.data.get("otp")

        if not emp_code or not otp:
            return Response(
                {"error": "Employee Code and OTP are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        employee = get_ess_employee(emp_code)

        if employee is None:
            return Response(
                {"error": "ESS Employee not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        if not employee.users:
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        user = employee.users

        # Check OTP match
        if str(user.otp) != str(otp):
            return Response(
                {"error": "Invalid OTP"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check expiry (valid for 10 minutes)
        if (timezone.now() - user.otp_created_at).seconds > 600:
            return Response(
                {"error": "OTP expired"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Mark OTP as verified
        user.is_2fa_verified = True
        user.save()

        return Response(
            {"message": "OTP verified successfully"},
            status=status.HTTP_200_OK
        )

class ESSResetPassword(APIView):

    def post(self, request):
        emp_code = request.data.get("emp_code")
        new_password = request.data.get("new_password")

        if not emp_code:
            return Response(
                {"error": "Employee Code is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not new_password:
            return Response(
                {"error": "New password is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        employee = get_ess_employee(emp_code)

        if employee is None:
            return Response(
                {"error": "ESS Employee not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        if not employee.users:
            return Response(
                {"error": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        user = employee.users

        if not user.is_2fa_verified:
            return Response(
                {"error": "Please verify OTP first"},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)

        # Clear OTP after successful reset
        user.otp = None
        user.otp_created_at = None
        user.is_2fa_verified = False
        user.must_change_password = False
        user.save()

        return Response(
            {"message": "Password reset successful"},
            status=status.HTTP_200_OK
        )


from .models import company   # v1.22.2


class MyCompaniesView(APIView):
    """v1.22.2 – the companies the signed-in user may open, for the company list after login (light: no notifications).

    GET /users/api/my-companies/ → {user, can_create, companies: [{id, name, schema_name, country, city, industry,
    state_label, logo, apps}]}. Only the user's own companies; nothing about other users.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django_tenants.utils import schema_context
        u = request.user
        labels = dict(company.INDUSTRY_CHOICES)
        out = []
        for c in u.tenants.exclude(schema_name='public').select_related('country').order_by('name'):
            apps, neutral, origin = [], False, ''
            try:
                with schema_context(c.schema_name):
                    from ZeoApps.services import APPS, is_active
                    apps = [a[2] for a in APPS if is_active(a[0])]
                    from ZeoApps.models import CompanyOption   # v1.22.3: test copies
                    opts = {o.key: o.value or {} for o in CompanyOption.objects.filter(key__in=('neutralized', 'duplicated_from'))}
                    neutral = bool(opts.get('neutralized', {}).get('on'))
                    origin = opts.get('duplicated_from', {}).get('name', '')
            except Exception:
                apps = apps or []
            logo = ''
            try:
                logo = request.build_absolute_uri(c.logo.url) if c.logo else ''
            except Exception:
                logo = ''
            out.append({'id': c.id, 'name': c.name, 'schema_name': c.schema_name,
                        'country': getattr(c.country, 'country_name', '') or '', 'city': c.city or '',
                        'industry': labels.get(c.industry_type or '', ''),
                        'state_label': c.country.get_state_label() if c.country_id and hasattr(c.country, 'get_state_label') else '',
                        'logo': logo, 'apps': apps, 'neutralized': neutral, 'copy_of': origin})
        res = {'user': {'id': u.pk, 'username': u.username, 'email': u.email, 'name': ' '.join(x for x in [u.first_name, u.last_name] if x) if hasattr(u, 'first_name') else ''},
               'can_create': bool(u.is_superuser), 'companies': out}
        try:   # v1.26.0 invitations of this login to other companies
            from ZeoCloud.models import UserInvite
            from .invites import invite_json
            res['invites'] = [invite_json(i) for i in UserInvite.objects.filter(user=u, state='pending').select_related('company', 'invited_by')[:20]]
        except Exception:
            res['invites'] = []
        try:   # v1.23.0 ZEO Cloud: companies grouped by account, the user's role in each, notices, console link
            from ZeoCloud.views import my_companies_extra
            my_companies_extra(request, res)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('cloud company list')
        return Response(res)


def _cloud_owner(u, comp):
    try:
        from ZeoCloud.services import cloud_on
        return bool(cloud_on() and comp is not None and getattr(getattr(comp, 'cloud', None), 'account', None) and comp.cloud.account.owner_id == u.pk)
    except Exception:
        return False


class DuplicateCompanyView(APIView):
    """v1.22.3 – POST /users/api/my-companies/<id>/duplicate/ {name, neutralize} → {job, schema}
    Copies the company with all its data (like Odoo's Duplicate database). Administrators only."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        u = request.user
        src = u.tenants.exclude(schema_name='public').filter(pk=pk).first()
        try:   # v1.23.0 ZEO Cloud: the account owner duplicates within the plan; the copy joins the same account
            from ZeoCloud.views import duplicate_check
            refused = duplicate_check(u, src)
        except Exception:
            refused = None
        if refused is not None:
            return refused
        if not u.is_superuser and not getattr(request, '_cloud_owner_ok', False) and not _cloud_owner(u, src):
            return Response({'detail': 'Only an administrator can duplicate a company.'}, status=403)
        if src is None:
            return Response({'detail': 'Company not found.'}, status=404)
        name = ' '.join(str(request.data.get('name') or '').split())[:100]
        if len(name) < 2:
            return Response({'detail': 'Enter a name for the copy.', 'name': ['Enter a name for the copy.']}, status=400)
        if company.objects.filter(name__iexact=name).exists():
            return Response({'detail': 'A company with this name exists already.', 'name': ['A company with this name exists already.']}, status=400)
        from ZeoApps.duplicate import start
        job, schema = start(src, name, bool(request.data.get('neutralize', True)), u)
        return Response({'job': job, 'schema': schema}, status=202)


class DuplicateStatusView(APIView):
    """v1.22.3 – GET /users/api/my-companies/duplicate/<job>/ → {state: running|done|failed, step, step_no, steps, …}"""
    permission_classes = [IsAuthenticated]

    def get(self, request, job):
        from ZeoApps.duplicate import status
        s = status(job)
        if not s or s.get('by') != request.user.pk:
            return Response({'detail': 'Not found.'}, status=404)
        return Response(s)



class InvitesView(APIView):
    """v1.26.0 – invitations of an existing login.

    GET  /users/api/invites/                      my pending invitations (company list)
    GET  /users/api/invites/?schema=<company>     pending invitations sent by this company (Users screen)
    POST /users/api/invites/<id>/ {action: accept | decline | cancel}
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk=None):
        from ZeoCloud.models import UserInvite
        from . import invites
        sch = request.query_params.get('schema')
        if sch:
            comp = company.objects.filter(schema_name=sch).first()
            if comp is None or not invites.may_manage(request.user, comp):
                return Response([], status=status.HTTP_200_OK)
            qs = UserInvite.objects.filter(company=comp, state='pending').select_related('user', 'company', 'invited_by')
            return Response([invites.invite_json(i, for_inviter=True) for i in qs[:200]])
        qs = UserInvite.objects.filter(user=request.user, state='pending').select_related('company', 'invited_by')
        return Response([invites.invite_json(i) for i in qs[:50]])

    def post(self, request, pk=None):
        from ZeoCloud.models import UserInvite
        from . import invites
        inv = UserInvite.objects.select_related('company', 'user').filter(pk=pk).first()
        if inv is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        a = request.data.get('action')
        try:
            if a == 'accept':
                invites.accept(inv, request.user)
                return Response({'ok': True, 'detail': f'{inv.company.name} was added to your companies.', 'company_id': inv.company_id,
                                 'schema_name': inv.company.schema_name})
            if a in ('decline', 'cancel'):
                invites.answer(inv, request.user, 'declined' if a == 'decline' else 'cancelled')
                return Response({'ok': True})
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_409_CONFLICT)
        return Response({'detail': 'Unknown action.'}, status=status.HTTP_400_BAD_REQUEST)
