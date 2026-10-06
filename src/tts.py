from jnius import autoclass
import time

Locale = autoclass("java.util.Locale")
TextToSpeech = autoclass("android.speech.tts.TextToSpeech")


def _get_context():
    """Return (context, source). Prefer the app's activity; fall back to ActivityThread."""
    try:
        from flet_alarm import PythonActivity

        if PythonActivity and PythonActivity.mActivity is not None:
            return PythonActivity.mActivity.getApplicationContext(), "activity"
    except Exception:
        pass
    ActivityThread = autoclass("android.app.ActivityThread")
    app = ActivityThread.currentApplication()
    if app is None:
        raise RuntimeError("No Android context available")
    return app.getApplicationContext(), "activity-thread"


class TTS:
    """Text-to-speech WITHOUT a Python callback (avoids pyjnius proxy crashes).

    The engine binds asynchronously. Until it is bound, setLanguage() returns
    an error code (-1); once bound it returns >= 0. We poll that instead of
    waiting for an OnInitListener.
    """

    def __init__(self):
        context, self.context_source = _get_context()
        self.status = None
        self.ready = False
        self._tts = TextToSpeech(context, None)

    def wait_ready(self, timeout=6):
        t = time.time()
        while time.time() - t < timeout:
            try:
                self.status = self._tts.setLanguage(Locale.US)
            except Exception:
                self.status = None
            if self.status is not None and self.status >= 0:
                self.ready = True
                return True
            time.sleep(0.25)
        return False

    def speak(self, text):
        if self.ready:
            # QUEUE_FLUSH = 0, QUEUE_ADD = 1
            self._tts.speak(text, TextToSpeech.QUEUE_FLUSH, None, "lesson_alert")

    def shutdown(self):
        try:
            self._tts.stop()
            self._tts.shutdown()
        except Exception:
            pass
