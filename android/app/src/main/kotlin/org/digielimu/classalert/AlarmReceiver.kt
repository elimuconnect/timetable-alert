package org.digielimu.classalert

import android.app.AlarmManager
import android.app.BroadcastReceiver
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.provider.Settings
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

class AlarmReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_PREFIX =
            "com.zaimtech.CLASS_ALERT_ALARM_"

        const val EXTRA_ALARM_ID = "alarm_id"
        const val EXTRA_NOTIFICATION_ID = "notification_id"
        const val EXTRA_NOTIFICATION_TITLE = "notification_title"
        const val EXTRA_NOTIFICATION_BODY = "notification_body"
        const val EXTRA_SPEECH_TEXT = "speech_text"
        const val EXTRA_SCHEDULED_AT_MS = "scheduled_at_ms"
        const val EXTRA_REPEAT_WEEKLY = "repeat_weekly"

        private const val CHANNEL_ID = "class_alert_alarms"
        private const val CHANNEL_NAME = "Class Alert Alarms"
        private const val TAG = "ClassAlertAlarm"
        private const val WAKELOCK_TAG = "ClassAlert:AlarmTTS"
        private const val WAKELOCK_TIME = 60_000L

        private const val WEEK_MS =
            7L * 24L * 60L * 60L * 1000L
    }

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return

        if (!action.startsWith(ACTION_PREFIX)) {
            android.util.Log.d(TAG, "Ignoring unrelated action: $action")
            return
        }

        val alarmId = intent.getIntExtra(
            EXTRA_ALARM_ID,
            action.substringAfterLast("_").toIntOrNull() ?: 0
        )

        if (alarmId <= 0) {
            android.util.Log.e(TAG, "Invalid alarm ID")
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

        val speech = intent.getStringExtra(
            EXTRA_SPEECH_TEXT
        )?.trim()?.takeIf { it.isNotEmpty() } ?: body

        val scheduledAt = intent.getLongExtra(
            EXTRA_SCHEDULED_AT_MS,
            System.currentTimeMillis()
        )

        val repeatWeekly = intent.getBooleanExtra(
            EXTRA_REPEAT_WEEKLY,
            false
        )

        android.util.Log.i(
            TAG,
            "Alarm received: id=$alarmId, title=$title, scheduledAt=$scheduledAt"
        )

        // Reschedule first so notification or TTS errors do not
        // prevent the next weekly occurrence from being created.
        if (repeatWeekly) {
            scheduleNextWeek(
                context = context,
                alarmId = alarmId,
                notificationId = notificationId,
                title = title,
                body = body,
                speech = speech,
                scheduledAt = scheduledAt
            )
        }

        showNotification(
            context = context,
            notificationId = notificationId,
            title = title,
            body = body
        )

        speak(context, speech)
    }

    // ============================================================
    // TEXT TO SPEECH
    // ============================================================

    private fun speak(context: Context, text: String) {
        val speechText = text.trim()

        if (speechText.isEmpty()) {
            android.util.Log.w(TAG, "TTS skipped: empty text")
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
                acquire(WAKELOCK_TIME)
            }
        } catch (e: Exception) {
            android.util.Log.e(TAG, "Could not acquire wake lock", e)
            null
        }

        val finished = AtomicBoolean(false)

        fun releaseWakeLock() {
            if (!finished.compareAndSet(false, true)) return

            try {
                if (wakeLock?.isHeld == true) {
                    wakeLock.release()
                }
            } catch (e: Exception) {
                android.util.Log.w(TAG, "Wake lock release warning", e)
            }
        }

        try {
            val appContext = context.applicationContext

            lateinit var tts: TextToSpeech

            tts = TextToSpeech(appContext) { status ->
                if (status != TextToSpeech.SUCCESS) {
                    android.util.Log.e(
                        TAG,
                        "TTS initialization failed: status=$status"
                    )
                    try {
                        tts.shutdown()
                    } catch (_: Exception) {
                    }
                    releaseWakeLock()
                    return@TextToSpeech
                }

                try {
                    var languageResult = tts.setLanguage(
                        Locale("en", "KE")
                    )

                    if (
                        languageResult == TextToSpeech.LANG_MISSING_DATA ||
                        languageResult == TextToSpeech.LANG_NOT_SUPPORTED
                    ) {
                        languageResult = tts.setLanguage(Locale.UK)
                    }

                    if (
                        languageResult == TextToSpeech.LANG_MISSING_DATA ||
                        languageResult == TextToSpeech.LANG_NOT_SUPPORTED
                    ) {
                        languageResult = tts.setLanguage(Locale.US)
                    }

                    if (
                        languageResult == TextToSpeech.LANG_MISSING_DATA ||
                        languageResult == TextToSpeech.LANG_NOT_SUPPORTED
                    ) {
                        android.util.Log.e(
                            TAG,
                            "No supported TTS language is available"
                        )
                        tts.shutdown()
                        releaseWakeLock()
                        return@TextToSpeech
                    }

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
                                android.util.Log.d(
                                    TAG,
                                    "TTS started: $utteranceId"
                                )
                            }

                            override fun onDone(utteranceId: String?) {
                                android.util.Log.d(
                                    TAG,
                                    "TTS finished: $utteranceId"
                                )
                                try {
                                    tts.shutdown()
                                } catch (_: Exception) {
                                }
                                releaseWakeLock()
                            }

                            @Deprecated("Deprecated in Java")
                            override fun onError(utteranceId: String?) {
                                android.util.Log.e(
                                    TAG,
                                    "TTS error: $utteranceId"
                                )
                                try {
                                    tts.shutdown()
                                } catch (_: Exception) {
                                }
                                releaseWakeLock()
                            }

                            override fun onError(
                                utteranceId: String?,
                                errorCode: Int
                            ) {
                                android.util.Log.e(
                                    TAG,
                                    "TTS error: id=$utteranceId code=$errorCode"
                                )
                                try {
                                    tts.shutdown()
                                } catch (_: Exception) {
                                }
                                releaseWakeLock()
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

                    val result = tts.speak(
                        speechText,
                        TextToSpeech.QUEUE_FLUSH,
                        params,
                        utteranceId
                    )

                    if (result != TextToSpeech.SUCCESS) {
                        android.util.Log.e(
                            TAG,
                            "tts.speak() failed: result=$result"
                        )
                        tts.shutdown()
                        releaseWakeLock()
                    }
                } catch (e: Exception) {
                    android.util.Log.e(TAG, "TTS processing failed", e)
                    try {
                        tts.shutdown()
                    } catch (_: Exception) {
                    }
                    releaseWakeLock()
                }
            }
        } catch (e: Exception) {
            android.util.Log.e(TAG, "Could not create TextToSpeech", e)
            releaseWakeLock()
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
            ) as? NotificationManager ?: return

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
                .setStyle(
                    Notification.BigTextStyle().bigText(body)
                )
                .setCategory(Notification.CATEGORY_ALARM)
                .setPriority(Notification.PRIORITY_HIGH)
                .setAutoCancel(true)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .build()

            manager.notify(notificationId, notification)

            android.util.Log.i(
                TAG,
                "Notification displayed: id=$notificationId"
            )
        } catch (e: Exception) {
            android.util.Log.e(TAG, "Notification display failed", e)
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
                android.util.Log.e(TAG, "AlarmManager unavailable")
                return
            }

            val nextTime = scheduledAt + WEEK_MS

            // If the stored schedule is old, advance to the next
            // future weekly occurrence instead of scheduling in the past.
            val now = System.currentTimeMillis()
            var nextScheduledTime = nextTime

            while (nextScheduledTime <= now) {
                nextScheduledTime += WEEK_MS
            }

            val intent = Intent(
                context,
                AlarmReceiver::class.java
            ).apply {
                action = "$ACTION_PREFIX$alarmId"
                setPackage(context.packageName)

                putExtra(EXTRA_ALARM_ID, alarmId)
                putExtra(EXTRA_NOTIFICATION_ID, notificationId)
                putExtra(EXTRA_NOTIFICATION_TITLE, title)
                putExtra(EXTRA_NOTIFICATION_BODY, body)
                putExtra(EXTRA_SPEECH_TEXT, speech)
                putExtra(
                    EXTRA_SCHEDULED_AT_MS,
                    nextScheduledTime
                )
                putExtra(EXTRA_REPEAT_WEEKLY, true)
            }

            var flags = PendingIntent.FLAG_UPDATE_CURRENT

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                flags = flags or PendingIntent.FLAG_IMMUTABLE
            }

            val pendingIntent = PendingIntent.getBroadcast(
                context,
                alarmId,
                intent,
                flags
            )

            if (
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                !alarmManager.canScheduleExactAlarms()
            ) {
                android.util.Log.e(
                    TAG,
                    "Cannot reschedule: exact alarm permission unavailable"
                )
                return
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                alarmManager.setExactAndAllowWhileIdle(
                    AlarmManager.RTC_WAKEUP,
                    nextScheduledTime,
                    pendingIntent
                )
            } else {
                alarmManager.setExact(
                    AlarmManager.RTC_WAKEUP,
                    nextScheduledTime,
                    pendingIntent
                )
            }

            android.util.Log.i(
                TAG,
                "Next weekly alarm scheduled: id=$alarmId, time=$nextScheduledTime"
            )
        } catch (e: Exception) {
            android.util.Log.e(
                TAG,
                "Could not schedule next weekly alarm",
                e
            )
        }
    }
}
