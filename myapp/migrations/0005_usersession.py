from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('myapp', '0004_alter_chat_id_alter_message_id_alter_user_id_applog'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserSession',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(blank=True, max_length=40)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.TextField(blank=True)),
                ('device_type', models.CharField(blank=True, max_length=50)),
                ('browser', models.CharField(blank=True, max_length=100)),
                ('os', models.CharField(blank=True, max_length=100)),
                ('is_online', models.BooleanField(default=True)),
                ('last_seen', models.DateTimeField(auto_now=True)),
                ('login_time', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sessions', to='myapp.user')),
            ],
            options={
                'ordering': ['-last_seen'],
            },
        ),
    ]
