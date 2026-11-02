import contextlib
import io
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from app import AppError, AzureCLI, DeskServer, KeyVault, NoRedirect, load_config, main


VAULTS = {
    "nonprod": {"name": "team-nonprod-vault", "nickname": "Testing", "env": "test"},
    "prod": {"name": "team-prod-vault", "nickname": "Production", "env": "prod"},
    "team-dev-vault": {"name": "team-dev-vault", "nickname": "Development", "env": "dev"},
    "team-live-vault": {"name": "team-live-vault", "nickname": "Live services", "env": "prod"},
    "team-shared-vault": {"name": "team-shared-vault", "nickname": "", "env": "other"},
}


class FixtureIdentity:
    def __init__(self):
        self.locked = False

    def lock(self):
        self.locked = True


class FixtureService:
    def __init__(self):
        self.identity = FixtureIdentity()
        self.saves = []
        self.records = {
            "nonprod": {"payments-api-key": "test-value=with=equals", "Payments-Legacy": "legacy",
                        "storage-connection-string": "line one\r\nline two",
                        "inactive-secret": "inactive", "<script>unsafe</script>": "<img src=x onerror=alert(1)>"},
            "prod": {"payments-api-key": "production-fixture-only"},
            "team-dev-vault": {"dev-api-key": "development-fixture-only"},
            "team-live-vault": {"live-api-key": "second-production-fixture-only"},
            "team-shared-vault": {},
        }

    def inventory(self, env):
        return [{"name": name, "enabled": name != "inactive-secret", "updated": 1730000000,
                 "expires": None, "notBefore": None} for name in sorted(self.records[env])]

    def read(self, env, name):
        if name not in self.records[env]:
            raise AppError(404, "not_found", "Secret not found.")
        return {"name": name, "value": self.records[env][name]}

    def save(self, env, name, value):
        self.saves.append((env, name, value))
        self.records[env][name] = value
        return {"name": name}


