"""
URL configuration for zeo project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path,include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('core/',include('Core.urls')),
    path('users/',include('UserManagement.urls')),
    path('employee/',include('EmpManagement.urls')),
    path('organisation/',include('OrganisationManager.urls')),
    path('calendars/',include('calendars.urls')),
    path('payroll/',include('PayrollManagement.urls')),
    path('project/',include('ProjectManagement.urls')),
    path('performance/',include('PerformanceManagement.urls')),
    path('recruitment/',include('RecruitmentManagement.urls')),
    path('learning/',include('LearningManagement.urls')),
    path('dashboard/',include('DashboardManagement.urls')),
    path('tools/',include('DataTools.urls')),
    path('leave-policy/',include('LeavePolicy.urls')),
    path('hr-actions/',include('HRActions.urls')),
    path('expense/',include('ExpenseManagement.urls')),
    path('project-control/',include('ProjectControl.urls')),
    path('chatter/', include('Chatter.urls')),
    path('org-structure/', include('OrgStructure.urls')),        # v1.12.0
    path('shift-planner/', include('ShiftPlanner.urls')),        # v1.12.0
    path('attendance-plus/', include('AttendancePlus.urls')),    # v1.12.0
    path('iclock/', include('AttendancePlus.adms_urls')),        # v1.12.0 ZKTeco ADMS push (devices use this fixed path)
    path('asset-plus/', include('AssetPlus.urls')),              # v1.12.0
    path('employee-profile/', include('EmployeeProfile.urls')),  # v1.13.0
    path('self-service/', include('SelfService.urls')),          # v1.13.0
    



]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
