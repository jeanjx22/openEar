package com.example.openeartodo

import com.google.gson.Gson
import com.google.gson.JsonObject
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.Header
import retrofit2.http.POST
import java.util.concurrent.TimeUnit

object LlmClient {

    data class ProviderConfig(val baseUrl: String, val model: String)

    val providers = mapOf(
        "cohere" to ProviderConfig("https://api.cohere.com/compatibility/v1/", "command-r-08-2024"),
        "groq" to ProviderConfig("https://api.groq.com/openai/v1/", "llama-3.3-70b-versatile")
    )

    interface ChatApi {
        @POST("chat/completions")
        suspend fun complete(
            @Header("Authorization") auth: String,
            @Body request: ChatRequest
        ): ChatResponse
    }

    data class ChatRequest(
        val model: String,
        val messages: List<Msg>,
        val temperature: Double = 0.1
    )
    data class Msg(val role: String, val content: String)
    data class ChatResponse(val choices: List<Choice>)
    data class Choice(val message: Msg)

    data class ExtractedTodo(
        val text: String,
        val eventTime: String?
    )

    data class ExtractionResult(
        val todos: List<ExtractedTodo>,
        val summary: String
    )

    private val httpClient = OkHttpClient.Builder()
        .readTimeout(60, TimeUnit.SECONDS)
        .connectTimeout(30, TimeUnit.SECONDS)
        .build()

    private fun buildApi(baseUrl: String): ChatApi =
        Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(httpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ChatApi::class.java)

    private fun cleanJson(text: String): String =
        text.trim().removePrefix("```json").removePrefix("```").removeSuffix("```").trim()

    suspend fun classifyEmailsBatch(
        apiKey: String,
        provider: String,
        emails: List<Triple<String, String, String>>
    ): List<Boolean> {
        val config = providers[provider] ?: providers["cohere"]!!
        val api = buildApi(config.baseUrl)

        val emailLines = emails.mapIndexed { i, (sender, subject, snippet) ->
            "${i + 1}. From: $sender | Subject: $subject | Preview: ${snippet.take(150)}"
        }.joinToString("\n")

        val systemPrompt = """Classify each email as IMPORTANT or NOT IMPORTANT. An email is IMPORTANT if it likely requires the user's attention, awareness, or action.

IMPORTANT examples: appointments, school notices, medical info, security alerts, bills, schedule changes, forms to fill, events to attend, deadlines, deliveries.
NOT IMPORTANT examples: marketing, promotions, newsletters, social media notifications, automated receipts, spam.

Return ONLY a JSON array of booleans in the same order. Example: [true, false, true]"""

        val request = ChatRequest(
            model = config.model,
            messages = listOf(
                Msg("system", systemPrompt),
                Msg("user", "Classify these emails:\n$emailLines")
            )
        )

        val response = api.complete("Bearer $apiKey", request)
        val content = response.choices.firstOrNull()?.message?.content
            ?: return List(emails.size) { false }

        return try {
            val parsed = Gson().fromJson(cleanJson(content), Array<Boolean>::class.java).toList()
            if (parsed.size == emails.size) parsed else List(emails.size) { false }
        } catch (e: Exception) {
            List(emails.size) { false }
        }
    }

    suspend fun extractTodosWithSummary(
        apiKey: String,
        provider: String,
        sender: String,
        subject: String,
        body: String
    ): ExtractionResult {
        val config = providers[provider] ?: providers["cohere"]!!
        val api = buildApi(config.baseUrl)

        val systemPrompt = """Analyze this email and return a JSON object with two fields:

1. "todos": array of objects, each with "text" (short task, under 80 chars) and "event_time" (ISO 8601 datetime if a specific date/time is mentioned, or null).
2. "summary": a 2-3 sentence summary of the email covering the key points.

Extract ANYTHING that might need the user's attention, awareness, or action. When in doubt, include it:
- Appointments, meetings, events (even just "be aware" reminders)
- Deadlines, due dates, expirations
- Requests requiring a response (RSVPs, forms, signatures, replies)
- Security alerts, password changes, account warnings
- Payments due, bills, invoices
- Schedule changes, cancellations, delays
- Medical: appointments, prescriptions, test results, follow-ups
- School: events, homework, supplies, teacher communications
- Deliveries, pickups, reservations
- Anything time-sensitive or that the user should not forget

Include dates/times in the task text when mentioned.
For event_time: use ISO 8601 format (e.g. "2026-05-20T14:00:00"). Use the date/time from the email. If only a date is mentioned with no time, default to 09:00. If no specific date/time, set event_time to null.

Only skip: pure marketing/promotions, social media notifications, automated "no-reply" receipts with no action needed.

Return ONLY valid JSON:
{"todos": [{"text": "Dentist appointment", "event_time": "2026-05-20T14:00:00"}, {"text": "Review security alert", "event_time": null}], "summary": "Summary..."}"""

        val request = ChatRequest(
            model = config.model,
            messages = listOf(
                Msg("system", systemPrompt),
                Msg("user", "From: $sender\nSubject: $subject\n\n${body.take(3000)}")
            )
        )

        val response = api.complete("Bearer $apiKey", request)
        val content = response.choices.firstOrNull()?.message?.content
            ?: return ExtractionResult(emptyList(), "")

        return try {
            val json = Gson().fromJson(cleanJson(content), JsonObject::class.java)
            val summary = json.get("summary")?.asString ?: ""
            val todosArray = json.getAsJsonArray("todos") ?: return ExtractionResult(emptyList(), summary)
            val todos = todosArray.map { elem ->
                if (elem.isJsonObject) {
                    val obj = elem.asJsonObject
                    ExtractedTodo(
                        text = obj.get("text")?.asString ?: "",
                        eventTime = obj.get("event_time")?.let { if (it.isJsonNull) null else it.asString }
                    )
                } else {
                    ExtractedTodo(text = elem.asString, eventTime = null)
                }
            }.filter { it.text.isNotBlank() }
            ExtractionResult(todos, summary)
        } catch (e: Exception) {
            ExtractionResult(emptyList(), "")
        }
    }
}
