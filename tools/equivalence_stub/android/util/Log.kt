package android.util
// Minimal stand-in for android.util.Log, for standalone kotlinc compilation.
object Log {
    fun i(tag: String, msg: String): Int { println("I/$tag: $msg"); return 0 }
    fun w(tag: String, msg: String): Int { println("W/$tag: $msg"); return 0 }
    fun e(tag: String, msg: String): Int { println("E/$tag: $msg"); return 0 }
    fun d(tag: String, msg: String): Int { println("D/$tag: $msg"); return 0 }
}
