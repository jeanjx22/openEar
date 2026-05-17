package com.example.openeartodo

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

class IgnoredSenderAdapter(
    private val onDelete: (IgnoredSender) -> Unit
) : RecyclerView.Adapter<IgnoredSenderAdapter.ViewHolder>() {

    private val items = mutableListOf<IgnoredSender>()

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        val tvPattern: TextView = itemView.findViewById(R.id.tvPattern)
        val tvLabel: TextView = itemView.findViewById(R.id.tvLabel)
        val btnDelete: ImageButton = itemView.findViewById(R.id.btnDelete)
    }

    fun setItems(newItems: List<IgnoredSender>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_sender, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val sender = items[position]
        holder.tvPattern.text = sender.pattern
        holder.tvLabel.text = "Excluded"
        holder.btnDelete.setOnClickListener { onDelete(sender) }
    }

    override fun getItemCount() = items.size
}
