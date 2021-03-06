# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2017-11-10 07:09
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0022_auto_20171110_0857'),
    ]

    operations = [
        migrations.AddField(
            model_name='template',
            name='inventory',
            field=models.CharField(blank=True, default=None, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='template',
            name='project',
            field=models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.SET_NULL, to='main.Project'),
        ),
        migrations.AlterIndexTogether(
            name='template',
            index_together=set([('id', 'name', 'kind', 'inventory', 'project')]),
        ),
    ]
