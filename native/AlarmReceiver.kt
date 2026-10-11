package org.digielimu.classalert

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

class AlarmReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_PREFIX = "com.zaimtech.CLASS_ALERT_ALARM_"

        const val EXTRA_ALARM_ID = "alarm_id"
        const val EXTRA_NOTIFICATION_ID = "notification_id"
        const val EXTRA_NOTIFICATION_TITLE = "notification_title"
        const val EXTRA_NOTIFICATION_BODY = "notification_body"
        const val EXTRA_SPEECH_TEXT = "speech_text"
        const val EXTRA_SCHEDULED_AT_MS = "scheduled_at_ms"
        const val EXTRA_REPEAT_WEEKLY = "repeat_weekly"

        private const val TAG = "ClassAlertAlarm"
        private const val CHANNEL_ID = "class_alert_alarms"
        private const val CHANNEL_NAME = "Class Alert Alarms"
        private const val WAKELOCK_TAG = "ClassAlert:AlarmTTS"
        private const val WAKELOCK_TIME_MS = 60_000L
        private const val TTS_TIMEOUT_MS = 15_000L
        private const val WEEK_MS = 7L * 24L * 60L * 60L * 1000L
    }

    private fun localTime(timestamp: Long): String {
        return try {
            SimpleDateFormat(
                "yyyy-MM-dd HH:mm:ss",
                Locale.getDefault()
            ).format(Date(timestamp))
        } catch (e: Exception) {
            "unavailable"
        }
    }

    // The scheduled time is sent as a String from Python (Pyjnius can
    // mis-type large numbers) and as a String from scheduleNextWeek().
    // A Long extra is still accepted as a fallback.
    private fun readScheduledAt(intent: Intent): Long {
        val asString = intent.getStringExtra(EXTRA_SCHEDULED_AT_MS)
        val parsed = asString?.trim()?.toLongOrNull()
        if (parsed != null && parsed > 0L) return parsed
        return intent.getLongExtra(EXTRA_SCHEDULED_AT_MS, 0L)
    }

    override fun onReceive(context: Context, intent: Intent) {
        NativeLog.init(context)
        val action = intent.action

        NativeLog.i(
            TAG,
            "ALARM_RECEIVER_ENTERED: action=$action, " +
                "deviceTime=${localTime(System.currentTimeMillis())}"
        )

        if (action == null) {
            NativeLog.e(TAG, "ALARM_ERROR: Intent action is null")
            return
        }

        if (!action.startsWith(ACTION_PREFIX)) {
            NativeLog.w(TAG, "ALARM_IGNORED: Unexpected action=$action")
            return
        }

        val alarmId = intent.getIntExtra(
            EXTRA_ALARM_ID,
            action.substringAfterLast("_").toIntOrNull() ?: 0
        )

        if (alarmId <= 0) {
            NativeLog.e(TAG, "ALARM_ERROR: Invalid alarm ID")
            return
        }

        val notificationId = intent.getIntExtra(
            EXTRA_NOTIFICATION_ID,
            alarmId
        )

        val title = intent.getStringExtra(
            EXTRA_NOTIFICATION_TITLE
        ) ?: "Class Alert"

        val body = intent.getStringExtra(
            EXTRA_NOTIFICATION_BODY
        ) ?: "Your lesson is starting."

        val suppliedSpeech = intent.getStringExtra(EXTRA_SPEECH_TEXT)
        val speech = suppliedSpeech?.trim()?.ifEmpty { null } ?: body

        val scheduledAt = readScheduledAt(intent)

        val repeatWeekly = intent.getBooleanExtra(
            EXTRA_REPEAT_WEEKLY,
            false
        )

        NativeLog.i(TAG, "ALARM_TRIGGERED: id=$alarmId")
        NativeLog.i(TAG, "ALARM_ACTION: $action")
        NativeLog.i(TAG, "ALARM_SCHEDULED_TIME: ${localTime(scheduledAt)}")
        NativeLog.i(TAG, "ALARM_CURRENT_TIME: ${localTime(System.currentTimeMillis())}")
        NativeLog.i(TAG, "ALARM_NOTIFICATION_TITLE: $title")
        NativeLog.i(TAG, "ALARM_NOTIFICATION_BODY: $body")
        NativeLog.i(
            TAG,
            "ALARM_SPEECH_EXTRA: ${suppliedSpeech ?: "<MISSING>"}"
        )
        NativeLog.i(TAG, "ANNOUNCEMENT_TEXT: $speech")
        NativeLog.i(TAG, "ALARM_REPEAT_WEEKLY: $repeatWeekly")

        if (suppliedSpeech.isNullOrBlank()) {
            NativeLog.w(
                TAG,
                "SPEECH_WARNING: speech_text missing; using notification body"
            )
        }

        if (repeatWeekly) {
            scheduleNextWeek(
                context = context.applicationContext,
                alarmId = alarmId,
                notificationId = notificationId,
                title = title,
                body = body,
                speech = speech,
                scheduledAt = if (scheduledAt > 0L) {
                    scheduledAt
                } else {
                    System.currentTimeMillis()
                }
            )
        }

        showNotification(
            context = context,
            notificationId = notificationId,
            title = title,
            body = body
        )

        NativeLog.i(TAG, "TTS_REQUESTED: alarmId=$alarmId, text=$speech")

        // Preferred path: speak from a foreground service so audio keeps
        // working while the phone is locked or dozing.
        val serviceStarted = startSpeechService(
            context.applicationContext,
            alarmId,
            speech
        )

        if (!serviceStarted) {
            // Fallback: speak inside the receiver. goAsync() keeps the
            // receiver (and its process) alive until speech finishes.
            NativeLog.w(TAG, "SPEECH_FALLBACK: speaking inside receiver")
            val pending = goAsync()
            speak(context.applicationContext, speech, pending)
        }
    }

    private fun startSpeechService(
        context: Context,
        alarmId: Int,
        speech: String
    ): Boolean {
        return try {
            val serviceIntent = Intent(context, SpeechService::class.java).apply {
                putExtra(EXTRA_ALARM_ID, alarmId)
                putExtra(EXTRA_SPEECH_TEXT, speech)
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(serviceIntent)
            } else {
                context.startService(serviceIntent)
            }

            NativeLog.i(TAG, "SPEECH_SERVICE_STARTED: alarmId=$alarmId")
            true
        } catch (e: Exception) {
            NativeLog.e(TAG, "SPEECH_SERVICE_START_FAILED", e)
            false
        }
    }

    // ============================================================
    // TEXT TO SPEECH
    // ============================================================

    private fun speak(
        context: Context,
        text: String,
        pending: PendingResult?
    ) {
        val speechText = text.trim()

        NativeLog.i(TAG, "TTS_TEXT_RECEIVED: $speechText")

        if (speechText.isEmpty()) {
            NativeLog.e(TAG, "TTS_SKIPPED: speech text is empty")
            try {
                pending?.finish()
            } catch (e: Exception) {
                NativeLog.w(TAG, "PendingResult finish failed", e)
            }
            return
        }

        val powerManager = context.getSystemService(
            Context.POWER_SERVICE
        ) as? PowerManager

        val wakeLock = try {
            powerManager?.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                WAKELOCK_TAG
            )?.apply {
                setReferenceCounted(false)
                acquire(WAKELOCK_TIME_MS)
            }
        } catch (e: Exception) {
            NativeLog.e(TAG, "TTS_WAKELOCK_ERROR", e)
            null
        }

        val finished = AtomicBoolean(false)

        fun finish(tts: TextToSpeech?) {
            if (!finished.compareAndSet(false, true)) return

            try {
                tts?.stop()
            } catch (e: Exception) {
                NativeLog.w(TAG, "TTS stop failed", e)
            }

            try {
                tts?.shutdown()
            } catch (e: Exception) {
                NativeLog.w(TAG, "TTS shutdown failed", e)
            }

            try {
                if (wakeLock?.isHeld == true) {
                    wakeLock.release()
                }
            } catch (e: Exception) {
                NativeLog.w(TAG, "Wake lock release failed", e)
            }

            try {
                pending?.finish()
            } catch (e: Exception) {
                NativeLog.w(TAG, "PendingResult finish failed", e)
            }

            NativeLog.i(TAG, "TTS_FINISHED_CLEANUP")
        }

        try {
            var engine: TextToSpeech? = null

            engine = TextToSpeech(context) { status ->
                val tts = engine

                if (tts == null) {
                    NativeLog.e(TAG, "TTS_INIT_ERROR: engine unavailable in callback")
                    finish(null)
                    return@TextToSpeech
                }

                if (status != TextToSpeech.SUCCESS) {
                    NativeLog.e(TAG, "TTS_INIT_ERROR: status=$status")
                    finish(tts)
                    return@TextToSpeech
                }

                NativeLog.i(TAG, "TTS_INITIALIZED: status=$status")

                try {
                    var languageResult = tts.setLanguage(Locale("en", "KE"))
                    var selectedLanguage = "en-KE"

                    if (
                        languageResult == TextToSpeech.LANG_MISSING_DATA ||
                        languageResult == TextToSpeech.LANG_NOT_SUPPORTED
                    ) {
                        languageResult = tts.setLanguage(Locale.UK)
                        selectedLanguage = "en-GB"
                    }

                    if (
                        languageResult == TextToSpeech.LANG_MISSING_DATA ||
                        languageResult == TextToSpeech.LANG_NOT_SUPPORTED
                    ) {
                        languageResult = tts.setLanguage(Locale.US)
                        selectedLanguage = "en-US"
                    }

                    if (
                        languageResult == TextToSpeech.LANG_MISSING_DATA ||
                        languageResult == TextToSpeech.LANG_NOT_SUPPORTED
                    ) {
                        NativeLog.e(TAG, "TTS_LANGUAGE_ERROR: no supported language")
                        finish(tts)
                        return@TextToSpeech
                    }

                    NativeLog.i(
                        TAG,
                        "TTS_LANGUAGE_SELECTED: $selectedLanguage, result=$languageResult"
                    )

                    tts.setSpeechRate(0.95f)
                    tts.setPitch(1.0f)

                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                        tts.setAudioAttributes(
                            AudioAttributes.Builder()
                                .setUsage(AudioAttributes.USAGE_ALARM)
                                .setContentType(
                                    AudioAttributes.CONTENT_TYPE_SPEECH
                                )
                                .build()
                        )
                    }

                    tts.setOnUtteranceProgressListener(
                        object : UtteranceProgressListener() {
                            override fun onStart(utteranceId: String?) {
                                NativeLog.i(
                                    TAG,
                                    "TTS_STARTED: id=$utteranceId, text=$speechText"
                                )
                            }

                            override fun onDone(utteranceId: String?) {
                                NativeLog.i(
                                    TAG,
                                    "TTS_COMPLETED: id=$utteranceId"
                                )
                                finish(tts)
                            }

                            @Deprecated("Deprecated in Java")
                            override fun onError(utteranceId: String?) {
                                NativeLog.e(TAG, "TTS_ERROR: id=$utteranceId")
                                finish(tts)
                            }

                            override fun onError(
                                utteranceId: String?,
                                errorCode: Int
                            ) {
                                NativeLog.e(
                                    TAG,
                                    "TTS_ERROR: id=$utteranceId, code=$errorCode"
                                )
                                finish(tts)
                            }
                        }
                    )

                    val utteranceId =
                        "class_alert_${System.currentTimeMillis()}"

                    val params = Bundle().apply {
                        putString(
                            TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID,
                            utteranceId
                        )
                    }

                    NativeLog.i(
                        TAG,
                        "TTS_SPEAK_CALL: id=$utteranceId, text=$speechText"
                    )

                    val result = tts.speak(
                        speechText,
                        TextToSpeech.QUEUE_FLUSH,
                        params,
                        utteranceId
                    )

                    NativeLog.i(TAG, "TTS_SPEAK_RESULT: $result")

                    if (result == TextToSpeech.ERROR) {
                        NativeLog.e(TAG, "TTS_SPEAK_ERROR: speak returned ERROR")
                        finish(tts)
                    }
                } catch (e: Exception) {
                    NativeLog.e(TAG, "TTS_PROCESSING_ERROR", e)
                    finish(tts)
                }
            }

            // Safety net: never leave the receiver / wake lock hanging if
            // TTS init or speech never completes.
            val timeoutEngine = engine
            Handler(Looper.getMainLooper()).postDelayed({
                if (!finished.get()) {
                    NativeLog.w(TAG, "TTS_TIMEOUT: forcing cleanup")
                    finish(timeoutEngine)
                }
            }, TTS_TIMEOUT_MS)

        } catch (e: Exception) {
            NativeLog.e(TAG, "TTS_CREATE_ERROR", e)
            finish(null)
        }
    }

    // ============================================================
    // NOTIFICATION
    // ============================================================

    private fun showNotification(
        context: Context,
        notificationId: Int,
        title: String,
        body: String
    ) {
        try {
            val manager = context.getSystemService(
                Context.NOTIFICATION_SERVICE
            ) as? NotificationManager ?: run {
                NativeLog.e(TAG, "NOTIFICATION_ERROR: manager unavailable")
                return
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    CHANNEL_NAME,
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = "Smart timetable lesson alerts"
                    enableVibration(true)

                    setSound(
                        Settings.System.DEFAULT_ALARM_ALERT_URI,
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ALARM)
                            .setContentType(
                                AudioAttributes.CONTENT_TYPE_SONIFICATION
                            )
                            .build()
                    )
                }

                manager.createNotificationChannel(channel)
            }

            val builder = if (
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            ) {
                Notification.Builder(context, CHANNEL_ID)
            } else {
                @Suppress("DEPRECATION")
                Notification.Builder(context)
            }

            val notification = builder
                .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(Notification.BigTextStyle().bigText(body))
                .setCategory(Notification.CATEGORY_ALARM)
                .setPriority(Notification.PRIORITY_HIGH)
                .setAutoCancel(true)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .build()

            manager.notify(notificationId, notification)

            NativeLog.i(
                TAG,
                "NOTIFICATION_DISPLAYED: id=$notificationId, title=$title, body=$body"
            )
        } catch (e: Exception) {
            NativeLog.e(TAG, "NOTIFICATION_DISPLAY_ERROR", e)
        }
    }

    // ============================================================
    // SCHEDULE NEXT WEEK
    // ============================================================

    private fun scheduleNextWeek(
        context: Context,
        alarmId: Int,
        notificationId: Int,
        title: String,
        body: String,
        speech: String,
        scheduledAt: Long
    ) {
        try {
            val alarmManager = context.getSystemService(
                Context.ALARM_SERVICE
            ) as? AlarmManager ?: run {
                NativeLog.e(TAG, "WEEKLY_RESCHEDULE_ERROR: AlarmManager unavailable")
                return
            }

            var nextTime = scheduledAt + WEEK_MS
            val now = System.currentTimeMillis()

            while (nextTime <= now) {
                nextTime += WEEK_MS
            }

            val nextIntent = Intent(
                context,
                AlarmReceiver::class.java
            ).apply {
                action = "$ACTION_PREFIX$alarmId"

                putExtra(EXTRA_ALARM_ID, alarmId)
                putExtra(EXTRA_NOTIFICATION_ID, notificationId)
                putExtra(EXTRA_NOTIFICATION_TITLE, title)
                putExtra(EXTRA_NOTIFICATION_BODY, body)
                putExtra(EXTRA_SPEECH_TEXT, speech)
                // String, to match what Python sends
                putExtra(EXTRA_SCHEDULED_AT_MS, nextTime.toString())
                putExtra(EXTRA_REPEAT_WEEKLY, true)
            }

            var flags = PendingIntent.FLAG_UPDATE_CURRENT

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                flags = flags or PendingIntent.FLAG_IMMUTABLE
            }

            val pendingIntent = PendingIntent.getBroadcast(
                context,
                alarmId,
                nextIntent,
                flags
            )

            if (
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                !alarmManager.canScheduleExactAlarms()
            ) {
                NativeLog.e(
                    TAG,
                    "WEEKLY_RESCHEDULE_ERROR: exact-alarm permission unavailable"
                )
                return
            }

            // setAlarmClock is treated by Android as a real alarm clock:
            // it fires reliably in Doze and when the phone is locked.
            val showIntent = context.packageManager
                .getLaunchIntentForPackage(context.packageName)
                ?.let { launch ->
                    launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    PendingIntent.getActivity(context, 0, launch, flags)
                }

            alarmManager.setAlarmClock(
                AlarmManager.AlarmClockInfo(nextTime, showIntent),
                pendingIntent
            )

            NativeLog.i(
                TAG,
                "WEEKLY_ALARM_SCHEDULED: id=$alarmId, " +
                    "nextTime=${localTime(nextTime)}"
            )
        } catch (e: Exception) {
            NativeLog.e(TAG, "WEEKLY_RESCHEDULE_ERROR", e)
        }
    }
}

