# v1.62.10 (item 171)
"""v1.62.10 (item 171) – the list of the Face / badge register screen: every active employee of the user's branches with
whether a face is registered and the badge (barcode) number. Read only – the face and the badge are still saved by
/calendars/api/attendance/enroll_face/ and /register_barcode/ (unchanged). The face data itself is never sent.

GET /calendars/api/face-register/  →  [{id, emp_code, name, branch, department, face, barcode}]
Rights: company administrator, or view / add / change attendance face register, or the attendance rights of HR."""
from rest_framework.response import Response
from rest_framework.views import APIView

RIGHTS = {'view_attendance_faceregister', 'add_attendance_faceregister', 'change_attendance_faceregister',
          'add_attendance', 'change_attendance'}


class FaceRegisterListView(APIView):
    def get(self, request):
        from Translations.i18n import tr
        from AccessControl.access import ctx, limit_by_branch
        from EmpManagement.models import emp_master
        if not request.user or not request.user.is_authenticated:
            return Response({'detail': tr('Please log in.')}, status=401)
        c = ctx(request)
        if not (c.admin or (c.codes & RIGHTS)):
            return Response({'detail': tr('You do not have permission for this.')}, status=403)
        qs = emp_master.objects.exclude(is_active=False).select_related('emp_branch_id', 'emp_dept_id').order_by('emp_code')
        qs = limit_by_branch(request, qs, branch_path='emp_branch_id')
        # v1.67.16 (bug list 11/11 item 1): a face registered in self service (My profile › Face punch – AttendancePlus.FaceProfile,
        # the face the self-service punch checks) counted as "No" here – only the older photo enrolment (face_encoding) was read.
        prof = {}
        try:
            from AttendancePlus.models import FaceProfile
            for fp in FaceProfile.objects.exclude(descriptors=[]).values('employee_id', 'consent_at', 'registered_by_id', 'consent_by_id'):
                prof[fp['employee_id']] = fp
        except Exception:   # noqa: BLE001 – Attendance plus not installed
            pass
        out = []
        for e in qs.only('id', 'emp_code', 'emp_first_name', 'emp_last_name', 'emp_branch_id__branch_name', 'emp_dept_id__dept_name',
                         'barcode_number', 'face_encoding', 'users_id'):
            out.append({'id': e.id, 'emp_code': e.emp_code or '',
                        'name': ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x),
                        'branch': getattr(e.emp_branch_id, 'branch_name', '') or '',
                        'department': getattr(e.emp_dept_id, 'dept_name', '') or '',
                        'face': bool(e.face_encoding) or e.id in prof, 'barcode': e.barcode_number or '',
                        'face_at': prof[e.id]['consent_at'].isoformat() if e.id in prof and prof[e.id]['consent_at'] else None,
                        'face_how': ('self service' if e.users_id and prof[e.id]['registered_by_id'] == e.users_id else 'HR') if e.id in prof
                        else ('photo' if e.face_encoding else '')})
        return Response(out)
