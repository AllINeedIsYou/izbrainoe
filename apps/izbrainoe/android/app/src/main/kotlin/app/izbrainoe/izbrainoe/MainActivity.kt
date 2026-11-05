package app.izbrainoe.izbrainoe

import android.app.Activity
import android.content.Intent
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.media.MediaMetadataRetriever
import android.webkit.MimeTypeMap
import java.util.UUID
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
    private var galleryResult: MethodChannel.Result? = null
    private val galleryRequest = 9137
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "app.izbrainoe/media")
            .setMethodCallHandler { call, result ->
                if(call.method == "pickGallery") {
                    if(galleryResult != null) { result.error("gallery", "Галерея уже открыта", null); return@setMethodCallHandler }
                    galleryResult = result
                    try {
                        val intent = if(Build.VERSION.SDK_INT >= 33) Intent(MediaStore.ACTION_PICK_IMAGES).apply {
                            putExtra(MediaStore.EXTRA_PICK_IMAGES_MAX, minOf(30,MediaStore.getPickImagesMaxLimit()))
                        } else Intent(Intent.ACTION_GET_CONTENT).apply {
                            type = "*/*"
                            putExtra(Intent.EXTRA_MIME_TYPES,arrayOf("image/*","video/*"))
                            putExtra(Intent.EXTRA_ALLOW_MULTIPLE,true)
                            addCategory(Intent.CATEGORY_OPENABLE)
                        }
                        startActivityForResult(intent,galleryRequest)
                    } catch (_: Exception) {galleryResult=null;result.error("gallery","Не удалось открыть галерею",null)}
                    return@setMethodCallHandler
                }
                if (call.method != "previewImage" && call.method != "previewMedia") {result.notImplemented(); return@setMethodCallHandler}
                val path = call.argument<String>("path")
                if (path == null) {result.error("image", "Не найдено изображение", null);return@setMethodCallHandler}
                Executors.newSingleThreadExecutor().apply {
                    execute {
                        try {
                            if(call.method == "previewMedia") {
                                val retriever=MediaMetadataRetriever()
                                try {
                                    retriever.setDataSource(path)
                                    if(call.argument<Boolean>("video") != true) {
                                        val bytes=retriever.embeddedPicture
                                        runOnUiThread {result.success(bytes)}
                                    } else {
                                        val frame=retriever.getFrameAtTime(0,MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                                        if(frame==null) {runOnUiThread {result.success(null)}}
                                        else {
                                            val scale=minOf(1.0,640.0/maxOf(frame.width,frame.height))
                                            val small=Bitmap.createScaledBitmap(frame,maxOf(1,(frame.width*scale).toInt()),maxOf(1,(frame.height*scale).toInt()),true)
                                            val output=ByteArrayOutputStream();small.compress(Bitmap.CompressFormat.JPEG,72,output)
                                            if(small!==frame)small.recycle();frame.recycle()
                                            val bytes=output.toByteArray();runOnUiThread {result.success(bytes)}
                                        }
                                    }
                                } finally {retriever.release()}
                                return@execute
                            }
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
    @Deprecated("Flutter activity result bridge")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if(requestCode!=galleryRequest) {super.onActivityResult(requestCode,resultCode,data);return}
        val reply=galleryResult ?: return
        galleryResult=null
        if(resultCode!=Activity.RESULT_OK) {reply.success(emptyList<Any>());return}
        val uris=mutableListOf<android.net.Uri>()
        data?.clipData?.let {clip-> for(i in 0 until minOf(clip.itemCount,30))uris.add(clip.getItemAt(i).uri)}
        if(uris.isEmpty())data?.data?.let {uris.add(it)}
        Executors.newSingleThreadExecutor().apply {
            execute {
                val created=mutableListOf<File>()
                try {
                    val files=uris.map {uri->
                        var name="Фото"
                        contentResolver.query(uri,arrayOf(OpenableColumns.DISPLAY_NAME),null,null,null)?.use {c-> if(c.moveToFirst())name=c.getString(0) ?: name}
                        if(!name.contains('.')) {
                            val ext=MimeTypeMap.getSingleton().getExtensionFromMimeType(contentResolver.getType(uri) ?: "") ?: "jpg"
                            name+=".$ext"
                        }
                        val file=File(cacheDir,"gallery-${UUID.randomUUID()}");created.add(file)
                        var size=0L
                        contentResolver.openInputStream(uri)?.use {input->file.outputStream().use {output->
                            val buffer=ByteArray(65536)
                            while(true){val read=input.read(buffer);if(read<0)break;size+=read;if(size>2L*1024*1024*1024)throw IllegalArgumentException("size");output.write(buffer,0,read)}
                        }} ?: throw IllegalArgumentException("file")
                        mapOf("path" to file.absolutePath,"name" to name)
                    }
                    runOnUiThread {reply.success(files)}
                } catch (_: Exception) {created.forEach {it.delete()};runOnUiThread {reply.error("gallery","Не удалось прочитать выбранные файлы",null)}}
                finally {shutdown()}
            }
        }
    }

}
