from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from django.conf import settings
from functools import wraps
import os
import re
import time
import json
import logging
from datetime import datetime, timedelta
from django.views.decorators.http import require_POST
from .models import User, Chat, Message, AppLog, UserSession, CallSignal
from .logging_service import get_logger

logger = get_logger(__name__)


def custom_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('is_logged_in'):
            return redirect('login')
        # Show maintenance page to non-admin users when maintenance mode is ON
        if MAINTENANCE_MODE and not request.session.get('is_admin'):
            return render(request, 'maintenance.html', status=503)
        return view_func(request, *args, **kwargs)
    return wrapper


# ── Maintenance Mode (in-memory flag) ──────────────────────────────────────
MAINTENANCE_MODE = False


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('is_logged_in'):
            return redirect('login')
        if not request.session.get('is_admin'):
            is_api = request.path.startswith('/admin-stats') or \
                     request.path.startswith('/admin-logs') or \
                     request.path.startswith('/admin-online') or \
                     request.path.startswith('/admin-server')
            if is_api:
                return JsonResponse({"error": "Access denied. Admins only."}, status=403)
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return wrapper


def index(request):
    return render(request, 'index.html')


def home(request):
    user = None
    email = request.session.get('email')

    if email:
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            request.session.flush()

    return render(request, 'home.html', {'user': user})


def signup(request):
    if request.method == "POST":
        name      = request.POST.get('name', '').strip()
        email     = request.POST.get('email', '').strip()
        mobile    = request.POST.get('mobile', '').strip()
        password  = request.POST.get('password', '')
        cpassword = request.POST.get('cpassword', '')
        profile_image = request.FILES.get('profile_image')

        if not name or not email or not password:
            return render(request, 'signup.html', {'msg': "Name, email and password are required"})

        if User.objects.filter(email=email).exists():
            logger.warning(f'Signup attempt with existing email: {email}')
            return render(request, 'signup.html', {'msg': "Email already exists"})

        if cpassword and password != cpassword:
            return render(request, 'signup.html', {'msg': "Password and Confirm Password do not match"})

        try:
            mobile_int = int(mobile) if mobile else 0
        except ValueError:
            return render(request, 'signup.html', {'msg': "Mobile number must be numeric"})

        try:
            user = User(
                name=name,
                email=email,
                mobile=mobile_int,
                password=password,
            )
            user.save()
            if profile_image:
                # File storage may be read-only in production (e.g. Vercel).
                # Never let a failed upload block account creation.
                try:
                    user.profile_image = profile_image
                    user.save(update_fields=['profile_image'])
                except OSError as e:
                    logger.warning(f'Profile image not saved for {email}: {str(e)}')
            logger.info(f'New user registered: {name} ({email})')
        except Exception as e:
            logger.error(f'Signup failed for {email}: {str(e)}')
            return render(request, 'signup.html', {'msg': f"Account creation failed: {str(e)}"})

        # Auto-login: take the user straight into the app, no second login needed
        _track_login_session(request, user)
        return redirect('home')

    return render(request, 'signup.html')


def signup_desh(request):
    return render(request, 'signup_desh.html')


def _track_login_session(request, user):
    """Shared login bookkeeping: session keys + device tracking. Used by login & signup."""
    request.session['email'] = user.email
    request.session['profile'] = user.profile_image.url if user.profile_image else ''
    request.session['is_logged_in'] = True
    request.session['user_id'] = user.id
    request.session['is_admin'] = (user.email.strip().lower() == getattr(settings, 'ADMIN_EMAIL', ''))
    # Track session & device info
    ua = request.META.get('HTTP_USER_AGENT', '')
    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
    if ip and ',' in ip:
        ip = ip.split(',')[0].strip()
    device_type = 'mobile' if any(x in ua.lower() for x in ['mobile', 'android', 'iphone']) else \
                  'tablet' if 'tablet' in ua.lower() or 'ipad' in ua.lower() else 'desktop'
    browser = 'Chrome' if 'Chrome' in ua else 'Firefox' if 'Firefox' in ua else \
              'Safari' if 'Safari' in ua else 'Edge' if 'Edg' in ua else 'Other'
    os_name = 'Android' if 'Android' in ua else 'iOS' if 'iPhone' in ua or 'iPad' in ua else \
              'Windows' if 'Windows' in ua else 'Mac' if 'Mac' in ua else \
              'Linux' if 'Linux' in ua else 'Other'
    try:
        UserSession.objects.filter(user=user).update(is_online=False)
        UserSession.objects.create(
            user=user,
            session_key=request.session.session_key or '',
            ip_address=ip or None,
            user_agent=ua[:500],
            device_type=device_type,
            browser=browser,
            os=os_name,
            is_online=True
        )
    except Exception as e:
        logger.error(f'Session tracking error: {e}')


