from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from django.conf import settings
from functools import wraps
import os
import re
import time
import json
import logging
from datetime import timedelta
from django.views.decorators.http import require_POST
from .models import User, Chat, Message, MessageReaction, AppLog, UserSession, CallSignal, SiteSetting, Announcement
from .logging_service import get_logger

logger = get_logger(__name__)


def _ist(ts):
    """Convert any timestamp to India (IST) aware datetime."""
    from datetime import timezone as _dt_tz
    from django.utils import timezone
    if timezone.is_naive(ts):
        ts = timezone.make_aware(ts, _dt_tz.utc)
    return timezone.localtime(ts)


def _t12(ts):
    """User-facing chat time in 12-hour IST format: '2:30 PM'."""
    try:
        return _ist(ts).strftime('%I:%M %p').lstrip('0')
    except Exception:
        try:
            return ts.strftime('%I:%M %p').lstrip('0')
        except Exception:
            return ''


def _ist_date(ts):
    """IST calendar date for grouping/comparing messages."""
    try:
        return _ist(ts).date()
    except Exception:
        return ts.date()


def _ist_str(ts, fmt):
    """Format any timestamp in IST. Never raises — returns '' on bad input."""
    try:
        return _ist(ts).strftime(fmt)
    except Exception:
        return ''


def _ist_ymd(ts):
    """IST calendar day: '2026-09-18' (chat day pills, date grouping)."""
    return _ist_str(ts, '%Y-%m-%d')


def _ist_hms(ts):
    """IST clock with seconds: '14:30:05'."""
    return _ist_str(ts, '%H:%M:%S')


def _ist_full(ts):
    """IST full stamp for logs/admin: '2026-09-18 14:30:05'."""
    return _ist_str(ts, '%Y-%m-%d %H:%M:%S')


def _ist_log(ts):
    """Admin-log stamp: '19/09/2026 11:30:05 PM' (12-hour IST, DD/MM/YYYY)."""
    s = _ist_str(ts, '%d/%m/%Y %I:%M:%S %p')
    # Drop the hour's leading zero: ' 09:30' -> ' 9:30' (date keeps its zeros).
    return s.replace(' 0', ' ', 1) if s else ''


def _ist_min(ts):
    """IST stamp without seconds for admin tables: '2026-09-18 14:30'."""
    return _ist_str(ts, '%Y-%m-%d %H:%M')


def _ist_day_start_utc():
    """IST midnight (start of 'today' in India) as a UTC-aware datetime.

    Use with timestamp__gte filters — the naive __date lookup runs in UTC
    and miscounts near midnight IST.
    """
    from django.utils import timezone
    from datetime import timezone as _dt_tz
    now_ist = _ist(timezone.now())
    return now_ist.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_dt_tz.utc)


# ── Schema resilience (zero-downtime deploys) ──────────────────────────
# If new code reaches production before `migrate 0014` has run, Postgres
# raises "column myapp_message.reply_to_id does not exist" (or
# is_deleted/edited_at/forwarded, or relation myapp_messagereaction).
# Instead of 500ing every poll, detect that specific error and retry the
# query without the new 0014 fields. Once migrate runs, the fast path
# succeeds again automatically — no restart needed.
_MSG_POWER_FIELDS = ('reply_to', 'edited_at', 'forwarded', 'is_deleted')


def _is_missing_schema_error(e):
    s = str(e).lower()
    return (
        'does not exist' in s
        or 'undefinedcolumn' in s
        or 'no such column' in s
        or 'no such table' in s
    )


def _legacy_insert_message(sender_id, receiver_id, text='', kind='text', image_url='', image_file_id=''):
    """INSERT a message using only columns that actually exist in the DB.

    Django's ORM INSERT always includes every model field, so on a stale
    prod DB (pre-0014) even `create(text=...)` fails with
    "column myapp_message.is_deleted does not exist". Raw SQL here lets
    text/image sends keep working until `migrate` runs.
    """
    from django.db import connection
    from django.utils import timezone
    with connection.cursor() as cur:
        existing = {c.name for c in connection.introspection.get_table_description(cur, 'myapp_message')}
    data = {
        'sender_id': int(sender_id),
        'receiver_id': int(receiver_id),
        'text': text or '',
        'timestamp': timezone.now(),
        'is_read': False,
    }
    if 'kind' in existing:
        data['kind'] = kind or 'text'
    if 'image_url' in existing:
        data['image_url'] = image_url or ''
    if 'image_file_id' in existing:
        data['image_file_id'] = image_file_id or ''
    # 0014 columns: include with safe defaults when the DB has them
    # (new DB needs them for NOT NULL), skip when it doesn't (old DB).
    if 'forwarded' in existing:
        data['forwarded'] = False
    if 'is_deleted' in existing:
        data['is_deleted'] = False
    if 'edited_at' in existing:
        data['edited_at'] = None
    if 'reply_to_id' in existing:
        data['reply_to_id'] = None
    cols = ', '.join(f'"{c}"' for c in data)
    placeholders = ', '.join(['%s'] * len(data))
    vals = list(data.values())
    with connection.cursor() as cur:
        if connection.vendor == 'postgresql':
            cur.execute(f'INSERT INTO "myapp_message" ({cols}) VALUES ({placeholders}) RETURNING "id"', vals)
            row = cur.fetchone()
            return row[0] if row else None
        cur.execute(f'INSERT INTO "myapp_message" ({cols}) VALUES ({placeholders})', vals)
        return cur.lastrowid


def custom_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('is_logged_in'):
            return redirect('login')
        # Kicked out mid-session? (account deactivated by admin)
        uid = request.session.get('user_id')
        if uid:
            try:
                _u = User.objects.get(id=uid)
                if not _u.is_active:
                    request.session.flush()
                    return redirect('access_denied')
            except User.DoesNotExist:
                request.session.flush()
                return redirect('login')
        # Show maintenance page to non-admin users when maintenance mode is ON
        if is_maintenance_on() and not request.session.get('is_admin') and not request.session.get('admin_auth'):
            return render(request, 'maintenance.html', status=503)
        return view_func(request, *args, **kwargs)
    return wrapper


# ── Maintenance Mode (DB-persisted flag — works across serverless instances) ───
def is_maintenance_on():
    try:
        return SiteSetting.objects.filter(key='maintenance').values_list('value', flat=True).first() == 'on'
    except Exception:
        return False


def set_maintenance_on(on):
    SiteSetting.objects.update_or_create(key='maintenance', defaults={'value': 'on' if on else 'off'})


def _maintenance_block():
    """503 JSON for mutating APIs during maintenance (admins bypass)."""
    return JsonResponse({"status": "error", "maintenance": True, "message": "Maintenance mode is on"}, status=503)


def _maintenance_on_for(request):
    return is_maintenance_on() and not request.session.get('is_admin') and not request.session.get('admin_auth')


