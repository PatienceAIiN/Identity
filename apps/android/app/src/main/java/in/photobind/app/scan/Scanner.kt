package `in`.photobind.app.scan

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import `in`.photobind.app.ui.*
import java.util.concurrent.Executors

/**
 * Live QR scanner. Recognition runs on-device via ML Kit — camera frames never
 * leave the phone, which matters because what is being scanned is often
 * somebody's identity code.
 *
 * This composable only reports decoded text. Deciding whether that text is one
 * of our codes, and asking the server about it, is the caller's job, so this
 * file holds no knowledge of links or accounts.
 */
@Composable
fun CameraScanner(
    modifier: Modifier = Modifier,
    paused: Boolean = false,
    onDecoded: (String) -> Unit,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var granted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED)
    }
    var denied by remember { mutableStateOf(false) }
    val ask = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()) { ok ->
        granted = ok; denied = !ok
    }
    LaunchedEffect(Unit) { if (!granted) ask.launch(Manifest.permission.CAMERA) }

    if (!granted) {
        Box(modifier.background(AppColors.surface), contentAlignment = Alignment.Center) {
            Text(
                if (denied) "Camera access was declined. You can still choose an image."
                else "Asking for camera access…",
                style = MaterialTheme.typography.bodyMedium, color = AppColors.muted,
                modifier = Modifier.padding(MD))
        }
        return
    }

    // A QR stays in frame for many frames; firing on each one would hammer the
    // server, so a decode is reported once until the caller resumes scanning.
    val pausedNow = rememberUpdatedState(paused)
    var handled by remember { mutableStateOf(false) }
    LaunchedEffect(paused) { if (!paused) handled = false }

    val executor = remember { Executors.newSingleThreadExecutor() }
    DisposableEffect(Unit) { onDispose { executor.shutdown() } }

    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            val previewView = PreviewView(ctx).apply {
                scaleType = PreviewView.ScaleType.FILL_CENTER
            }
            val providerFuture = ProcessCameraProvider.getInstance(ctx)
            providerFuture.addListener({
                val provider = providerFuture.get()
                val preview = Preview.Builder().build().also {
                    it.setSurfaceProvider(previewView.surfaceProvider)
                }
                val scanner = BarcodeScanning.getClient()
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                analysis.setAnalyzer(executor) { proxy: ImageProxy ->
                    val media = proxy.image
                    if (media == null || handled || pausedNow.value) {
                        proxy.close(); return@setAnalyzer
                    }
                    val image = InputImage.fromMediaImage(
                        media, proxy.imageInfo.rotationDegrees)
                    scanner.process(image)
                        .addOnSuccessListener { codes ->
                            val text = codes.firstOrNull {
                                it.format == Barcode.FORMAT_QR_CODE
                            }?.rawValue
                            if (!text.isNullOrBlank() && !handled) {
                                handled = true
                                onDecoded(text)
                            }
                        }
                        .addOnCompleteListener { proxy.close() }
                }
                runCatching {
                    provider.unbindAll()
                    provider.bindToLifecycle(lifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                }
            }, ContextCompat.getMainExecutor(ctx))
            previewView
        },
    )
}
