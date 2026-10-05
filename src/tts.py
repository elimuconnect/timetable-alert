from jnius import autoclass, PythonJavaClass, java_method
import time

Locale = autoclass("java.util.Locale")
TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
ActivityThread = autoclass("android.app.ActivityThread")
Bundle = autoclass("android.os.Bundle")


class InitListener(PythonJavaClass):
    __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
    __javacontext__ = "app"

    def __init__(self):
        super().__init__()
        self.ready = False
        self.status = None

    @java_method("(I)V")
    def onInit(self, status):
        self.status = status
        self.ready = (status == TextToSpeech.SUCCESS)


class TTS:
    def __init__(self):
        # In Flet there's no PythonActivity (that's Kivy/p4a),
        # so grab the application context this way:
        context = ActivityThread.currentApplication().getApplicationContext()
        self._listener = InitListener()          # keep a reference, or it gets garbage collected
        self._tts = TextToSpeech(context, self._listener)

    def wait_ready(self, timeout=5):
        t = time.time()
        while not self._listener.ready and time.time() - t < timeout:
            time.sleep(0.1)
        if self._listener.ready:
            self._tts.setLanguage(Locale.US)
        return self._listener.ready

    def speak(self, text):
        if self._listener.ready:
            # QUEUE_FLUSH = 0, QUEUE_ADD = 1
            self._tts.speak(text, TextToSpeech.QUEUE_FLUSH, None, "lesson_alert")

    def shutdown(self):
        self._tts.stop()
        self._tts.shutdown()