def _is_revoked(request):
    """True when the session's user was deactivated (or vanished). Flushes the session."""
    uid = request.session.get('user_id')
    if not uid:
        return False
    try:
        active = User.objects.get(id=uid).is_active
    except User.DoesNotExist:
        active = False
    if not active:
        request.session.flush()
        return True
    return False


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # Standalone console session (/admin/) also grants API access
        if request.session.get('admin_auth'):
            return view_func(request, *args, **kwargs)
        if not request.session.get('is_logged_in'):
            return redirect('login')
        # Every logged-in user may READ the maintenance flag (their pages poll it).
        # Toggling (POST) stays admin-only.
        if request.method == 'GET' and request.path.rstrip('/') == '/admin-maintenance':
            return view_func(request, *args, **kwargs)
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
    stats = {'unread': 0, 'today': 0, 'total': 0}
    recent_chats = []

    if email:
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            request.session.flush()
            user = None

    if email and _maintenance_on_for(request):
        return render(request, 'maintenance.html', status=503)

    if user:
        try:
            from django.utils import timezone
            mine = Message.objects.filter(Q(sender=user) | Q(receiver=user))
            stats['unread'] = mine.filter(receiver=user, is_read=False).count()
            stats['today'] = mine.filter(timestamp__gte=_ist_day_start_utc()).count()
            stats['total'] = mine.count()
            latest = mine.select_related('sender', 'receiver').order_by('-timestamp')[:60]
            seen = {}
            for m in latest:
                other = m.receiver if m.sender_id == user.id else m.sender
                if other.id not in seen:
                    seen[other.id] = {
                        'id': other.id,
                        'name': other.name,
                        'avatar': other.avatar,
                        'last_text': m.text,
                        'time': _t12(m.timestamp),
                        'unread': mine.filter(sender=other, receiver=user, is_read=False).count(),
                    }
                if len(seen) >= 5:
                    break
            recent_chats = list(seen.values())
        except Exception as e:
            logger.error(f'Home stats error: {e}')

    return render(request, 'home.html', {'user': user, 'stats': stats, 'recent_chats': recent_chats})


def signup(request):
    if request.method == "POST":
        name      = request.POST.get('name', '').strip()
        email     = request.POST.get('email', '').strip()
        mobile    = request.POST.get('mobile', '').strip()
        password  = request.POST.get('password', '')
        cpassword = request.POST.get('cpassword', '')
        profile_image = request.FILES.get('profile_image')

        if not name or not email or not password:
            if request.method == "POST" and not request.POST:
                return render(request, 'signup.html', {'msg': "Submit failed — photo may be too large. Try again without photo."})
            missing = [f for f, v in (("Name", name), ("Email", email), ("Password", password)) if not v]
            return render(request, 'signup.html', {'msg': "Please fill: " + ", ".join(missing)})

        if name.strip().lower() in RESERVED_USERNAMES:
            return render(request, 'signup.html', {'msg': "This username is reserved. Please choose another."})

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
                # Profile photos go to ImageKit (local disk is ephemeral on Vercel).
                # Never let a failed upload block account creation.
                try:
                    url, fid = _upload_avatar(profile_image)
                    user.avatar_url = url
                    user.avatar_file_id = fid
                    user.save(update_fields=['avatar_url', 'avatar_file_id'])
                except Exception as e:
                    logger.warning(f'Signup avatar skipped for {email}: {str(e)}')
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
    # Chat login is separate from console admin: never leak the admin tab in.
    request.session.pop('admin_auth', None)
    request.session.pop('admin_user', None)
    request.session['email'] = user.email
    request.session['profile'] = user.avatar
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


# Usernames nobody may register (they'd clash with the admin console's identity)
RESERVED_USERNAMES = {'admin', 'administrator', 'root', 'system', 'support', 'moderator', 'mod', 'help', 'dashsocial', 'official', 'service'}


def login(request):
    if request.method == 'POST':
        identifier = (request.POST.get('identifier') or request.POST.get('email') or '').strip()
        password = request.POST.get('password')

        # One input accepts either username (name) or email
        user = User.objects.filter(email__iexact=identifier).first()
        if user is None:
            user = User.objects.filter(name__iexact=identifier).order_by('id').first()

        if user is None:
            logger.warning(f'Failed login - account not found: {identifier}')
            return render(request, 'login.html', {'msg': "Account doesn't exist"})

        if not user.is_active:
            logger.warning(f'Blocked login - deactivated account: {user.email}')
            return render(request, 'access_denied.html', {'email': user.email}, status=403)

        if user.password == password:
            _track_login_session(request, user)
            logger.info(f'User login successful: {user.name} ({user.email})')
            return redirect('home')

        logger.warning(f'Failed login - wrong password for: {user.email}')
        return render(request, 'login.html', {'msg': "Password doesn't match"})

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


def access_denied(request):
    # Active logged-in users landing here by mistake go home
    uid = request.session.get('user_id')
    if uid:
        try:
            if User.objects.get(id=uid).is_active:
                return redirect('home')
        except User.DoesNotExist:
            pass
    return render(request, 'access_denied.html', {'email': request.session.get('email', '')})


@custom_login_required
def main(request):
    return render(request, 'main.html')


def _recent_map(user, limit=300):
    """Latest message per conversation partner: {user_id: {'text', 'time'}}.

    Text is prefixed with 'You: ' for own messages (chat-app style), time is
    12-hour IST for today, 'Yesterday', or 'dd Mon' for older.
    """
    from django.utils import timezone
    today = _ist_date(timezone.now())
    out = {}
    try:
        recent = Message.objects.filter(
            Q(sender=user) | Q(receiver=user)
        ).select_related('sender', 'receiver').order_by('-timestamp')[:limit]
        for m in recent:
            oid = m.receiver_id if m.sender_id == user.id else m.sender_id
            if oid in out:
                continue
            if getattr(m, 'is_deleted', False):
                text = 'This message was deleted'
            elif getattr(m, 'kind', 'text') == 'image':
                text = 'Photo' + (f" — {m.text}" if m.text else '')
            else:
                text = m.text or ''
            if len(text) > 42:
                text = text[:42].rstrip() + '…'
            if m.sender_id == user.id:
                text = 'You: ' + text
            ts = m.timestamp
            if _ist_date(ts) == today:
                when = _t12(ts)
            elif _ist_date(ts) == today - timedelta(days=1):
                when = 'Yesterday'
            else:
                when = _ist_str(ts, '%d %b')
            out[oid] = {'text': text, 'time': when}
    except Exception as e:
        if _is_missing_schema_error(e):
            # Prod DB predates migrate 0014: retry without the new columns.
            try:
                recent = list(Message.objects.filter(
                    Q(sender=user) | Q(receiver=user)
                ).select_related('sender', 'receiver').defer(*_MSG_POWER_FIELDS).order_by('-timestamp')[:limit])
                for m in recent:
                    oid = m.receiver_id if m.sender_id == user.id else m.sender_id
                    if oid in out:
                        continue
                    text = m.text or ''
                    if getattr(m, 'kind', 'text') == 'image' and 'Photo' not in text[:5]:
                        text = 'Photo' + (f" — {text}" if text else '')
                    if len(text) > 42:
                        text = text[:42].rstrip() + '…'
                    if m.sender_id == user.id:
                        text = 'You: ' + text
                    ts = m.timestamp
                    if _ist_date(ts) == today:
                        when = _t12(ts)
                    elif _ist_date(ts) == today - timedelta(days=1):
                        when = 'Yesterday'
                    else:
                        when = _ist_str(ts, '%d %b')
                    out[oid] = {'text': text, 'time': when}
                logger.warning('Recent map: 0014 columns missing, served legacy preview. Run migrate.')
                return out
            except Exception as e2:
                logger.error(f'Recent map error: {e2}')
                return out
        logger.error(f'Recent map error: {e}')
    return out


