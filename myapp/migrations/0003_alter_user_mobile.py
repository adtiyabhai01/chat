from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('myapp', '0002_message_is_read'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='mobile',
            field=models.BigIntegerField(blank=True, default=0, null=True),
        ),
    ]
