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

    fun getGmailAccounts(context: Context): Set<String> {
        val p = prefs(context)
        val set = p.getStringSet("gmail_accounts", null)
        if (set != null) return set
        // Migrate from single-account
        val legacy = p.getString("gmail_account", null)
        return if (legacy != null) {
            val migrated = setOf(legacy)
            p.edit().putStringSet("gmail_accounts", migrated).apply()
            migrated
        } else emptySet()
    }

    fun addGmailAccount(context: Context, email: String) {
        val current = getGmailAccounts(context).toMutableSet()
        current.add(email)
        prefs(context).edit().putStringSet("gmail_accounts", current).apply()
    }

    fun removeGmailAccount(context: Context, email: String) {
        val current = getGmailAccounts(context).toMutableSet()
        current.remove(email)
        prefs(context).edit().putStringSet("gmail_accounts", current).apply()
    }

    // Backward-compat: returns first account, used by code that doesn't loop
    fun getGmailAccount(context: Context): String? =
        getGmailAccounts(context).firstOrNull()

    fun getLastSyncTime(context: Context): Long =
        prefs(context).getLong("last_sync_time", 0L)

    fun setLastSyncTime(context: Context, time: Long) =
        prefs(context).edit().putLong("last_sync_time", time).apply()

    fun getLookbackOverride(context: Context): String =
        prefs(context).getString("lookback_override", "") ?: ""

    fun setLookbackOverride(context: Context, value: String) =
        prefs(context).edit().putString("lookback_override", value).apply()
}
