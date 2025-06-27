from django.shortcuts import render
from django.contrib.auth.models import User,Group,Permission
from .serializers import CustomUserSerializer,CustomTokenObtainPairSerializer,CompanySerializer,DomainSerializer,UserListSerializer,Non_EssUserListSerializer
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
        serializer = CompanySerializer(tenants, many=True)
        return Response(serializer.data)
    
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
                is_active=True  # Filter for users that are active
            ).exclude(is_ess=True)

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
            # Filter users based on schema_name and only show active users
            return CustomUser.objects.filter(
                tenants__schema_name=schema_name,
                is_active=True,is_ess=False  # Filter for users that are active
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