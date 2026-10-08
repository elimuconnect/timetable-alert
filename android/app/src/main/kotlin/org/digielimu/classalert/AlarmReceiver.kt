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
import java.util.Locale

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
    }

    override fun onReceive(context: Context, intent: Intent) {

        val action = intent.action ?: return

        if (!action.startsWith(ACTION_PREFIX)) {
            return
        }

        val alarmId = intent.getIntExtra(EXTRA_ALARM_ID, 0)

        val notificationId =
            intent.getIntExtra(
                EXTRA_NOTIFICATION_ID,
                alarmId
            )

        val title =
            intent.getStringExtra(
                EXTRA_NOTIFICATION_TITLE
            ) ?: "Class Alert"

        val body =
            intent.getStringExtra(
                EXTRA_NOTIFICATION_BODY
            ) ?: "Lesson alert"

        val speech =
            intent.getStringExtra(
                EXTRA_SPEECH_TEXT
            ) ?: body

        val scheduledAt =
            intent.getLongExtra(
                EXTRA_SCHEDULED_AT_MS,
                System.currentTimeMillis()
            )

        val repeatWeekly =
            intent.getBooleanExtra(
                EXTRA_REPEAT_WEEKLY,
                false
            )

        android.util.Log.d(
            "ClassAlertAlarm",
            "ALARM RECEIVED id=$alarmId speech=$speech"
        )

        showNotification(
            context,
            notificationId,
            title,
            body
        )

        if (repeatWeekly) {
            scheduleNextWeek(
                context,
                alarmId,
                notificationId,
                title,
                body,
                speech,
                scheduledAt
            )
        }

        speak(
            context,
            speech
        )
    }

    private fun speak(
        context: Context,
        text: String
    ) {

        val wakeLockManager =
            context.getSystemService(
                Context.POWER_SERVICE
            ) as PowerManager

        val wakeLock =
            wakeLockManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "ClassAlert:AlarmTTS"
            )

        try {
            wakeLock.acquire(15_000L)
        } catch (_: Exception) {
        }

        val tts = TextToSpeech(
            context.applicationContext
        ) { status ->

            if (status != TextToSpeech.SUCCESS) {
                android.util.Log.e(
                    "ClassAlertAlarm",
                    "TTS initialization failed: $status"
                )

                try {
                    wakeLock.release()
                } catch (_: Exception) {
                }

                return@TextToSpeech
            }

            try {

                val languageResult =
                    tts.setLanguage(
                        Locale("en", "KE")
                    )

                if (
                    languageResult ==
                    TextToSpeech.LANG_MISSING_DATA ||
                    languageResult ==
                    TextToSpeech.LANG_NOT_SUPPORTED
                ) {
                    tts.setLanguage(
                        Locale.US
                    )
                }

                tts.setSpeechRate(0.95f)

                tts.setOnUtteranceProgressListener(
                    object : UtteranceProgressListener() {

                        override fun onStart(
                            utteranceId: String?
                        ) {
                            android.util.Log.d(
                                "ClassAlertAlarm",
                                "TTS STARTED"
                            )
                        }

                        override fun onDone(
                            utteranceId: String?
                        ) {
                            android.util.Log.d(
                                "ClassAlertAlarm",
                                "TTS DONE"
                            )

                            try {
                                tts.shutdown()
                            } catch (_: Exception) {
                            }

                            try {
                                wakeLock.release()
                            } catch (_: Exception) {
                            }
                        }

                        override fun onError(
                            utteranceId: String?
                        ) {
                            android.util.Log.e(
                                "ClassAlertAlarm",
                                "TTS ERROR"
                            )

                            try {
                                tts.shutdown()
                            } catch (_: Exception) {
                            }

                            try {
                                wakeLock.release()
                            } catch (_: Exception) {
                            }
                        }

                        override fun onError(
                            utteranceId: String?,
                            errorCode: Int
                        ) {
                            android.util.Log.e(
                                "ClassAlertAlarm",
                                "TTS ERROR code=$errorCode"
                            )

                            try {
                                tts.shutdown()
                            } catch (_: Exception) {
                            }

                            try {
                                wakeLock.release()
                            } catch (_: Exception) {
                            }
                        }
                    }
                )

                val params = Bundle()

                val result =
                    tts.speak(
                        text,
                        TextToSpeech.QUEUE_FLUSH,
                        params,
                        "class_alert_${System.currentTimeMillis()}"
                    )

                android.util.Log.d(
                    "ClassAlertAlarm",
                    "TTS speak result=$result"
                )

                if (
                    result != TextToSpeech.SUCCESS
                ) {
                    tts.shutdown()

                    try {
                        wakeLock.release()
                    } catch (_: Exception) {
                    }
                }

            } catch (e: Exception) {

                android.util.Log.e(
                    "ClassAlertAlarm",
                    "TTS exception",
                    e
                )

                try {
                    tts.shutdown()
                } catch (_: Exception) {
                }

                try {
                    wakeLock.release()
                } catch (_: Exception) {
                }
            }
        }
    }

    private fun showNotification(
        context: Context,
        notificationId: Int,
        title: String,
        body: String
    ) {

        val manager =
            context.getSystemService(
                Context.NOTIFICATION_SERVICE
            ) as NotificationManager

        if (Build.VERSION.SDK_INT >= 26) {

            val channel =
                NotificationChannel(
                    CHANNEL_ID,
                    CHANNEL_NAME,
                    NotificationManager.IMPORTANCE_HIGH
                )

            channel.description =
                "Class timetable lesson alerts"

            channel.enableVibration(true)

            channel.setSound(
                android.provider.Settings.System.DEFAULT_NOTIFICATION_URI,
                AudioAttributes.Builder()
                    .setUsage(
                        AudioAttributes.USAGE_NOTIFICATION
                    )
                    .build()
            )

            manager.createNotificationChannel(
                channel
            )
        }

        val notification =
            if (Build.VERSION.SDK_INT >= 26) {

                Notification.Builder(
                    context,
                    CHANNEL_ID
                )
                    .setSmallIcon(
                        android.R.drawable.ic_dialog_info
                    )
                    .setContentTitle(title)
                    .setContentText(body)
                    .setStyle(
                        Notification.BigTextStyle()
                            .bigText(body)
                    )
                    .setPriority(
                        Notification.PRIORITY_HIGH
                    )
                    .setAutoCancel(true)
                    .build()

            } else {

                Notification.Builder(context)
                    .setSmallIcon(
                        android.R.drawable.ic_dialog_info
                    )
                    .setContentTitle(title)
                    .setContentText(body)
                    .setPriority(
                        Notification.PRIORITY_HIGH
                    )
                    .setAutoCancel(true)
                    .build()
            }

        try {
            manager.notify(
                notificationId,
                notification
            )
        } catch (e: Exception) {
            android.util.Log.e(
                "ClassAlertAlarm",
                "Notification error",
                e
            )
        }
    }

    private fun scheduleNextWeek(
        context: Context,
        alarmId: Int,
        notificationId: Int,
        title: String,
        body: String,
        speech: String,
        scheduledAt: Long
    ) {

        val nextTime =
            scheduledAt +
                    (7L * 24L * 60L * 60L * 1000L)

        val alarmManager =
            context.getSystemService(
                Context.ALARM_SERVICE
            ) as AlarmManager

        val action =
            ACTION_PREFIX + alarmId

        val intent =
            Intent(
                context,
                AlarmReceiver::class.java
            ).apply {

                this.action = action

                putExtra(
                    EXTRA_ALARM_ID,
                    alarmId
                )

                putExtra(
                    EXTRA_NOTIFICATION_ID,
                    notificationId
                )

                putExtra(
                    EXTRA_NOTIFICATION_TITLE,
                    title
                )

                putExtra(
                    EXTRA_NOTIFICATION_BODY,
                    body
                )

                putExtra(
                    EXTRA_SPEECH_TEXT,
                    speech
                )

                putExtra(
                    EXTRA_SCHEDULED_AT_MS,
                    nextTime
                )

                putExtra(
                    EXTRA_REPEAT_WEEKLY,
                    true
                )
            }

        val flags =
            PendingIntent.FLAG_UPDATE_CURRENT or
                    PendingIntent.FLAG_IMMUTABLE

        val pendingIntent =
            PendingIntent.getBroadcast(
                context,
                alarmId,
                intent,
                flags
            )

        try {

            alarmManager.setExactAndAllowWhileIdle(
                AlarmManager.RTC_WAKEUP,
                nextTime,
                pendingIntent
            )

            android.util.Log.d(
                "ClassAlertAlarm",
                "NEXT WEEK scheduled: $nextTime"
            )

        } catch (e: Exception) {

            android.util.Log.e(
                "ClassAlertAlarm",
                "Could not schedule next week",
                e
            )
        }
    }
}
