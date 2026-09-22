package android.graphics
// Minimal stand-in for android.graphics.Rect's public shape, for standalone kotlinc compilation.
data class Rect(var left: Int = 0, var top: Int = 0, var right: Int = 0, var bottom: Int = 0)
