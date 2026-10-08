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
import android.media.AudioManager
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

        const val EXTRA_ALARM_ID =
            "alarm_id"

        const val EXTRA_NOTIFICATION_ID =
            "notification_id"

        const val EXTRA_NOTIFICATION_TITLE =
            "notification_title"

        const val EXTRA_NOTIFICATION_BODY =
            "notification_body"

        const val EXTRA_SPEECH_TEXT =
            "speech_text"

        const val EXTRA_SCHEDULED_AT_MS =
            "scheduled_at_ms"

        const val EXTRA_REPEAT_WEEKLY =
            "repeat_weekly"

        private const val CHANNEL_ID =
            "class_alert_alarms"

        private const val CHANNEL_NAME =
            "Class Alert Alarms"

        private const val TAG =
            "ClassAlertAlarm"

        private const val WAKELOCK_TAG =
            "ClassAlert:AlarmTTS"

        private const val WAKELOCK_TIME =
            30_000L
    }


    // ============================================================
    // ALARM RECEIVED
    // ============================================================

    override fun onReceive(
        context: Context,
        intent: Intent
    ) {

        val action =
            intent.action ?: return

        if (!action.startsWith(ACTION_PREFIX)) {

            android.util.Log.d(
                TAG,
                "Ignoring unrelated action: $action"
            )

            return
        }


        // --------------------------------------------------------
        // RECOVER ALARM ID
        // --------------------------------------------------------

        var alarmId =
            intent.getIntExtra(
                EXTRA_ALARM_ID,
                0
            )

        if (alarmId == 0) {

            try {

                alarmId =
                    action
                        .substringAfterLast("_")
                        .toInt()

            } catch (_: Exception) {
            }
        }


        // --------------------------------------------------------
        // READ DATA
        // --------------------------------------------------------

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
            ) ?: "Your lesson is starting."

        val speech =
            intent.getStringExtra(
                EXTRA_SPEECH_TEXT
            )
                ?.trim()
                ?.takeIf { it.isNotEmpty() }
                ?: body

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
            TAG,
            "================================================"
        )

        android.util.Log.d(
            TAG,
            "ALARM RECEIVED"
        )

        android.util.Log.d(
            TAG,
            "id=$alarmId"
        )

        android.util.Log.d(
            TAG,
            "notificationId=$notificationId"
        )

        android.util.Log.d(
            TAG,
            "title=$title"
        )

        android.util.Log.d(
            TAG,
            "body=$body"
        )

        android.util.Log.d(
            TAG,
            "speech=$speech"
        )

        android.util.Log.d(
            TAG,
            "scheduledAt=$scheduledAt"
        )

        android.util.Log.d(
            TAG,
            "repeatWeekly=$repeatWeekly"
        )

        android.util.Log.d(
            TAG,
            "================================================"
        )


        // --------------------------------------------------------
        // SHOW NOTIFICATION IMMEDIATELY
        // --------------------------------------------------------

        showNotification(
            context,
            notificationId,
            title,
            body
        )


        // --------------------------------------------------------
        // WEEKLY RESCHEDULE
        // --------------------------------------------------------

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


        // --------------------------------------------------------
        // SPEAK
        // --------------------------------------------------------

        speak(
            context,
            speech
        )
    }


    // ============================================================
    // TEXT TO SPEECH
    // ============================================================

    private fun speak(
        context: Context,
        text: String
    ) {

        val speechText =
            text.trim()

        if (speechText.isEmpty()) {

            android.util.Log.e(
                TAG,
                "TTS aborted: empty text"
            )

            return
        }


        android.util.Log.d(
            TAG,
            "Starting TTS: $speechText"
        )


        // --------------------------------------------------------
        // WAKE LOCK
        // --------------------------------------------------------

        val powerManager =
            context.getSystemService(
                Context.POWER_SERVICE
            ) as PowerManager

        val wakeLock =
            powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                WAKELOCK_TAG
            )


        try {

            wakeLock.acquire(
                WAKELOCK_TIME
            )

            android.util.Log.d(
                TAG,
                "WakeLock acquired"
            )

        } catch (e: Exception) {

            android.util.Log.e(
                TAG,
                "WakeLock acquisition failed",
                e
            )
        }


        // --------------------------------------------------------
        // AUDIO MANAGER
        // --------------------------------------------------------

        try {

            val audioManager =
                context.getSystemService(
                    Context.AUDIO_SERVICE
                ) as AudioManager

            android.util.Log.d(
                TAG,
                "Music volume=" +
                        audioManager.getStreamVolume(
                            AudioManager.STREAM_MUSIC
                        )
            )

            android.util.Log.d(
                TAG,
                "Notification volume=" +
                        audioManager.getStreamVolume(
                            AudioManager.STREAM_NOTIFICATION
                        )
            )

        } catch (e: Exception) {

            android.util.Log.e(
                TAG,
                "Could not inspect audio volume",
                e
            )
        }


        // --------------------------------------------------------
        // CREATE TTS
        // --------------------------------------------------------

        val tts =
            TextToSpeech(
                context.applicationContext
            ) { status ->


                android.util.Log.d(
                    TAG,
                    "TTS initialization status=$status"
                )


                if (
                    status !=
                    TextToSpeech.SUCCESS
                ) {

                    android.util.Log.e(
                        TAG,
                        "TTS initialization FAILED"
                    )

                    shutdownTts(
                        tts,
                        wakeLock
                    )

                    return@TextToSpeech
                }


                try {

                    // ------------------------------------------------
                    // SELECT LANGUAGE
                    // ------------------------------------------------

                    var languageResult =
                        tts.setLanguage(
                            Locale("en", "KE")
                        )


                    android.util.Log.d(
                        TAG,
                        "en-KE language result=$languageResult"
                    )


                    if (
                        languageResult ==
                        TextToSpeech.LANG_MISSING_DATA
                        ||
                        languageResult ==
                        TextToSpeech.LANG_NOT_SUPPORTED
                    ) {

                        android.util.Log.d(
                            TAG,
                            "en-KE unavailable, trying en-US"
                        )

                        languageResult =
                            tts.setLanguage(
                                Locale.US
                            )
                    }


                    if (
                        languageResult ==
                        TextToSpeech.LANG_MISSING_DATA
                        ||
                        languageResult ==
                        TextToSpeech.LANG_NOT_SUPPORTED
                    ) {

                        android.util.Log.e(
                            TAG,
                            "No supported TTS language available"
                        )

                        shutdownTts(
                            tts,
                            wakeLock
                        )

                        return@TextToSpeech
                    }


                    // ------------------------------------------------
                    // SPEECH SETTINGS
                    // ------------------------------------------------

                    tts.setSpeechRate(
                        0.95f
                    )

                    tts.setPitch(
                        1.0f
                    )


                    // ------------------------------------------------
                    // AUDIO ATTRIBUTES
                    // ------------------------------------------------

                    if (
                        Build.VERSION.SDK_INT >=
                        Build.VERSION_CODES.LOLLIPOP
                    ) {

                        tts.setAudioAttributes(
                            AudioAttributes.Builder()
                                .setUsage(
                                    AudioAttributes.USAGE_ALARM
                                )
                                .setContentType(
                                    AudioAttributes.CONTENT_TYPE_SPEECH
                                )
                                .build()
                        )
                    }


                    // ------------------------------------------------
                    // UTTERANCE CALLBACK
                    // ------------------------------------------------

                    tts.setOnUtteranceProgressListener(

                        object :
                            UtteranceProgressListener() {

                            override fun onStart(
                                utteranceId: String?
                            ) {

                                android.util.Log.d(
                                    TAG,
                                    "TTS STARTED: $utteranceId"
                                )
                            }


                            override fun onDone(
                                utteranceId: String?
                            ) {

                                android.util.Log.d(
                                    TAG,
                                    "TTS DONE: $utteranceId"
                                )

                                shutdownTts(
                                    tts,
                                    wakeLock
                                )
                            }


                            override fun onError(
                                utteranceId: String?
                            ) {

                                android.util.Log.e(
                                    TAG,
                                    "TTS ERROR: $utteranceId"
                                )

                                shutdownTts(
                                    tts,
                                    wakeLock
                                )
                            }


                            override fun onError(
                                utteranceId: String?,
                                errorCode: Int
                            ) {

                                android.util.Log.e(
                                    TAG,
                                    "TTS ERROR code=$errorCode " +
                                            "id=$utteranceId"
                                )

                                shutdownTts(
                                    tts,
                                    wakeLock
                                )
                            }
                        }
                    )


                    // ------------------------------------------------
                    // SPEAK
                    // ------------------------------------------------

                    val utteranceId =
                        "class_alert_" +
                                System.currentTimeMillis()


                    val params =
                        Bundle()


                    if (
                        Build.VERSION.SDK_INT >=
                        Build.VERSION_CODES.LOLLIPOP
                    ) {

                        params.putString(
                            TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID,
                            utteranceId
                        )
                    }


                    val result =
                        tts.speak(
                            speechText,
                            TextToSpeech.QUEUE_FLUSH,
                            params,
                            utteranceId
                        )


                    android.util.Log.d(
                        TAG,
                        "tts.speak() result=$result"
                    )


                    if (
                        result !=
                        TextToSpeech.SUCCESS
                    ) {

                        android.util.Log.e(
                            TAG,
                            "tts.speak() FAILED"
                        )

                        shutdownTts(
                            tts,
                            wakeLock
                        )
                    }

                } catch (e: Exception) {

                    android.util.Log.e(
                        TAG,
                        "TTS processing exception",
                        e
                    )

                    shutdownTts(
                        tts,
                        wakeLock
                    )
                }
            }
    }


    // ============================================================
    // SHUTDOWN TTS
    // ============================================================

    private fun shutdownTts(
        tts: TextToSpeech?,
        wakeLock: PowerManager.WakeLock
    ) {

        try {

            tts?.stop()

        } catch (_: Exception) {
        }


        try {

            tts?.shutdown()

            android.util.Log.d(
                TAG,
                "TTS shutdown"
            )

        } catch (_: Exception) {
        }


        try {

            if (wakeLock.isHeld) {

                wakeLock.release()

                android.util.Log.d(
                    TAG,
                    "WakeLock released"
                )
            }

        } catch (_: Exception) {
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

        val manager =
            context.getSystemService(
                Context.NOTIFICATION_SERVICE
            ) as NotificationManager


        // --------------------------------------------------------
        // CHANNEL
        // --------------------------------------------------------

        if (
            Build.VERSION.SDK_INT >=
            Build.VERSION_CODES.O
        ) {

            val channel =
                NotificationChannel(
                    CHANNEL_ID,
                    CHANNEL_NAME,
                    NotificationManager.IMPORTANCE_HIGH
                )


            channel.description =
                "Smart timetable lesson alerts"


            channel.enableVibration(
                true
            )


            channel.setSound(
                android.provider.Settings.System.DEFAULT_ALARM_ALERT_URI,
                AudioAttributes.Builder()
                    .setUsage(
                        AudioAttributes.USAGE_ALARM
                    )
                    .setContentType(
                        AudioAttributes.CONTENT_TYPE_SONIFICATION
                    )
                    .build()
            )


            manager.createNotificationChannel(
                channel
            )
        }


        // --------------------------------------------------------
        // NOTIFICATION
        // --------------------------------------------------------

        val notification =
            if (
                Build.VERSION.SDK_INT >=
                Build.VERSION_CODES.O
            ) {

                Notification.Builder(
                    context,
                    CHANNEL_ID
                )
                    .setSmallIcon(
                        android.R.drawable.ic_lock_idle_alarm
                    )
                    .setContentTitle(
                        title
                    )
                    .setContentText(
                        body
                    )
                    .setStyle(
                        Notification.BigTextStyle()
                            .bigText(body)
                    )
                    .setPriority(
                        Notification.PRIORITY_HIGH
                    )
                    .setCategory(
                        Notification.CATEGORY_ALARM
                    )
                    .setAutoCancel(
                        true
                    )
                    .setVisibility(
                        Notification.VISIBILITY_PUBLIC
                    )
                    .build()

            } else {

                Notification.Builder(
                    context
                )
                    .setSmallIcon(
                        android.R.drawable.ic_lock_idle_alarm
                    )
                    .setContentTitle(
                        title
                    )
                    .setContentText(
                        body
                    )
                    .setPriority(
                        Notification.PRIORITY_HIGH
                    )
                    .setCategory(
                        Notification.CATEGORY_ALARM
                    )
                    .setAutoCancel(
                        true
                    )
                    .build()
            }


        try {

            manager.notify(
                notificationId,
                notification
            )

            android.util.Log.d(
                TAG,
                "Notification displayed: $notificationId"
            )

        } catch (e: Exception) {

            android.util.Log.e(
                TAG,
                "Notification error",
                e
            )
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

        val nextTime =
            scheduledAt +
                    (
                        7L *
                                24L *
                                60L *
                                60L *
                                1000L
                    )


        val alarmManager =
            context.getSystemService(
                Context.ALARM_SERVICE
            ) as AlarmManager


        val action =
            ACTION_PREFIX +
                    alarmId


        val intent =
            Intent(
                context,
                AlarmReceiver::class.java
            ).apply {

                this.action =
                    action

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

            if (
                Build.VERSION.SDK_INT >=
                Build.VERSION_CODES.M
            ) {

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


            android.util.Log.d(
                TAG,
                "NEXT WEEK scheduled: $nextTime"
            )

        } catch (e: Exception) {

            android.util.Log.e(
                TAG,
                "Could not schedule next week",
                e
            )
        }
    }
}
