package com.example.openeartodo

import android.content.Context

object Prefs {
    private const val NAME = "openear_prefs"

    private fun prefs(context: Context) =
        context.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    fun getLlmApiKey(context: Context): String =
        prefs(context).getString("llm_api_key", "") ?: ""

    fun setLlmApiKey(context: Context, key: String) =
        prefs(context).edit().putString("llm_api_key", key).apply()

    fun getLlmProvider(context: Context): String =
        prefs(context).getString("llm_provider", "cohere") ?: "cohere"

    fun setLlmProvider(context: Context, provider: String) =
        prefs(context).edit().putString("llm_provider", provider).apply()

    fun getGmailAccount(context: Context): String? =
        prefs(context).getString("gmail_account", null)

    fun setGmailAccount(context: Context, email: String?) =
        prefs(context).edit().putString("gmail_account", email).apply()

    fun getLastSyncTime(context: Context): Long =
        prefs(context).getLong("last_sync_time", 0L)

    fun setLastSyncTime(context: Context, time: Long) =
        prefs(context).edit().putLong("last_sync_time", time).apply()

    fun getLookbackOverride(context: Context): String =
        prefs(context).getString("lookback_override", "") ?: ""

    fun setLookbackOverride(context: Context, value: String) =
        prefs(context).edit().putString("lookback_override", value).apply()
}