@custom_login_required
def chat(request):
    email = request.session.get('email')

    try:
        user = User.objects.get(email=email)
        users = list(User.objects.exclude(id=user.id).order_by('name'))
        previews = _recent_map(user)
        for u in users:
            info = previews.get(u.id, {})
            u.last_text = info.get('text', '')
            u.last_time = info.get('time', '')
    except User.DoesNotExist:
        request.session.flush()
        return redirect('login')

    return render(request, 'chat.html', {
        'user': user,
        'users': users
    })


@custom_login_required
def profile(request):
    try:
        user = User.objects.get(id=request.session.get('user_id'))
    except (User.DoesNotExist, ValueError, TypeError):
        request.session.flush()
        return redirect('login')

    msg = ''
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        mobile = (request.POST.get('mobile') or '').strip()
        photo = request.FILES.get('avatar')
        if not name:
            msg = 'Name cannot be empty.'
        elif name.lower() != user.name.lower() and name.lower() in RESERVED_USERNAMES:
            msg = 'This username is reserved. Please choose another.'
        else:
            try:
                mobile_int = int(mobile) if mobile else 0
            except ValueError:
                mobile_int = None
            if mobile_int is None:
                msg = 'Mobile number must be numeric.'
            else:
                if photo:
                    try:
                        url, fid = _upload_avatar(photo)
                        _delete_avatar_file(user.avatar_file_id)
                        user.avatar_url = url
                        user.avatar_file_id = fid
                    except ValueError as e:
                        msg = str(e)
                    except Exception as e:
                        logger.error(f'Avatar upload failed for {user.email}: {e}')
                        msg = 'Photo upload failed, try again.'
                if not msg:
                    user.name = name
                    user.mobile = mobile_int
                    user.save(update_fields=['name', 'mobile', 'avatar_url', 'avatar_file_id'])
                    request.session['profile'] = user.avatar
                    msg = 'Profile updated.'
    return render(request, 'profile.html', {'user': user, 'msg': msg})


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
        try:
            messages = Message.objects.filter(
                (Q(sender=current_user) & Q(receiver=other_user)) |
                (Q(sender=other_user) & Q(receiver=current_user))
            ).order_by('timestamp')
            # Force evaluation inside try so a stale DB (pre-0014) falls back.
            messages = list(messages)
        except Exception as e:
            if _is_missing_schema_error(e):
                messages = list(Message.objects.filter(
                    (Q(sender=current_user) & Q(receiver=other_user)) |
                    (Q(sender=other_user) & Q(receiver=current_user))
                ).defer(*_MSG_POWER_FIELDS).order_by('timestamp'))
                logger.warning('start_chat: 0014 columns missing, served legacy history. Run migrate.')
            else:
                raise

    context = {
        'other_user': other_user,
        'chat': chat,
        'messages': messages,
        'current_user': current_user
    }

    return render(request, 'chat/start_chat.html', context)


@require_POST
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
    if _is_revoked(request):
        return JsonResponse({"status": "error", "revoked": True, "message": "Account deactivated"}, status=401)
    if _maintenance_on_for(request):
        return _maintenance_block()

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

    try:
        Message.objects.create(
            sender=sender,
            receiver=receiver,
            text=message_text,
            reply_to=_resolve_reply(user_id, receiver.id, data.get("reply_to")),
        )
    except Exception as e:
        if _is_missing_schema_error(e):
            # Stale DB: ORM INSERT includes 0014 columns -> retry as raw
            # SQL with only existing columns (reply/quote dropped).
            try:
                _legacy_insert_message(sender.id, receiver.id, text=message_text)
            except Exception as e2:
                logger.error(f'Message send failed: {e2}')
                return JsonResponse({"status": "error", "message": "Could not send. DB migration pending."}, status=500)
            logger.warning('send_message: 0014 columns missing, sent via legacy insert. Run migrate.')
        else:
            logger.error(f'Message send failed: {e}')
            return JsonResponse({"status": "error", "message": "Could not send"}, status=500)
    logger.info(f'Message sent: {sender.name} -> {receiver.name} ({len(message_text)} chars)')
    return JsonResponse({"status": "success"})