@csrf_exempt
def login(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')

        try:
            user = User.objects.get(email=email)

            if user.password == password:
                _track_login_session(request, user)
                logger.info(f'User login successful: {user.name} ({email})')
                return redirect('home')

            logger.warning(f'Failed login - wrong password for: {email}')
            return render(request, 'login.html', {'msg': "Password doesn't match"})

        except User.DoesNotExist:
            logger.warning(f'Failed login - user not found: {email}')
            return render(request, 'login.html', {'msg': "Email doesn't exist"})

    return render(request, 'login.html')


def logout_view(request):
    email = request.session.get('email')
    user_id = request.session.get('user_id')
    if email:
        logger.info(f'User logout: {email}')
    if user_id:
        try:
            UserSession.objects.filter(user_id=user_id, is_online=True).update(is_online=False)
        except Exception:
            pass
    request.session.flush()
    return redirect('login')


@custom_login_required
def main(request):
    return render(request, 'main.html')


@custom_login_required
def chat(request):
    email = request.session.get('email')

    try:
        user = User.objects.get(email=email)
        users = User.objects.exclude(id=user.id)
    except User.DoesNotExist:
        request.session.flush()
        return redirect('login')

    return render(request, 'chat.html', {
        'user': user,
        'users': users
    })


@custom_login_required
def start_chat(request, user_id):
    other_user = get_object_or_404(User, id=user_id)

    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return redirect('login')

    current_user = get_object_or_404(User, id=current_user_id)

    chat = Chat.objects.filter(
        (Q(user1=current_user) & Q(user2=other_user)) |
        (Q(user1=other_user) & Q(user2=current_user))
    ).first()

    messages = []
    if chat:
        messages = Message.objects.filter(
            (Q(sender=current_user) & Q(receiver=other_user)) |
            (Q(sender=other_user) & Q(receiver=current_user))
        ).order_by('timestamp')

    context = {
        'other_user': other_user,
        'chat': chat,
        'messages': messages,
        'current_user': current_user
    }

    return render(request, 'chat/start_chat.html', context)


@require_POST
@csrf_exempt
def send_message(request):
    try:
        data = json.loads(request.body)
    except Exception as e:
        logger.error(f'Invalid JSON in send_message: {str(e)}')
        return JsonResponse({"status": "error", "message": "Invalid JSON"})

    user_id = request.session.get('user_id')
    if not user_id:
        logger.warning('send_message called without user_id')
        return JsonResponse({"status": "error", "message": "Login required"})

    try:
        sender = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f'Sender not found: {user_id}')
        return JsonResponse({"status": "error", "message": "Sender not found"})

    receiver_id = data.get("receiver_id")
    message_text = data.get("content", "").strip()

    if not receiver_id or not message_text:
        logger.warning(f'Invalid message data from {sender.name}')
        return JsonResponse({"status": "error", "message": "Invalid data"})

    try:
        receiver = User.objects.get(id=receiver_id)
    except User.DoesNotExist:
        logger.error(f'Receiver not found: {receiver_id}')
        return JsonResponse({"status": "error", "message": "Receiver not found"})

    Message.objects.create(
        sender=sender,
        receiver=receiver,
        text=message_text
    )
    logger.info(f'Message sent: {sender.name} -> {receiver.name} ({len(message_text)} chars)')
    return JsonResponse({"status": "success"})


