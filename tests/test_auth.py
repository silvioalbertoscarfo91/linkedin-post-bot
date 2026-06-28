from linkedin_post_bot.auth import is_authorized


def test_authorized_user_matches():
    assert is_authorized(42, 42) is True


def test_other_user_rejected():
    assert is_authorized(99, 42) is False


def test_none_user_rejected():
    assert is_authorized(None, 42) is False
