import types
import unittest

from core.action_verifier import ActionVerifier, Verdict


def scene(text="same", title="EIDOS", regions=None):
    return types.SimpleNamespace(
        ocr_full_text=text,
        window_title=title,
        regions=[types.SimpleNamespace(text=x) for x in (regions or [text])],
    )


class TestActionVerifierCausality(unittest.TestCase):
    def setUp(self):
        self.verifier = ActionVerifier()

    def verify(self, action, before, after):
        return self.verifier.verify(before, after, {"action": action}, "")

    def test_type_without_observable_effect_is_not_success(self):
        result = self.verify("type", scene(), scene())
        self.assertFalse(result.verified)
        self.assertEqual(result.verdict, Verdict.STUCK)

    def test_type_with_observable_effect_is_verified(self):
        result = self.verify("type", scene("field"), scene("field hello", regions=["field", "hello"]))
        self.assertTrue(result.verified)

    def test_wait_without_change_is_not_success(self):
        result = self.verify("wait", scene(), scene())
        self.assertFalse(result.verified)

    def test_extract_without_observable_result_is_not_success(self):
        result = self.verify("extract", scene(), scene())
        self.assertFalse(result.verified)

    def test_scroll_without_observable_effect_is_not_success(self):
        result = self.verify("scroll", scene(), scene())
        self.assertFalse(result.verified)
        self.assertEqual(result.verdict, Verdict.STUCK)

    def test_scroll_with_new_content_is_verified(self):
        result = self.verify(
            "scroll",
            scene("top", regions=["top"]),
            scene("lower content", regions=["lower", "content"]),
        )
        self.assertTrue(result.verified)

    def test_navigation_without_observable_change_is_not_success(self):
        result = self.verify("navigate_url", scene(), scene())
        self.assertFalse(result.verified)

    def test_unknown_action_without_change_fails_closed(self):
        result = self.verify("teleport", scene(), scene())
        self.assertFalse(result.verified)
        self.assertEqual(result.verdict, Verdict.STUCK)

    def test_unknown_action_with_observable_change_can_be_verified(self):
        result = self.verify("custom", scene("before"), scene("after", regions=["after", "new"]))
        self.assertTrue(result.verified)


if __name__ == "__main__":
    unittest.main()
