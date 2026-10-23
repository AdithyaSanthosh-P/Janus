package com.januscompanion

import android.content.Intent
import android.provider.AlarmClock
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.uimanager.ViewManager

/**
 * Phone actions for Janus (src/services/phoneActions.ts): an alarm or a timer,
 * handed to the phone's clock app with Android's AlarmClock intents. With
 * EXTRA_SKIP_UI the clock app sets it without opening a screen, so Janus stays
 * in front and keeps listening. Resolves once the clock app has the intent;
 * rejects when no clock app handles it.
 */
class JanusPhoneModule(private val context: ReactApplicationContext) : ReactContextBaseJavaModule(context) {
  override fun getName(): String = "JanusPhone"

  @ReactMethod
  fun setAlarm(hour: Double, minute: Double, label: String?, promise: Promise) {
    val intent = Intent(AlarmClock.ACTION_SET_ALARM).apply {
      putExtra(AlarmClock.EXTRA_HOUR, hour.toInt())
      putExtra(AlarmClock.EXTRA_MINUTES, minute.toInt())
      putExtra(AlarmClock.EXTRA_SKIP_UI, true)
      if (!label.isNullOrBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, label)
    }
    start(intent, promise)
  }

  @ReactMethod
  fun setTimer(seconds: Double, label: String?, promise: Promise) {
    val intent = Intent(AlarmClock.ACTION_SET_TIMER).apply {
      putExtra(AlarmClock.EXTRA_LENGTH, seconds.toInt())
      putExtra(AlarmClock.EXTRA_SKIP_UI, true)
      if (!label.isNullOrBlank()) putExtra(AlarmClock.EXTRA_MESSAGE, label)
    }
    start(intent, promise)
  }

  private fun start(intent: Intent, promise: Promise) {
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    if (intent.resolveActivity(context.packageManager) == null) {
      promise.reject("no_clock_app", "no clock app on this phone takes it")
      return
    }
    try {
      context.startActivity(intent)
      promise.resolve(true)
    } catch (e: Exception) {
      promise.reject("start_failed", e.message ?: "the clock app refused it", e)
    }
  }
}

class JanusPhonePackage : ReactPackage {
  override fun createNativeModules(context: ReactApplicationContext): List<NativeModule> = listOf(JanusPhoneModule(context))

  override fun createViewManagers(context: ReactApplicationContext): List<ViewManager<*, *>> = emptyList()
}
