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

        serializer = UserAllocatedCompanySerializer(
            tenants,
            many=True,
            context={"user": user_profile}
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
    
    @action(detail=True, methods=['post'])
    def deactivate_user(self, request, pk=None):
        user = self.get_object()
        user.is_active = False
        user.save()
        # logger.info(f"User {user.username} has been deactivated by {request.user.username}")
        return Response({"message": "User has been deactivated successfully"}, status=status.HTTP_200_OK)

class TenantUserListView(generics.ListAPIView):
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
            return CustomUser.objects.filter(
                tenants__schema_name=schema_name,
                is_active=True)  # Filter for users that are active
            # ).exclude(is_ess=True)

from django.contrib.auth import login

# from .authentication import CentralizedJWTAuthentication
class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class CompanyViewSet(viewsets.ModelViewSet):
    queryset = company.objects.all()
    serializer_class = CompanySerializer
    def perform_create(self, serializer):
        # Call the super's perform_create to save the instance
        instance = serializer.save()

        # Trigger the signal manually after the instance is saved
        add_company_to_superusers(sender=company, instance=instance, created=True)
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

class NoEssUerListView(generics.ListAPIView):
    # permission_classes = [IsAuthenticated]
    serializer_class = Non_EssUserListSerializer

    def get_queryset(self):
        # Get the schema name from the request parameters
        schema_name = self.request.GET.get('schema')

        # Ensure the schema name is provided
        if not schema_name:
            raise ValidationError({"error": "Schema name is required"})

        # Use schema_context to access the correct tenant's users
        with schema_context(schema_name):
            return CustomUser.objects.filter(
                tenants__schema_name=schema_name,
                is_active=True
            ).filter(
                Q(is_ess=False) | Q(is_ess__isnull=True)
            )

class GroupPermTenantUserListView(generics.ListAPIView):
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
            return CustomUser.objects.filter(
                tenants__schema_name=schema_name,
                is_active=True  # Filter for users that are active
            )

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
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        # Generate OTP
        otp = generate_otp()
        user.otp = otp
        user.otp_created_at = timezone.now()
        user.save()

        # Send email
        subject = "Password Reset OTP"
        message = f"Your OTP for resetting password is: {otp}"
        email_message = EmailMessage(subject, message, to=[email])
        email_message.send()

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

        try:
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        user.set_password(new_password)
        
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
