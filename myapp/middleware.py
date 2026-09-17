"""Response-header middleware: never let the browser cache app HTML.

Why: after logout, pressing the mobile/desktop back button must not
resurrect a logged-in page (chat/home) from the back-forward cache or
disk cache. The next load then hits the server, which redirects to login.
Only HTML responses are touched — static/media/API payloads are skipped.
"""


class NoStoreCacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        ctype = response.get('Content-Type', '')
        if ctype.startswith('text/html'):
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
        return response
