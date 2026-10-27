"""v1.62.0 – security headers on every answer of the API (README-UPDATE 83.3).

nginx adds the headers of the web app itself (deploy/nginx-zeo.conf); this middleware makes the API answer safely even when
it is reached without nginx:
  * JSON / API answers: Content-Security-Policy "default-src 'none'; frame-ancestors 'none'" (an API answer is never a page);
  * uploaded files that a browser could run (HTML, SVG, XML): "sandbox" – no script, no forms, no same-origin rights;
  * every answer: Permissions-Policy (no camera / microphone / location for the API), X-Content-Type-Options nosniff,
    Referrer-Policy and Cross-Origin-Opener-Policy come from Django's SecurityMiddleware, X-Frame-Options DENY from its
    clickjacking middleware; HSTS from SecurityMiddleware when ZEO_HTTPS is on.
The Django admin pages (/admin/) keep their own scripts and styles.
"""

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
RUNNABLE = ('text/html', 'image/svg', 'application/xml', 'text/xml', 'application/xhtml')


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        r = self.get_response(request)
        try:
            ct = (r.get('Content-Type') or '').lower()
            path = request.path
            r.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=(), usb=()')
            r.setdefault('X-Content-Type-Options', 'nosniff')
            if path.startswith('/media/') or path.startswith('/_protected_media/'):
                if ct.startswith(RUNNABLE):
                    r['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
            elif path.startswith('/admin/') or path.startswith('/static/'):
                pass
            elif 'json' in ct or not ct.startswith('text/html'):
                r.setdefault('Content-Security-Policy', API_CSP)
            if 'json' in ct:
                r.setdefault('Cache-Control', 'no-store')
        except Exception:
            pass
        return r
