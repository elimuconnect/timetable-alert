from jnius import autoclass
import time

Locale = autoclass("java.util.Locale")
TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
JavaString = autoclass("java.lang.String")
Bundle = autoclass("android.os.Bundle")


def _get_context():
    """Return (context, source). Prefer the app's activity; fall back to ActivityThread."""
    try:
        from flet_alarm import PythonActivity

        if PythonActivity and PythonActivity.mActivity is not None:
            return (
                PythonActivity.mActivity.getApplicationContext(),
                "activity",
            )
    except Exception:
        pass

    ActivityThread = autoclass("android.app.ActivityThread")
    app = ActivityThread.currentApplication()

    if app is None:
        raise RuntimeError("No Android context available")

    return app.getApplicationContext(), "activity-thread"


class TTS:
    """Android Text-to-Speech wrapper compatible with Pyjnius."""

    def __init__(self):
        context, self.context_source = _get_context()

        self.status = None
        self.ready = False
        self.error = None

        # Create the Android TTS engine.
        self._tts = TextToSpeech(context, None)

        # Give Android time to bind the TTS engine.
        time.sleep(0.5)

    def wait_ready(self, timeout=8):
        """Wait until Android TTS is ready and English can be used."""

        start = time.time()

        while time.time() - start < timeout:
            try:
                self.status = self._tts.setLanguage(Locale.US)

                if self.status == TextToSpeech.LANG_MISSING_DATA:
                    self.error = "TTS language data is missing"

                elif self.status == TextToSpeech.LANG_NOT_SUPPORTED:
                    self.error = "English (US) is not supported"

                elif self.status is not None and self.status >= 0:
                    self.ready = True
                    self.error = None
                    return True

            except Exception as err:
                self.status = None
                self.error = str(err)

            time.sleep(0.25)

        return False

    def speak(self, text):
        """Speak text using Android TextToSpeech."""

        if not self.ready:
            print(
                f"TTS not ready. "
                f"status={self.status}, "
                f"error={self.error}"
            )
            return False

        try:
            text = str(text).strip()

            if not text:
                print("TTS received empty text.")
                return False

            print(f"TTS speaking: {text}")

            # Explicit Java types prevent Pyjnius overload-resolution errors.
            java_text = JavaString(text)
            params = Bundle()

            result = self._tts.speak(
                java_text,
                TextToSpeech.QUEUE_FLUSH,
                params,
                JavaString("lesson_alert"),
            )

            print(f"TTS speak result: {result}")

            if result == TextToSpeech.SUCCESS:
                return True

            self.error = f"Android TTS speak returned error code {result}"
            return False

        except Exception as err:
            print(f"TTS speak error: {err}")
            self.error = str(err)
            return False

    def stop(self):
        try:
            self._tts.stop()
        except Exception:
            pass

    def shutdown(self):
        try:
            self._tts.stop()
            self._tts.shutdown()
        except Exception:
            pass
