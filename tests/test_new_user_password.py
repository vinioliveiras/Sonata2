"""Vini: Settings let him make a user without a password -- the account was
locked (AccountsService), never on the login screen, unable to log in. A
password is now needed: the form's Create stays off without one."""
import inspect
import unittest
from unittest import mock

from sonata2.backend import users as U


class NewUserTest(unittest.TestCase):
    def test_backend_refuses_no_password(self):
        with mock.patch.object(U, "_bus") as bus:
            self.assertEqual(U.create_user("ana", "Ana", False, ""), "A password is needed")
        bus.assert_not_called()

    def test_password_always_set(self):
        bus = mock.Mock()
        bus.call_sync.return_value.unpack.return_value = ["/org/freedesktop/Accounts/User1001"]
        with mock.patch.object(U, "_bus", return_value=bus), \
                mock.patch.object(U, "set_password", return_value=None) as sp:
            self.assertIsNone(U.create_user("ana", "Ana", False, "s3cret"))
        self.assertEqual(sp.call_args.args[1], "s3cret")

    def test_form_needs_it(self):
        from sonata2.settings import app
        src = inspect.getsource(app.Settings._add_user_dialog)
        self.assertIn('set_response_enabled("create"', src)
        self.assertIn("bool(pw.get_text())", src)


if __name__ == "__main__":
    unittest.main()
