from django.db import models
from django.db.models import Q

class BranchAccessMixin:
    """
    Mixin to filter QuerySets based on the user's accessible branches.
    This assumes the User model has an 'accessible_branches' ManyToMany field.
    """
    def get_queryset(self):
        # Get the original queryset
        qs = super().get_queryset()
        user = self.request.user

        # No filtering for unauthenticated users (usually handled by permissions)
        if not user.is_authenticated:
            return qs.none()

        # # Superusers see everything
        # if user.is_superuser:
        #     return qs

        if hasattr(user, 'accessible_branches'):
             # This legacy check is removed but kept for structure compatibility if needed temporarily
             pass

        # Use the specific UserBranchAccess model in the current tenant
        # We need to import it dynamically to avoid circular imports if this mixin is in Core
        from OrganisationManager.models import UserBranchAccess
        
        # Get branch IDs authorized for this user in the current tenant/schema
        user_branch_ids = UserBranchAccess.objects.filter(user=user).values_list('branch_id', flat=True)

        if not user_branch_ids:
             # Fallback logic: 
             # 1. If user is Superuser (already handled above) -> Pass
             # 2. If user is NOT superuser and has NO explicit branch access:
             #    a) If ESS user -> Show only data related to their own 'home' branch (from emp_master)
             #    b) Otherwise -> Show NOTHING (strict security)
             
            if hasattr(user, 'is_ess') and user.is_ess:
                 # Check their employee record
                 # user.employees is a ReverseManager
                 emp = user.employees.first()
                 if emp and emp.emp_branch_id:
                     user_branch_ids = [emp.emp_branch_id.id]
                 else:
                     return qs.none()
            else:
                 # If not ESS and no permissions, return empty
                 return qs.none()
        
        # At this point, user_branch_ids contains the list of allowed branch IDs

        # SUPPORT FOR SPECIFIC BRANCH SELECTION
        # If the user requests a specific branch (e.g., ?branch_id=5), intersect it with allowed branches.
        requested_branch_id = self.request.query_params.get('branch_id')
        if requested_branch_id:
            try:
                requested_branch_id = int(requested_branch_id)
                # Check if the requested branch is in the allowed list
                # Note: user_branch_ids is currently a QuerySet or list of IDs
                allowed_set = set(user_branch_ids) 
                
                if requested_branch_id in allowed_set:
                    # User is allowed to see this branch, so we strictly filter by THIS branch only.
                    user_branch_ids = [requested_branch_id]
                else:
                    # User requested a branch they don't have access to -> Return Empty
                    return qs.none()
            except ValueError:
                # Invalid branch_id format
                pass

        # Identify the field to filter by
        model = qs.model
        fields = [f.name for f in model._meta.get_fields()]

        # Common fields used for branch linking
        branch_fields = ['branch', 'branch_id', 'emp_branch_id', 'work_location']
        
        # Filter Logic
        q_objects = Q()
        
        filtered = False
        for field in branch_fields:
            if field in fields:
                # Create a Q object like Q(branch__id__in=user_branch_ids) 
                # Note: we use __in on the ID itself or the relation
                # If field is a ForeignKey, field__in=[ids] works.
                kwarg = {f"{field}__in": user_branch_ids}
                q_objects |= Q(**kwarg)
                filtered = True
        
        if filtered:
            return qs.filter(q_objects).distinct()
        
        # Support for indirect relationships
        indirect_fields = {
            'emp_id': 'emp_id__emp_branch_id__in',
            'employee': 'employee__emp_branch_id__in',
        }
        
        for field_name, filter_path in indirect_fields.items():
            if field_name in fields:
                    try:
                        kwarg = {filter_path: user_branch_ids}
                        return qs.filter(**kwarg).distinct()
                    except:
                        pass

        return qs

        return qs