class ConfigTests(unittest.TestCase):
    def config(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_config(path)

    def test_valid_config_normalizes_case(self):
        self.assertEqual(self.config({"nonprod": "Team-Nonprod", "prod": "Team-Prod"}),
                         {"team-nonprod": {"name": "team-nonprod", "nickname": "", "env": "test"},
                          "team-prod": {"name": "team-prod", "nickname": "", "env": "prod"}})

    def test_multiple_vault_config_with_optional_nicknames(self):
        data = [{"name": "Team-Dev", "nickname": " Development ", "env": "dev"},
                {"name": "team-test", "env": "test"},
                {"name": "team-prod", "nickname": "Live", "env": "prod"},
                {"name": "other-prod", "env": "prod"},
                {"name": "shared-vault", "nickname": "", "env": "other"}]
        result = self.config(data)
        self.assertEqual(len(result), 5)
        self.assertEqual(result["team-dev"], {"name": "team-dev", "nickname": "Development", "env": "dev"})
        self.assertEqual(result["team-test"]["nickname"], "")
        self.assertEqual(result["other-prod"]["env"], "prod")

    def test_single_vault_is_supported(self):
        self.assertEqual(len(self.config([{"name": "only-prod", "env": "prod"}])), 1)

    def test_rejects_bad_vault_entries(self):
        for data in ([{}], ["vault"], [{"name": "valid-vault"}],
                     [{"name": "valid-vault", "env": "staging"}],
                     [{"name": "valid-vault", "env": []}],
                     [{"name": "valid-vault", "env": "prod", "nickname": None}],
                     [{"name": "valid-vault", "env": "dev", "nickname": "a" * 81}],
                     [{"name": "valid-vault", "env": "test", "nickname": "bad\nname"}],
                     [{"name": "valid-vault", "env": "test", "extra": True}],
                     [{"name": "same-vault", "env": "dev"}, {"name": "SAME-VAULT", "env": "prod"}]):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.config(data)

    def test_rejects_bad_configs(self):
        for data in ({}, [], {"nonprod": "aa", "prod": "valid-vault"},
                     {"nonprod": "a--b", "prod": "valid-vault"},
                     {"nonprod": "https://evil.test", "prod": "valid-vault"},
                     {"nonprod": "same-vault", "prod": "SAME-VAULT"},
                     {"nonprod": None, "prod": "valid-vault"},
                     {**VAULTS, "other": "another-vault"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.config(data)

    def test_missing_config(self):
        with self.assertRaisesRegex(ValueError, "Copy config.example.json"):
            load_config(Path("does-not-exist.json"))

    def test_debug_flag_configures_terminal_logging(self):
        for flags, level in (([], logging.WARNING), (["--debug"], logging.DEBUG)):
            server = Mock()
            server.serve_forever.side_effect = KeyboardInterrupt
            with (self.subTest(flags=flags), patch("sys.argv", ["app.py", *flags]),
                  patch("app.logging.basicConfig") as configure,
                  patch("app.load_config", return_value=VAULTS), patch("app.AzureCLI"),
                  patch("app.DeskServer", return_value=server), patch("builtins.print")):
                main()
            self.assertEqual(configure.call_args.kwargs["level"], level)
            server.server_close.assert_called_once()


class AzureCLITests(unittest.TestCase):
    def identity(self):
        with patch("app.shutil.which", return_value="az"):
            return AzureCLI()

    def test_missing_cli(self):
        with patch("app.shutil.which", return_value=None), self.assertRaises(ValueError):
            AzureCLI()

    @patch("app.subprocess.run")
    def test_cache_and_refresh(self, run):
        run.return_value = Mock(returncode=0, stdout=json.dumps({
            "accessToken": "fake-token", "expires_on": time.time() + 3600,
        }))
        identity = self.identity()
        self.assertEqual(identity.token(), "fake-token")
        self.assertEqual(identity.token(), "fake-token")
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertIn("https://vault.azure.net", command)
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        identity.clear()
        identity.token()
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("fake-token", str(run.call_args))

    @patch("app.subprocess.run")
    def test_expiring_cached_token_is_not_reused(self, run):
        run.return_value = Mock(returncode=0, stdout=json.dumps({
            "accessToken": "fresh-token", "expires_on": time.time() + 3600,
        }))
        identity = self.identity()
        identity._token = "old-token"
        identity._expires = time.time() + 60
        self.assertEqual(identity.token(), "fresh-token")
        run.assert_called_once()

    @patch("app.subprocess.run")
    def test_cli_timeout_and_launch_failure(self, run):
        for error, expected in ((subprocess.TimeoutExpired("az", 45), 504), (OSError("private detail"), 503)):
            run.side_effect = error
            with self.subTest(expected=expected), self.assertRaises(AppError) as caught:
                self.identity().token()
            self.assertEqual(caught.exception.status, expected)
            self.assertNotIn("private detail", str(caught.exception))

    @patch("app.subprocess.run")
    def test_cli_errors_do_not_leak_output(self, run):
        run.return_value = Mock(returncode=1, stderr="sensitive diagnostic")
        with self.assertLogs("keyvault_desk", level="DEBUG") as logs, self.assertRaises(AppError) as caught:
            self.identity().token()
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertIn("exit_code=1", "\n".join(logs.output))
        self.assertNotIn("sensitive", "\n".join(logs.output))

    @patch("app.subprocess.run")
    def test_malformed_token(self, run):
        for output in ("not json", "{}", '{"accessToken":null,"expires_on":99999999999}'):
            run.return_value = Mock(returncode=0, stdout=output)
            with self.subTest(output=output), self.assertRaises(AppError) as caught:
                self.identity().token()
            self.assertEqual(caught.exception.status, 502)

    @patch("app.subprocess.run")
    def test_lock_during_token_acquisition(self, run):
        identity = self.identity()

        def finish(*args, **kwargs):
            identity.lock()
            return Mock(returncode=0, stdout=json.dumps({
                "accessToken": "fake-token", "expires_on": time.time() + 3600,
            }))

        run.side_effect = finish
        with self.assertRaises(AppError):
            identity.token()
        self.assertIsNone(identity._token)
        with self.assertRaises(AppError):
            identity.token()
        self.assertEqual(run.call_count, 1)


class KeyVaultTests(unittest.TestCase):
    def setUp(self):
        self.identity = Mock()
        self.identity.token.return_value = "fake-token"
        self.vault = KeyVault(VAULTS, self.identity)

    def response(self, data):
        return contextlib.closing(io.BytesIO(json.dumps(data).encode()))

    def test_inventory_pagination_and_metadata_only(self):
        pages = [
            {"value": [{"id": "https://team-nonprod-vault.vault.azure.net/secrets/z-secret",
                        "attributes": {"enabled": False, "exp": 123}, "value": "never-send-this"}],
             "nextLink": "https://team-nonprod-vault.vault.azure.net/secrets?skiptoken=page2"},
            {"value": [{"id": "https://team-nonprod-vault.vault.azure.net/secrets/a-secret",
                        "attributes": {"enabled": True, "updated": 456}}]},
        ]
        with patch.object(self.vault, "_request", side_effect=pages) as request:
            items = self.vault.inventory("nonprod")
        self.assertEqual([item["name"] for item in items], ["a-secret", "z-secret"])
        self.assertNotIn("value", items[0])
        self.assertFalse(items[1]["enabled"])
        self.assertEqual(request.call_count, 2)

    def test_pagination_loop_is_error_not_partial_success(self):
        page = {"value": [], "nextLink": "https://team-nonprod-vault.vault.azure.net/secrets?again"}
        with patch.object(self.vault, "_request", return_value=page), self.assertRaises(AppError):
            self.vault.inventory("nonprod")

    def test_rejects_cross_origin_continuation_before_token(self):
        for url in ("http://team-nonprod-vault.vault.azure.net/secrets",
                    "https://evil.test/secrets", "https://team-prod-vault.vault.azure.net/secrets",
                    "https://team-nonprod-vault.vault.azure.net.evil.test/secrets",
                    "https://team-nonprod-vault.vault.azure.net/keys",
                    "https://team-nonprod-vault.vault.azure.net/secrets#fragment",
                    "//evil.test/secrets", "http:secrets?token=private",
                    "https://team-nonprod-vault.vault.azure.net:8443/secrets",
                    "https://user:password@team-nonprod-vault.vault.azure.net/secrets",
                    "https://team-nonprod-vault.vault.azure.net/secrets/other-secret",
                    "https://team-nonprod-vault.vault.azure.net:bad/secrets",
                    "https://[broken/secrets", "/secrets?skiptoken=bad\nvalue", None):
            with self.subTest(url=url), self.assertLogs("keyvault_desk", level="DEBUG"), self.assertRaises(AppError):
                self.vault._request("nonprod", url, absolute=True)
        self.identity.token.assert_not_called()

    def test_equivalent_and_relative_continuations(self):
        for url in ("https://TEAM-NONPROD-VAULT.vault.azure.net:443/secrets?skiptoken=private",
                    "/secrets?skiptoken=private", "secrets?skiptoken=private",
                    "?skiptoken=private", "/secrets/?skiptoken=private"):
            with (self.subTest(url=url),
                  patch.object(self.vault.opener, "open", return_value=self.response({"value": []})) as open_):
                self.assertEqual(self.vault._request("nonprod", url, absolute=True), {"value": []})
                self.assertIn("skiptoken=private", open_.call_args.args[0].full_url)

    def test_relative_pagination_reaches_second_page(self):
        pages = [
            {"value": [], "nextLink": "?api-version=7.4&skiptoken=private"},
            {"value": []},
        ]
        with patch.object(self.vault, "_request", side_effect=pages) as request:
            self.assertEqual(self.vault.inventory("nonprod"), [])
        self.assertFalse(request.call_args_list[0].kwargs["absolute"])
        self.assertTrue(request.call_args_list[1].kwargs["absolute"])

    def test_requests_use_selected_vault_not_environment(self):
        for vault_id in ("team-dev-vault", "team-live-vault", "team-shared-vault"):
            with (self.subTest(vault_id=vault_id),
                  patch.object(self.vault.opener, "open", return_value=self.response({"value": []})) as open_):
                self.vault.inventory(vault_id)
                self.assertEqual(urllib.parse.urlsplit(open_.call_args.args[0].full_url).hostname,
                                 f"{vault_id}.vault.azure.net")

    def test_debug_url_rejection_explains_reason_without_url(self):
        with self.assertLogs("keyvault_desk", level="DEBUG") as logs, self.assertRaises(AppError):
            self.vault._request("nonprod", "https://evil.test/secrets?skiptoken=private-marker", absolute=True)
        text = "\n".join(logs.output)
        self.assertIn("reason=host_mismatch", text)
        self.assertIn("expected_host=team-nonprod-vault.vault.azure.net", text)
        self.assertNotIn("private-marker", text)
        self.assertNotIn("evil.test", text)
        self.identity.token.assert_not_called()

    def test_debug_requests_do_not_log_secrets_tokens_or_names(self):
        with (self.assertLogs("keyvault_desk", level="DEBUG") as logs,
              patch.object(self.vault.opener, "open", return_value=self.response({"value": "response-marker"}))):
            self.vault._request("prod", "/secrets/name-marker?query=private-marker",
                                method="PUT", body={"value": "body-marker"})
        text = "\n".join(logs.output)
        self.assertIn("method=PUT operation=secret", text)
        self.assertIn("duration_ms=", text)
        for marker in ("name-marker", "private-marker", "body-marker", "response-marker", "fake-token"):
            self.assertNotIn(marker, text)

    def test_read_and_put_preserves_metadata(self):
        properties = {"value": "old-value", "tags": {"owner": "team"}, "contentType": "text/plain",
                      "attributes": {"enabled": True, "exp": 9999999999, "nbf": 10, "created": 20}}
        with patch.object(self.vault, "_request", side_effect=[properties, {}]) as request:
            result = self.vault.save("nonprod", "test-secret", "new\nvalue")
        body = request.call_args.kwargs["body"]
        self.assertEqual(body["value"], "new\nvalue")
        self.assertEqual(body["tags"], {"owner": "team"})
        self.assertEqual(body["contentType"], "text/plain")
        self.assertEqual(body["attributes"], {"enabled": True, "exp": 9999999999, "nbf": 10})
        self.assertEqual(result, {"name": "test-secret"})

    def test_create_after_404_only(self):
        with patch.object(self.vault, "_request", side_effect=[AppError(404, "not_found", "missing"), {}]) as request:
            self.vault.save("prod", "new-secret", "value")
        self.assertEqual(request.call_args.kwargs["body"], {"value": "value"})
        with patch.object(self.vault, "_request", side_effect=AppError(403, "forbidden", "denied")) as request:
            with self.assertRaises(AppError):
                self.vault.save("prod", "new-secret", "value")
        self.assertEqual(request.call_count, 1)

    def test_401_refresh_once(self):
        error = urllib.error.HTTPError("https://unused.test", 401, "Unauthorized", {}, None)
        with patch.object(self.vault.opener, "open", side_effect=[error, self.response({"value": "hello"})]) as open_:
            self.assertEqual(self.vault.read("prod", "test"), {"name": "test", "value": "hello"})
        self.identity.clear.assert_called_once()
        self.assertEqual(open_.call_count, 2)

    def test_safe_azure_errors(self):
        for status in (400, 401, 403, 404, 409, 429, 500, 302):
            error = urllib.error.HTTPError("https://unused.test", status, "sensitive", {}, None)
            with self.subTest(status=status), patch.object(self.vault.opener, "open", side_effect=error):
                with self.assertRaises(AppError) as caught:
                    self.vault.read("prod", "test")
                self.assertNotIn("sensitive", str(caught.exception))
                self.assertEqual(caught.exception.status, status if status in (400, 401, 403, 404, 409, 429) else 502)

    def test_network_and_invalid_json(self):
        for error, expected in ((urllib.error.URLError("private diagnostics"), 504), (TimeoutError(), 504)):
            with patch.object(self.vault.opener, "open", side_effect=error), self.assertRaises(AppError) as caught:
                self.vault.read("prod", "test")
            self.assertEqual(caught.exception.status, expected)
        with patch.object(self.vault.opener, "open", return_value=contextlib.closing(io.BytesIO(b"bad json"))):
            with self.assertRaises(AppError) as caught:
                self.vault.read("prod", "test")
        self.assertEqual(caught.exception.status, 502)

    def test_redirect_is_never_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_write_transport_error_is_not_retried(self):
        with patch.object(self.vault.opener, "open", side_effect=TimeoutError()) as open_:
            with self.assertRaises(AppError):
                self.vault._request("prod", "/secrets/test?api-version=7.4", method="PUT",
                                    body={"value": "synthetic-only"})
        open_.assert_called_once()
        request = open_.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer fake-token")
        self.assertNotIn("synthetic-only", request.full_url)
        self.assertEqual(json.loads(request.data), {"value": "synthetic-only"})


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = FixtureService()
        cls.server = DeskServer(("127.0.0.1", 0), VAULTS, cls.service)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = cls.server.origin

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.locked = False
        self.service.identity.locked = False
        self.service.saves.clear()

    def request(self, path, method="GET", body=None, headers=None, raw=None):
        data = json.dumps(body).encode() if body is not None else raw
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"X-Desk-Token": self.server.session, "Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = response.read()
            return response.status, response.headers, payload

    def test_static_assets_and_no_secret_caching(self):
        for path, mime in (("/", "text/html"), ("/app.js", "text/javascript"),
                           ("/style.css", "text/css"), ("/favicon.svg", "image/svg+xml")):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(mime, headers["Content-Type"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertNotIn(b"__DESK_TOKEN__", body)

    def test_session_and_inventory(self):
        status, _, body = self.request("/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["vaults"], VAULTS)
        status, _, body = self.request("/api/nonprod/secrets")
        self.assertEqual(status, 200)
        self.assertNotIn(b"test-value", body)

    def test_multiple_vaults_route_to_the_selected_vault(self):
        for vault_id in ("team-dev-vault", "team-live-vault", "team-shared-vault"):
            with self.subTest(vault_id=vault_id):
                status, _, body = self.request(f"/api/{vault_id}/secrets")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["secrets"], self.service.inventory(vault_id))

    def test_production_guard_uses_config_not_route_name(self):
        status, _, body = self.request("/api/team-live-vault/secrets/new-key", "PUT",
                                       {"value": "test", "confirmProduction": False})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "production_confirmation")
        self.assertFalse(self.service.saves)
        self.assertEqual(self.request("/api/team-live-vault/secrets/new-key", "PUT",
                                      {"value": "test", "confirmProduction": True})[0], 200)
        self.assertEqual(self.service.saves[0][0], "team-live-vault")

    def test_unknown_vault_never_reaches_service(self):
        with patch.object(self.service, "inventory") as inventory:
            self.assertEqual(self.request("/api/unconfigured-vault/secrets")[0], 404)
        inventory.assert_not_called()

    def test_exact_value_roundtrip_and_escaping(self):
        value = " \nsecret=1\r\n☃  "
        status, _, body = self.request("/api/nonprod/secrets/new-test", "PUT",
                                       {"value": value, "confirmProduction": False})
        self.assertEqual(status, 200)
        self.assertNotIn(value.encode(), body)
        status, _, body = self.request("/api/nonprod/secrets/new-test")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["value"], value)

    def test_host_origin_and_token_protection(self):
        for headers in ({"Host": "evil.test"}, {"Origin": "https://evil.test"},
                        {"Sec-Fetch-Site": "cross-site"}, {"X-Desk-Token": ""},
                        {"X-Desk-Token": "wrong"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/nonprod/secrets", headers=headers)[0], 403)
        self.assertEqual(self.request("/", headers={"Host": "evil.test"})[0], 403)
        self.assertEqual(self.request("/api/session", headers={"Origin": self.base})[0], 200)

    def test_production_confirmation_required(self):
        for confirmation in (False, None, "true", 1):
            with self.subTest(confirmation=confirmation):
                status, _, _ = self.request("/api/prod/secrets/test", "PUT",
                                            {"value": "test", "confirmProduction": confirmation})
                self.assertEqual(status, 400)
        self.assertFalse(self.service.saves)
        self.assertEqual(self.request("/api/prod/secrets/test", "PUT",
                                      {"value": "test", "confirmProduction": True})[0], 200)

    def test_value_byte_boundary(self):
        for value, expected in (("", 400), ("a" * 25600, 200), ("a" * 25601, 400),
                                ("é" * 12800, 200), ("é" * 12801, 400), (123, 400), ("\ud800", 400)):
            with self.subTest(length=len(value) if isinstance(value, str) else value):
                self.assertEqual(self.request("/api/nonprod/secrets/size-test", "PUT",
                                              {"value": value, "confirmProduction": False})[0], expected)

    def test_invalid_json_and_request_shapes(self):
        for raw, expected in ((b"{", 400), (b"[]", 400), (b"{}", 400)):
            with self.subTest(raw_length=len(raw)):
                self.assertEqual(self.request("/api/nonprod/secrets/test", "PUT", raw=raw)[0], expected)
        self.assertEqual(self.request("/api/nonprod/secrets/test", "PUT", raw=b"{}",
                                      headers={"Content-Type": "text/plain"})[0], 400)

    def test_rejects_oversized_content_length_before_reading(self):
        status, _, body = self.request("/api/nonprod/secrets/test", "PUT",
                                       headers={"Content-Length": str(160 * 1024 + 1)})
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], "body_too_large")
        self.assertFalse(self.service.saves)

    def test_path_validation_and_unsupported_operations(self):
        for path in ("/api/other/secrets", "/api/prod/secrets/a_b", "/api/prod/secrets/" + "a" * 128,
                     "/api/prod/secrets/%2e%2e", "/config.json", "/../app.py"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/api/prod/secrets/test", "DELETE")[0], 405)

    def test_conflicting_writes(self):
        with self.server.write_lock:
            status, _, body = self.request("/api/nonprod/secrets/test", "PUT",
                                           {"value": "test", "confirmProduction": False})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "write_in_progress")

    def test_lock_clears_and_invalidates_all_apis(self):
        self.assertEqual(self.request("/api/lock", "POST")[0], 200)
        self.assertTrue(self.service.identity.locked)
        self.assertEqual(self.request("/api/session")[0], 401)
        self.assertEqual(self.request("/api/nonprod/secrets")[0], 401)
        self.assertEqual(self.request("/")[0], 200)

    def test_lock_suppresses_in_flight_secret_response(self):
        def read(env, name):
            self.server.locked = True
            return {"name": name, "value": "must-not-return"}

        with patch.object(self.service, "read", side_effect=read):
            status, _, body = self.request("/api/nonprod/secrets/test")
        self.assertEqual(status, 401)
        self.assertNotIn(b"must-not-return", body)

    def test_unexpected_error_is_explicit_and_sanitized(self):
        with (patch.object(self.service, "inventory", side_effect=RuntimeError("sensitive")),
              self.assertLogs("keyvault_desk", level="DEBUG") as logs):
            status, _, body = self.request("/api/nonprod/secrets")
        self.assertEqual(status, 500)
        self.assertNotIn(b"sensitive", body)
        text = "\n".join(logs.output)
        self.assertNotIn("sensitive", text)
        self.assertIn("exception=RuntimeError", text)
        self.assertIn("Failure location", text)
        self.assertIn("status=500 code=internal_error", text)

    def test_request_diagnostics_omit_secret_identifiers_and_values(self):
        with self.assertLogs("keyvault_desk", level="DEBUG") as logs:
            status, _, _ = self.request("/api/nonprod/secrets/name-marker?query=private-marker", "PUT",
                                        {"value": "body-marker", "confirmProduction": False})
        self.assertEqual(status, 200)
        text = "\n".join(logs.output)
        self.assertIn("env=test operation=secret status=200", text)
        for marker in ("name-marker", "private-marker", "body-marker", self.server.session):
            self.assertNotIn(marker, text)

    def test_expected_errors_are_logged_without_debug(self):
        with self.assertLogs("keyvault_desk", level="WARNING") as logs:
            self.request("/api/nonprod/secrets", headers={"X-Desk-Token": "invalid-private-marker"})
        text = "\n".join(logs.output)
        self.assertIn("status=403 code=invalid_session", text)
        self.assertNotIn("invalid-private-marker", text)


if __name__ == "__main__":
    unittest.main()
