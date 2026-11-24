import unittest

from git_pulse_agent.agy_runner import parse_agy_models_output, select_model_order
from git_pulse_agent.scheduler import render_service_unit, render_timer_unit


SAMPLE_MODELS = """Fetching available models...
gemini-4-neon-medium\tGemini 4 Neon (Medium)
gemini-3.8-flash-high\tGemini 3.8 Flash (High)
claude-sonnet-5-5-medium\tClaude Sonnet 5.5 (Medium)
"""


class TestAgyAndScheduler(unittest.TestCase):
    def test_parse_and_order_models(self):
        models = parse_agy_models_output(SAMPLE_MODELS)
        self.assertEqual(
            models,
            ["gemini-4-neon-medium", "gemini-3.8-flash-high", "claude-sonnet-5-5-medium"],
        )
        ordered = select_model_order(models, preferred=["claude-sonnet-5-5-medium", "missing-model"])
        self.assertEqual(ordered[0], "claude-sonnet-5-5-medium")
        self.assertEqual(len(ordered), 3)

    def test_render_systemd_units(self):
        svc = render_service_unit("/usr/local/bin/git-pulse run")
        tmr = render_timer_unit("15:00:00")
        self.assertIn("ExecStart=/usr/local/bin/git-pulse run", svc)
        self.assertIn("OnCalendar=*-*-* 15:00:00", tmr)
        self.assertIn("Persistent=true", tmr)


if __name__ == "__main__":
    unittest.main()
