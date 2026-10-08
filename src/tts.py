from jnius import autoclass
import time

# Android classes
Locale = autoclass("java.util.Locale")
TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
JavaString = autoclass("java.lang.String")
Bundle = autoclass("android.os.Bundle")


def _get_context():
    """
    Get a valid Android application context.

    We prefer the Flet activity when available, then fall back
    to Android's ActivityThread.
    """

    try:
        from flet_alarm import PythonActivity

        if PythonActivity is not None:
            activity = PythonActivity.mActivity

            if activity is not None:
                return (
                    activity.getApplicationContext(),
                    "flet-activity",
                )

    except Exception as e:
        print(f"TTS activity context unavailable: {e}")

    try:
        ActivityThread = autoclass("android.app.ActivityThread")
        app = ActivityThread.currentApplication()

        if app is not None:
            return (
                app.getApplicationContext(),
                "activity-thread",
            )

    except Exception as e:
        print(f"TTS ActivityThread context unavailable: {e}")

    raise RuntimeError("No Android context available for TTS")


class TTS:
    """
    Reliable Android Text-to-Speech wrapper.

    Designed for both:
      1. Manual voice tests while the app is open.
      2. Scheduled timetable alarms after Android wakes the app.
    """

    def __init__(self):
        self.context, self.context_source = _get_context()

        self.status = None
        self.ready = False
        self.error = None
        self._tts = None

        print(
            f"TTS: Creating Android TextToSpeech "
            f"(context={self.context_source})"
        )

        self._create_tts()

    def _create_tts(self):
        """
        Create the Android TextToSpeech engine.
        """

        try:
            self._tts = TextToSpeech(
                self.context,
                None,
            )

            # Give Android a short opportunity to initialize.
            time.sleep(0.5)

            print("TTS: Android TextToSpeech object created.")

        except Exception as e:
            self._tts = None
            self.error = str(e)

            print(
                f"TTS: Failed to create TextToSpeech: {e}"
            )

    def wait_ready(self, timeout=8):
        """
        Wait until Android TTS is initialized and a usable
        English language is available.

        Returns:
            True  -> ready
            False -> failed
        """

        if self._tts is None:
            self.error = "TextToSpeech engine was not created."
            return False

        start = time.time()

        print("TTS: Waiting for Android TTS to become ready...")

        while time.time() - start < timeout:

            try:
                # First try Kenyan English.
                try:
                    status = self._tts.setLanguage(
                        Locale("en", "KE")
                    )

                    if (
                        status != TextToSpeech.LANG_MISSING_DATA
                        and status != TextToSpeech.LANG_NOT_SUPPORTED
                        and status >= 0
                    ):
                        self.status = status
                        self.ready = True
                        self.error = None

                        self._configure_voice()

                        print(
                            "TTS: Ready using English (Kenya)."
                        )

                        return True

                except Exception as e:
                    print(
                        f"TTS: en-KE unavailable: {e}"
                    )

                # Fall back to US English.
                status = self._tts.setLanguage(
                    Locale.US
                )

                self.status = status

                if status == TextToSpeech.LANG_MISSING_DATA:
                    self.error = (
                        "TTS language data is missing."
                    )

                elif status == TextToSpeech.LANG_NOT_SUPPORTED:
                    self.error = (
                        "English language is not supported."
                    )

                elif status >= 0:
                    self.ready = True
                    self.error = None

                    self._configure_voice()

                    print(
                        "TTS: Ready using English (US)."
                    )

                    return True

            except Exception as e:
                self.status = None
                self.error = str(e)

                print(
                    f"TTS: Initialization check error: {e}"
                )

            time.sleep(0.25)

        self.ready = False

        print(
            f"TTS: Timed out waiting for TTS. "
            f"status={self.status}, "
            f"error={self.error}"
        )

        return False

    def _configure_voice(self):
        """
        Configure speech characteristics.

        This is deliberately done after the language has been
        successfully selected.
        """

        try:
            # Natural speaking speed.
            self._tts.setSpeechRate(0.95)

        except Exception as e:
            print(
                f"TTS: Could not set speech rate: {e}"
            )

    def speak(self, text):
        """
        Speak text immediately.

        Returns:
            True  -> Android accepted the speech request.
            False -> failed.
        """

        if self._tts is None:
            self.error = "TTS engine is not available."
            print("TTS: Engine unavailable.")
            return False

        if not self.ready:
            print(
                f"TTS: Not ready. "
                f"status={self.status}, "
                f"error={self.error}"
            )
            return False

        try:
            text = str(text).strip()

            if not text:
                self.error = "Empty speech text."
                print("TTS: Empty speech text.")
                return False

            print(
                f"TTS: Speaking scheduled message: {text}"
            )

            java_text = JavaString(text)

            params = Bundle()

            result = self._tts.speak(
                java_text,
                TextToSpeech.QUEUE_FLUSH,
                params,
                JavaString(
                    f"lesson_alert_{int(time.time() * 1000)}"
                ),
            )

            print(
                f"TTS: Android speak() returned {result}"
            )

            if result == TextToSpeech.SUCCESS:
                self.error = None
                return True

            self.error = (
                f"Android TTS speak returned error code "
                f"{result}"
            )

            return False

        except Exception as e:
            self.error = str(e)

            print(
                f"TTS: speak() exception: {e}"
            )

            return False

    def stop(self):
        """
        Stop current speech.
        """

        try:
            if self._tts is not None:
                self._tts.stop()

        except Exception as e:
            print(
                f"TTS: stop error: {e}"
            )

    def shutdown(self):
        """
        Release Android TTS resources.
        """

        try:
            if self._tts is not None:
                self._tts.stop()
                self._tts.shutdown()

        except Exception as e:
            print(
                f"TTS: shutdown error: {e}"
            )

        finally:
            self._tts = None
            self.ready = False