def show_logs(request):
    logs_data = []
    log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs.txt')

    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        recent_messages = Message.objects.select_related('sender', 'receiver').order_by('-timestamp')[:30]
        for msg in recent_messages:
            logs_data.append({
                'time': msg.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'level': 'INFO',
                'message': f'Message from {msg.sender.name} to {msg.receiver.name}: "{msg.text[:60]}"',
                'source': 'chat',
            })
    except Exception:
        pass

    sql_pattern = re.compile(r'^\(([\d.]+)\)\s+(.+)$', re.DOTALL)
    file_pattern = re.compile(r'^File (.+) first seen')
    changed_pattern = re.compile(r'^(.+) changed, reloading')
    signal_pattern = re.compile(r'Signal results:')

    for line in reversed(lines[-300:]):
        line = line.strip()
        if not line:
            continue

        parts = line.split(' | ')
        if len(parts) == 3:
            logs_data.append({'time': parts[0], 'level': parts[1], 'message': parts[2], 'source': 'app'})
            continue

        m = sql_pattern.match(line)
        if m and ('SELECT' in line or 'INSERT' in line or 'UPDATE' in line or 'DELETE' in line):
            sql_preview = m.group(2).replace('\n', ' ').strip()[:120]
            logs_data.append({'time': now, 'level': 'DEBUG', 'message': f'SQL ({m.group(1)}s): {sql_preview}', 'source': 'sql'})
            continue

        m = changed_pattern.search(line)
        if m:
            logs_data.append({'time': now, 'level': 'WARNING', 'message': f'Reload triggered: {m.group(1).split("/")[-1]}', 'source': 'server'})
            continue

        if any(kw in line for kw in ['Watching for file changes', 'Apps ready', 'autoreload_started', 'Performing system checks']):
            logs_data.append({'time': now, 'level': 'INFO', 'message': line[:120], 'source': 'server'})
            continue

        if file_pattern.match(line) or signal_pattern.search(line):
            continue

        if 10 < len(line) < 200 and not line.startswith('File '):
            level = 'ERROR' if 'error' in line.lower() or 'exception' in line.lower() or 'traceback' in line.lower() else \
                    'WARNING' if 'warn' in line.lower() or 'changed' in line.lower() else 'INFO'
            logs_data.append({'time': now, 'level': level, 'message': line[:150], 'source': 'server'})

    seen = set()
    unique_logs = []
    for log in logs_data:
        key = log['message'][:80]
        if key not in seen:
            seen.add(key)
            unique_logs.append(log)
        if len(unique_logs) >= 80:
            break

    return render(request, 'logs.html', {'logs': unique_logs})


@custom_login_required
def get_messages(request, user_id):
    current_user_id = request.session.get('user_id')

    if not current_user_id:
        return JsonResponse({"messages": []})

    try:
        Message.objects.filter(
            sender_id=user_id,
            receiver_id=current_user_id,
            is_read=False
        ).update(is_read=True)

        messages = Message.objects.filter(
            Q(sender_id=current_user_id, receiver_id=user_id) |
            Q(sender_id=user_id, receiver_id=current_user_id)
        ).order_by('timestamp')

        data = [
            {
                "id": msg.id,
                "sender": msg.sender_id,
                "message": msg.text,
                "time": msg.timestamp.strftime("%H:%M"),
                "is_read": msg.is_read
            }
            for msg in messages
        ]
        return JsonResponse({"messages": data})
    except Exception as e:
        logger.error(f'Error fetching messages: {str(e)}')
        return JsonResponse({"messages": [], "error": str(e)})


def get_users_with_unread(request):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return JsonResponse({"users": []})

    try:
        users = User.objects.exclude(id=current_user_id)
        user_list = []

        for u in users:
            unread_count = Message.objects.filter(
                sender=u,
                receiver_id=current_user_id,
                is_read=False
            ).count()
            user_list.append({"id": u.id, "unread": unread_count})

        return JsonResponse({"users": user_list})
    except Exception as e:
        logger.error(f'Error fetching unread counts: {str(e)}')
        return JsonResponse({"users": [], "error": str(e)})


_typing_store = {}


@csrf_exempt
def set_typing(request):
    if request.method != 'POST':
        return JsonResponse({"status": "error"})
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return JsonResponse({"status": "error"})
    try:
        data = json.loads(request.body)
        receiver_id = str(data.get('receiver_id', ''))
        is_typing = bool(data.get('is_typing', False))
        key = f"{current_user_id}_{receiver_id}"
        _typing_store[key] = time.time() if is_typing else 0
        return JsonResponse({"status": "ok"})
    except Exception as e:
        logger.error(f'Error in set_typing: {str(e)}')
        return JsonResponse({"status": "error", "message": str(e)})


