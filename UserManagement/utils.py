from rest_framework_simplejwt.tokens import RefreshToken

def get_tokens_for_user(user, tenant_schema):
    refresh = RefreshToken.for_user(user)
    refresh['tenant'] = tenant_schema

    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }