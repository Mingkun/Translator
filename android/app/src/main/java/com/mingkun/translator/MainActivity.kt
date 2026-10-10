package com.mingkun.translator

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Bundle
import android.webkit.WebChromeClient
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
                intent.type = "image/*"
                startActivityForResult(android.content.Intent.createChooser(intent, "选择图片"), 1001)
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
            web.loadUrl("http://43.165.188.170/Translator/")
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
}