// ================================================================
// SPEECH SERVICE: foreground service that speaks the lesson message.
// Foreground status keeps the process and the TTS engine running while
// the phone is locked, and lets audio play from the background.
// ================================================================

class SpeechService : Service() {

    companion object {
        private const val TAG = "ClassAlertSpeech"
        private const val CHANNEL_ID = "class_alert_speech"
        private const val NOTIFICATION_ID = 7001
        private const val TIMEOUT_MS = 25_000L
        private const val WAKELOCK_MS = 60_000L
    }

    private val handler = Handler(Looper.getMainLooper())
    private val finished = AtomicBoolean(false)
    private var tts: TextToSpeech? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var focusRequest: AudioFocusRequest? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        NativeLog.init(this)
        enterForeground()

        val text = intent
            ?.getStringExtra(AlarmReceiver.EXTRA_SPEECH_TEXT)
            ?.trim()
            .orEmpty()

        NativeLog.i(TAG, "SVC_STARTED: text=$text")

        // A new alarm can arrive while a previous one is still speaking.
        releaseAll()
        finished.set(false)

        if (text.isEmpty()) {
            NativeLog.e(TAG, "SVC_SKIPPED: speech text is empty")
            finish()
            return START_NOT_STICKY
        }

        acquireWakeLock()
        requestFocus()
        startSpeaking(text)

