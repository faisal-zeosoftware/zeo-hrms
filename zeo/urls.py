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
    path('api/v1/', include('ZeoAdmin.urls_v1')),   # v1.61.0 public API v1
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
    path('zeo-projects/', include('ZeoProjects.urls')),          # v1.20.0 Projects app
    path('zeo-apps/', include('ZeoApps.urls')),                  # v1.21.0 apps of the company (Settings → Apps)
    path('zeo-hse/', include('ZeoHSE.urls')),                    # v1.21.0 HSE
    path('flow/', include('ZeoFlow.urls')),                       # v1.29.0 workflow automation
    path('zeo-crm/', include('ZeoCRM.urls')),                    # v1.21.0 CRM
    path('zeo-property/', include('ZeoProperty.urls')),          # v1.21.0 Property
    path('zeo-masters/', include('ZeoMasters.urls')),          # v1.41.0 business partners (shared masters under every business app)
    path('zeo-maintenance/', include('ZeoMaintenance.urls')),    # v1.21.0 Maintenance
    path('zeo-people/', include('ZeoPeople.urls')),               # v1.36.0 People
    path('zeo-hr/', include('ZeoHR.urls')),                       # v1.48.0 HR operations
    path('insights/', include('ZeoInsights.urls')),               # v1.53.0 report builder and dashboard builder
    path('zeo-admin/', include('ZeoAdmin.urls')),                 # v1.52.0 administration: scheduled reports, API keys, webhooks, imports, out of office, health
    path('zeo-ai/', include('ZeoAI.urls')),                       # v1.37.0 AI assistants
    path('accounting/', include('ZeoAccounting.urls')),          # v1.35.0 accounting integration
    path('zeo-assets/', include('ZeoAssets.urls')),              # v1.33.0 Asset Management
    path('self-service/', include('SelfService.urls')),          # v1.13.0
    path('country-pack/', include('CountryPack.urls')),          # v1.14.0
    path('language/', include('LanguagePack.urls')),            # v1.15.0
    path('translations/', include('Translations.urls')),        # v1.15.0
    path('ai/', include('AISettings.urls')),                    # v1.16.0
    path('views/', include('SavedViews.urls')),                  # v1.16.0
    path('payroll-plus/', include('PayrollPlus.urls')),         # v1.18.0
    path('print-templates/', include('PrintTemplates.urls')),   # v1.62.0 print – print templates (designs of the printed documents)
    path('cloud/', include('ZeoCloud.urls')),                  # v1.23.0 ZEO Cloud
    path('scim/v2/', include('ZeoCloud.scim_urls')),            # v1.54.0 user provisioning (Entra ID, Okta …)
    path('group/', include('ZeoGroup.urls')),               # v1.27.0 group features (public)
    path('licence/', include('ZeoLicence.urls')),           # v1.28.0 Enterprise licence (public)
    path('mobile/', include('ZeoMobile.urls')),             # v1.25.0 mobile app (public)
    path('mobile-co/', include('ZeoMobile.urls_company')),  # v1.25.0 mobile app (company data)
    



]
# v1.56.1: uploaded files only for signed-in users (zeo/media_auth.py) – Django checks the login, nginx sends the file
from django.urls import re_path as _re_path
from zeo import media_auth as _media_auth
urlpatterns += [_re_path(r'^media/(?P<path>.+)$', _media_auth.serve)]
