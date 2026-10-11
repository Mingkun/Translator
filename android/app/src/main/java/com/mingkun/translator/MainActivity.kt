package com.mingkun.translator

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Bundle
import android.webkit.WebChromeClient
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.PermissionRequest
import android.content.pm.PackageManager

class MainActivity : Activity() {
    private lateinit var web: WebView
    private var filePathCallback: android.webkit.ValueCallback<Array<android.net.Uri>>? = null
    private var pendingPermRequest: PermissionRequest? = null
    private var updateDownloadId = -1L

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            try {
                java.io.File(getExternalFilesDir(null), "crash.txt").writeText(
                    "time=" + System.currentTimeMillis() + "\nthread=" + t.name + "\n" + android.util.Log.getStackTraceString(e))
            } catch (_: Exception) {}
            android.os.Process.killProcess(android.os.Process.myPid())
        }
        val lastCrash = try {
            val cf = java.io.File(getExternalFilesDir(null), "crash.txt")
            if (cf.isFile) cf.readText().also { cf.delete() } else null
        } catch (_: Exception) { null }
        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.mediaPlaybackRequiresUserGesture = false
        web.addJavascriptInterface(Bridge(), "AppBridge")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                return false
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                wv: WebView?,
                cb: android.webkit.ValueCallback<Array<android.net.Uri>>,
                params: FileChooserParams?
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = cb
                val intent = android.content.Intent(android.content.Intent.ACTION_GET_CONTENT)
                intent.addCategory(android.content.Intent.CATEGORY_OPENABLE)
                // 根据 input 的 accept 属性选择类型（图片/音频/任意）
                val accept = params?.acceptTypes?.firstOrNull { it.isNotBlank() }?.trim() ?: "*/*"
                intent.type = accept
                val labels = if (accept.startsWith("audio")) "选择音频文件" else if (accept.startsWith("image")) "选择图片" else "选择文件"
                startActivityForResult(android.content.Intent.createChooser(intent, labels), 1001)
                return true
            }

            override fun onPermissionRequest(request: PermissionRequest?) {
                if (request == null) return
                if (!request.resources.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) {
                    runOnUiThread { request.deny() }
                    return
                }
                if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                    runOnUiThread { request.grant(request.resources) }
                } else {
                    pendingPermRequest = request
                    try { requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 2001) } catch (_: Exception) { runOnUiThread { request.deny() } }
                }
            }
        }
        web.setDownloadListener { url, _, contentDisposition, mimeType, _ ->
            try {
                val name = android.webkit.URLUtil.guessFileName(url, contentDisposition, mimeType)
                val isApk = name.endsWith(".apk", true)
                val request = android.app.DownloadManager.Request(android.net.Uri.parse(url))
                request.setMimeType(mimeType)
                request.setTitle(name)
                val cookies = android.webkit.CookieManager.getInstance().getCookie(url)
                if (cookies != null) request.addRequestHeader("cookie", cookies)
                request.addRequestHeader("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
                request.setNotificationVisibility(android.app.DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                if (isApk) {
                    request.setDestinationInExternalFilesDir(this, null, "translator-update.apk")
                } else {
                    request.setDestinationInExternalPublicDir(android.os.Environment.DIRECTORY_DOWNLOADS, name)
                }
                Thread {
                    try {
                        val conn = java.net.URL(url).openConnection() as java.net.HttpURLConnection
                        conn.connectTimeout = 15000; conn.readTimeout = 60000
                        val file = java.io.File(getExternalFilesDir(null), if (isApk) "translator-update.apk" else name)
                        java.io.FileOutputStream(file).use { output -> conn.inputStream.use { input -> input.copyTo(output) } }
                        conn.disconnect()
                        if (isApk) runOnUiThread { openDownloadedApk() }
                    } catch (_: Exception) {}
                }.start()
            } catch (e: Exception) {
                try {
                    startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url)))
                } catch (_: Exception) {
                }
            }
        }
        try {
            if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 2002)
            }
        } catch (_: Exception) {
        }
        setContentView(web)
        try {
            val dlReceiver = object : android.content.BroadcastReceiver() {
                override fun onReceive(context: android.content.Context?, intent: android.content.Intent?) {
                    val id = intent?.getLongExtra(android.app.DownloadManager.EXTRA_DOWNLOAD_ID, -1L) ?: -1L
                    if (id > 0 && id == updateDownloadId) openDownloadedApk()
                }
            }
            val dlFilter = android.content.IntentFilter(android.app.DownloadManager.ACTION_DOWNLOAD_COMPLETE)
            if (android.os.Build.VERSION.SDK_INT >= 33) registerReceiver(dlReceiver, dlFilter, android.content.Context.RECEIVER_NOT_EXPORTED)
            else registerReceiver(dlReceiver, dlFilter)
        } catch (_: Exception) {
        }

        if (savedInstanceState != null) {
            try { web.restoreState(savedInstanceState) } catch (_: Exception) {}
        } else if (lastCrash != null) {
            val esc = lastCrash.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            web.loadDataWithBaseURL(null, "<html><body style='font-family:monospace;padding:16px;font-size:12px;white-space:pre-wrap;line-height:1.5'>" +
                "<h3>翻译鹦鹉 v1.1 上次启动崩溃现场</h3>" +
                "<p style='color:#b02a37'>请截图发给机器人排查；重新打开应用会再次尝试正常启动</p><hr>" +
                esc.take(6000) + "</body></html>", "text/html", "utf-8", null)
        } else {
            web.loadUrl("https://5130599.best/Translator/")
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: android.content.Intent?) {
        if (requestCode == 1001) {
            val cb = filePathCallback
            filePathCallback = null
            val results = if (resultCode == RESULT_OK && data?.data != null) arrayOf(data.data!!) else null
            cb?.onReceiveValue(results)
            return
        }
        super.onActivityResult(requestCode, resultCode, data)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (requestCode == 2001) {
            val req = pendingPermRequest
            pendingPermRequest = null
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED && req != null) {
                runOnUiThread { req.grant(req.resources) }
            } else {
                req?.let { runOnUiThread { it.deny() } }
            }
            return
        }
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
    }

    private fun openDownloadedApk() {
        try {
            val dir = getExternalFilesDir(null) ?: return
            val file = java.io.File(dir, "translator-update.apk")
            if (!file.exists()) return
            val uri = androidx.core.content.FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
            val install = android.content.Intent(android.content.Intent.ACTION_VIEW)
            install.setDataAndType(uri, "application/vnd.android.package-archive")
            install.addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
            install.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
            startActivity(install)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) {
            web.goBack()
        } else {
            super.onBackPressed()
        }
    }

    inner class Bridge {
        @JavascriptInterface
        fun hasStoragePermission(): Boolean {
            return if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.os.Environment.isExternalStorageManager()
            } else {
                checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED
            }
        }

        @JavascriptInterface
        fun requestStoragePermission() {
            try {
                if (android.os.Build.VERSION.SDK_INT >= 30) {
                    val intent = android.content.Intent(android.provider.Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        android.net.Uri.parse("package:$packageName"))
                    startActivity(intent)
                } else {
                    requestPermissions(arrayOf(android.Manifest.permission.READ_EXTERNAL_STORAGE), 100)
                }
            } catch (_: Exception) {}
        }

        @JavascriptInterface
        fun searchFiles(ext: String): String {
            try {
                if (!hasStoragePermission()) return "[{\"n\":\"__NEED_PERM__\"}]"
                val exts = ext.lowercase().split(",").map { it.trim() }.filter { it.isNotEmpty() }.toSet()
                val audioE = setOf("mp3", "wav", "ogg", "m4a", "flac", "aac", "wma", "opus")
                val videoE = setOf("mp4", "webm", "mkv", "avi", "mov", "3gp", "m4v", "ts", "flv")
                val checkMedia = exts.isEmpty() || exts.any { audioE.contains(it) || videoE.contains(it) }
                val results = org.json.JSONArray()
                val queue = ArrayDeque<java.io.File>()
                queue.add(java.io.File("/storage/emulated/0"))
                try {
                    java.io.File("/storage").listFiles()?.forEach { d ->
                        if (d.isDirectory && d.name != "emulated" && d.name != "self") queue.add(d)
                    }
                } catch (_: Exception) {}
                val skipDirs = setOf("Android", "LOST.DIR", "obb", ".thumbnails", "cache")
                var count = 0
                while (queue.isNotEmpty() && count < 100000) {
                    val dir = queue.removeFirst()
                    val files = dir.listFiles() ?: continue
                    for (f in files) {
                        if (f.name.startsWith(".")) continue
                        if (f.isDirectory) { if (f.name !in skipDirs) queue.add(f); continue }
                        val fe = f.extension.lowercase()
                        if (exts.isNotEmpty() && !exts.contains(fe)) continue
                        if (checkMedia && (audioE.contains(fe) || videoE.contains(fe))) {
                            if (!isRealMedia(f)) continue
                        }
                        val o = org.json.JSONObject()
                        o.put("n", f.name); o.put("d", false)
                        o.put("s", f.length()); o.put("t", f.lastModified()); o.put("p", f.absolutePath)
                        results.put(o); count++
                    }
                }
                return results.toString()
            } catch (_: Exception) { return "[]" }
        }

        private fun isRealMedia(f: java.io.File): Boolean {
            try {
                java.io.FileInputStream(f).use { fis ->
                    val h = ByteArray(12)
                    val n = fis.read(h)
                    if (n < 4) return false
                    val b = { i: Int -> h[i].toInt() and 0xFF }
                    val m4 = String(h, 0, 4, Charsets.ISO_8859_1)
                    if (m4 == "fLaC") return true
                    if (m4 == "OggS") return true
                    if (m4 == "RIFF") return true
                    if (m4.startsWith("FLV")) return true
                    if (m4.startsWith("ID3")) return true
                    if (b(0) == 0xFF && (b(1) and 0xE0) == 0xE0) return true
                    if (b(0) == 0x1A && b(1) == 0x45 && b(2) == 0xDF && b(3) == 0xA3) return true
                    if (b(0) == 0x30 && b(1) == 0x26 && b(2) == 0xB2 && b(3) == 0x75) return true
                    if (b(0) == 0x47) return true
                    if (n >= 8) {
                        val tag = String(h, 4, 4, Charsets.ISO_8859_1)
                        if (tag == "ftyp") return true
                    }
                    return false
                }
            } catch (_: Exception) { return true }
        }

        @JavascriptInterface
        fun listFiles(path: String): String {
            try {
                if (!hasStoragePermission()) return "[]"
                val dir = if (path.isEmpty()) java.io.File("/storage/emulated/0") else java.io.File(path)
                if (!dir.exists() || !dir.isDirectory) return "[]"
                val items = dir.listFiles()?.sortedWith(compareByDescending<java.io.File> { it.isDirectory }.thenByDescending { it.lastModified() }) ?: emptyList()
                val arr = org.json.JSONArray()
                for (f in items.take(500)) {
                    if (f.name.startsWith(".")) continue
                    val o = org.json.JSONObject()
                    o.put("n", f.name)
                    o.put("d", f.isDirectory)
                    o.put("s", if (f.isFile) f.length() else 0)
                    o.put("t", f.lastModified())
                    o.put("p", f.absolutePath)
                    arr.put(o)
                }
                return arr.toString()
            } catch (_: Exception) { return "[]" }
        }

        @JavascriptInterface
        fun readFile(path: String): String {
            try {
                val f = java.io.File(path)
                if (!f.exists() || f.length() > 100 * 1024 * 1024) return ""
                val bytes = f.readBytes()
                return android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)
            } catch (_: Exception) { return "" }
        }
    }
}
