from shoreguard.api.password import validate_password


class TestValidatePassword:
    def test_valid(self):
        assert validate_password("securepass1") is None

    def test_too_short_default(self):
        err = validate_password("short")
        assert err is not None
        assert "at least 8" in err

    def test_too_short_custom(self):
        err = validate_password("abcdefgh", min_length=12)
        assert err is not None
        assert "at least 12" in err

    def test_too_long(self):
        err = validate_password("a" * 129)
        assert err is not None
        assert "at most 128" in err

    def test_exact_min_length(self):
        assert validate_password("12345678") is None

    def test_exact_max_length(self):
        assert validate_password("a" * 128) is None

    def test_complexity_valid(self):
        assert validate_password("Abcdef1x", require_complexity=True) is None

    def test_complexity_missing_uppercase(self):
        err = validate_password("abcdef1x", require_complexity=True)
        assert err is not None
        assert "uppercase" in err

    def test_complexity_missing_lowercase(self):
        err = validate_password("ABCDEF1X", require_complexity=True)
        assert err is not None
        assert "lowercase" in err

    def test_complexity_missing_digit(self):
        err = validate_password("Abcdefgh", require_complexity=True)
        assert err is not None
        assert "digit" in err

    def test_complexity_multiple_missing(self):
        err = validate_password("abcdefgh", require_complexity=True)
        assert err is not None
        assert "uppercase" in err
        assert "digit" in err

    def test_complexity_disabled_by_default(self):
        assert validate_password("alllowercase") is None
