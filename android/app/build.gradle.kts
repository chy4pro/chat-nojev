import java.io.File
import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing: reads a properties file kept OUTSIDE the repo
// (storeFile / storePassword / keyAlias / keyPassword), named by the
// JEV_KEYSTORE_PROPS env var. Without it, release builds are unsigned.
//
// Upstream defaulted this to a Windows path ('H:/android/keys/...'). Gradle's
// file() resolves a string through a URI, and on Linux a drive-letter prefix
// fails to convert at configuration time — the build dies before it can decide
// whether to sign, so an unsigned build was impossible off Windows. No default:
// unset means unsigned, which is what the comment above always claimed.
val releaseProps = Properties().apply {
    val path = System.getenv("JEV_KEYSTORE_PROPS")?.takeIf { it.isNotBlank() }
    val f = path?.let { File(it) }
    if (f != null && f.exists()) FileInputStream(f).use { load(it) }
}

android {
    namespace = "com.jev.probe"
    compileSdk = 35

    defaultConfig {
        // 和上游同一个 id 的话，两个应用在手机上只能装一个，而且签名不同时互相覆盖不了。
        // 这个分支用自己的 id，可以和上游那个并排装、来回比。代码包名(namespace)不动。
        applicationId = "com.jev.probe.nojev"
        minSdk = 30
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        // ML Kit's bundled Chinese recognizer ships native libs for every ABI.
        // The target phone (and every phone this can run on: minSdk 30) is
        // arm64, so keep only that one — the other three are dead weight.
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        if (releaseProps.isNotEmpty()) {
            create("release") {
                storeFile = file(releaseProps.getProperty("storeFile"))
                storePassword = releaseProps.getProperty("storePassword")
                keyAlias = releaseProps.getProperty("keyAlias")
                keyPassword = releaseProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release")
        }
    }

    // Uncompressed, page-aligned .so files: required for the 16 KB page-size
    // devices Android 15+ ships, and it lets the loader mmap the ML Kit natives
    // instead of unpacking them at install time.
    packaging {
        jniLibs {
            useLegacyPackaging = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    // On-device OCR. The *bundled* Chinese model (not the play-services variant):
    // it works on phones with no Google Play services and needs no model download.
    implementation("com.google.mlkit:text-recognition-chinese:16.0.1")
}
