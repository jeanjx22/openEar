package com.example.openeartodo

import android.accounts.Account
import android.content.Context
import android.util.Base64
import com.google.android.gms.auth.GoogleAuthUtil
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.Path
import retrofit2.http.Query
import java.util.concurrent.TimeUnit

object GmailClient {
    private const val BASE_URL = "https://gmail.googleapis.com/"
    private const val SCOPE = "oauth2:https://www.googleapis.com/auth/gmail.readonly"

    data class EmailInfo(
        val gmailId: String,
        val sender: String,
        val subject: String,
        val date: String,
        val snippet: String
    )

    data class EmailPage(
        val emails: List<EmailInfo>,
        val nextPageToken: String?
    )

    interface GmailApi {
        @GET("gmail/v1/users/me/messages")
        suspend fun listMessages(
            @Header("Authorization") auth: String,
            @Query("maxResults") maxResults: Int = 20,
            @Query("q") query: String? = null,
            @Query("pageToken") pageToken: String? = null
        ): MessageListResponse

        @GET("gmail/v1/users/me/messages/{id}")
        suspend fun getMessage(
            @Header("Authorization") auth: String,
            @Path("id") id: String,
            @Query("format") format: String = "metadata",
            @Query("metadataHeaders") headers: List<String>? = null
        ): Message
    }

    data class MessageListResponse(
        val messages: List<MessageRef>?,
        val nextPageToken: String?
    )
    data class MessageRef(val id: String)
    data class Message(val id: String, val snippet: String?, val payload: Payload?)
    data class Payload(
        val mimeType: String?,
        val headers: List<GmailHeader>?,
        val body: Body?,
        val parts: List<Part>?
    )
    data class GmailHeader(val name: String, val value: String)
    data class Body(val data: String?, val size: Int?)
    data class Part(
        val mimeType: String?,
        val body: Body?,
        val parts: List<Part>?
    )

    private val api: GmailApi by lazy {
        Retrofit.Builder()
            .baseUrl(BASE_URL)
            .addConverterFactory(GsonConverterFactory.create())
            .client(
                OkHttpClient.Builder()
                    .connectTimeout(30, TimeUnit.SECONDS)
                    .readTimeout(30, TimeUnit.SECONDS)
                    .build()
            )
            .build()
            .create(GmailApi::class.java)
    }

    suspend fun getAccessToken(context: Context, email: String): String {
        return withContext(Dispatchers.IO) {
            GoogleAuthUtil.getToken(context, Account(email, "com.google"), SCOPE)
        }
    }

    suspend fun fetchEmails(
        token: String,
        query: String? = null,
        pageToken: String? = null,
        maxResults: Int = 20
    ): EmailPage {
        val auth = "Bearer $token"
        val response = api.listMessages(auth, maxResults, query, pageToken)
        val refs = response.messages ?: return EmailPage(emptyList(), null)

        val emails = coroutineScope {
            refs.map { ref ->
                async(Dispatchers.IO) {
                    val msg = api.getMessage(
                        auth, ref.id,
                        format = "metadata",
                        headers = listOf("From", "Subject", "Date")
                    )
                    val hdrs = msg.payload?.headers ?: emptyList()
                    EmailInfo(
                        gmailId = msg.id,
                        sender = hdrs.find { it.name.equals("From", true) }?.value ?: "Unknown",
                        subject = hdrs.find { it.name.equals("Subject", true) }?.value ?: "(no subject)",
                        date = hdrs.find { it.name.equals("Date", true) }?.value ?: "",
                        snippet = msg.snippet ?: ""
                    )
                }
            }.awaitAll()
        }

        return EmailPage(emails, response.nextPageToken)
    }

    suspend fun fetchRecentEmails(token: String, maxResults: Int = 20): List<EmailInfo> {
        return fetchEmails(token, maxResults = maxResults).emails
    }

    suspend fun fetchEmailBody(token: String, gmailId: String): String {
        val msg = api.getMessage("Bearer $token", gmailId, format = "full")
        return extractBody(msg.payload) ?: msg.snippet ?: ""
    }

    private fun extractBody(payload: Payload?): String? {
        if (payload == null) return null
        if (payload.mimeType == "text/plain" && payload.body?.data != null) {
            return decode(payload.body.data)
        }
        payload.parts?.forEach { part ->
            if (part.mimeType == "text/plain" && part.body?.data != null) {
                return decode(part.body.data)
            }
            if (part.parts != null) {
                val nested = extractBody(Payload(null, null, null, part.parts))
                if (nested != null) return nested
            }
        }
        return null
    }

    private fun decode(data: String): String =
        String(Base64.decode(data, Base64.URL_SAFE), Charsets.UTF_8)
}