        handler.postDelayed({
            if (!finished.get()) {
                NativeLog.w(TAG, "SVC_TIMEOUT: forcing stop")
                finish()
            }
        }, TIMEOUT_MS)

        return START_NOT_STICKY
    }

    override fun onDestroy() {
        releaseAll()
        super.onDestroy()
    }

    private fun enterForeground() {
        try {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE)
                as NotificationManager

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                manager.createNotificationChannel(
                    NotificationChannel(
                        CHANNEL_ID,
                        "Class Alert voice",
                        NotificationManager.IMPORTANCE_LOW
                    )
                )
            }

            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Notification.Builder(this, CHANNEL_ID)
            } else {
                @Suppress("DEPRECATION")
                Notification.Builder(this)
            }

            val notification = builder
                .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
                .setContentTitle("Class Alert")
                .setContentText("Speaking lesson reminder")
                .setOngoing(true)
                .build()

            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
                )
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }

            NativeLog.i(TAG, "SVC_FOREGROUND_ENTERED")
        } catch (e: Exception) {
            NativeLog.e(TAG, "SVC_FOREGROUND_ERROR", e)
        }
    }

    private fun acquireWakeLock() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "ClassAlert:SpeechService"
            ).apply {
                setReferenceCounted(false)
                acquire(WAKELOCK_MS)
            }
        } catch (e: Exception) {
            NativeLog.e(TAG, "SVC_WAKELOCK_ERROR", e)
        }
    }

    private fun requestFocus() {
        try {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return

            val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
            val attributes = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build()

            val request = AudioFocusRequest.Builder(
                AudioManager.AUDIOFOCUS_GAIN_TRANSIENT
            )
                .setAudioAttributes(attributes)
                .setOnAudioFocusChangeListener { }
                .build()

            focusRequest = request
            val result = audioManager.requestAudioFocus(request)
            NativeLog.i(TAG, "SVC_AUDIO_FOCUS_RESULT: $result")
        } catch (e: Exception) {
            NativeLog.e(TAG, "SVC_AUDIO_FOCUS_ERROR", e)
        }
    }

    private fun pickLanguage(engine: TextToSpeech): String? {
        val candidates = listOf(
            Locale("en", "KE") to "en-KE",
            Locale.UK to "en-GB",
            Locale.US to "en-US"
        )

        for ((locale, label) in candidates) {
            val result = engine.setLanguage(locale)
            if (
                result != TextToSpeech.LANG_MISSING_DATA &&
                result != TextToSpeech.LANG_NOT_SUPPORTED
            ) {
                return label
            }
        }
        return null
    }

    private fun startSpeaking(text: String) {
        try {
            var engine: TextToSpeech? = null

            engine = TextToSpeech(applicationContext) { status ->
                val speaker = engine

                if (speaker == null || status != TextToSpeech.SUCCESS) {
                    NativeLog.e(TAG, "SVC_TTS_INIT_ERROR: status=$status")
                    finish()
                    return@TextToSpeech
                }

                NativeLog.i(TAG, "SVC_TTS_INITIALIZED")

                try {
                    val language = pickLanguage(speaker)
                    if (language == null) {
                        NativeLog.e(TAG, "SVC_TTS_LANGUAGE_ERROR: none supported")
                        finish()
                        return@TextToSpeech
                    }
                    NativeLog.i(TAG, "SVC_TTS_LANGUAGE: $language")

                    speaker.setSpeechRate(0.95f)
                    speaker.setPitch(1.0f)
                    speaker.setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ALARM)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build()
                    )

                    speaker.setOnUtteranceProgressListener(
                        object : UtteranceProgressListener() {
                            override fun onStart(utteranceId: String?) {
                                NativeLog.i(TAG, "SVC_TTS_STARTED: id=$utteranceId")
                            }

                            override fun onDone(utteranceId: String?) {
                                NativeLog.i(TAG, "SVC_TTS_COMPLETED: id=$utteranceId")
                                finish()
                            }

                            @Deprecated("Deprecated in Java")
                            override fun onError(utteranceId: String?) {
                                NativeLog.e(TAG, "SVC_TTS_ERROR: id=$utteranceId")
                                finish()
                            }

                            override fun onError(utteranceId: String?, errorCode: Int) {
                                NativeLog.e(
                                    TAG,
                                    "SVC_TTS_ERROR: id=$utteranceId, code=$errorCode"
                                )
                                finish()
                            }
                        }
                    )

                    val utteranceId = "class_alert_svc_${System.currentTimeMillis()}"
                    val params = Bundle().apply {
                        putString(
                            TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID,
                            utteranceId
                        )
                    }

                    val result = speaker.speak(
                        text,
                        TextToSpeech.QUEUE_FLUSH,
                        params,
                        utteranceId
                    )
                    NativeLog.i(TAG, "SVC_TTS_SPEAK_RESULT: $result")

                    if (result == TextToSpeech.ERROR) {
                        finish()
                    }
                } catch (e: Exception) {
                    NativeLog.e(TAG, "SVC_TTS_PROCESSING_ERROR", e)
                    finish()
                }
            }

            tts = engine
        } catch (e: Exception) {
            NativeLog.e(TAG, "SVC_TTS_CREATE_ERROR", e)
            finish()
        }
    }

    private fun releaseAll() {
        handler.removeCallbacksAndMessages(null)

        try {
            tts?.stop()
        } catch (e: Exception) {
            NativeLog.w(TAG, "SVC_TTS_STOP_FAILED", e)
        }
        try {
            tts?.shutdown()
        } catch (e: Exception) {
            NativeLog.w(TAG, "SVC_TTS_SHUTDOWN_FAILED", e)
        }
        tts = null

        try {
            val request = focusRequest
            if (request != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
                audioManager.abandonAudioFocusRequest(request)
            }
        } catch (e: Exception) {
            NativeLog.w(TAG, "SVC_FOCUS_ABANDON_FAILED", e)
        }
        focusRequest = null

        try {
            if (wakeLock?.isHeld == true) {
                wakeLock?.release()
            }
        } catch (e: Exception) {
            NativeLog.w(TAG, "SVC_WAKELOCK_RELEASE_FAILED", e)
        }
        wakeLock = null
    }

    private fun finish() {
        if (!finished.compareAndSet(false, true)) return

        releaseAll()

        try {
            stopForeground(Service.STOP_FOREGROUND_REMOVE)
        } catch (e: Exception) {
            NativeLog.w(TAG, "SVC_STOP_FOREGROUND_FAILED", e)
        }
        stopSelf()
        NativeLog.i(TAG, "SVC_FINISHED")
    }
}

