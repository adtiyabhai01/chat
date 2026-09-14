from django.db import models
from django.utils import timezone


class User(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    mobile = models.BigIntegerField(default=0, blank=True, null=True)
    password = models.CharField(max_length=20)
    profile_image = models.ImageField(default="", upload_to="profile_img/")

    def __str__(self):
        return f"{self.name}"
    

class Chat(models.Model):
    user1 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user1')
    user2 = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user2')

    def __str__(self):
        return f"{self.user1} - {self.user2}"


class Message(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages')
    text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)



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
