package com.example.openeartodo

import androidx.lifecycle.LiveData
import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query

@Entity(tableName = "allowed_sender")
data class AllowedSender(
    @PrimaryKey val pattern: String,
    val label: String,
    val createdAt: Long = System.currentTimeMillis()
)

@Dao
interface AllowedSenderDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(sender: AllowedSender)

    @Query("SELECT * FROM allowed_sender ORDER BY createdAt DESC")
    fun getAll(): LiveData<List<AllowedSender>>

    @Query("SELECT * FROM allowed_sender")
    suspend fun getAllSync(): List<AllowedSender>

    @Delete
    suspend fun delete(sender: AllowedSender)
}
