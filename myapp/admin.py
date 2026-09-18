from django.contrib import admin
from . models import *

# Register your models here.
#admin 0000

admin.site.register(User)
admin.site.register(Chat)
admin.site.register(Message)
admin.site.register(UserSession)
admin.site.register(AppLog)
admin.site.register(CallSignal)
admin.site.register(SiteSetting)