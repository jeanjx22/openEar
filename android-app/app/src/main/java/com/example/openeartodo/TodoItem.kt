package com.example.openeartodo

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "todoitem")
data class TodoItem(
    val text: String,
    val createdAt: Long = System.currentTimeMillis(),
    var isCompleted: Boolean = false,
    @PrimaryKey(autoGenerate = true) val id: Long = 0
)