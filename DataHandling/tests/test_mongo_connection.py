import unittest

from app.db.connection import (
    build_verified_mongo_options,
    validate_mongo_tls_options,
)


class MongoConnectionSecurityTests(unittest.TestCase):
    def test_default_options_preserve_certificate_verification(self):
        options = build_verified_mongo_options()

        self.assertEqual(options, {"serverSelectionTimeoutMS": 5000})
        self.assertNotIn("tlsAllowInvalidCertificates", options)
        self.assertNotIn("tlsAllowInvalidHostnames", options)
        self.assertNotIn("tlsInsecure", options)

    def test_custom_ca_file_is_forwarded_without_disabling_verification(self):
        options = build_verified_mongo_options(
            ca_file=" /run/secrets/mongo-ca.pem ",
            server_selection_timeout_ms=2500,
        )

        self.assertEqual(
            options,
            {
                "serverSelectionTimeoutMS": 2500,
                "tlsCAFile": "/run/secrets/mongo-ca.pem",
            },
        )

    def test_secure_uri_options_are_allowed(self):
        validate_mongo_tls_options(
            "mongodb+srv://example.invalid/db?tls=true&tlsAllowInvalidCertificates=false"
        )

    def test_insecure_certificate_option_is_rejected_case_insensitively(self):
        with self.assertRaisesRegex(ValueError, "(?i)tlsallowinvalidcertificates"):
            validate_mongo_tls_options(
                "mongodb://example.invalid/db?TLSALLOWINVALIDCERTIFICATES=true"
            )

    def test_insecure_hostname_and_combined_options_are_rejected(self):
        for option in ("tlsAllowInvalidHostnames=yes", "tlsInsecure=1"):
            with self.subTest(option=option):
                with self.assertRaises(ValueError):
                    validate_mongo_tls_options(
                        f"mongodb://example.invalid/db?{option}"
                    )

    def test_legacy_cert_none_option_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ssl_cert_reqs"):
            validate_mongo_tls_options(
                "mongodb://example.invalid/db?ssl_cert_reqs=CERT_NONE"
            )


if __name__ == "__main__":
    unittest.main()
