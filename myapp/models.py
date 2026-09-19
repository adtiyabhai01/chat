from django.db import models
from django.utils import timezone


class User(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    mobile = models.BigIntegerField(default=0, blank=True, null=True)
    password = models.CharField(max_length=20)
    profile_image = models.ImageField(default="", upload_to="profile_img/")
    avatar_url = models.URLField(max_length=500, blank=True, default='')
    avatar_file_id = models.CharField(max_length=128, blank=True, default='')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name}"

    @property
    def avatar(self):
        """Best profile picture: ImageKit URL first, legacy upload fallback."""
        if self.avatar_url:
            return self.avatar_url
        try:
            if self.profile_image:
                return self.profile_image.url
        except Exception:
            pass
        return ''
    

class Chat(models.Model):
    user1 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user1')
    user2 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user2')

    def __str__(self):
        return f"{self.user1} - {self.user2}"


class Message(models.Model):
    KIND_CHOICES = [('text', 'Text'), ('image', 'Image')]
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages')
    text = models.TextField(blank=True, default='')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='text')
    image_url = models.URLField(max_length=500, blank=True, default='')
    image_file_id = models.CharField(max_length=128, blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
    # Message powers: reply / edit / forward / delete-for-everyone
    reply_to = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies')
    edited_at = models.DateTimeField(null=True, blank=True)
    forwarded = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    @property
    def edited(self):
        return self.edited_at is not None


class MessageReaction(models.Model):
    """One emoji reaction per user per message (tap again to remove)."""
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reactions')
    emoji = models.CharField(max_length=12)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('message', 'user')]

    def __str__(self):
        return f"{self.user.name} {self.emoji} on #{self.message_id}"


class CallSignal(models.Model):
    """WebRTC signaling message (offer/answer/ice/hangup/reject/busy).

    Transport is plain HTTP polling (works on serverless hosts with no
    WebSocket support). Media itself flows peer-to-peer via WebRTC.
    """
    SIGNAL_KINDS = ('offer', 'answer', 'ice', 'hangup', 'reject', 'busy')

    call_id = models.CharField(max_length=64, db_index=True)
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_signals')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_signals')
    kind = models.CharField(max_length=16)
    payload = models.TextField(default='{}')
    timestamp = models.DateTimeField(auto_now_add=True)
    consumed = models.BooleanField(default=False)

    class Meta:
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['receiver', 'consumed', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.kind} {self.sender.name} -> {self.receiver.name} ({self.call_id[:8]})"



class UserSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_key = models.CharField(max_length=40, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    device_type = models.CharField(max_length=50, blank=True)
    browser = models.CharField(max_length=100, blank=True)
    os = models.CharField(max_length=100, blank=True)
    is_online = models.BooleanField(default=True)
    last_seen = models.DateTimeField(auto_now=True)
    login_time = models.DateTimeField(auto_now_add=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ['-last_seen']

    def __str__(self):
        return f"{self.user.name} - {'Online' if self.is_online else 'Offline'}"


class AppLog(models.Model):
    LOG_LEVELS = [
        ('DEBUG', 'Debug'),
        ('INFO', 'Info'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
        ('CRITICAL', 'Critical'),
    ]
    
    level = models.CharField(max_length=10, choices=LOG_LEVELS)
    logger_name = models.CharField(max_length=255)
    message = models.TextField()
    module = models.CharField(max_length=255, blank=True)
    function = models.CharField(max_length=255, blank=True)
    line_number = models.IntegerField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp']),
            models.Index(fields=['level', '-timestamp']),
        ]
    
    def __str__(self):
        return f"[{self.level}] {self.logger_name} - {self.message[:50]}"


class SiteSetting(models.Model):
    """Tiny key-value store for cross-instance flags (e.g. maintenance mode).

    An in-memory global does NOT work on serverless hosts where every
    request may hit a different process — so this lives in the DB.
    """
    key = models.CharField(max_length=64, unique=True)
    value = models.CharField(max_length=255, default='')
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.key}={self.value}"


class Announcement(models.Model):
    """Admin broadcast shown as a banner on the chat page. Only one active at a time."""
    text = models.CharField(max_length=500)
    is_active = models.BooleanField(default=True)
    created_by = models.CharField(max_length=100, default='admin')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{'[ON] ' if self.is_active else ''}{self.text[:60]}"
