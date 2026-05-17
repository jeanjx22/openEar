package com.example.openeartodo

import androidx.lifecycle.LiveData
import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update

@Dao
interface TodoDao {
    @Insert
    suspend fun insert(todo: TodoItem)

    @Update
    suspend fun update(todo: TodoItem)

    @Delete
    suspend fun delete(todo: TodoItem)

    @Query("SELECT * FROM todoitem WHERE isCompleted = 0 AND (snoozedUntil IS NULL OR snoozedUntil < :now) ORDER BY createdAt DESC")
    fun getActiveTodos(now: Long = System.currentTimeMillis()): LiveData<List<TodoItem>>

    @Query("SELECT * FROM todoitem WHERE isCompleted = 0")
    suspend fun getActiveTodosSync(): List<TodoItem>

    @Query("SELECT * FROM todoitem WHERE isCompleted = 0 AND snoozedUntil IS NOT NULL AND snoozedUntil <= :now")
    suspend fun getUnsnoozedTodos(now: Long = System.currentTimeMillis()): List<TodoItem>

    @Query("SELECT * FROM todoitem WHERE id = :id")
    suspend fun getById(id: Long): TodoItem?

    @Query("SELECT * FROM todoitem WHERE isCompleted = 1 ORDER BY completedAt DESC")
    fun getCompletedTodos(): LiveData<List<TodoItem>>

    @Query("DELETE FROM todoitem WHERE isCompleted = 1 AND completedAt < :cutoff")
    suspend fun deleteCompletedBefore(cutoff: Long)
}