def get_typing(request, user_id):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return JsonResponse({"typing": False})
    key = f"{user_id}_{current_user_id}"
    last = _typing_store.get(key, 0)
    typing = (time.time() - last) < 3
    return JsonResponse({"typing": typing})


# ── Voice/Video Calls (WebRTC signaling over HTTP polling) ──────────────────
# Media flows peer-to-peer via WebRTC; only tiny signaling messages go
# through the server, so this works on hosts without WebSocket support.
CALL_SIGNAL_KINDS = ('offer', 'answer', 'ice', 'hangup', 'reject', 'busy')
CALL_SIGNAL_TTL_MINUTES = 10
CALL_MAX_PAYLOAD = 12000


def _prune_call_signals():
    try:
        from django.utils import timezone
        cutoff = timezone.now() - timedelta(minutes=CALL_SIGNAL_TTL_MINUTES)
        CallSignal.objects.filter(timestamp__lt=cutoff).delete()
    except Exception as e:
        logger.error(f'Call signal prune error: {e}')


@csrf_exempt
def send_call_signal(request):
    if request.method != 'POST':
        return JsonResponse({"status": "error", "message": "POST only"}, status=405)
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return JsonResponse({"status": "error", "message": "Login required"}, status=401)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    kind = data.get('kind', '')
    receiver_id = data.get('receiver_id')
    call_id = str(data.get('call_id', ''))[:64]
    payload = data.get('payload', {})

    if kind not in CALL_SIGNAL_KINDS:
        return JsonResponse({"status": "error", "message": "Bad signal kind"}, status=400)
    if not receiver_id or not call_id or not isinstance(payload, dict):
        return JsonResponse({"status": "error", "message": "Invalid data"}, status=400)
    try:
        payload_str = json.dumps(payload)
    except Exception:
        return JsonResponse({"status": "error", "message": "Bad payload"}, status=400)
    if len(payload_str) > CALL_MAX_PAYLOAD:
        return JsonResponse({"status": "error", "message": "Payload too large"}, status=400)

    try:
        sender = User.objects.get(id=current_user_id)
        receiver = User.objects.get(id=receiver_id)
    except User.DoesNotExist:
        return JsonResponse({"status": "error", "message": "User not found"}, status=404)

    try:
        CallSignal.objects.create(
            call_id=call_id, sender=sender, receiver=receiver,
            kind=kind, payload=payload_str,
        )
    except Exception as e:
        logger.error(f'Call signal save failed: {e}')
        return JsonResponse({"status": "error", "message": "Could not save signal. DB table missing?"}, status=500)
    _prune_call_signals()
    return JsonResponse({"status": "ok"})


def get_call_signals(request):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return JsonResponse({"signals": []})
    _prune_call_signals()
    try:
        qs = CallSignal.objects.filter(
            receiver_id=current_user_id, consumed=False
        ).select_related('sender').order_by('timestamp')[:50]
        data = [
            {
                "id": s.id,
                "call_id": s.call_id,
                "kind": s.kind,
                "sender_id": s.sender_id,
                "sender_name": s.sender.name,
                "payload": json.loads(s.payload or '{}'),
                "time": s.timestamp.strftime("%H:%M:%S"),
            }
            for s in qs
        ]
        CallSignal.objects.filter(id__in=[s["id"] for s in data]).update(consumed=True)
        return JsonResponse({"signals": data})
    except Exception as e:
        logger.error(f'Error fetching call signals: {e}')
        return JsonResponse({"signals": [], "error": str(e)})


# ── Admin Dashboard ──────────────────────────────────────────────────────────

@admin_required
def admin_dashboard(request):
    return render(request, 'admin_dashboard.html')


def debug_admin_check(request):
    """Temporary debug - remove after fixing"""
    if not request.session.get('is_logged_in'):
        return JsonResponse({'error': 'not logged in'})
    session_email = request.session.get('email', '')
    session_is_admin = request.session.get('is_admin', False)
    env_admin_email = getattr(settings, 'ADMIN_EMAIL', 'NOT SET')
    return JsonResponse({
        'session_email': session_email,
        'session_is_admin': session_is_admin,
        'env_admin_email': env_admin_email,
        'match': session_email.strip().lower() == env_admin_email,
    })


