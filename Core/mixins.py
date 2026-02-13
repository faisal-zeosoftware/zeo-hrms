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

        # 🟢 SUPERUSER → ALL BRANCHES
        if user.is_superuser:
            with schema_context(tenant.schema_name):
                user_branch_ids = list(
                    brnch_mstr.objects.values_list("id", flat=True)
                )

        else:
            # 🔐 NORMAL USER → ASSIGNED BRANCHES
            with schema_context(tenant.schema_name):
                user_branch_ids = (
                    UserBranchAccess.objects
                    .filter(user=user)
                    .values_list("branch__id", flat=True)
                    .distinct()
                )

            if not user_branch_ids.exists():
                # ESS fallback
                if getattr(user, "is_ess", False):
                    emp = user.employees.first()
                    if emp and emp.emp_branch_id:
                        user_branch_ids = [emp.emp_branch_id.id]
                    else:
                        return qs.none()
                else:
                    return qs.none()

        # ======================================================
        # 🎯 MULTI BRANCH QUERY PARAM SUPPORT
        # Accepts:
        # branch_id=1
        # branch_id=1,3,4
        # branch_id=[1,3,4]
        # ======================================================

        requested_branch_id = self.request.query_params.get("branch_id")

        if requested_branch_id:
            try:
                requested_branch_id = requested_branch_id.strip("[]")

                requested_ids = [
                    int(x) for x in requested_branch_id.split(",")
                    if x.strip()
                ]

                allowed_ids = set(user_branch_ids)

                valid_ids = [
                    bid for bid in requested_ids if bid in allowed_ids
                ]

                if not valid_ids:
                    return qs.none()

                user_branch_ids = valid_ids

            except Exception:
                return qs.none()

        # ======================================================
        # 🔍 APPLY FILTERING ON AVAILABLE BRANCH FIELDS
        # ======================================================

        fields = {f.name for f in qs.model._meta.get_fields()}
        q_objects = Q()
        filtered = False

        for field in ["branch", "emp_branch_id", "work_location","branch_id"]:
            if field in fields:
                q_objects |= Q(**{f"{field}__id__in": user_branch_ids})
                filtered = True

        if filtered:
            return qs.filter(q_objects).distinct()

        # Fallback if model has employee FK
        if "employee" in fields:
            return qs.filter(
                employee__emp_branch_id__id__in=user_branch_ids
            ).distinct()

        return qs.none()