@custom_login_required
def show_logs(request):
    logs_data = []
    log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs.txt')

    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    from django.utils import timezone
    now = _ist_full(timezone.now())

    try:
        recent_messages = Message.objects.select_related('sender', 'receiver').order_by('-timestamp')[:30]
        for msg in recent_messages:
            logs_data.append({
                'time': _ist_full(msg.timestamp),
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


def get_messages(request, user_id):
    # NOTE: no page-redirect decorator here on purpose — kicked users must get
    # JSON {"revoked": true} (a redirect's HTML would just break polling).
    current_user_id = request.session.get('user_id')

    if not current_user_id:
        return JsonResponse({"messages": []})
    if _is_revoked(request):
        return JsonResponse({"revoked": True}, status=401)

    try:
        Message.objects.filter(
            sender_id=user_id,
            receiver_id=current_user_id,
            is_read=False
        ).update(is_read=True)

        # Latest 300 only: keeps every poll payload + DOM small and smooth.
        # (Older history stays in the DB.)
        from django.utils import timezone
        try:
            messages = list(Message.objects.filter(
                Q(sender_id=current_user_id, receiver_id=user_id) |
                Q(sender_id=user_id, receiver_id=current_user_id)
            ).select_related('sender', 'reply_to', 'reply_to__sender').order_by('-timestamp')[:300])
        except Exception as e:
            if not _is_missing_schema_error(e):
                raise
            # Legacy DB (pre-0014): fetch without the new columns.
            messages = list(Message.objects.filter(
                Q(sender_id=current_user_id, receiver_id=user_id) |
                Q(sender_id=user_id, receiver_id=current_user_id)
            ).select_related('sender').defer(*_MSG_POWER_FIELDS).order_by('-timestamp')[:300])
            logger.warning('get_messages: 0014 columns missing, served legacy payload. Run migrate.')
        messages.reverse()
        try:
            reactions = _reactions_map(messages, current_user_id)
        except Exception:
            reactions = {}
        data = [
            {
                "id": msg.id,
                "sender": msg.sender_id,
                "message": msg.text,
                "kind": getattr(msg, 'kind', 'text'),
                "image": getattr(msg, 'image_url', ''),
                # IST: the bubble must show the real Indian send time,
                # not the raw UTC value stored in the DB.
                "time": _t12(msg.timestamp),
                "date": _ist_ymd(msg.timestamp),
                "is_read": msg.is_read,
                "reply": _reply_payload(msg, current_user_id),
                "edited": getattr(msg, 'edited_at', None) is not None,
                "forwarded": bool(getattr(msg, 'forwarded', False)),
                "deleted": bool(getattr(msg, 'is_deleted', False)),
                "reactions": reactions.get(msg.id, []),
            }
            for msg in messages
        ]
        # IST "today" so day pills (Today/Yesterday) match server dates exactly
        return JsonResponse({"messages": data, "today": _ist_date(timezone.now()).isoformat()})
    except Exception as e:
        logger.error(f'Error fetching messages: {str(e)}')
        return JsonResponse({"messages": [], "error": str(e)})


def get_users_with_unread(request):
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return JsonResponse({"users": []})
    if _is_revoked(request):
        return JsonResponse({"revoked": True}, status=401)

    try:
        me = User.objects.get(id=current_user_id)
        previews = _recent_map(me)
        users = User.objects.exclude(id=current_user_id)
        user_list = []

        for u in users:
            unread_count = Message.objects.filter(
                sender=u,
                receiver_id=current_user_id,
                is_read=False
            ).count()
            info = previews.get(u.id, {})
            user_list.append({
                "id": u.id,
                "unread": unread_count,
                "last_text": info.get('text', ''),
                "last_time": info.get('time', ''),
            })

        return JsonResponse({"users": user_list})
    except Exception as e:
        logger.error(f'Error fetching unread counts: {str(e)}')
        return JsonResponse({"users": [], "error": str(e)})


_typing_store = {}


IMAGE_UPLOAD_TYPES = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')


def _upload_avatar(photo):
    """Upload a profile photo to ImageKit. Returns (url, file_id). Raises ValueError."""
    if photo.content_type not in IMAGE_UPLOAD_TYPES:
        raise ValueError('Only JPG, PNG, WEBP or GIF photos')
    client = _imagekit_client()
    if client is None:
        raise ValueError('Image storage not configured')
    ext = os.path.splitext(photo.name or '')[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        ext = '.jpg'
    photo.seek(0)
    res = client.files.upload(
        file=photo.read(),
        file_name=f"avatar_{int(time.time())}{ext}",
        folder=getattr(settings, 'IMAGEKIT_FOLDER', '/dashsocial-chat') + '/profiles',
        use_unique_file_name=True,
    )
    if isinstance(res, dict):
        url = res.get('url') or ''
        fid = res.get('fileId') or res.get('file_id') or ''
    else:
        url = getattr(res, 'url', '') or ''
        fid = getattr(res, 'file_id', '') or getattr(res, 'fileId', '') or ''
    if not url:
        raise ValueError('Upload failed, try again')
    return url, fid


def _delete_avatar_file(fid):
    if not fid:
        return
    try:
        client = _imagekit_client()
        if client is not None:
            client.files.delete(fid)
    except Exception as e:
        logger.warning(f'Avatar delete failed for {fid}: {e}')


def _imagekit_client():
    try:
        from imagekitio import ImageKit
    except ImportError:
        return None
    pk = getattr(settings, 'IMAGEKIT_PRIVATE_KEY', '')
    if not pk:
        return None
    return ImageKit(private_key=pk)


@require_POST
def send_image(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({"status": "error", "message": "Login required"}, status=401)
    if _is_revoked(request):
        return JsonResponse({"status": "error", "revoked": True, "message": "Account deactivated"}, status=401)
    if _maintenance_on_for(request):
        return _maintenance_block()
    try:
        receiver_id = int(request.POST.get('receiver_id') or 0)
    except (ValueError, TypeError):
        receiver_id = 0
    caption = (request.POST.get('caption') or '').strip()[:500]
    photo = request.FILES.get('photo')
    if not receiver_id or photo is None:
        return JsonResponse({"status": "error", "message": "Photo and receiver required"}, status=400)
    if photo.content_type not in IMAGE_UPLOAD_TYPES:
        return JsonResponse({"status": "error", "message": "Only JPG, PNG, WEBP or GIF photos"}, status=400)
    try:
        sender = User.objects.get(id=user_id)
        receiver = User.objects.get(id=receiver_id)
    except User.DoesNotExist:
        return JsonResponse({"status": "error", "message": "User not found"}, status=404)
    client = _imagekit_client()
    if client is None:
        return JsonResponse({"status": "error", "message": "Image storage not configured"}, status=500)
    ext = os.path.splitext(photo.name or '')[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        ext = '.jpg'
    try:
        photo.seek(0)
        res = client.files.upload(
            file=photo.read(),
            file_name=f"chat_{user_id}_{receiver_id}_{int(time.time())}{ext}",
            folder=getattr(settings, 'IMAGEKIT_FOLDER', '/dashsocial-chat'),
            use_unique_file_name=True,
        )
        if isinstance(res, dict):
            url = res.get('url') or ''
            fid = res.get('fileId') or res.get('file_id') or ''
        else:
            url = getattr(res, 'url', '') or ''
            fid = getattr(res, 'file_id', '') or getattr(res, 'fileId', '') or ''
        if not url:
            raise ValueError('upload returned no URL')
    except Exception as e:
        logger.error(f'ImageKit upload failed: {e}')
        return JsonResponse({"status": "error", "message": "Upload failed, try again"}, status=500)
    try:
        msg = Message.objects.create(
            sender=sender, receiver=receiver, text=caption,
            kind='image', image_url=url, image_file_id=fid or '',
            reply_to=_resolve_reply(user_id, receiver_id, request.POST.get('reply_to')),
        )
        msg_id = msg.id
    except Exception as e:
        if _is_missing_schema_error(e):
            try:
                msg_id = _legacy_insert_message(
                    sender.id, receiver.id, text=caption,
                    kind='image', image_url=url, image_file_id=fid or '',
                )
            except Exception as e2:
                logger.error(f'Image send failed: {e2}')
                return JsonResponse({"status": "error", "message": "Could not send. DB migration pending."}, status=500)
            logger.warning('send_image: 0014 columns missing, sent via legacy insert. Run migrate.')
        else:
            raise
    logger.info(f'Image sent: {sender.name} -> {receiver.name}')
    return JsonResponse({"status": "success", "id": msg_id, "url": url})


# ── Message powers (reply / edit / forward / react / delete-for-all) ────

def _resolve_reply(user_id, other_id, reply_id):
    """Validated reply target or None (same thread, not deleted)."""
    try:
        rid = int(reply_id or 0)
    except (ValueError, TypeError):
        return None
    if not rid:
        return None
    try:
        try:
            target = Message.objects.select_related('sender').get(id=rid, is_deleted=False)
        except Exception as e:
            if _is_missing_schema_error(e):
                # Legacy DB: no is_deleted column — accept any same-thread target.
                target = Message.objects.select_related('sender').get(id=rid)
            else:
                raise
    except Message.DoesNotExist:
        return None
    except Exception:
        return None
    pair = {target.sender_id, target.receiver_id}
    if pair != {int(user_id), int(other_id)}:
        return None
    return target


def _reactions_map(messages, current_user_id):
    """{message_id: [{'emoji', 'count', 'mine'}]} — two queries, no N+1."""
    grouped = {}
    if not messages:
        return grouped
    try:
        rows = MessageReaction.objects.filter(
            message_id__in=[m.id for m in messages]
        ).values('message_id', 'emoji', 'user_id')
        per_msg = {}
        for r in rows:
            per_msg.setdefault(r['message_id'], []).append(r)
        for m in messages:
            chips = {}
            for r in per_msg.get(m.id, []):
                c = chips.setdefault(r['emoji'], {'emoji': r['emoji'], 'count': 0, 'mine': False})
                c['count'] += 1
                if r['user_id'] == current_user_id:
                    c['mine'] = True
            grouped[m.id] = sorted(chips.values(), key=lambda c: -c['count'])
    except Exception as e:
        if _is_missing_schema_error(e):
            logger.warning('Reactions table missing, serving without reactions. Run migrate.')
            return {}
        raise
    return grouped


def _reply_payload(msg, current_user_id):
    """Quoted-message snippet for a bubble, or None."""
    try:
        ref = getattr(msg, 'reply_to', None)
        if ref is None:
            try:
                rid = getattr(msg, 'reply_to_id', None)
            except Exception:
                return None
            if not rid:
                return None
            try:
                ref = Message.objects.select_related('sender').get(id=rid)
            except (Message.DoesNotExist, ValueError, TypeError):
                return None
            except Exception as e:
                if _is_missing_schema_error(e):
                    return None
                raise
        if getattr(ref, 'is_deleted', False):
            text = 'This message was deleted'
        elif getattr(ref, 'kind', 'text') == 'image':
            text = 'Photo' + (f" — {ref.text[:60]}" if ref.text else '')
        else:
            text = (ref.text or '')[:80]
        return {
            'id': ref.id,
            'name': 'You' if ref.sender_id == current_user_id else ref.sender.name,
            'text': text,
            'deleted': bool(getattr(ref, 'is_deleted', False)),
        }
    except Exception:
        return None


def _delete_image_file_if_orphan(fid):
    """Remove the ImageKit file only when no message still references it."""
    if not fid:
        return
    if Message.objects.filter(image_file_id=fid).exists():
        return
    try:
        client = _imagekit_client()
        if client is not None:
            client.files.delete(fid)
    except Exception as e:
        logger.warning(f'ImageKit delete failed for {fid}: {e}')


@require_POST
def delete_message(request):
    """Delete for everyone: both sides see 'This message was deleted'."""
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({"status": "error", "message": "Login required"}, status=401)
    if _is_revoked(request):
        return JsonResponse({"status": "error", "revoked": True, "message": "Account deactivated"}, status=401)
    if _maintenance_on_for(request):
        return _maintenance_block()
    try:
        msg = Message.objects.get(id=data.get('message_id'))
    except (Message.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Message not found"}, status=404)
    except Exception as e:
        if _is_missing_schema_error(e):
            return JsonResponse({"status": "error", "message": "Delete unavailable: DB migration pending"}, status=503)
        raise
    if msg.sender_id != user_id:
        return JsonResponse({"status": "error", "message": "You can only delete your own messages"}, status=403)
    try:
        if getattr(msg, 'is_deleted', False):
            return JsonResponse({"status": "ok"})
        fid = msg.image_file_id if getattr(msg, 'kind', 'text') == 'image' else ''
        msg.is_deleted = True
        msg.text = ''
        msg.image_url = ''
        msg.image_file_id = ''
        msg.save(update_fields=['is_deleted', 'text', 'image_url', 'image_file_id'])
    except Exception as e:
        if _is_missing_schema_error(e):
            return JsonResponse({"status": "error", "message": "Delete unavailable: DB migration pending"}, status=503)
        raise
    try:
        MessageReaction.objects.filter(message=msg).delete()
    except Exception:
        pass
    _delete_image_file_if_orphan(fid)
    logger.info(f'Message {msg.id} deleted for everyone by user {user_id}')
    return JsonResponse({"status": "ok"})


@require_POST
def edit_message(request):
    """Edit own text message. Receivers see the new text + an 'edited' tag."""
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({"status": "error", "message": "Login required"}, status=401)
    if _is_revoked(request):
        return JsonResponse({"status": "error", "revoked": True, "message": "Account deactivated"}, status=401)
    if _maintenance_on_for(request):
        return _maintenance_block()
    try:
        msg = Message.objects.get(id=data.get('message_id'))
    except (Message.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Message not found"}, status=404)
    except Exception as e:
        if _is_missing_schema_error(e):
            return JsonResponse({"status": "error", "message": "Edit unavailable: DB migration pending"}, status=503)
        raise
    if msg.sender_id != user_id:
        return JsonResponse({"status": "error", "message": "You can only edit your own messages"}, status=403)
    try:
        if getattr(msg, 'is_deleted', False) or getattr(msg, 'kind', 'text') != 'text':
            return JsonResponse({"status": "error", "message": "This message can't be edited"}, status=400)
        text = (data.get('content') or '').strip()[:2000]
        if not text:
            return JsonResponse({"status": "error", "message": "Message is empty"}, status=400)
        from django.utils import timezone
        msg.text = text
        msg.edited_at = timezone.now()
        msg.save(update_fields=['text', 'edited_at'])
    except Exception as e:
        if _is_missing_schema_error(e):
            return JsonResponse({"status": "error", "message": "Edit unavailable: DB migration pending"}, status=503)
        raise
    return JsonResponse({"status": "ok"})


REACT_EMOJIS = ('❤️', '😂', '😮', '😢', '🙏', '👍', '🔥')


@require_POST
def react_message(request):
    """Toggle one emoji reaction per user (tap same emoji = remove)."""
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({"status": "error", "message": "Login required"}, status=401)
    if _is_revoked(request):
        return JsonResponse({"status": "error", "revoked": True, "message": "Account deactivated"}, status=401)
    if _maintenance_on_for(request):
        return _maintenance_block()
    emoji = (data.get('emoji') or '').strip()[:12]
    if emoji not in REACT_EMOJIS:
        return JsonResponse({"status": "error", "message": "Invalid emoji"}, status=400)
    try:
        try:
            msg = Message.objects.get(id=data.get('message_id'), is_deleted=False)
        except Exception as e:
            if _is_missing_schema_error(e):
                # Legacy DB: fetch without the is_deleted filter.
                msg = Message.objects.get(id=data.get('message_id'))
            else:
                raise
    except (Message.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Message not found"}, status=404)
    except Exception as e:
        if _is_missing_schema_error(e):
            return JsonResponse({"status": "error", "message": "Reactions unavailable: DB migration pending"}, status=503)
        raise
    if msg.sender_id != user_id and msg.receiver_id != user_id:
        return JsonResponse({"status": "error", "message": "Not your conversation"}, status=403)
    try:
        existing = MessageReaction.objects.filter(message=msg, user_id=user_id).first()
        if existing is not None and existing.emoji == emoji:
            existing.delete()
            action = 'removed'
        else:
            MessageReaction.objects.update_or_create(
                message=msg, user_id=user_id, defaults={'emoji': emoji})
            action = 'added'
        summary = _reactions_map([msg], user_id).get(msg.id, [])
    except Exception as e:
        if _is_missing_schema_error(e):
            return JsonResponse({"status": "error", "message": "Reactions unavailable: DB migration pending"}, status=503)
        raise
    return JsonResponse({"status": "ok", "action": action, "reactions": summary})


@require_POST
def forward_message(request):
    """Copy a message into another chat (text or photo reference, marked Forwarded)."""
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({"status": "error", "message": "Login required"}, status=401)
    if _is_revoked(request):
        return JsonResponse({"status": "error", "revoked": True, "message": "Account deactivated"}, status=401)
    if _maintenance_on_for(request):
        return _maintenance_block()
    try:
        try:
            src = Message.objects.get(id=data.get('message_id'), is_deleted=False)
        except Exception as e:
            if _is_missing_schema_error(e):
                src = Message.objects.get(id=data.get('message_id'))
            else:
                raise
    except (Message.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Message not found"}, status=404)
    except Exception as e:
        if _is_missing_schema_error(e):
            return JsonResponse({"status": "error", "message": "Forward unavailable: DB migration pending"}, status=503)
        raise
    if src.sender_id != user_id and src.receiver_id != user_id:
        return JsonResponse({"status": "error", "message": "Not your conversation"}, status=403)
    try:
        receiver = User.objects.get(id=data.get('receiver_id'))
    except (User.DoesNotExist, ValueError, TypeError):
        return JsonResponse({"status": "error", "message": "Receiver not found"}, status=404)
    if receiver.id == user_id:
        return JsonResponse({"status": "error", "message": "Can't forward to yourself"}, status=400)
    try:
        Message.objects.create(
            sender_id=user_id, receiver=receiver, text=src.text,
            kind=getattr(src, 'kind', 'text'),
            image_url=getattr(src, 'image_url', ''),
            image_file_id=getattr(src, 'image_file_id', ''),
            forwarded=True,
        )
    except Exception as e:
        if _is_missing_schema_error(e):
            # Legacy DB without forwarded column: copy as plain message.
            try:
                _legacy_insert_message(
                    user_id, receiver.id, text=src.text,
                    kind=getattr(src, 'kind', 'text'),
                    image_url=getattr(src, 'image_url', ''),
                    image_file_id=getattr(src, 'image_file_id', ''),
                )
            except Exception as e2:
                logger.error(f'Forward failed: {e2}')
                return JsonResponse({"status": "error", "message": "Forward unavailable: DB migration pending"}, status=503)
            logger.warning('forward_message: forwarded column missing, sent as plain copy. Run migrate.')
        else:
            raise
    return JsonResponse({"status": "ok"})


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
    if _is_revoked(request):
        return JsonResponse({"revoked": True}, status=401)
    key = f"{user_id}_{current_user_id}"
    last = _typing_store.get(key, 0)
    typing = (time.time() - last) < 3
    return JsonResponse({"typing": typing})


# ── Presence (online / last seen) ─────────────────────────────────────
# The chat page pings heartbeat every ~10s; anyone seen within
# PRESENCE_ONLINE_SECONDS counts as "Active now", so the green dot flips
# within ~2s of a friend opening the app — no refresh needed anywhere.
PRESENCE_ONLINE_SECONDS = 30


def _presence_label(last_seen):
    """Exact last-seen stamp in IST ('Active now' handled by caller).

    Shows the real time — 'Last seen today at 11:34 PM' — instead of a
    vague '5m ago', so it always matches the actual session record.
    """
    try:
        from django.utils import timezone
        if last_seen is None:
            return 'Last seen long ago'
        now = timezone.now()
        t = _t12(last_seen)
        if _ist_date(last_seen) == _ist_date(now):
            return f'Last seen today at {t}'
        if _ist_date(last_seen) == _ist_date(now - timedelta(days=1)):
            return f'Last seen yesterday at {t}'
        return f"Last seen {_ist(last_seen).strftime('%d %b %Y')} at {t}"
    except Exception:
        return 'Last seen recently'


def heartbeat(request):
    """Keep the current user's session fresh so others see 'Active now'."""
    if request.method != 'POST':
        return JsonResponse({"status": "error", "message": "POST only"}, status=405)
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return JsonResponse({"status": "error", "message": "Login required"}, status=401)
    if _is_revoked(request):
        return JsonResponse({"revoked": True}, status=401)
    try:
        from django.utils import timezone
        # auto_now only fires on save(), so stamp explicitly via update().
        UserSession.objects.filter(
            user_id=current_user_id, is_online=True
        ).update(is_online=True, last_seen=timezone.now())
        return JsonResponse({"status": "ok"})
    except Exception as e:
        logger.error(f'Heartbeat error: {e}')
        return JsonResponse({"status": "error"}, status=500)


def presence(request):
    """Bulk online state for every conversation partner (one poll, no N+1)."""
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return JsonResponse({"users": {}})
    if _is_revoked(request):
        return JsonResponse({"revoked": True}, status=401)
    try:
        from django.utils import timezone
        now = timezone.now()
        # Same 10-min prune the admin pages use, so counts stay consistent.
        UserSession.objects.filter(
            is_online=True, last_seen__lt=now - timedelta(minutes=10)
        ).update(is_online=False)
        cutoff = now - timedelta(seconds=PRESENCE_ONLINE_SECONDS)
        others = list(User.objects.exclude(id=current_user_id).values_list('id', flat=True))
        latest = {}
        if others:
            for s in UserSession.objects.filter(user_id__in=others).order_by('-last_seen'):
                if s.user_id not in latest:
                    latest[s.user_id] = s
        out = {}
        for uid in others:
            s = latest.get(uid)
            online = bool(s and s.is_online and s.last_seen and s.last_seen >= cutoff)
            out[str(uid)] = {
                'online': online,
                'label': 'Active now' if online else _presence_label(s.last_seen if s else None),
            }
        return JsonResponse({"users": out})
    except Exception as e:
        logger.error(f'Presence error: {e}')
        return JsonResponse({"users": {}})


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


def send_call_signal(request):
    if request.method != 'POST':
        return JsonResponse({"status": "error", "message": "POST only"}, status=405)
    current_user_id = request.session.get('user_id')
    if not current_user_id:
        return JsonResponse({"status": "error", "message": "Login required"}, status=401)
    if _is_revoked(request):
        return JsonResponse({"status": "error", "revoked": True, "message": "Account deactivated"}, status=401)
    if _maintenance_on_for(request):
        return _maintenance_block()
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
    if _is_revoked(request):
        return JsonResponse({"revoked": True}, status=401)
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
                "time": _ist_hms(s.timestamp),
            }
            for s in qs
        ]
        CallSignal.objects.filter(id__in=[s["id"] for s in data]).update(consumed=True)
        ann = Announcement.objects.filter(is_active=True).order_by('-created_at').first()
        out = {"signals": data}
        if ann:
            out["announcement"] = {"id": ann.id, "text": ann.text}
        return JsonResponse(out)
    except Exception as e:
        logger.error(f'Error fetching call signals: {e}')
        return JsonResponse({"signals": [], "error": str(e)})


# ── Admin Dashboard ──────────────────────────────────────────────────────────

@admin_required
def admin_users_toggle(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST only'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    try:
        target = User.objects.get(id=data.get('user_id'))
    except (User.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'status': 'error', 'message': 'User not found'}, status=404)
    if target.id == request.session.get('user_id'):
        return JsonResponse({'status': 'error', 'message': 'You cannot change your own account'}, status=400)
    target.is_active = bool(data.get('is_active', True))
    target.save(update_fields=['is_active'])
    if not target.is_active:
        try:
            UserSession.objects.filter(user=target).update(is_online=False)
        except Exception:
            pass
    logger.info(f'Admin set {target.email} active={target.is_active}')
    return JsonResponse({'status': 'ok', 'is_active': target.is_active})


@admin_required
def admin_chart_data(request):
    """14-day activity series + user split for the console charts."""
    from django.utils import timezone
    from django.db.models import Count
    from django.db.models.functions import TruncDate
    try:
        base = _ist_date(timezone.now())
        days = [base - timedelta(days=i) for i in range(13, -1, -1)]
        labels = [d.strftime('%d %b') for d in days]

        def series(model, field):
            rows = (model.objects.filter(**{f'{field}__date__gte': days[0]})
                    .annotate(d=TruncDate(field)).values('d').annotate(c=Count('id')))
            by_day = {r['d']: r['c'] for r in rows}
            return [by_day.get(d, 0) for d in days]

        return JsonResponse({
            'labels': labels,
            'messages': series(Message, 'timestamp'),
            'logins': series(UserSession, 'login_time'),
            'active': User.objects.filter(is_active=True).count(),
            'off': User.objects.filter(is_active=False).count(),
        })
    except Exception as e:
        logger.error(f'Chart data error: {e}')
        return JsonResponse({'error': str(e)}, status=500)


@admin_required
def admin_announce_create(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST only'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    text = (data.get('text') or '').strip()[:500]
    if not text:
        return JsonResponse({'status': 'error', 'message': 'Message required'}, status=400)
    Announcement.objects.filter(is_active=True).update(is_active=False)
    a = Announcement.objects.create(
        text=text, is_active=True,
        created_by=request.session.get('admin_user', 'admin'),
    )
    logger.info(f'Announcement broadcast: {text[:60]}')
    return JsonResponse({'status': 'ok', 'item': {
        'id': a.id, 'text': a.text,
        'time': _ist_min(a.created_at),
    }})


@admin_required
def admin_announce_toggle(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST only'}, status=405)
    try:
        data = json.loads(request.body)
        a = Announcement.objects.get(id=data.get('id'))
    except (Announcement.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'status': 'error', 'message': 'Not found'}, status=404)
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    on = bool(data.get('is_active', True))
    if on:
        Announcement.objects.filter(is_active=True).update(is_active=False)
    a.is_active = on
    a.save(update_fields=['is_active'])
    return JsonResponse({'status': 'ok', 'is_active': a.is_active})


@admin_required
def admin_announce_delete(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST only'}, status=405)
    try:
        data = json.loads(request.body)
        Announcement.objects.get(id=data.get('id')).delete()
    except (Announcement.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'status': 'error', 'message': 'Not found'}, status=404)
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    return JsonResponse({'status': 'ok'})


@admin_required
def admin_users_create(request):
    """Create login credentials with just username + password.

    Email is auto-derived (login works with the username anyway).
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST only'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return JsonResponse({'status': 'error', 'message': 'Username and password required'}, status=400)
    if username.lower() in RESERVED_USERNAMES:
        return JsonResponse({'status': 'error', 'message': 'Username is reserved'}, status=400)
    if User.objects.filter(name__iexact=username).exists():
        return JsonResponse({'status': 'error', 'message': 'Username already exists'}, status=400)
    email = f"{username}@dashsocial.local"
    n = 2
    while User.objects.filter(email__iexact=email).exists():
        email = f"{username}+{n}@dashsocial.local"
        n += 1
    try:
        u = User(name=username, email=email, mobile=0, password=password)
        u.save()
    except Exception as e:
        logger.error(f'Admin create user failed: {e}')
        return JsonResponse({'status': 'error', 'message': 'Could not create user'}, status=500)
    logger.info(f'Admin created user {username} ({email})')
    return JsonResponse({'status': 'ok', 'user': {
        'id': u.id, 'name': u.name, 'email': u.email, 'mobile': '',
        'password': u.password, 'is_active': True,
    }})


@admin_required
def admin_users_delete(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST only'}, status=405)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    try:
        target = User.objects.get(id=data.get('user_id'))
    except (User.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'status': 'error', 'message': 'User not found'}, status=404)
    if target.id == request.session.get('user_id'):
        return JsonResponse({'status': 'error', 'message': 'You cannot delete your own account'}, status=400)
    email = target.email
    target.delete()  # cascades: messages, chats, sessions, signals
    logger.info(f'Admin deleted user {email}')
    return JsonResponse({'status': 'ok'})


@admin_required
def admin_user_detail(request, user_id):
    try:
        u = User.objects.get(id=user_id)
    except (User.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'error': 'User not found'}, status=404)
    try:
        sessions = UserSession.objects.filter(user=u).order_by('-last_seen')[:20]
        sess_data = [
            {
                'device': s.device_type or '—',
                'browser': s.browser or '—',
                'os': s.os or '—',
                'ip': s.ip_address or 'N/A',
                'online': s.is_online,
                'login': _ist_min(s.login_time),
                'seen': _ist_min(s.last_seen),
            }
            for s in sessions
        ]
        recent = Message.objects.filter(
            Q(sender=u) | Q(receiver=u)
        ).select_related('sender', 'receiver').order_by('-timestamp')[:10]
        try:
            recent_data = [
                {
                    'direction': 'sent' if m.sender_id == u.id else 'received',
                    'other': m.receiver.name if m.sender_id == u.id else m.sender.name,
                    'text': m.text[:80],
                    'time': _ist_min(m.timestamp),
                }
                for m in recent
            ]
        except Exception as e:
            if not _is_missing_schema_error(e):
                raise
            recent = list(Message.objects.filter(
                Q(sender=u) | Q(receiver=u)
            ).select_related('sender', 'receiver').defer(*_MSG_POWER_FIELDS).order_by('-timestamp')[:10])
            recent_data = [
                {
                    'direction': 'sent' if m.sender_id == u.id else 'received',
                    'other': m.receiver.name if m.sender_id == u.id else m.sender.name,
                    'text': m.text[:80],
                    'time': _ist_min(m.timestamp),
                }
                for m in recent
            ]
        return JsonResponse({
            'id': u.id,
            'name': u.name,
            'email': u.email,
            'mobile': u.mobile or '',
            'is_active': u.is_active,
            'sent': Message.objects.filter(sender=u).count(),
            'received': Message.objects.filter(receiver=u).count(),
            'logins': UserSession.objects.filter(user=u).count(),
            'sessions': sess_data,
            'recent': recent_data,
        })
    except Exception as e:
        logger.error(f'User detail error: {e}')
        return JsonResponse({'error': str(e)}, status=500)


# ── Standalone Admin Console (/admin/) ─────────────────────────────────────
# Single admin page with its own username/password login, separate from
# the chat user sessions.

def _console_authed(request):
    return bool(request.session.get('admin_auth'))


def console_admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('admin_auth'):
            return redirect('admin_console')
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_console(request):
    if request.method == 'POST':
        action = request.POST.get('action', 'login')
        if action == 'logout':
            request.session.pop('admin_auth', None)
            request.session.pop('admin_user', None)
            return redirect('admin_console')
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        if username == getattr(settings, 'CONSOLE_ADMIN_USERNAME', 'admin') and \
                password == getattr(settings, 'CONSOLE_ADMIN_PASSWORD', ''):
            request.session['admin_auth'] = True
            request.session['admin_user'] = username
            logger.info(f'Console admin login: {username}')
            return redirect('admin_console')
        logger.warning(f'Failed console admin login attempt: {username}')
        return render(request, 'admin.html', {'show_login': True, 'login_error': 'Invalid username or password'})

    if not _console_authed(request):
        return render(request, 'admin.html', {'show_login': True})

    # Console data (server-rendered once; logs/health refresh via JS)
    stats = {'total_users': 0, 'active': 0, 'off': 0, 'messages': 0, 'recent': 0, 'online': 0}
    users_data = []
    try:
        from django.utils import timezone
        users = list(User.objects.all().order_by('name'))
        stats['total_users'] = len(users)
        stats['active'] = sum(1 for u in users if u.is_active)
        stats['off'] = stats['total_users'] - stats['active']
        stats['messages'] = Message.objects.count()
        stats['recent'] = Message.objects.filter(
            timestamp__gte=timezone.now() - timedelta(hours=24)).count()
        cutoff = timezone.now() - timedelta(minutes=10)
        UserSession.objects.filter(is_online=True, last_seen__lt=cutoff).update(is_online=False)
        stats['online'] = UserSession.objects.filter(is_online=True).count()
        for u in users:
            sess = UserSession.objects.filter(user=u).order_by('-last_seen').first()
            users_data.append({
                'id': u.id,
                'name': u.name,
                'email': u.email,
                'mobile': u.mobile or '',
                'password': u.password,
                'is_active': u.is_active,
                'messages': Message.objects.filter(Q(sender=u) | Q(receiver=u)).count(),
                'is_online': sess.is_online if sess else False,
                'last_seen': _ist_min(sess.last_seen) if sess else 'Never',
            })
    except Exception as e:
        logger.error(f'Admin console data error: {e}')
    return render(request, 'admin.html', {
        'stats': stats,
        'users_data': users_data,
        'admin_user': request.session.get('admin_user', 'admin'),
        'announcements': list(Announcement.objects.all()[:20]),
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
    # NOTE: logs are append-only — nothing here (or anywhere else in the
    # codebase) ever deletes AppLog rows. `limit`/`page` only control which
    # slice is *displayed*, never what is stored.
    try:
        import math
        level_filter = request.GET.get('level', '')
        limit_raw = (request.GET.get('limit') or '50').strip().lower()
        try:
            page = max(1, int(request.GET.get('page') or 1))
        except (ValueError, TypeError):
            page = 1
        qs = AppLog.objects.all().order_by('-timestamp')
        if level_filter:
            qs = qs.filter(level=level_filter.upper())
        total = qs.count()
        if limit_raw == 'all':
            cap = 5000
            limit_out = 'all'
        else:
            try:
                cap = int(limit_raw)
            except (ValueError, TypeError):
                cap = 50
            cap = max(1, min(cap, 5000))
            limit_out = cap
        pages = max(1, math.ceil(total / cap))
        page = min(page, pages)
        offset = (page - 1) * cap
        logs = qs[offset:offset + cap]
        logs_data = [
            {
                'time': _ist_log(log.timestamp),
                'level': log.level,
                'message': log.message,
                'source': log.logger_name,
                'module': log.module,
                'function': log.function,
                'line': log.line_number
            }
            for log in logs
        ]
        return JsonResponse({
            'logs': logs_data, 'total': total, 'limit': limit_out,
            'page': page, 'pages': pages, 'per_page': cap,
        })
    except Exception as e:
        logger.error(f'Error fetching admin logs: {str(e)}')
        return JsonResponse({"logs": [], "total": 0, "page": 1, "pages": 1, "error": str(e)}, status=500)


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
                'last_seen': _ist_full(s.last_seen),
                'login_time': _ist_full(s.login_time),
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
def admin_storage(request):
    """Storage dashboard: DB counts + live ImageKit usage (files & bytes)."""
    from django.db.models import Count
    try:
        try:
            chat_images = Message.objects.filter(kind='image', is_deleted=False).count()
            avatars = User.objects.exclude(avatar_url='').count()
            top_rows = (Message.objects.filter(kind='image', is_deleted=False)
                        .values('sender__name').annotate(c=Count('id')).order_by('-c')[:5])
            top = [{'name': r['sender__name'] or '?', 'images': r['c']} for r in top_rows]
            recent_rows = (Message.objects.filter(kind='image', is_deleted=False)
                           .select_related('sender', 'receiver').order_by('-timestamp')[:10])
        except Exception as e:
            if not _is_missing_schema_error(e):
                raise
            # Legacy DB without is_deleted: count all images.
            chat_images = Message.objects.filter(kind='image').count()
            avatars = User.objects.exclude(avatar_url='').count()
            top_rows = (Message.objects.filter(kind='image')
                        .values('sender__name').annotate(c=Count('id')).order_by('-c')[:5])
            top = [{'name': r['sender__name'] or '?', 'images': r['c']} for r in top_rows]
            recent_rows = (Message.objects.filter(kind='image')
                           .select_related('sender', 'receiver').order_by('-timestamp')[:10])
            logger.warning('admin_storage: is_deleted column missing, served legacy counts. Run migrate.')
        recent = [{
            'by': m.sender.name,
            'to': m.receiver.name,
            'text': (m.text[:40] + '…') if len(m.text or '') > 40 else (m.text or 'Photo'),
            'time': _ist_min(m.timestamp),
        } for m in recent_rows]
    except Exception as e:
        logger.error(f'Storage DB error: {e}')
        return JsonResponse({'error': str(e)}, status=500)

    live, live_error, truncated = None, None, False
    try:
        import base64
        import urllib.request
        pk = getattr(settings, 'IMAGEKIT_PRIVATE_KEY', '')
        if not pk:
            raise ValueError('ImageKit not configured')
        auth = base64.b64encode(f'{pk}:'.encode()).decode()
        total_files, total_bytes = 0, 0
        skip = 0
        for _ in range(5):  # max 5000 files — bounds latency on big accounts
            req = urllib.request.Request(
                f'https://api.imagekit.io/v1/files?limit=1000&skip={skip}',
                headers={'Authorization': f'Basic {auth}', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=8) as res:
                files = json.loads(res.read().decode() or '[]')
            if not files:
                break
            total_files += len(files)
            total_bytes += sum(int(f.get('size') or 0) for f in files)
            if len(files) < 1000:
                break
            skip += 1000
        else:
            truncated = True
        live = {'files': total_files, 'bytes': total_bytes}
    except Exception as e:
        live_error = str(e)[:120]
        logger.warning(f'ImageKit usage fetch failed: {e}')

    return JsonResponse({
        'db': {'chat_images': chat_images, 'avatars': avatars},
        'live': live, 'live_error': live_error, 'truncated': truncated,
        'top': top, 'recent': recent,
    })


@admin_required
def admin_server_health(request):
    try:
        import psutil
        import platform
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        up_secs = int(time.time() - psutil.boot_time())
        up = f"{up_secs // 86400}d {(up_secs % 86400) // 3600}h {(up_secs % 3600) // 60}m"
        return JsonResponse({
            'cpu_percent': cpu,
            'memory_percent': mem.percent,
            'memory_used_mb': round(mem.used / 1024 / 1024),
            'memory_total_mb': round(mem.total / 1024 / 1024),
            'disk_percent': disk.percent,
            'disk_used_gb': round(disk.used / 1024 / 1024 / 1024, 1),
            'disk_total_gb': round(disk.total / 1024 / 1024 / 1024, 1),
            'uptime': up,
            'python': platform.python_version(),
        })
    except ImportError:
        return JsonResponse({'error': 'psutil not installed. Run: pip install psutil'}, status=500)
    except Exception as e:
        logger.error(f'Server health error: {str(e)}')
        return JsonResponse({'error': str(e)}, status=500)


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


@admin_required
def admin_maintenance_toggle(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            on = bool(data.get('enabled', False))
            set_maintenance_on(on)
            logger.info(f'Maintenance mode {"ON" if on else "OFF"} by admin')
            return JsonResponse({'maintenance': on})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'maintenance': is_maintenance_on()})
