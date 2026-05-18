package com.example.openeartodo

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class TodoAdapter(
    private val items: MutableList<TodoItem>,
    private val onCheckedChanged: (TodoItem, Boolean) -> Unit,
    private val onAlarmClicked: (TodoItem) -> Unit
) : RecyclerView.Adapter<TodoAdapter.ViewHolder>() {

    private val timeFmt = SimpleDateFormat("MMM d, h:mm a", Locale.getDefault())

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        val cbComplete: CheckBox = itemView.findViewById(R.id.cbComplete)
        val tvText: TextView = itemView.findViewById(R.id.tvTodoText)
        val tvReminder: TextView = itemView.findViewById(R.id.tvReminder)
        val ivAlarm: ImageView = itemView.findViewById(R.id.ivAlarm)
        val ivEmail: ImageView = itemView.findViewById(R.id.ivEmail)
    }

    fun setItems(newItems: List<TodoItem>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return ViewHolder(inflater.inflate(R.layout.item_todo, parent, false))
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        holder.tvText.text = item.text

        holder.cbComplete.setOnCheckedChangeListener(null)
        holder.cbComplete.isChecked = item.isCompleted
        holder.cbComplete.setOnCheckedChangeListener { _, isChecked ->
            onCheckedChanged(item, isChecked)
        }

        val now = System.currentTimeMillis()
        val parts = mutableListOf<String>()
        if (item.eventAt != null) {
            parts.add("📅 Event: ${timeFmt.format(Date(item.eventAt))}")
        }
        val hasNotify = item.reminderAt != null && item.reminderAt > now
        val hasAlarm = item.alarmAt != null && item.alarmAt > now
        if (hasNotify) parts.add("🔔 Notification: ${timeFmt.format(Date(item.reminderAt!!))}")
        if (hasAlarm) parts.add("⏰ Alarm: ${timeFmt.format(Date(item.alarmAt!!))}")
        if (item.recurrence != null) parts.add("🔁 Repeats ${item.recurrence}")
        if (item.sourceAccount != null) parts.add("✉️ ${item.sourceAccount}")

        if (parts.isNotEmpty()) {
            holder.ivAlarm.alpha = if (hasNotify || hasAlarm) 1.0f else 0.6f
            holder.tvReminder.visibility = View.VISIBLE
            holder.tvReminder.text = parts.joinToString("\n")
        } else {
            holder.ivAlarm.alpha = 0.4f
            holder.tvReminder.visibility = View.GONE
        }
        holder.ivAlarm.setOnClickListener { onAlarmClicked(item) }

        // Email icon
        if (item.sourceEmailSummary != null) {
            holder.ivEmail.visibility = View.VISIBLE
            holder.ivEmail.setOnClickListener { view ->
                MaterialAlertDialogBuilder(view.context)
                    .setTitle("Source Email")
                    .setMessage(item.sourceEmailSummary)
                    .setPositiveButton("OK", null)
                    .show()
            }
        } else {
            holder.ivEmail.visibility = View.GONE
            holder.ivEmail.setOnClickListener(null)
        }
    }

    override fun getItemCount() = items.size
}
