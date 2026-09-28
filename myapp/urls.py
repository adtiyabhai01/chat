#my app urls

"""
URL configuration for myproject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path
from django.views.generic import RedirectView
from . import views
from .views import get_users_with_unread

urlpatterns = [
    path('', views.signup_desh, name='signup_desh'),
    path('signup/', views.signup, name='signup'),
    path('login/', views.login, name='login'),
    path('signup_desh/', views.signup_desh, name='signup_desh'),
    path('home/', views.home, name='home'),
    path('profile/', views.profile, name='profile'),
    path('chat/', views.chat, name='chat'),
    path('logout_view/', views.logout_view, name='logout_view'),
    path('get-messages/<int:user_id>/', views.get_messages, name='get_messages'),
    path('send-message/', views.send_message, name='send_message'),
    path('send-image/', views.send_image, name='send_image'),
    path('delete-message/', views.delete_message, name='delete_message'),
    path('edit-message/', views.edit_message, name='edit_message'),
    path('react-message/', views.react_message, name='react_message'),
    path('forward-message/', views.forward_message, name='forward_message'),
    path('show_logs/', views.show_logs, name='show_logs'),
    path('get_users_with_unread/', views.get_users_with_unread, name='get_users_with_unread'),
    path('users-with-unread/', get_users_with_unread, name='users_with_unread'),
    path('set-typing/', views.set_typing, name='set_typing'),
    path('get-typing/<int:user_id>/', views.get_typing, name='get_typing'),
    path('heartbeat/', views.heartbeat, name='heartbeat'),
    path('presence/', views.presence, name='presence'),
    path('send-call-signal/', views.send_call_signal, name='send_call_signal'),
    path('get-call-signals/', views.get_call_signals, name='get_call_signals'),
    path('admin/', views.admin_console, name='admin_console'),
    path('admin-dashboard/', RedirectView.as_view(url='/admin/', permanent=False)),
    path('admin-users/', RedirectView.as_view(url='/admin/', permanent=False)),
    path('admin-users/list/', views.admin_users_list, name='admin_users_list'),
    path('admin-users/toggle/', views.admin_users_toggle, name='admin_users_toggle'),
    path('admin-users/delete/', views.admin_users_delete, name='admin_users_delete'),
    path('admin-users/create/', views.admin_users_create, name='admin_users_create'),
    path('admin-chart-data/', views.admin_chart_data, name='admin_chart_data'),
    path('admin-announce/', views.admin_announce_create, name='admin_announce_create'),
    path('admin-announce/toggle/', views.admin_announce_toggle, name='admin_announce_toggle'),
    path('admin-announce/delete/', views.admin_announce_delete, name='admin_announce_delete'),
    path('admin-user-detail/<int:user_id>/', views.admin_user_detail, name='admin_user_detail'),
    path('access-denied/', views.access_denied, name='access_denied'),
    path('admin-stats/', views.admin_stats, name='admin_stats'),
    path('admin-logs/', views.admin_logs, name='admin_logs'),
    path('admin-online-users/', views.admin_online_users, name='admin_online_users'),
    path('admin-storage/', views.admin_storage, name='admin_storage'),
    path('admin-server-health/', views.admin_server_health, name='admin_server_health'),
    path('admin-maintenance/', views.admin_maintenance_toggle, name='admin_maintenance_toggle'),
    path('admin-force-logout/', views.admin_force_logout, name='admin_force_logout'),
    path('admin-active-calls/', views.admin_active_calls, name='admin_active_calls'),
    path('admin-audit-log/', views.admin_audit_log, name='admin_audit_log'),
    path('admin-live-stats/', views.admin_live_stats, name='admin_live_stats'),
    path('save-location/', views.save_location, name='save_location'),
]