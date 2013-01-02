# -*- coding: utf-8 -*-
# This code is distributed under the two-clause BSD license.
# Copyright (c) 2012-2013 Raphaël Barrois


from __future__ import absolute_import, unicode_literals

import logging

from django.db import models


from . import conf


PERM_USER = 'user'
PERM_GROUP_MEMBER = 'grpmember'
PERM_GROUP_ADMIN = 'grpadmin'
PERM_ADMIN = 'admin'

PERM_LEVELS = (
    PERM_USER,
    PERM_GROUP_MEMBER,
    PERM_GROUP_ADMIN,
    PERM_ADMIN,
)


logger = logging.getLogger(__name__)


def get_model(model_name):
    """Retrieve a django model.

    This handles:
    - Explicit models.Model subclass
    - Absolute dotted path to import the model
    """
    if issubclass(model_name, models.Model):
        return model_name

    # Not a Model or a Model instance, must be a class path
    if '.' not in model_name:
        raise ValueError("Invalid model name %s: should include module name."
                % model_name)

    app, cls = model_name.rsplit('.', 1)
    return models.get_model(app, cls)


class AuthResult(object):
    """Result of an authentication query."""

    def __init__(self, success, data=None):
        if not data:
            data = {}
        self.success = success
        self.data = data
        self.perms = self._setup_perms(self.data)

    def _setup_perms(self, data):
        perms = set()
        perms.add(PERM_USER)

        if 'perms' in data:
            perms.add(data['perms'])

        if 'grpauth' in data:
            if data['grpauth'] == 'admin':
                perms.add(PERM_GROUP_ADMIN)
                perms.add(PERM_GROUP_MEMBER)
            elif self._data['grpauth'] == 'membre':
                perms.add(PERM_GROUP_MEMBER)

    @property
    def username(self):
        return self.data.get('username', '')

    @property
    def firstname(self):
        return self.data.get('firstname', '')

    @property
    def lastname(self):
        return self.data.get('lastname', '')

    @property
    def promo(self):
        return self.data.get('promo', '')

    @property
    def email(self):
        return self.data.get('email', '')

    @property
    def is_dead(self):
        return bool(self.data.get('deathdate', ''))

    @property
    def is_admin(self):
        return PERM_ADMIN in self.perms

    def has_perm(self, perm):
        return perm in self.perms


class AuthGroupeXBackend(object):
    """Authentication backend for auth-groupe-x"""

    supports_anonymous_user = False
    supports_object_permissions = False

    def __init__(self, config=None, *args, **kwargs):
        super(AuthGroupeXBackend, self).__init__(*args, **kwargs)
        self.config = config or conf.AuthGroupeXConf()
        self.user_model = get_model(self.config.USER_MODEL)

    def get_user(self, user_id):
        """Retrieve a user by ID.

        Args:
            user_id: int, the ID of the user

        Returns:
            Either an instance of self.config.USER_MODEL or None
        """
        try:
            return self.user_model.objects.get(pk=user_id)
        except self.user_model.DoesNotExist:
            return None

    def authenticate(self, **kwargs):
        """Create a user if the authgroupex data has been passed.

        This data should be present in the 'authgroupex' keyword argument.
        """
        if 'authgroupex' in kwargs:
            auth_data = kwargs['authgroupex']
        else:
            logger.info('Trying to authenticate, no authgroupex in data.')
            return None

        if not auth_data.username:
            logger.error('Received a AuthResult object without a username.')
            return None

        try:
            user = self.user_model.objects.get(username=auth_data.username)
        except self.user_model.DoesNotExist:
            try:
                user = self.create_user_from_auth_data(auth_data)
            except ValueError:
                logger.warning('Received authgroupex with invalid name %s',
                        auth_data.username)
                return None

        self.update_user(user, auth_data)
        return user

    def create_user_from_auth_data(self, auth_data):
        """Create a new Django user from AuthGroupeX data.

        This only sets the basic username field;
        groups and other data are handled by the update_user method.
        """
        username = auth_data.username
        user = self.user_model.objects.create(username=username, is_active=True)
        user.set_unusable_password()
        logger.info('Created a new user with username {name}', name=username)
        return user

    def update_user(self, user, auth_data):
        """Update various fields of the user according to auth-groupe-x data."""
        self.update_profile(user, auth_data)
        self.update_perms(user, auth_data)
        self.update_groups(user, auth_data)

        # Refresh DB user
        user.save()
        logger.info('Updated user {name}', name=user.username)

    def update_profile(self, user, auth_data):
        """Update fields of the profile according to auth-groupe-x data."""
        # Update basic profile data
        if auth_data.firstname:
            user.first_name = auth_data.firstname
        if auth_data.lastname:
            user.last_name = auth_data.lastname
        if auth_data.email:
            user.email = auth_data.email

        if self.config.PROFILE_CLASS:
            profile_model = get_model(self.config.PROFILE_CLASS)
            try:
                profile = user.get_profile()
            except profile_model.DoesNotExist:
                profile = profile_model.objects.create(user=user)
            if auth_data.promo:
                profile.promo = auth_data.promo
            profile.save()

    def update_perms(self, user, auth_data):
        # Handle superadmins
        if self.config.SUPERADMIN_PERMS:
            user.is_superuser = any(auth_data.has_perm(perm)
                                    for perm in self.config.SUPERADMIN_PERMS)

        # Handle staff status
        if self.config.STAFF_PERMS:
            user.is_staff = any(auth_data.has_perm(perm)
                                    for perm in self.config.STAFF_PERMS)

        # Handle active status
        if auth_data.is_dead and self.config.DISABLE_DEADS:
            user.is_active = False


    def update_groups(self, user, auth_data):
        if not self.config.MAP_GROUPS:
            return

        group_model = get_model(self.config.GROUP_MODEL)
        new_group_names = set()
        old_group_names = set()
        for perm in PERM_LEVELS:
            if auth_data.has_perm(perm):
                new_group_names |= set(self.config.MAP_GROUPS.get(perm, []))
            else:
                old_group_names |= set(self.config.MAP_GROUPS.get(perm, []))

        new_groups = list(group_model.objects.filter(name__in=new_group_names))
        old_groups = list(group_model.objects.filter(name__in=old_group_names))

        if old_groups:
            logger.info(u"Removing user %s from groups %s", user, new_groups)
            user.groups.remove(*list(old_groups))

        if new_groups:
            logger.info(u"Adding user %s to groups %s", user, new_groups)
            user.groups.add(*list(new_groups))
