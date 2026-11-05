package app.izbrainoe.izbrainoe

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.Executors
import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.os.Build
import android.os.PersistableBundle
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var secureViews = 0
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "app.izbrainoe/media")
            .setMethodCallHandler { call, result ->
                if (call.method != "previewImage") {result.notImplemented(); return@setMethodCallHandler}
                val path = call.argument<String>("path")
                if (path == null) {result.error("image", "Не найдено изображение", null);return@setMethodCallHandler}
                Executors.newSingleThreadExecutor().apply {
                    execute {
                        try {
                            val picture = if (Build.VERSION.SDK_INT >= 28) {
                                ImageDecoder.decodeBitmap(ImageDecoder.createSource(File(path))) {decoder, info, _ ->
                                    val scale = minOf(1.0, 1536.0 / maxOf(info.size.width, info.size.height))
                                    decoder.setTargetSize(maxOf(1,(info.size.width*scale).toInt()),maxOf(1,(info.size.height*scale).toInt()))
                                    decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                                }
                            } else {
                                val bounds=BitmapFactory.Options().apply {inJustDecodeBounds=true}
                                BitmapFactory.decodeFile(path,bounds)
                                val options=BitmapFactory.Options().apply {inSampleSize=maxOf(1,maxOf(bounds.outWidth,bounds.outHeight)/1536)}
                                BitmapFactory.decodeFile(path,options) ?: throw IllegalArgumentException("image")
                            }
                            val output=ByteArrayOutputStream()
                            picture.compress(Bitmap.CompressFormat.JPEG,78,output);picture.recycle()
                            val bytes=output.toByteArray()
                            runOnUiThread {result.success(bytes)}
                        } catch (_: Exception) {runOnUiThread {result.error("image", "Не удалось открыть изображение", null)}}
                        finally {shutdown()}
                    }
                }
            }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "app.izbrainoe/security")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "protect" -> {
                        secureViews = maxOf(0, secureViews + if (call.argument<Boolean>("enabled") == true) 1 else -1)
                        if (secureViews > 0) window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                        else window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                        result.success(null)
                    }
                    "copySecret" -> {
                        val text = call.argument<String>("text") ?: ""
                        val clip = ClipData.newPlainText("Изbrainое", text)
                        if (Build.VERSION.SDK_INT >= 24) {
                            clip.description.extras = PersistableBundle().apply {
                                putBoolean("android.content.extra.IS_SENSITIVE", true)
                            }
                        }
                        (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(clip)
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }
}
