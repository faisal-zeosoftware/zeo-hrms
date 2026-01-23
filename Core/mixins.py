from django.db import models
from django.db.models import Q
class BranchAccessMixin:

    def get_queryset(self):
        from django_tenants.utils import schema_context
        qs = super().get_queryset()
        user = self.request.user

        if not user.is_authenticated:
            return qs.none()

        from OrganisationManager.models import UserBranchAccess, brnch_mstr

        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return qs.none()

        # 🟢 SUPERUSER = ALL BRANCHES
        if user.is_superuser:
            with schema_context(tenant.schema_name):
                all_branch_ids = list(
                    brnch_mstr.objects.values_list("id", flat=True)
                )
            user_branch_ids = all_branch_ids

        else:
            # 🔐 NORMAL USER → ONLY ASSIGNED BRANCHES
            with schema_context(tenant.schema_name):
                user_branch_ids = (
                    UserBranchAccess.objects
                    .filter(user=user)
                    .values_list('branch__id', flat=True)
                    .distinct()
                )

            if not user_branch_ids.exists():
                if getattr(user, 'is_ess', False):
                    emp = user.employees.first()
                    if emp and emp.emp_branch_id:
                        user_branch_ids = [emp.emp_branch_id.id]
                    else:
                        return qs.none()
                else:
                    return qs.none()

        # 🎯 branch_id query param
        requested_branch_id = self.request.query_params.get('branch_id')
        if requested_branch_id:
            try:
                requested_branch_id = int(requested_branch_id)
                if requested_branch_id in set(user_branch_ids):
                    user_branch_ids = [requested_branch_id]
                else:
                    return qs.none()
            except ValueError:
                return qs.none()

        fields = {f.name for f in qs.model._meta.get_fields()}
        q_objects = Q()
        filtered = False

        for field in ['branch', 'emp_branch_id', 'work_location']:
            if field in fields:
                q_objects |= Q(**{f"{field}__id__in": user_branch_ids})
                filtered = True

        if filtered:
            return qs.filter(q_objects).distinct()

        if 'employee' in fields:
            return qs.filter(
                employee__emp_branch_id__id__in=user_branch_ids
            ).distinct()

        return qs.none()