// ================================================================
// NATIVE LOG: writes to logcat AND to files/native_alarm.log so the
// Python side can show it inside the app (no adb needed).
// ================================================================

object NativeLog {
    private const val FILE_NAME = "native_alarm.log"
    private const val MAX_BYTES = 100_000L
    private val lock = Any()

    @Volatile
    private var logFile: File? = null

    fun init(context: Context) {
        try {
            logFile = File(context.applicationContext.filesDir, FILE_NAME)
        } catch (e: Exception) {
            Log.e("ClassAlertAlarm", "NativeLog init failed", e)
        }
    }

    private fun write(level: String, message: String, tr: Throwable?) {
        val file = logFile ?: return
        try {
            synchronized(lock) {
                if (file.exists() && file.length() > MAX_BYTES) {
                    file.delete()
                }
                val stamp = SimpleDateFormat(
                    "yyyy-MM-dd HH:mm:ss.SSS",
                    Locale.getDefault()
                ).format(Date())
                val extra = if (tr != null) " | ${tr.javaClass.simpleName}: ${tr.message}" else ""
                file.appendText("$stamp $level $message$extra\n")
            }
        } catch (e: Exception) {
            Log.e("ClassAlertAlarm", "NativeLog write failed", e)
        }
    }

    fun i(tag: String, message: String) {
        Log.i(tag, message)
        write("I", message, null)
    }

    fun w(tag: String, message: String, tr: Throwable? = null) {
        if (tr != null) Log.w(tag, message, tr) else Log.w(tag, message)
        write("W", message, tr)
    }

    fun e(tag: String, message: String, tr: Throwable? = null) {
        if (tr != null) Log.e(tag, message, tr) else Log.e(tag, message)
        write("E", message, tr)
    }
}
