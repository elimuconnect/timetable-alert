package org.digielimu.classalert

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.os.Build
import android.os.Bundle
import android.os.Handler
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
        ) ?: "Kindly teacher, your lesson is starting. Go to class"

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

        // Keep the receiver (and its process) alive until speech finishes.
        // goAsync() must be called inside onReceive.
        val pending = goAsync()
        speak(context.applicationContext, speech, pending)
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

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                alarmManager.setExactAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP,
                    nextTime,
                    pendingIntent
                )
            } else {
                alarmManager.setExact(
                    AlarmManager.RTC_WAKEUP,
                    nextTime,
                    pendingIntent
                )
            }

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
