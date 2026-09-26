import re
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ManagementAuthenticationTests(TestCase):
    def setUp(self):
        self.password = "Original-secure-pass-928!"
        self.user = get_user_model().objects.create_user(
            username="committee", email="committee@example.com",
            password=self.password, is_staff=True,
        )
        self.new_password = "New-secure-pass-738!"

    def test_login_and_header_and_post_logout(self):
        response = self.client.get(reverse("election:login"))
        self.assertContains(response, "パスワードをお忘れですか")
        response = self.client.post(reverse("election:login"), {
            "username": self.user.username, "password": self.password,
            "next": "https://untrusted.example/",
        })
        self.assertRedirects(response, reverse("election:management_cycle_list"))
        response = self.client.get(reverse("election:management_cycle_list"))
        self.assertContains(response, self.user.username)
        self.assertContains(response, reverse("election:password_change"))
        self.assertEqual(self.client.get(reverse("election:logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("election:logout")), reverse("election:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_preserves_local_next_and_rejects_non_staff_and_inactive(self):
        for staff, active in [(False, True), (True, False)]:
            self.user.is_staff, self.user.is_active = staff, active
            self.user.save()
            response = self.client.post(reverse("election:login"), {
                "username": self.user.username, "password": self.password,
            })
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.assertNotIn("_auth_user_id", self.client.session)
        self.user.is_staff = self.user.is_active = True
        self.user.save()
        response = self.client.post(reverse("election:login"), {
            "username": self.user.username, "password": self.password,
            "next": "/management/new/",
        })
        self.assertRedirects(response, "/management/new/", fetch_redirect_response=False)

    def request_reset(self, email=None):
        return self.client.post(reverse("election:password_reset"), {
            "email": email or self.user.email,
        })

    def reset_link(self):
        self.request_reset()
        return urlsplit(re.search(r"https?://\S+", mail.outbox[-1].body).group()).path

    def test_reset_email_and_single_use_link(self):
        link = self.reset_link()
        self.assertEqual(mail.outbox[-1].to, [self.user.email])
        self.assertIn("日本加速器学会", mail.outbox[-1].subject)
        response = self.client.get(link, follow=True)
        self.assertTrue(response.context["validlink"])
        form_url = response.redirect_chain[-1][0]
        response = self.client.post(form_url, {
            "new_password1": self.new_password, "new_password2": self.new_password,
        })
        self.assertRedirects(response, reverse("election:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))
        response = self.client.get(link, follow=True)
        self.assertFalse(response.context["validlink"])
        self.assertFalse(self.client.login(username=self.user.username, password=self.password))
        self.assertTrue(self.client.login(username=self.user.username, password=self.new_password))

    def test_unknown_inactive_and_unusable_accounts_have_same_confirmation(self):
        for kind in ["unknown", "inactive", "unusable"]:
            if kind == "inactive":
                self.user.is_active = False
                self.user.save()
            if kind == "unusable":
                self.user.is_active = True
                self.user.set_unusable_password()
                self.user.save()
            response = self.request_reset("unknown@example.com" if kind == "unknown" else None)
            self.assertRedirects(response, reverse("election:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_expired_and_invalid_tokens(self):
        link = self.reset_link()
        future = default_token_generator._now() + timedelta(days=4)
        with patch.object(default_token_generator, "_now", return_value=future):
            response = self.client.get(link, follow=True)
        self.assertFalse(response.context["validlink"])
        response = self.client.get(reverse("election:password_reset_confirm", args=["invalid", "invalid"]))
        self.assertContains(response, "このリンクは無効")

    def test_reset_rejects_weak_or_mismatched_passwords(self):
        response = self.client.get(self.reset_link(), follow=True)
        form_url = response.redirect_chain[-1][0]
        for first, second in [("123", "123"), (self.new_password, "different")]:
            response = self.client.post(form_url, {"new_password1": first, "new_password2": second})
            self.assertTrue(response.context["form"].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_change_requires_login_and_current_password_and_preserves_session(self):
        url = reverse("election:password_change")
        self.assertRedirects(self.client.get(url), reverse("election:login") + "?next=" + url)
        self.client.force_login(self.user)
        data = {"old_password": "wrong", "new_password1": self.new_password, "new_password2": self.new_password}
        response = self.client.post(url, data)
        self.assertIn("old_password", response.context["form"].errors)
        data["old_password"] = self.password
        self.assertRedirects(self.client.post(url, data), reverse("election:password_change_done"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))
        self.assertEqual(self.client.get(reverse("election:management_cycle_list")).status_code, 200)


    def test_admin_login_redirects_to_shared_login(self):
        response = self.client.get(reverse("admin:login"))
        self.assertRedirects(response, reverse("election:login") + "?next=/admin/")
        response = self.client.get(reverse("admin:index"), follow=True)
        self.assertTemplateUsed(response, "election/auth/login.html")
        self.assertEqual(response.context["next"], reverse("admin:index"))

    def test_admin_login_preserves_destination_after_authentication(self):
        destination = reverse("admin:auth_user_changelist") + "?is_staff__exact=1"
        response = self.client.get(reverse("admin:login"), {"next": destination}, follow=True)
        self.assertEqual(response.context["next"], destination)
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        response = self.client.post(reverse("election:login"), {
            "username": self.user.username, "password": self.password,
            "next": response.context["next"],
        })
        self.assertRedirects(response, destination)

    def test_admin_login_does_not_accept_credentials(self):
        response = self.client.post(reverse("admin:login"), {
            "username": self.user.username, "password": self.password,
        })
        self.assertEqual(response.status_code, 405)
        self.assertNotIn("_auth_user_id", self.client.session)
