# -*- coding: utf-8 -*-
# Generated by Django 1.11.2 on 2017-07-11 01:18
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0004_auto_20170710_0857'),
    ]

    operations = [
        migrations.AddField(
            model_name='history',
            name='inventory',
            field=models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='history', related_query_name='history', to='main.Inventory'),
        ),
        migrations.AlterIndexTogether(
            name='history',
            index_together=set([('id', 'project', 'playbook', 'status', 'inventory', 'start_time', 'stop_time')]),
        ),
    ]