import os
import unittest
from pathlib import Path

from celery_utils.processing.file_utils import get_audio_duration

FIXTURES = Path(os.getenv("AUDIO_FIXTURES", "/app/fixtures"))

# Reference durations measured with the previous reader (mutagen 1.47.0) on the
# same fixtures; metering may drift by a frame, not by a second.
EXPECTED_SECONDS = {".aac": 1.088, ".mp3": 1.08, ".mpeg": 1.019, ".ogg": 1.0, ".wav": 1.0}


class AudioDurationTests(unittest.TestCase):
    def test_supported_formats_report_their_length(self):
        for suffix, expected in EXPECTED_SECONDS.items():
            with self.subTest(suffix=suffix):
                duration = get_audio_duration(FIXTURES / f"workerfail_silent_tone{suffix}")
                self.assertAlmostEqual(duration, expected, delta=0.05)

    def test_unsupported_extension_reports_zero(self):
        self.assertEqual(get_audio_duration(FIXTURES / "accept_declaration.txt"), 0)
