from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('myapp', '0005_usersession'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersession',
            name='latitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='usersession',
            name='longitude',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
