# lint-amnesty, pylint: disable=missing-module-docstring
import logging

from django.contrib.auth import get_user_model  # lint-amnesty, pylint: disable=unused-import
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from social_django.models import UserSocialAuth

from common.djangoapps.student.models import AccountRecovery, Registration, get_retired_email_by_email
from openedx.core.djangolib.oauth2_retirement_utils import retire_dot_oauth2_models

from ...models import UserRetirementStatus

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Manually move a user into the retirement queue, so that they can be
    picked up by the user retirement pipeline. This should only be done in
    the case that a user has tried and is unable to delete their account
    via the UI.

    Most of this code has been lifted from openedx/core/djangoapps/user_api/accounts/views

    As this is a fairly sensitive operation, we want to make sure that human
    error is accounted for. In order to make sure that something like a typo
    during command invocation does not result in the retirement of a
    different user, you must supply both the username and email address linked
    to the user account.
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Username to be retired'
        )
        parser.add_argument(
            '--user_email',
            type=str,
            help='User email address.'
        )
        parser.add_argument(
            '--user_file',
            type=str,
            help='Comma separated file that have username and user_email of the users that needs to be retired'
        )

    def check_user_exist(self, user_model, userfile, user_name, useremail):
        """
        Function to check if user exists. This function will execute for userfile
        or user_name and useremail.

        Args:
            userfile: file that have username and email of users to be retired
            user_name: username of user to be retired
            useremail: email of user to be retired
        """
        unknown_users = []
        users = []
        if userfile:
            try:
                userinfo = open(userfile, 'r')
            except Exception as exc:
                error_message = f'Error while reading file: {exc}'
                logger.error(error_message)
                raise CommandError(error_message)  # lint-amnesty, pylint: disable=raise-missing-from

            for record in userinfo:
                userdata = record.split(',')
                username = userdata[0].strip()
                user_email = userdata[1].strip()
                try:
                    users.append(User.objects.get(username=username, email=user_email))
                except user_model.DoesNotExist:
                    unknown_users.append({username: user_email})
        elif user_name and useremail:
            try:
                users.append(User.objects.get(username=username, email=user_email))
            except user_model.DoesNotExist:
                unknown_users.append({username: useremail})
        else:
            raise CommandError("Please provide user_file or username and user_email parameter when runing command")
        return users, unknown_users

    def handle(self, *args, **options):
        """
        Execute the command.
        """
        userfile = options['user_file']
        user_name = options['username']
        useremail = options['user_email']

        user_model = get_user_model()

        users, unknown_users = self.check_user_exist(user_model, userfile, user_name, useremail)
        # Raise if found any such user that does not exit
        if len(unknown_users) > 0:
            error_message = (
                'Could not find users with specified username and email '
                'address. Make sure you have everything correct before '
                'trying again'
            )
            logger.error(error_message)
            raise CommandError(error_message + f': {unknown_users}')  # lint-amnesty, pylint: disable=raise-missing-from

        try:
            with transaction.atomic():
                for user in users:
                    # Add user to retirement queue.
                    UserRetirementStatus.create_retirement(user)
                    # Unlink LMS social auth accounts
                    UserSocialAuth.objects.filter(user_id=user.id).delete()
                    # Change LMS password & email
                    user.email = get_retired_email_by_email(user.email)
                    user.set_unusable_password()
                    user.save()

                    # TODO: Unlink social accounts & change password on each IDA.
                    # Remove the activation keys sent by email to the user for account activation.
                    Registration.objects.filter(user=user).delete()

                    # Delete OAuth tokens associated with the user.
                    retire_dot_oauth2_models(user)
                    AccountRecovery.retire_recovery_email(user.id)
        except KeyError:
            error_message = f'Username not specified {user}'
            logger.error(error_message)
            raise CommandError(error_message)  # lint-amnesty, pylint: disable=raise-missing-from
        except user_model.DoesNotExist:
            error_message = f'The user "{user.username}" does not exist.'
            logger.error(error_message)
            raise CommandError(error_message)  # lint-amnesty, pylint: disable=raise-missing-from
        except Exception as exc:  # pylint: disable=broad-except
            error_message = f'500 error deactivating account: {exc}'
            logger.error(error_message)
            raise CommandError(error_message)  # lint-amnesty, pylint: disable=raise-missing-from
        logger.info("User succesfully moved to the retirment pipeline")
