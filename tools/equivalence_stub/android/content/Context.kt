package android.content
// Minimal stand-in for android.content.Context + SharedPreferences, just enough
// surface for Prefs.kt to type-check under standalone kotlinc.
interface SharedPreferences {
    interface Editor {
        fun putString(key: String, value: String?): Editor
        fun putBoolean(key: String, value: Boolean): Editor
        fun putInt(key: String, value: Int): Editor
        fun putLong(key: String, value: Long): Editor
        fun putFloat(key: String, value: Float): Editor
        fun putStringSet(key: String, value: Set<String>?): Editor
        fun remove(key: String): Editor
        fun clear(): Editor
        fun apply()
        fun commit(): Boolean
    }
    fun getString(key: String, defValue: String?): String?
    fun getStringSet(key: String, defValue: Set<String>?): Set<String>?
    fun getBoolean(key: String, defValue: Boolean): Boolean
    fun getInt(key: String, defValue: Int): Int
    fun getLong(key: String, defValue: Long): Long
    fun getFloat(key: String, defValue: Float): Float
    fun contains(key: String): Boolean
    fun edit(): Editor
}

abstract class Context {
    companion object { const val MODE_PRIVATE = 0 }
    abstract fun getSharedPreferences(name: String, mode: Int): SharedPreferences
}