@admin_required
def admin_stats(request):
    try:
        from django.utils import timezone
        total_users = User.objects.count()
        total_messages = Message.objects.count()
        recent_activity = Message.objects.filter(
            timestamp__gte=timezone.now() - timedelta(hours=24)
        ).count()
        cutoff = timezone.now() - timedelta(minutes=10)
        UserSession.objects.filter(is_online=True, last_seen__lt=cutoff).update(is_online=False)
        online_users = UserSession.objects.filter(is_online=True).count()
        return JsonResponse({
            'total_users': total_users,
            'total_messages': total_messages,
            'recent_activity': recent_activity,
            'online_users': online_users,
        })
    except Exception as e:
        logger.error(f'Error fetching admin stats: {str(e)}')
        return JsonResponse({"error": str(e)}, status=500)


@admin_required
def admin_logs(request):
    try:
        level_filter = request.GET.get('level', '')
        qs = AppLog.objects.all().order_by('-timestamp')
        if level_filter:
            qs = qs.filter(level=level_filter.upper())
        logs = qs[:100]
        logs_data = [
            {
                'time': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'level': log.level,
                'message': log.message,
                'source': log.logger_name,
                'module': log.module,
                'function': log.function,
                'line': log.line_number
            }
            for log in logs
        ]
        return JsonResponse({'logs': logs_data})
    except Exception as e:
        logger.error(f'Error fetching admin logs: {str(e)}')
        return JsonResponse({"logs": [], "error": str(e)}, status=500)


@admin_required
def admin_online_users(request):
    try:
        from django.utils import timezone
        cutoff = timezone.now() - timedelta(minutes=10)
        UserSession.objects.filter(is_online=True, last_seen__lt=cutoff).update(is_online=False)
        sessions = UserSession.objects.select_related('user').order_by('-last_seen')[:50]
        data = [
            {
                'user': s.user.name,
                'email': s.user.email,
                'is_online': s.is_online,
                'device_type': s.device_type,
                'browser': s.browser,
                'os': s.os,
                'ip': s.ip_address or 'N/A',
                'last_seen': s.last_seen.strftime('%Y-%m-%d %H:%M:%S'),
                'login_time': s.login_time.strftime('%Y-%m-%d %H:%M:%S'),
                'latitude': s.latitude,
                'longitude': s.longitude,
            }
            for s in sessions
        ]
        return JsonResponse({'sessions': data})
    except Exception as e:
        logger.error(f'Error fetching online users: {str(e)}')
        return JsonResponse({"sessions": [], "error": str(e)}, status=500)


@admin_required
def admin_server_health(request):
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        return JsonResponse({
            'cpu_percent': cpu,
            'memory_percent': mem.percent,
            'memory_used_mb': round(mem.used / 1024 / 1024),
            'memory_total_mb': round(mem.total / 1024 / 1024),
            'disk_percent': disk.percent,
            'disk_used_gb': round(disk.used / 1024 / 1024 / 1024, 1),
            'disk_total_gb': round(disk.total / 1024 / 1024 / 1024, 1),
        })
    except ImportError:
        return JsonResponse({'error': 'psutil not installed. Run: pip install psutil'}, status=500)
    except Exception as e:
        logger.error(f'Server health error: {str(e)}')
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def save_location(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error'}, status=405)
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'status': 'error', 'message': 'not logged in'}, status=401)
    try:
        data = json.loads(request.body)
        lat = float(data.get('latitude'))
        lng = float(data.get('longitude'))
        UserSession.objects.filter(
            user_id=user_id, is_online=True
        ).update(latitude=lat, longitude=lng)
        return JsonResponse({'status': 'ok'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@csrf_exempt
@admin_required
def admin_maintenance_toggle(request):
    global MAINTENANCE_MODE
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            MAINTENANCE_MODE = bool(data.get('enabled', False))
            logger.info(f'Maintenance mode {"ON" if MAINTENANCE_MODE else "OFF"} by admin')
            return JsonResponse({'maintenance': MAINTENANCE_MODE})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'maintenance': MAINTENANCE_MODE})
